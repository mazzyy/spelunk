"""Find duplicate files in your own folders.

Three passes keep it fast: group by exact size, then by a hash of the first
and last 64 KB, and only then hash whole files (SHA-256). Files with a
unique size are never read at all.

Only your own files are compared. App bundles, photo/music/video libraries,
Library, hidden folders, node_modules and .git are skipped, because removing
one "copy" inside those breaks the thing that contains it.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple

from .scanner import SMALL_BUCKET, Node, iter_nodes

PARTIAL = 64 * 1024
SKIP_SUFFIXES = (".app", ".photoslibrary", ".musiclibrary", ".fcpbundle", ".imovielibrary", ".tvlibrary",
                 ".logicx", ".band", ".pages", ".numbers", ".key", ".xcodeproj", ".xcworkspace", ".bundle",
                 ".framework", ".plugin", ".kext", ".sparsebundle", ".dmg.part")
SKIP_NAMES = {"node_modules", ".git", "Library", "venv", ".venv", "__pycache__", "site-packages"}


def _skipped(rel_parts: Iterable[str]) -> bool:
    for part in rel_parts:
        if part.startswith(".") or part in SKIP_NAMES or part.endswith(SKIP_SUFFIXES):
            return True
    return False


def candidates(tree: Node, home: str, min_bytes: int) -> List[str]:
    """Files at least ``min_bytes`` big, inside the home folder, not in skipped places."""
    out = []
    prefix = home.rstrip("/") + "/"
    for n in iter_nodes(tree):
        if n.is_dir or n.name == SMALL_BUCKET or n.size < min_bytes:
            continue
        path = n.path
        if not path.startswith(prefix):
            continue
        if _skipped(path[len(prefix):].split("/")[:-1]) or n.name.startswith("."):
            continue
        out.append(path)
    return out


def _partial_hash(path: str, size: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(PARTIAL))
        if size > 2 * PARTIAL:
            f.seek(size - PARTIAL)
            h.update(f.read(PARTIAL))
    return h.hexdigest()


class DuplicateSearch:
    """Runs in a background thread; the UI polls ``snapshot()``."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = "idle"  # idle | running | done | failed
        self.total = 0
        self.done = 0
        self.current = ""
        self.groups: List[dict] = []
        self.error: Optional[str] = None
        self.started = 0.0
        self.finished = 0.0
        self.min_bytes = 0

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "state": self.state, "total": self.total, "done": self.done, "current": self.current,
                "groups": self.groups, "error": self.error, "min_bytes": self.min_bytes,
                "wasted": sum(g["wasted"] for g in self.groups),
                "elapsed": round((self.finished or time.time()) - self.started, 1) if self.started else 0,
            }

    def start(self, tree: Node, home: str, min_bytes: int = 10_000_000, background: bool = True) -> bool:
        """Start a search. The candidate list is taken from the tree right away
        (the caller holds the tree lock), hashing happens in the background."""
        with self.lock:
            if self.state == "running":
                return False
            self.state, self.groups, self.error = "running", [], None
            self.total = self.done = 0
            self.started, self.finished, self.min_bytes = time.time(), 0.0, min_bytes
        paths = candidates(tree, home, min_bytes)
        if background:
            threading.Thread(target=self._run, args=(paths,), daemon=True).start()
        else:
            self._run(paths)
        return True

    def _run(self, paths: List[str]) -> None:
        try:
            groups = self._find(paths)
            with self.lock:
                self.groups, self.state = groups, "done"
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.state, self.error = "failed", str(exc)
        finally:
            with self.lock:
                self.finished, self.current = time.time(), ""

    def _find(self, paths: List[str]) -> List[dict]:
        by_size: Dict[int, List[str]] = {}
        stats: Dict[str, os.stat_result] = {}
        for p in paths:
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            stats[p] = st
            by_size.setdefault(st.st_size, []).append(p)
        same_size = [g for g in by_size.values() if len(g) > 1]
        with self.lock:
            self.total = sum(stats[p].st_size for g in same_size for p in g)

        groups: List[dict] = []
        for group in same_size:
            size = stats[group[0]].st_size
            by_partial: Dict[str, List[str]] = {}
            for p in group:
                try:
                    by_partial.setdefault(_partial_hash(p, size), []).append(p)
                except OSError:
                    continue
            for maybe in by_partial.values():
                if len(maybe) < 2:
                    with self.lock:
                        self.done += size * len(maybe)
                    continue
                by_full: Dict[str, List[str]] = {}
                for p in maybe:
                    with self.lock:
                        self.current = p
                    try:
                        by_full.setdefault(self._full_hash(p), []).append(p)
                    except OSError:
                        continue
                for same in by_full.values():
                    if len(same) > 1:
                        files = sorted(({"path": p, "mtime": stats[p].st_mtime, "inode": stats[p].st_ino}
                                        for p in same), key=lambda f: f["mtime"])
                        groups.append({"name": os.path.basename(files[0]["path"]), "size": size,
                                       "count": len(files), "wasted": size * (len(files) - 1), "files": files})
        groups.sort(key=lambda g: g["wasted"], reverse=True)
        return groups

    def _full_hash(self, path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                b = f.read(4 * 1024 * 1024)
                if not b:
                    break
                h.update(b)
                with self.lock:
                    self.done += len(b)
        return h.hexdigest()

    def forget(self, removed: Iterable[str]) -> None:
        """Drop files the user removed; groups with one file left disappear."""
        gone = set(removed)
        with self.lock:
            kept = []
            for g in self.groups:
                files = [f for f in g["files"] if f["path"] not in gone]
                if len(files) > 1:
                    kept.append({**g, "files": files, "count": len(files), "wasted": g["size"] * (len(files) - 1)})
            self.groups = kept
