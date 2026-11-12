"""Filesystem scanner.

Walks one or more root folders and builds a size tree. Design goals:

* Real disk usage, not apparent size: we use ``st_blocks * 512`` so sparse
  files (Docker.raw reports 1 TB but uses 7 GB) and iCloud "dataless" files
  are counted correctly.
* Hard links are counted once (tracked by device + inode).
* Never follows symlinks and never crosses into another mounted volume.
* Folders we are not allowed to read are kept in the tree and flagged
  ``denied`` so the UI can explain *why* space is invisible instead of
  silently dropping it.
* Memory stays bounded: once a folder is finished, children smaller than
  ``min_node_bytes`` are folded into a single "smaller items" bucket.

Only the Python standard library is used so the tool runs on the
``python3`` that ships with macOS (3.9+).
"""
from __future__ import annotations

import os
import stat
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional, Set, Tuple

SMALL_BUCKET = "(smaller items)"


class Node:
    """One file or folder in the size tree."""

    __slots__ = (
        "name",
        "size",
        "files",
        "is_dir",
        "children",
        "denied",
        "small_count",
        "parent",
        "mtime",
    )

    def __init__(self, name: str, is_dir: bool, parent: Optional["Node"] = None):
        self.name = name
        self.size = 0  # allocated bytes on disk (including children)
        self.files = 0  # number of files below (including self if file)
        self.is_dir = is_dir
        self.children: Optional[List["Node"]] = [] if is_dir else None
        self.denied = False  # True when the folder could not be read
        self.small_count = 0  # how many tiny items were folded into the bucket
        self.parent = parent
        self.mtime = 0.0

    # ------------------------------------------------------------------ paths
    @property
    def path(self) -> str:
        parts = []
        node: Optional[Node] = self
        while node is not None:
            parts.append(node.name)
            node = node.parent
        parts.reverse()
        if not parts:
            return "/"
        head = parts[0]
        if len(parts) == 1:
            return head
        return os.path.join(head, *parts[1:])

    def child(self, name: str) -> Optional["Node"]:
        for c in self.children or ():
            if c.name == name:
                return c
        return None

    def to_dict(self, depth: int = 1, limit: int = 200) -> dict:
        data = {
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "files": self.files,
            "is_dir": self.is_dir,
            "denied": self.denied,
            "mtime": self.mtime,
        }
        if self.is_dir:
            kids = sorted(self.children or (), key=lambda n: n.size, reverse=True)
            data["child_count"] = len(kids)
            if depth > 0:
                data["children"] = [k.to_dict(depth - 1, limit) for k in kids[:limit]]
            if self.small_count:
                data["small_count"] = self.small_count
        return data


