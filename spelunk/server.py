"""Local web server for the dashboard.

Security: the server only listens on 127.0.0.1, every API call must carry a
random per-launch token, the Host header must be the loopback address (blocks
DNS-rebinding attacks), and POST bodies must be JSON (forces a CORS preflight
that we never answer, so other websites can't call the API).
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__, apps, system
from .actions import Actions
from .duplicates import DuplicateSearch
from .knowledge import KnowledgeBase
from .scanner import SMALL_BUCKET, Node, ScanProgress, Scanner, find, remove

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
ACTIONABLE = {"safe", "review"}
SPLIT_RULES = {"caches", "caches-apple", "logs", "xdg-cache", "app-support-item", "containers-item",
               "group-containers-item", "homebrew", "user-files", "editor-extensions", "npm-global"}


class App:
    """Shared state between HTTP requests."""

    def __init__(self, roots: Optional[List[str]] = None, home: Optional[str] = None,
                 min_node_bytes: int = 1024 * 1024) -> None:
        self.home = home or os.path.expanduser("~")
        self.roots = roots or system.default_roots()
        self.kb = KnowledgeBase.load(home=self.home)
        self.actions = Actions(self.kb, home=self.home)
        self.actions.on_finish = self._after_job
        self.progress = ScanProgress()
        self.tree: Optional[Node] = None
        self.min_node_bytes = min_node_bytes
        self.lock = threading.Lock()
        self.scan_error: Optional[str] = None
        self._overview_cache: Optional[dict] = None
        self.app_mode = False  # True when running as Spelunk.app (texts say "Spelunk", not "your terminal")
        self.relaunch = None  # set by desktop.py when the app can restart itself
        self.settings_path = os.path.join(self.home, ".spelunk", "settings.json")
        self.settings = self._load_settings()
        self.duplicates = DuplicateSearch()
        self.app_dirs = apps.app_dirs(self.home)
        self._apps_cache: Optional[dict] = None
        self._teams: Optional[Dict[str, str]] = None

    # ----------------------------------------------------------- onboarding
    def _load_settings(self) -> dict:
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except OSError:
            pass

    def needs_onboarding(self) -> bool:
        """Show the welcome screen before the first scan of the app.

        Scanning touches folders macOS protects (Desktop, Documents, Mail,
        other apps' data...), and each one pops up a system permission
        dialog. We explain that first and let the user pick: Full Disk
        Access (no pop-ups) or folder-by-folder prompts. Nothing is scanned
        until they choose. With Full Disk Access already granted there is
        nothing to ask, so we go straight to scanning.
        """
        if not self.app_mode or self.settings.get("onboarded"):
            return False
        return system.full_disk_access(self.home) is not True

    def finish_onboarding(self, choice: str) -> bool:
        self.settings.update({"onboarded": True, "permission_choice": choice})
        self._save_settings()
        return self.start_scan()

    # ------------------------------------------------------------- scanning
    def start_scan(self, roots: Optional[List[str]] = None) -> bool:
        if self.progress.running:
            return False
        if roots:
            self.roots = roots
        self.progress = ScanProgress()
        self.progress.running = True
        self._overview_cache = None
        self._apps_cache = None
        threading.Thread(target=self._scan, daemon=True).start()
        return True

    def _scan(self) -> None:
        try:
            scanner = Scanner(min_node_bytes=self.min_node_bytes, progress=self.progress,
                              skip=["/Volumes", "/System/Volumes"])
            tree = scanner.scan(self.roots)
            with self.lock:
                self.tree = tree
            self.scan_error = None
        except Exception as exc:  # noqa: BLE001
            self.scan_error = str(exc)
            self.progress.running = False

    # ------------------------------------------------------------- queries
    def explain_node(self, d: dict) -> dict:
        if d["name"] == SMALL_BUCKET:
            d["info"] = {"id": "small", "title": "Many small items", "safety": "keep", "category": "other",
                         "what": "Files and folders under 1 MB each, grouped together.", "action": "none"}
        else:
            d["info"] = self.kb.explain(d["path"])
        for c in d.get("children", []):
            self.explain_node(c)
        return d

    def node(self, path: Optional[str], depth: int = 1) -> Optional[dict]:
        with self.lock:
            if self.tree is None:
                return None
            n = self.tree if not path else find(self.tree, path)
            if n is None:
                return None
            d = n.to_dict(depth=depth)
        if not path:
            d["name"] = "Scanned folders"
            d["path"] = ""
        return self.explain_node(d)

    def overview(self) -> dict:
        if self._overview_cache is None or self.progress.running:
            scanned: Dict[str, int] = {}
            if self.tree is not None:
                for r in self.tree.children or ():
                    scanned[r.path] = r.size
            ov = system.overview(scanned)
            ov["roots"] = self.roots
            ov["denied_roots"] = [r.path for r in (self.tree.children if self.tree else []) if r.denied]
            ov["full_disk_access"] = system.full_disk_access(self.home)
            ov["app_mode"] = self.app_mode
            if not self.progress.running:
                self._overview_cache = ov
            return ov
        return self._overview_cache

    def recommendations(self, min_bytes: int = 50 * 1000 * 1000, limit: int = 80) -> List[dict]:
        """Largest things worth a decision, each explained.

        A node is suggested once; we stop descending after a suggestion so
        the list never double-counts. Container rules (Caches, Logs...) are
        split so you see individual apps instead of one huge folder.
        """
        def item(n: Node, info: dict, tip: bool, actionable: bool) -> dict:
            return {"path": n.path, "name": n.name, "size": n.size, "files": n.files, "is_dir": n.is_dir,
                    "mtime": n.mtime, "info": info, "tip_only": tip and not actionable}

        def visit(n: Node) -> List[dict]:
            if n.size < min_bytes or n.name == SMALL_BUCKET:
                return []
            info = self.kb.explain(n.path)
            rid = info.get("id")
            actionable = info.get("safety") in ACTIONABLE and info.get("action") != "none"
            inherited = bool(info.get("inherited"))
            if not inherited:
                tip = info.get("safety") == "keep" and info.get("action") in ("app", "command")
                # Container rules are split into their children, but a single
                # big file (e.g. a video directly in ~/Movies) is its own item.
                if (rid not in SPLIT_RULES or not n.is_dir) and (actionable or tip):
                    return [item(n, info, tip, actionable)]
                return [x for c in (n.children or ()) for x in visit(c)]
            # Inside a folder explained by an ancestor rule: prefer anything
            # deeper that has its own specific rule...
            deeper = [x for c in (n.children or ()) for x in visit(c)]
            if deeper:
                return deeper
            # ...otherwise suggest the direct children of a container folder
            # (e.g. each app inside ~/Library/Caches) as one item each.
            if rid in SPLIT_RULES and actionable and os.path.dirname(n.path) == info.get("inherited_from"):
                return [item(n, info, False, actionable)]
            return []

        with self.lock:
            if self.tree is None:
                return []
            out = [x for root in (self.tree.children or ()) for x in visit(root)]
        out.sort(key=lambda x: x["size"], reverse=True)
        return out[:limit]

    def apps_report(self) -> dict:
        """Leftovers of removed apps and apps not opened for months (cached)."""
        if self._apps_cache is not None:
            return self._apps_cache
        installed = apps.installed_apps(self.app_dirs)
        apps.last_used(installed)
        if self._teams is None:
            self._teams = apps.team_ids(installed)
        sizes: Dict[str, int] = {}
        with self.lock:
            if self.tree is not None:
                for a in installed:
                    n = find(self.tree, a["path"])
                    if n is not None:
                        sizes[a["path"]] = n.size
        leftovers = apps.find_leftovers(self.home, installed, self._teams, min_group_bytes=100_000)
        unused = apps.unused_apps(self.home, installed, sizes)
        report = {"leftovers": leftovers, "unused": unused, "installed": len(installed),
                  "unused_after_days": apps.UNUSED_AFTER_DAYS}
        self._apps_cache = report
        return report

    def _after_job(self, job) -> None:  # noqa: ANN001
        removed = [r["path"] for r in job.results if r.get("ok")]
        with self.lock:
            if self.tree is not None:
                for path in removed:
                    n = find(self.tree, path)
                    if n is not None:
                        remove(n)
        self._overview_cache = None
        self._apps_cache = None
        self.duplicates.forget(removed)


class Handler(BaseHTTPRequestHandler):
    app: App
    token: str
    port: int
    server_version = f"spelunk/{__version__}"

    # Quiet default logging; the terminal shows a single status line instead.
    def log_message(self, fmt, *args):  # noqa: ANN001, D401
        return

    # ----------------------------------------------------------- plumbing
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _auth_ok(self) -> bool:
        given = self.headers.get("X-Spelunk-Token", "")
        return hmac.compare_digest(given, self.token)

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:  # noqa: ANN001
        self._send(code, json.dumps(data).encode("utf-8"))

    def _error(self, code: int, msg: str) -> None:
        self._json({"error": msg}, code)

    def _body(self) -> dict:
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ValueError("JSON body required")
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            raise ValueError("Body too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    # ------------------------------------------------------------- routes
    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error(403, "bad host")
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            if not self._auth_ok():
                return self._error(401, "missing or wrong token")
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            return self._api_get(url.path, q)
        return self._static(url.path)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error(403, "bad host")
        url = urlparse(self.path)
        if not url.path.startswith("/api/") or not self._auth_ok():
            return self._error(401, "missing or wrong token")
        try:
            body = self._body()
        except ValueError as exc:
            return self._error(400, str(exc))
        return self._api_post(url.path, body)

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not full.startswith(WEB_DIR + os.sep) or not os.path.isfile(full):
            return self._error(404, "not found")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _api_get(self, path: str, q: dict) -> None:
        app = self.app
        if path == "/api/state":
            return self._json({
                "version": __version__,
                "scan": app.progress.snapshot(),
                "has_tree": app.tree is not None,
                "roots": app.roots,
                "home": app.home,
                "error": app.scan_error,
                "platform": system.platform.system(),
                "app_mode": app.app_mode,
                "needs_onboarding": app.needs_onboarding() and app.tree is None and not app.progress.running,
                "can_relaunch": app.relaunch is not None,
            })
        if path == "/api/overview":
            return self._json(app.overview())
        if path == "/api/node":
            d = app.node(q.get("path") or None, depth=1)
            return self._json(d) if d else self._error(404, "not scanned")
        if path == "/api/recommendations":
            try:
                min_bytes = int(q.get("min", 50_000_000))
            except ValueError:
                min_bytes = 50_000_000
            return self._json({"items": app.recommendations(min_bytes=min_bytes)})
        if path == "/api/externals":
            return self._json({"volumes": system.external_volumes()})
        if path == "/api/explain":
            return self._json(app.kb.explain(q.get("path", "")))
        if path == "/api/job":
            job = app.actions.jobs.get(q.get("id", ""))
            return self._json(job.to_dict()) if job else self._error(404, "no such job")
        if path == "/api/history":
            return self._json({"items": app.actions.history()})
        if path == "/api/apps":
            if app.tree is None:
                return self._error(409, "Scan first")
            return self._json(app.apps_report())
        if path == "/api/duplicates":
            return self._json(app.duplicates.snapshot())
        return self._error(404, "unknown endpoint")

    def _api_post(self, path: str, body: dict) -> None:
        app = self.app
        if path == "/api/scan":
            roots = body.get("roots") or None
            started = app.start_scan(roots)
            return self._json({"started": started})
        if path == "/api/cancel":
            app.progress.cancelled.set()
            return self._json({"cancelled": True})
        if path == "/api/plan":
            try:
                plan = app.actions.plan(body.get("kind", ""), list(body.get("paths") or []), body.get("dest"))
            except ValueError as exc:
                return self._error(400, str(exc))
            return self._json(plan)
        if path == "/api/execute":
            try:
                job = app.actions.execute(body.get("plan_id", ""), bool(body.get("ack")))
            except (KeyError, PermissionError, ValueError) as exc:
                return self._error(409, str(exc).strip("'\""))
            return self._json(job.to_dict())
        if path == "/api/reveal":
            return self._json({"ok": system.reveal_in_finder(body.get("path", ""))})
        if path == "/api/open-privacy-settings":
            return self._json({"ok": system.open_full_disk_access_settings()})
        if path == "/api/duplicates":
            if app.tree is None:
                return self._error(409, "Scan first")
            try:
                min_bytes = int(body.get("min_bytes", 10_000_000))
            except (TypeError, ValueError):
                return self._error(400, "min_bytes must be a number")
            with app.lock:
                started = app.duplicates.start(app.tree, app.home, max(1_000_000, min_bytes))
            return self._json({"started": started})
        if path == "/api/onboarding":
            choice = body.get("choice")
            if choice not in ("ask", "full-disk-access"):
                return self._error(400, "choice must be 'ask' or 'full-disk-access'")
            return self._json({"started": app.finish_onboarding(choice)})
        if path == "/api/relaunch":
            if app.relaunch is None:
                return self._error(409, "Restart Spelunk yourself: quit it and open it again.")
            threading.Timer(0.3, app.relaunch).start()  # let this response reach the window first
            return self._json({"ok": True})
        return self._error(404, "unknown endpoint")


def make_server(app: App, port: int = 0, token: Optional[str] = None):
    """Create (but don't start) the HTTP server. Returns (server, url, token)."""
    token = token or secrets.token_urlsafe(24)
    handler = type("BoundHandler", (Handler,), {"app": app, "token": token})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    handler.port = httpd.server_address[1]
    url = f"http://127.0.0.1:{handler.port}/#t={token}"
    return httpd, url, token
