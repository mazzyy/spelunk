"""Command line entry point.

    python3 -m spelunk                 # open the dashboard (default)
    python3 -m spelunk serve --port 8765 --no-open
    python3 -m spelunk scan ~ --top 30 # text report in the terminal
    python3 -m spelunk explain ~/Library/Caches
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser

from . import __version__, system
from .knowledge import KnowledgeBase
from .scanner import Scanner, human


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import App, make_server

    app = App(roots=args.roots or None)
    httpd, url, _ = make_server(app, port=args.port)
    print(f"Spelunk {__version__} running at {url.split('#')[0]}")
    print("Press Ctrl+C to stop. Nothing is deleted or moved without your confirmation.")
    if not system.IS_MAC:
        print("Note: not running on macOS; system details (APFS, snapshots) are unavailable.")
    app.start_scan()
    if not args.no_open:
        webbrowser.open(url)
    else:
        print(f"Open this link (it contains your access token): {url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    from .server import App

    roots = args.roots or system.default_roots()
    app = App(roots=roots)
    started = time.time()
    scanner = Scanner(progress=app.progress, skip=["/Volumes", "/System/Volumes"])
    app.tree = scanner.scan(roots)
    elapsed = time.time() - started
    recs = app.recommendations(min_bytes=args.min_mb * 1000 * 1000, limit=args.top)
    if args.json:
        print(json.dumps({"roots": {r.path: r.size for r in app.tree.children}, "items": recs}, indent=2))
        return 0
    print(f"Scanned {app.progress.files:,} files in {elapsed:.0f}s\n")
    for r in app.tree.children:
        flag = "  (no access)" if r.denied else ""
        print(f"  {human(r.size):>9}  {r.path}{flag}")
    print(f"\nLargest items you can decide on (>{args.min_mb} MB):\n")
    for item in recs:
        info = item["info"]
        badge = {"safe": "SAFE  ", "review": "REVIEW", "keep": "KEEP  "}.get(info["safety"], "      ")
        print(f"  {human(item['size']):>9}  {badge}  {item['path']}")
        print(f"             {info['title']}: {info['what']}")
        if info.get("how"):
            print(f"             How: {info['how']}")
    if app.progress.denied:
        print(f"\n{app.progress.denied} folders could not be read. Give your terminal Full Disk Access "
              "(System Settings > Privacy & Security > Full Disk Access) to see them.")
    return 0


def _cmd_app(args: argparse.Namespace) -> int:
    from .desktop import main as desktop_main

    extra = ["--no-window"] if args.no_window else []
    return desktop_main(extra + ["--port", str(args.port)])


def _cmd_explain(args: argparse.Namespace) -> int:
    kb = KnowledgeBase.load()
    path = os.path.abspath(os.path.expanduser(args.path))
    info = kb.explain(path)
    print(json.dumps(info, indent=2))
    return 0


def main(argv=None) -> int:  # noqa: ANN001
    p = argparse.ArgumentParser(prog="spelunk", description="See what fills your Mac's disk and decide what goes.")
    p.add_argument("--version", action="version", version=f"spelunk {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="open the dashboard (default)")
    s.add_argument("--port", type=int, default=0, help="port (default: random free port)")
    s.add_argument("--no-open", action="store_true", help="don't open the browser")
    s.add_argument("roots", nargs="*", help="folders to scan (default: home, Applications, Library, Homebrew...)")
    s.set_defaults(func=_cmd_serve)

    c = sub.add_parser("scan", help="print a report in the terminal")
    c.add_argument("roots", nargs="*")
    c.add_argument("--top", type=int, default=30)
    c.add_argument("--min-mb", type=int, default=50)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_scan)

    e = sub.add_parser("explain", help="explain what a path is and whether it's safe to remove")
    e.add_argument("path")
    e.set_defaults(func=_cmd_explain)

    a = sub.add_parser("app", help="open Spelunk in its own window (needs pywebview; falls back to the browser)")
    a.add_argument("--no-window", action="store_true")
    a.add_argument("--port", type=int, default=0)
    a.set_defaults(func=_cmd_app)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        args = p.parse_args(["serve"] + (argv or sys.argv[1:]))
    return args.func(args)