class ScanProgress:
    """Thread-safe counters the UI can poll while a scan runs."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.files = 0
        self.dirs = 0
        self.bytes = 0
        self.denied = 0
        self.current = ""
        self.started = 0.0
        self.finished = 0.0
        self.running = False
        self.cancelled = threading.Event()

    def add(self, files: int, dirs: int, nbytes: int, current: str) -> None:
        with self.lock:
            self.files += files
            self.dirs += dirs
            self.bytes += nbytes
            self.current = current

    def snapshot(self) -> dict:
        with self.lock:
            elapsed = (self.finished or time.time()) - self.started if self.started else 0
            return {
                "running": self.running,
                "files": self.files,
                "dirs": self.dirs,
                "bytes": self.bytes,
                "denied": self.denied,
                "current": self.current,
                "elapsed": round(elapsed, 1),
            }


def allocated_size(st: os.stat_result) -> int:
    """Bytes actually used on disk. Falls back to apparent size."""
    blocks = getattr(st, "st_blocks", None)
    if blocks is None:
        return st.st_size
    return blocks * 512


class Scanner:
    """Builds a :class:`Node` tree for a list of roots."""

    def __init__(
        self,
        min_node_bytes: int = 1 * 1024 * 1024,
        workers: int = 8,
        skip: Iterable[str] = (),
        progress: Optional[ScanProgress] = None,
    ) -> None:
        self.min_node_bytes = min_node_bytes
        self.workers = max(1, workers)
        self.skip: Set[str] = {os.path.normpath(os.path.expanduser(p)) for p in skip}
        self.progress = progress or ScanProgress()
        self._seen_inodes: Set[Tuple[int, int]] = set()
        self._inode_lock = threading.Lock()
        if sys.getrecursionlimit() < 10000:
            sys.setrecursionlimit(10000)

    # ----------------------------------------------------------------- public
    def scan(self, roots: List[str]) -> Node:
        """Scan every root and return a synthetic top node holding them."""
        p = self.progress
        p.running = True
        p.started = time.time()
        p.finished = 0.0
        top = Node("", True)
        try:
            for root in roots:
                root = os.path.normpath(os.path.expanduser(root))
                if not os.path.lexists(root):
                    continue
                node = self.scan_root(root)
                node.parent = top
                top.children.append(node)
                top.size += node.size
                top.files += node.files
        finally:
            p.running = False
            p.finished = time.time()
        return top

    def scan_root(self, root: str) -> Node:
        """Scan a single root. Its first-level children run in parallel."""
        node = Node(root, True)
        try:
            st = os.lstat(root)
        except OSError:
            node.denied = True
            return node
        node.mtime = st.st_mtime
        if not stat.S_ISDIR(st.st_mode):
            node.is_dir = False
            node.children = None
            node.size = allocated_size(st)
            node.files = 1
            return node
        root_dev = st.st_dev
        entries, denied = self._list(root)
        if denied:
            node.denied = True
            with self.progress.lock:
                self.progress.denied += 1
            return node

        sub_dirs = []
        for entry in entries:
            child = self._entry_node(entry, node, root_dev)
            if child is None:
                continue
            if child.is_dir and not child.denied:
                sub_dirs.append((entry.path, child))
            node.children.append(child)

        if sub_dirs:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [pool.submit(self._walk, path, child, root_dev) for path, child in sub_dirs]
                for f in futures:
                    f.result()

        self._finish(node)
        return node

    # --------------------------------------------------------------- internal
    def _list(self, path: str):
        try:
            with os.scandir(path) as it:
                return list(it), False
        except (PermissionError, OSError):
            return [], True

    def _entry_node(self, entry: os.DirEntry, parent: Node, root_dev: int) -> Optional[Node]:
        """Create a node for a directory entry. Directories are returned empty
        (size 0) so the caller can decide how to walk them."""
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            n = Node(entry.name, True, parent)
            n.denied = True
            return n
        mode = st.st_mode
        if stat.S_ISLNK(mode):
            return None  # never follow or count links
        if stat.S_ISDIR(mode):
            if os.path.normpath(entry.path) in self.skip:
                return None
            if st.st_dev != root_dev and os.path.ismount(entry.path):
                return None  # another volume (external drive, network share)
            n = Node(entry.name, True, parent)
            n.mtime = st.st_mtime
            return n
        n = Node(entry.name, False, parent)
        n.mtime = st.st_mtime
        n.files = 1
        if st.st_nlink > 1:
            key = (st.st_dev, st.st_ino)
            with self._inode_lock:
                if key in self._seen_inodes:
                    return n  # counted already: size stays 0
                self._seen_inodes.add(key)
        n.size = allocated_size(st)
        return n

    def _walk(self, path: str, node: Node, root_dev: int) -> None:
        if self.progress.cancelled.is_set():
            return
        entries, denied = self._list(path)
        if denied:
            node.denied = True
            with self.progress.lock:
                self.progress.denied += 1
            return
        file_count = 0
        file_bytes = 0
        for entry in entries:
            child = self._entry_node(entry, node, root_dev)
            if child is None:
                continue
            node.children.append(child)
            if child.is_dir and not child.denied:
                self._walk(entry.path, child, root_dev)
            elif not child.is_dir:
                file_count += 1
                file_bytes += child.size
        self.progress.add(file_count, 1, file_bytes, path)
        self._finish(node)

    def _finish(self, node: Node) -> None:
        """Total up a finished folder and fold tiny children into a bucket."""
        total = 0
        files = 0
        keep: List[Node] = []
        small_bytes = 0
        small_files = 0
        small_count = 0
        for c in node.children or ():
            total += c.size
            files += c.files
            if c.size < self.min_node_bytes and not c.denied:
                small_bytes += c.size
                small_files += c.files
                small_count += 1 + c.small_count
            else:
                keep.append(c)
        if small_count:
            bucket = Node(SMALL_BUCKET, False, node)
            bucket.size = small_bytes
            bucket.files = small_files
            keep.append(bucket)
        node.children = keep
        node.small_count = small_count
        node.size = total
        node.files = files


# --------------------------------------------------------------------- helpers
def find(top: Node, path: str) -> Optional[Node]:
    """Locate the node for an absolute path inside a scanned tree."""
    path = os.path.normpath(os.path.expanduser(path))
    for root in top.children or ():
        rp = root.path
        if path == rp:
            return root
        prefix = rp.rstrip("/") + "/"
        if path.startswith(prefix):
            node = root
            for part in path[len(prefix):].split("/"):
                if not part:
                    continue
                node = node.child(part) if node.is_dir else None
                if node is None:
                    return None
            return node
    return None


def remove(node: Node) -> None:
    """Detach a node after it was deleted/moved and fix ancestor totals."""
    parent = node.parent
    if parent is None or parent.children is None:
        return
    try:
        parent.children.remove(node)
    except ValueError:
        return
    size, files = node.size, node.files
    p: Optional[Node] = parent
    while p is not None:
        p.size -= size
        p.files -= files
        p = p.parent


def iter_nodes(node: Node, max_depth: int = 64, _depth: int = 0):
    """Yield every node in the tree (pre-order)."""
    yield node
    if node.children and _depth < max_depth:
        for c in node.children:
            yield from iter_nodes(c, max_depth, _depth + 1)


def human(n: float) -> str:
    """Human-readable size using decimal units, like Finder does."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1000.0
    return f"{n:.1f} TB"
