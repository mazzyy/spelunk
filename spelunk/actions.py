"""Actions that change the disk: move to Bin, delete, move to an external drive.

Safety model (see CLAUDE.md, these are non-negotiable):

1. Nothing runs without a *plan* the user has seen. ``plan()`` only reads.
2. ``execute()`` needs the plan id; plans expire after 10 minutes.
3. Paths on the hard deny list or with safety ``never`` are always blocked.
4. Items marked ``review`` or ``keep`` need an explicit acknowledgement.
5. Moves copy first, verify every byte with SHA-256, and only then remove
   the original. Any failure leaves the original untouched.
6. Every action is appended to ``~/.spelunk/actions.jsonl``.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import threading
import time
import uuid
from typing import Dict, List, Optional

from .knowledge import KnowledgeBase, hard_denied
from .scanner import SMALL_BUCKET, allocated_size

PLAN_TTL = 600  # seconds
ARCHIVE_DIR = "Spelunk Archive"
KINDS = ("trash", "delete", "move")


def measure(path: str) -> Dict[str, int]:
    """Allocated bytes and file count of a file or folder (no symlink follow)."""
    total = 0
    files = 0
    try:
        st = os.lstat(path)
    except OSError:
        return {"bytes": 0, "files": 0}
    if not stat.S_ISDIR(st.st_mode):
        return {"bytes": allocated_size(st), "files": 1}
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        s = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(s.st_mode):
                        stack.append(e.path)
                    elif not stat.S_ISLNK(s.st_mode):
                        total += allocated_size(s)
                        files += 1
        except OSError:
            continue
    return {"bytes": total, "files": files}


def sha256_file(path: str, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class Job:
    """A running or finished action, polled by the UI."""

    def __init__(self, plan: dict) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.plan = plan
        self.state = "running"  # running | done | failed
        self.total = plan["total_bytes"]
        self.done_bytes = 0
        self.current = ""
        self.results: List[dict] = []
        self.started = time.time()
        self.finished = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.plan["kind"],
            "state": self.state,
            "total": self.total,
            "done_bytes": self.done_bytes,
            "current": self.current,
            "results": self.results,
            "freed": sum(r.get("freed", 0) for r in self.results),
            "elapsed": round((self.finished or time.time()) - self.started, 1),
        }


class Actions:
    def __init__(self, kb: KnowledgeBase, home: Optional[str] = None, state_dir: Optional[str] = None,
                 trash_dir: Optional[str] = None) -> None:
        self.kb = kb
        self.home = os.path.normpath(home or os.path.expanduser("~"))
        self.state_dir = state_dir or os.path.join(self.home, ".spelunk")
        self.trash_dir = trash_dir or os.path.join(self.home, ".Trash")
        self.log_path = os.path.join(self.state_dir, "actions.jsonl")
        self.plans: Dict[str, dict] = {}
        self.jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self.on_finish = None  # optional callback(job) after a job ends

    # ----------------------------------------------------------- checking
    def check(self, path: str) -> dict:
        """Can ``path`` be acted on at all? Never raises."""
        path = os.path.normpath(path)
        info = self.kb.explain(path)
        item = {
            "path": path,
            "title": info.get("title", ""),
            "safety": info.get("safety", "review"),
            "if_removed": info.get("if_removed", ""),
            "blocked": None,
        }
        if os.path.basename(path) == SMALL_BUCKET:
            item["blocked"] = "This is a summary of many small items, not a real file."
        elif not os.path.isabs(path):
            item["blocked"] = "Only absolute paths are allowed."
        elif not os.path.lexists(path):
            item["blocked"] = "It no longer exists."
        else:
            reason = hard_denied(path, self.home)
            if reason:
                item["blocked"] = reason
            elif item["safety"] == "never":
                item["blocked"] = "Spelunk never touches this: " + (info.get("what") or "")
            elif path == self.trash_dir or path.startswith(self.trash_dir + "/"):
                item["blocked"] = "Already in the Bin. Empty the Bin in Finder."
        return item

    # ------------------------------------------------------------- planning
    def plan(self, kind: str, paths: List[str], dest: Optional[str] = None,
             sizes: Optional[Dict[str, int]] = None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown action {kind!r}")
        self._expire()
        items = []
        seen = set()
        norm = sorted({os.path.normpath(p) for p in paths}, key=len)
        for p in norm:
            # Skip items already covered by a selected (allowed) parent folder.
            if any(p.startswith(s + "/") for s in seen):
                continue
            item = self.check(p)
            if not item["blocked"]:
                seen.add(p)
            if not item["blocked"]:
                m = measure(p)
                item["bytes"] = m["bytes"]
                item["files"] = m["files"]
            else:
                item["bytes"] = (sizes or {}).get(p, 0)
                item["files"] = 0
            items.append(item)
        ok = [i for i in items if not i["blocked"]]
        total = sum(i["bytes"] for i in ok)
        plan = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "items": items,
            "total_bytes": total,
            "count": len(ok),
            "blocked": len(items) - len(ok),
            "needs_ack": any(i["safety"] in ("review", "keep") for i in ok),
            "created": time.time(),
            "warnings": [],
            "dest": None,
        }
        if kind == "trash":
            plan["warnings"].append("Items go to the Bin. Space is only freed after you empty the Bin.")
        if kind == "delete":
            plan["warnings"].append("Permanent: deleted items cannot be recovered.")
        if kind == "move":
            plan["dest"] = self._dest_info(dest, total)
            plan["warnings"].extend(plan["dest"]["warnings"])
            if plan["dest"]["error"]:
                plan["count"] = 0
        with self._lock:
            self.plans[plan["id"]] = plan
        return plan

    def _dest_info(self, dest: Optional[str], total: int) -> dict:
        info = {"path": dest, "archive": None, "free": 0, "error": None, "warnings": []}
        if not dest:
            info["error"] = "Choose a destination drive."
            return info
        dest = os.path.normpath(dest)
        if not os.path.isdir(dest):
            info["error"] = "Destination is not connected."
            return info
        if not os.access(dest, os.W_OK):
            info["error"] = "Destination is read-only."
            return info
        if dest == self.home or dest.startswith(self.home + "/") or dest == "/":
            info["error"] = "Destination must be a different drive."
            return info
        free = shutil.disk_usage(dest).free
        info["free"] = free
        info["archive"] = os.path.join(dest, ARCHIVE_DIR)
        if total * 1.2 > free:
            info["error"] = "Not enough free space on the destination drive."
        info["warnings"].append(
            "Files are copied, checked byte-for-byte, and only then removed from your Mac. "
            "Symbolic links can't be copied to exFAT drives and are listed if skipped."
        )
        return info

    def _expire(self) -> None:
        now = time.time()
        with self._lock:
            for pid in [k for k, v in self.plans.items() if now - v["created"] > PLAN_TTL]:
                del self.plans[pid]

    # ------------------------------------------------------------ execution
    def execute(self, plan_id: str, acknowledged: bool = False, background: bool = True) -> Job:
        self._expire()
        with self._lock:
            plan = self.plans.pop(plan_id, None)
        if plan is None:
            raise KeyError("Plan expired or unknown. Review the items again.")
        if plan["needs_ack"] and not acknowledged:
            with self._lock:
                self.plans[plan_id] = plan
            raise PermissionError("Some items need you to confirm you reviewed them.")
        if plan["kind"] == "move" and plan["dest"]["error"]:
            raise ValueError(plan["dest"]["error"])
        job = Job(plan)
        self.jobs[job.id] = job
        if background:
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
        else:
            self._run(job)
        return job

    def _run(self, job: Job) -> None:
        plan = job.plan
        try:
            for item in plan["items"]:
                if item["blocked"]:
                    continue
                path = item["path"]
                job.current = path
                # Re-check right before acting: the disk may have changed.
                again = self.check(path)
                if again["blocked"]:
                    job.results.append({"path": path, "ok": False, "error": again["blocked"]})
                    continue
                try:
                    if plan["kind"] == "trash":
                        res = self._trash(path)
                    elif plan["kind"] == "delete":
                        res = self._delete(path)
                    else:
                        res = self._move(path, plan["dest"]["archive"], job)
                    res.setdefault("freed", item["bytes"] if res.get("ok") and plan["kind"] != "trash" else 0)
                except Exception as exc:  # noqa: BLE001 - report every failure to the user
                    res = {"ok": False, "error": str(exc)}
                res["path"] = path
                res["bytes"] = item["bytes"]
                job.results.append(res)
                job.done_bytes += item["bytes"]
                self._log(plan["kind"], res)
            job.state = "done" if all(r.get("ok") for r in job.results) else "failed"
        finally:
            job.finished = time.time()
            job.current = ""
            if job.state == "running":
                job.state = "failed"
            if self.on_finish:
                try:
                    self.on_finish(job)
                except Exception:  # noqa: BLE001
                    pass

    # --------------------------------------------------------------- trash
    def _trash(self, path: str) -> dict:
        os.makedirs(self.trash_dir, exist_ok=True)
        name = os.path.basename(path)
        target = os.path.join(self.trash_dir, name)
        if os.path.lexists(target):
            stem, ext = os.path.splitext(name)
            target = os.path.join(self.trash_dir, f"{stem} {time.strftime('%H.%M.%S')}{ext}")
        try:
            os.rename(path, target)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            shutil.move(path, target)
        return {"ok": True, "to": target, "freed": 0}

    # -------------------------------------------------------------- delete
    def _delete(self, path: str) -> dict:
        errors: List[str] = []

        def onerror(func, p, exc_info):  # noqa: ANN001
            errors.append(f"{p}: {exc_info[1]}")

        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, onerror=onerror)
        else:
            os.remove(path)
        if errors:
            return {"ok": False, "error": f"{len(errors)} items could not be deleted (first: {errors[0]})"}
        return {"ok": True}

    # ---------------------------------------------------------------- move
    def _archive_path(self, archive_root: str, path: str) -> str:
        return os.path.join(archive_root, path.lstrip("/"))

    def _move(self, path: str, archive_root: str, job: Job) -> dict:
        target = self._archive_path(archive_root, path)
        if os.path.lexists(target):
            target = f"{target} (moved {time.strftime('%Y-%m-%d %H.%M.%S')})"
        os.makedirs(os.path.dirname(target), exist_ok=True)

        copied: List[tuple] = []  # (src, dst)
        skipped_links: List[dict] = []

        def copy_link(src: str, dst: str) -> None:
            link = os.readlink(src)
            try:
                os.symlink(link, dst)
            except OSError:
                # exFAT/FAT drives can't store links: remember them instead.
                skipped_links.append({"path": src, "points_to": link})

        def copy_file(src: str, dst: str) -> None:
            shutil.copyfile(src, dst)
            try:
                shutil.copystat(src, dst)
            except OSError:
                pass  # exFAT can't hold all metadata; content is what matters
            copied.append((src, dst))

        if os.path.isdir(path) and not os.path.islink(path):
            for dirpath, dirnames, filenames in os.walk(path):
                rel = os.path.relpath(dirpath, path)
                out_dir = target if rel == "." else os.path.join(target, rel)
                os.makedirs(out_dir, exist_ok=True)
                for name in list(dirnames):
                    full = os.path.join(dirpath, name)
                    if os.path.islink(full):
                        copy_link(full, os.path.join(out_dir, name))
                        dirnames.remove(name)
                for name in filenames:
                    src = os.path.join(dirpath, name)
                    if os.path.islink(src):
                        copy_link(src, os.path.join(out_dir, name))
                        continue
                    job.current = src
                    copy_file(src, os.path.join(out_dir, name))
        elif os.path.islink(path):
            return {"ok": False, "error": "Symbolic links are not moved."}
        else:
            copy_file(path, target)

        # Verify every copied file before touching the original.
        for src, dst in copied:
            job.current = "verifying " + src
            if os.path.getsize(src) != os.path.getsize(dst) or sha256_file(src) != sha256_file(dst):
                return {"ok": False, "error": f"Verification failed for {src}. Original kept.", "to": target}

        manifest = {
            "original": path,
            "archived": target,
            "files": len(copied),
            "skipped_symlinks": skipped_links,
            "verified": "sha256",
            "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(os.path.join(archive_root, "manifest.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(manifest) + "\n")

        removed = self._delete(path)
        if not removed["ok"]:
            return {"ok": False, "error": "Copied and verified, but the original could not be removed: " + removed["error"], "to": target}
        return {"ok": True, "to": target, "files": len(copied), "skipped_symlinks": len(skipped_links)}

    # ----------------------------------------------------------------- log
    def _log(self, kind: str, res: dict) -> None:
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": kind, **res}) + "\n")
        except OSError:
            pass

    def history(self, limit: int = 100) -> List[dict]:
        try:
            with open(self.log_path, encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
