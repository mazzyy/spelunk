"""Entry point for the double-clickable Mac app (Spelunk.app).

Starts the local server on a random port and shows the dashboard in a
native window (WKWebView via pywebview). Closing the window quits the app.
If pywebview isn't installed (e.g. running from source), the dashboard
opens in the default browser instead.

    python3 -m spelunk app             # same thing from a checkout
    python3 -m spelunk app --no-window # headless, prints the URL (tests)
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from . import __version__
from .server import App, make_server

WINDOW_TITLE = "Spelunk"


def _make_window(webview, url: str):  # noqa: ANN001
    return webview.create_window(
        WINDOW_TITLE,
        url,
        width=1320,
        height=900,
        min_size=(900, 640),
        background_color="#f5f4f0",
        text_select=True,
    )


def main(argv=None) -> int:  # noqa: ANN001
    p = argparse.ArgumentParser(prog="Spelunk", description=f"Spelunk {__version__}")
    p.add_argument("--no-window", action="store_true", help="don't open any window; print the URL")
    p.add_argument("--port", type=int, default=0)
    # Finder may pass extra arguments such as -psn_0_12345; ignore them.
    args, _ = p.parse_known_args(argv)

    app = App()
    app.app_mode = True
    httpd, url, _token = make_server(app, port=args.port)
    threading.Thread(target=httpd.serve_forever, name="spelunk-http", daemon=True).start()
    app.start_scan()

    if args.no_window:
        print(url, flush=True)
        return _wait(httpd)

    try:
        import webview  # pywebview: bundled inside Spelunk.app
    except ImportError:
        webview = None

    if webview is None:
        webbrowser.open(url)
        print(f"Spelunk is running at {url.split('#')[0]} (Ctrl+C to quit)", flush=True)
        return _wait(httpd)

    _make_window(webview, url)
    webview.start()  # blocks on the main thread until the window closes
    httpd.shutdown()
    return 0


def _wait(httpd) -> int:  # noqa: ANN001
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
