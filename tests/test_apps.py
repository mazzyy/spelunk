import os
import plistlib
import time

from spelunk import apps


def make_app(base, name, bundle_id, exe="main"):
    path = os.path.join(base, name + ".app")
    os.makedirs(os.path.join(path, "Contents", "MacOS"))
    with open(os.path.join(path, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleName": name, "CFBundleIdentifier": bundle_id,
                       "CFBundleShortVersionString": "1.0", "CFBundleExecutable": exe}, f)
    with open(os.path.join(path, "Contents", "MacOS", exe), "wb") as f:
        f.write(b"\0" * 4096)
    return path


def touch(path, size=2048):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


def setup(tmp_path):
    home = str(tmp_path / "home")
    appdir = str(tmp_path / "Applications")
    os.makedirs(appdir)
    make_app(appdir, "Slack", "com.tinyspeck.slackmacgap")
    make_app(appdir, "WhatsApp", "net.whatsapp.WhatsApp")
    os.makedirs(os.path.join(appdir, "Utilities"))
    make_app(os.path.join(appdir, "Utilities"), "Helper Tool", "com.example.helpertool")
    L = os.path.join(home, "Library")
    # data of installed apps
    touch(f"{L}/Application Support/Slack/data.db")
    touch(f"{L}/Containers/net.whatsapp.WhatsApp/Data/x")
    touch(f"{L}/Group Containers/group.net.whatsapp.WhatsApp.shared/media")
    touch(f"{L}/Preferences/com.tinyspeck.slackmacgap.plist")
    # leftovers of removed apps
    touch(f"{L}/Application Support/com.surfshark.vpnclient.macos.direct/state", 300_000)
    touch(f"{L}/Preferences/com.surfshark.vpnclient.plist")
    touch(f"{L}/Caches/com.surfshark.vpnclient/cache", 50_000)
    touch(f"{L}/Application Support/GitKrakenCLI/bin", 200_000)
    # system and tool folders that must never be reported
    touch(f"{L}/Application Support/CrashReporter/x")
    touch(f"{L}/Caches/com.apple.Safari/x")
    touch(f"{L}/Caches/Homebrew/x")
    touch(f"{L}/Group Containers/group.is.workflow.my.app/x")
    touch(f"{L}/Preferences/loginwindow.plist")
    return home, appdir


def test_installed_apps_reads_bundles_and_subfolders(tmp_path):
    home, appdir = setup(tmp_path)
    found = {a["bundle_id"] for a in apps.installed_apps([appdir])}
    assert found == {"com.tinyspeck.slackmacgap", "net.whatsapp.WhatsApp", "com.example.helpertool"}


def test_leftovers_found_and_grouped(tmp_path):
    home, appdir = setup(tmp_path)
    installed = apps.installed_apps([appdir])
    groups = apps.find_leftovers(home, installed)
    by_id = {g["id"]: g for g in groups}
    assert set(by_id) == {"com.surfshark", "name:gitkrakencli"}
    surf = by_id["com.surfshark"]
    assert surf["name"] == "Surfshark"
    assert {i["location"] for i in surf["items"]} == {"Application Support", "Preferences", "Caches"}
    assert surf["size"] >= 350_000


def test_team_id_group_containers(tmp_path):
    home, appdir = setup(tmp_path)
    touch(f"{home}/Library/Group Containers/UBF8T346G9.Office/x")
    touch(f"{home}/Library/Group Containers/BJ4HAAB9B3.ZoomClient3rd/x")
    installed = apps.installed_apps([appdir])
    # Without Team IDs (e.g. not on macOS) nothing team-prefixed is reported.
    assert not any(g["id"].startswith("team:") for g in apps.find_leftovers(home, installed))
    teams = {installed[0]["path"]: "UBF8T346G9"}  # pretend an installed app is Microsoft's
    ids = {g["id"] for g in apps.find_leftovers(home, installed, teams)}
    assert "team:BJ4HAAB9B3" in ids and "team:UBF8T346G9" not in ids


def test_app_data_is_strict(tmp_path):
    home, appdir = setup(tmp_path)
    slack = next(a for a in apps.installed_apps([appdir]) if a["name"] == "Slack")
    paths = {os.path.relpath(d["path"], home) for d in apps.app_data(home, slack)}
    assert paths == {"Library/Application Support/Slack", "Library/Preferences/com.tinyspeck.slackmacgap.plist"}


def test_unused_apps(tmp_path):
    home, appdir = setup(tmp_path)
    installed = apps.installed_apps([appdir])
    now = time.time()
    for a in installed:
        a["last_used"] = now - 400 * apps.DAY if a["name"] == "Slack" else now - 3 * apps.DAY
    a = next(x for x in installed if x["name"] == "WhatsApp")
    a["last_used"] = None  # never opened
    unused = apps.unused_apps(home, installed, {}, now=now)
    names = {u["name"]: u for u in unused}
    assert set(names) == {"Slack", "WhatsApp"}
    assert names["Slack"]["days_unused"] == 400 and names["WhatsApp"]["days_unused"] is None
    assert names["Slack"]["data_size"] > 0


def test_parse_mdls_dates():
    raw = b"2025-11-19 10:00:00 +0000\x00\x002026-02-01 08:30:00 +0000"
    d = apps.parse_mdls_dates(raw, 3)
    assert d[0] and d[1] is None and d[2] > d[0]


def test_plain_name_folder_joins_its_vendor_group(tmp_path):
    home, appdir = setup(tmp_path)
    touch(f"{home}/Library/Caches/com.wondershare.filmoramacos/x", 20_000)
    touch(f"{home}/Library/Application Support/Wondershare Filmora Mac/x", 30_000)
    groups = apps.find_leftovers(home, apps.installed_apps([appdir]))
    ws = [g for g in groups if "wondershare" in g["id"] or "wondershare" in g["name"].lower()]
    assert len(ws) == 1 and len(ws[0]["items"]) == 2
