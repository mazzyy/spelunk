import http.client
import json
import threading
import time

import pytest

from spelunk.server import App, make_server


@pytest.fixture
def server(fake_home):
    app = App(roots=[fake_home], home=fake_home, min_node_bytes=100_000)
    httpd, url, token = make_server(app, port=0, token="tok")
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    app.start_scan()
    for _ in range(100):
        if app.tree is not None and not app.progress.running:
            break
        time.sleep(0.05)
    yield port, app
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, token="tok", host=None, ctype="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if token:
        headers["X-Spelunk-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body)
        headers["Content-Type"] = ctype
    conn.request(method, path, body=data, headers=headers)
    res = conn.getresponse()
    raw = res.read()
    try:
        return res.status, json.loads(raw)
    except ValueError:
        return res.status, raw


def test_token_required(server):
    port, _ = server
    assert call(port, "GET", "/api/state", token=None)[0] == 401
    assert call(port, "GET", "/api/state", token="wrong")[0] == 401
    assert call(port, "GET", "/api/state")[0] == 200


def test_host_header_checked(server):
    port, _ = server
    assert call(port, "GET", "/api/state", host="evil.example:80")[0] == 403


def test_post_requires_json(server):
    port, _ = server
    assert call(port, "POST", "/api/scan", body={}, ctype="text/plain")[0] == 400


def test_static_served_without_token(server):
    port, _ = server
    status, body = call(port, "GET", "/", token=None)
    assert status == 200 and b"Spelunk" in body
    assert call(port, "GET", "/../../etc/passwd", token=None)[0] == 404


def test_recommendations_and_node(server, fake_home):
    port, _ = server
    status, data = call(port, "GET", "/api/recommendations?min=1000000")
    assert status == 200
    ids = {i["info"]["id"] for i in data["items"]}
    assert "caches-shipit" in ids and "node-modules" in ids
    status, node = call(port, "GET", f"/api/node?path={fake_home}/Library")
    assert status == 200 and node["info"]["id"] == "library"


def test_plan_execute_roundtrip(server, fake_home):
    port, app = server
    target = f"{fake_home}/Downloads/installer.dmg"
    status, plan = call(port, "POST", "/api/plan", {"kind": "delete", "paths": [target]})
    assert status == 200 and plan["count"] == 1
    status, job = call(port, "POST", "/api/execute", {"plan_id": plan["id"]})
    assert status == 200
    for _ in range(100):
        status, job = call(port, "GET", f"/api/job?id={job['id']}")
        if job["state"] != "running":
            break
        time.sleep(0.05)
    assert job["state"] == "done"
    status, node = call(port, "GET", f"/api/node?path={fake_home}/Downloads")
    assert all(c["name"] != "installer.dmg" for c in node.get("children", []))


def test_generic_cache_children_are_suggested(tmp_path):
    from tests.conftest import write
    home = str(tmp_path / "h")
    write(f"{home}/Library/Caches/com.example.someapp/blob", 2_000_000)   # no specific rule
    write(f"{home}/Library/Caches/Google/c", 2_000_000)                  # specific rule
    write(f"{home}/proj/node_modules/x/y.js", 2_000_000)
    app = App(roots=[home], home=home, min_node_bytes=100_000)
    from spelunk.scanner import Scanner
    app.tree = Scanner(min_node_bytes=100_000).scan([home])
    recs = {r["path"]: r["info"]["id"] for r in app.recommendations(min_bytes=1_000_000)}
    assert recs[f"{home}/Library/Caches/com.example.someapp"] == "caches"
    assert recs[f"{home}/Library/Caches/Google"] == "caches-browsers"
    assert recs[f"{home}/proj/node_modules"] == "node-modules"
    assert f"{home}/Library/Caches" not in recs
