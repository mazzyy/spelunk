"""Installed apps, when they were last opened, and leftovers of removed apps.

Leftover detection is deliberately conservative: an entry in ~/Library is
only reported when no installed app could plausibly own it. Everything found
here is "your call" — the user decides, and every removal still goes through
the normal plan/confirm flow in actions.py.
"""
from __future__ import annotations

import datetime as _dt
import os
import plistlib
import re
import subprocess
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import system
from .actions import measure

DAY = 86400
UNUSED_AFTER_DAYS = 180

# Where apps keep data, relative to the home folder.
LIBRARY_LOCATIONS = [
    "Library/Application Support",
    "Library/Caches",
    "Library/Containers",
    "Library/Group Containers",
    "Library/Preferences",
    "Library/Saved Application State",
    "Library/HTTPStorages",
    "Library/WebKit",
    "Library/Logs",
    "Library/Application Scripts",
    "Library/Cookies",
]
SUFFIXES = (".plist", ".savedState", ".binarycookies")

# Folder names that belong to macOS, shared frameworks or command-line tools
# (not to a removable app). Apple apps themselves are recognised by scanning
# /System/Applications.
SYSTEM_NAMES = {
    n.lower() for n in """
    AddressBook Animoji AppleMediaServices Accessibility Assistant Assistants Biome CallHistoryDB
    CallHistoryTransactions CloudDocs CloudKit CoreFollowUp CrashReporter DiagnosticReports
    DifferentialPrivacy DiskImages Dock FileProvider FontCollections GameKit Knowledge MobileSync
    NetworkServiceProxy SyncServices TrustedPeersHelper Spotlight iCloud ubiquity DuetExpertCenter
    Trial SafariSafeBrowsing SiriTTS QuickLook GeoServices PassKit FamilyCircle CloudStorage
    Python pip pipx pnpm npm node yarn deno uv cargo rustup Homebrew Jupyter ms-playwright
    puppeteer node-gyp typescript go-build electron electron-builder pypoetry poetry huggingface
    torch matplotlib fontconfig claude-cli-nodejs selenium chroma
    passd routined homeenergyd identityservicesd locationaccessstored photoanalysisd studentd
    familycircled networkserviced icdd tipsd transparencyd replayd sharingd contactsd
    siriknowledged knowledge-agent mediaanalysisd suggestd callservicesd
    """.split()
}
SYSTEM_PREFIXES = ("com.apple.", "group.com.apple.", "systemgroup.com.apple.", "is.workflow.")

BUNDLE_ID = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9_-]+){2,}$")
TEAM_PREFIX = re.compile(r"^([A-Z0-9]{10})\.(.+)$")
GENERIC_TOKENS = {"com", "org", "net", "io", "app", "apps", "co", "de", "uk", "mac", "macos", "osx",
                  "desktop", "client", "helper", "group", "shared", "the", "inc"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def app_dirs(home: str) -> List[str]:
    """Where apps are installed. /System/Applications is included so Apple's
    own apps count as owners of their data (they're never offered for removal)."""
    return ["/Applications", os.path.join(home, "Applications"), "/System/Applications"]


# ---------------------------------------------------------------- installed
def read_app(path: str) -> Optional[dict]:
    info_path = os.path.join(path, "Contents", "Info.plist")
    try:
        with open(info_path, "rb") as f:
            info = plistlib.load(f)
    except Exception:  # noqa: BLE001 - unreadable or broken bundle
        return None
    name = info.get("CFBundleDisplayName") or info.get("CFBundleName") or os.path.basename(path)[:-4]
    exe = info.get("CFBundleExecutable") or ""
    return {
        "path": path,
        "name": str(name),
        "bundle_id": str(info.get("CFBundleIdentifier") or ""),
        "version": str(info.get("CFBundleShortVersionString") or ""),
        "executable": os.path.join(path, "Contents", "MacOS", exe) if exe else "",
    }


def installed_apps(dirs: Iterable[str]) -> List[dict]:
    """Apps in the given folders and one level of subfolders (e.g. Utilities)."""
    apps: List[dict] = []
    for base in dirs:
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for name in entries:
            full = os.path.join(base, name)
            if name.endswith(".app"):
                a = read_app(full)
                if a:
                    apps.append(a)
            elif os.path.isdir(full) and not os.path.islink(full):
                try:
                    inner = sorted(os.listdir(full))
                except OSError:
                    continue
                for sub in inner:
                    if sub.endswith(".app"):
                        a = read_app(os.path.join(full, sub))
                        if a:
                            apps.append(a)
    return apps


def parse_mdls_dates(raw: bytes, count: int) -> List[Optional[float]]:
    """Parse `mdls -raw -nullMarker "" -name kMDItemLastUsedDate ...` output."""
    parts = raw.split(b"\0")
    out: List[Optional[float]] = []
    for i in range(count):
        val = parts[i].decode("utf-8", "replace").strip() if i < len(parts) else ""
        try:
            out.append(_dt.datetime.strptime(val, "%Y-%m-%d %H:%M:%S %z").timestamp())
        except ValueError:
            out.append(None)
    return out


def last_used(apps: List[dict]) -> None:
    """Fill ``last_used`` (epoch seconds or None) and ``last_used_source``.

    Spotlight's kMDItemLastUsedDate is what Finder shows as "Last opened".
    Without Spotlight, fall back to when the app's program file was last read.
    """
    dates: List[Optional[float]] = [None] * len(apps)
    if system.IS_MAC and apps:
        try:
            r = subprocess.run(["mdls", "-raw", "-nullMarker", "", "-name", "kMDItemLastUsedDate"]
                               + [a["path"] for a in apps], capture_output=True, timeout=30)
            if r.returncode == 0:
                dates = parse_mdls_dates(r.stdout, len(apps))
        except (OSError, subprocess.SubprocessError):
            pass
    for a, d in zip(apps, dates):
        if d is not None:
            a["last_used"], a["last_used_source"] = d, "spotlight"
            continue
        try:
            a["last_used"] = os.stat(a["executable"]).st_atime if a["executable"] else None
            a["last_used_source"] = "file access" if a["last_used"] else None
        except OSError:
            a["last_used"], a["last_used_source"] = None, None


def team_ids(apps: List[dict]) -> Dict[str, str]:
    """Apple developer Team ID per app path (macOS only, used for Group Containers)."""
    out: Dict[str, str] = {}
    if not system.IS_MAC:
        return out
    for a in apps:
        if a["bundle_id"].startswith("com.apple."):
            continue
        try:
            r = subprocess.run(["codesign", "-dv", a["path"]], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        m = re.search(rb"TeamIdentifier=([A-Z0-9]{10})", r.stderr)
        if m:
            out[a["path"]] = m.group(1).decode()
    return out


# ---------------------------------------------------------------- ownership
class Owners:
    """Everything that identifies an installed app, for matching Library data."""

    def __init__(self, apps: List[dict], teams: Optional[Dict[str, str]] = None) -> None:
        self.bundle_ids: Set[str] = {a["bundle_id"].lower() for a in apps if a["bundle_id"]}
        self.vendors: Set[str] = {".".join(b.split(".")[:2]) for b in self.bundle_ids if b.count(".") >= 1}
        self.names: Set[str] = set()
        self.tokens: Set[str] = set()
        for a in apps:
            self.names.add(_norm(a["name"]))
            self.names.add(_norm(os.path.basename(a["path"])[:-4]))
            for t in re.split(r"[.\-_ ]", a["bundle_id"].lower()):
                if len(t) >= 3 and t not in GENERIC_TOKENS:
                    self.tokens.add(_norm(t))
        self.teams: Set[str] = set((teams or {}).values())

    def owns_bundle_id(self, key: str) -> bool:
        k = key.lower()
        if k.startswith(SYSTEM_PREFIXES):
            return True
        for b in self.bundle_ids:
            if k == b or k.startswith(b + ".") or b.startswith(k + "."):
                return True
        return ".".join(k.split(".")[:2]) in self.vendors

    def owns_name(self, name: str) -> bool:
        n = _norm(name)
        if not n or n in SYSTEM_NAMES or name.lower() in SYSTEM_NAMES:
            return True
        if name.lower().startswith(SYSTEM_PREFIXES):
            return True
        if n in self.names or n in self.tokens:
            return True
        for other in self.names | self.tokens:
            if len(n) >= 4 and len(other) >= 4 and (n in other or other in n):
                return True
        return False


def library_key(location: str, entry: str) -> Tuple[str, str]:
    """(kind, key) for an entry: kind is 'bundle', 'team' or 'name'."""
    key = entry
    for s in SUFFIXES:
        if key.endswith(s):
            key = key[: -len(s)]
    if location.endswith("Group Containers"):
        m = TEAM_PREFIX.match(key)
        if m:
            return "team", m.group(1) + "|" + m.group(2)
        if key.startswith("group."):
            key = key[len("group."):]
    if BUNDLE_ID.match(key):
        return "bundle", key
    return "name", key


def owner_label(kind: str, key: str) -> Tuple[str, str]:
    """(group id, readable name) for grouping leftovers of one app."""
    if kind == "bundle":
        parts = key.split(".")
        gid = ".".join(parts[:2]).lower()
        name = parts[1].capitalize() if len(parts) > 1 else key
        return gid, name
    if kind == "team":
        team, rest = key.split("|", 1)
        return "team:" + team, rest.split(".")[-1].capitalize()
    return "name:" + _norm(key), key


# ---------------------------------------------------------------- leftovers
def find_leftovers(home: str, apps: List[dict], teams: Optional[Dict[str, str]] = None,
                   min_group_bytes: int = 1) -> List[dict]:
    """Library data whose app no longer seems to be installed, grouped per app."""
    owners = Owners(apps, teams)
    groups: Dict[str, dict] = {}
    for loc in LIBRARY_LOCATIONS:
        base = os.path.join(home, loc)
        try:
            entries = os.listdir(base)
        except OSError:
            continue  # missing, or protected without Full Disk Access
        for entry in entries:
            if entry.startswith("."):
                continue
            kind, key = library_key(loc, entry)
            if kind == "bundle":
                owned = owners.owns_bundle_id(key)
            elif kind == "team":
                team, rest = key.split("|", 1)
                if team in owners.teams:
                    owned = True
                elif not owners.teams:
                    owned = True  # can't tell without Team IDs: stay quiet
                else:
                    owned = owners.owns_bundle_id(rest) if BUNDLE_ID.match(rest) else owners.owns_name(rest)
            else:
                if not loc.endswith(("Application Support", "Caches")):
                    continue  # plain names elsewhere are mostly system services
                owned = owners.owns_name(key)
            if owned:
                continue
            path = os.path.join(base, entry)
            size = measure(path)
            gid, label = owner_label(kind, key)
            g = groups.setdefault(gid, {"id": gid, "name": label, "items": [], "size": 0, "mtime": 0.0})
            try:
                mtime = os.lstat(path).st_mtime
            except OSError:
                mtime = 0.0
            g["items"].append({"path": path, "location": loc.replace("Library/", ""), "size": size["bytes"],
                               "mtime": mtime})
            g["size"] += size["bytes"]
            g["mtime"] = max(g["mtime"], mtime)
    _merge_name_groups(groups)
    out = [g for g in groups.values() if g["size"] >= min_group_bytes]
    for g in out:
        g["items"].sort(key=lambda i: i["size"], reverse=True)
    out.sort(key=lambda g: g["size"], reverse=True)
    return out


def _merge_name_groups(groups: Dict[str, dict]) -> None:
    """Fold a plain-name group ("Wondershare Filmora Mac") into the bundle-ID
    group of the same vendor ("com.wondershare") so one app shows up once."""
    vendors = {gid.split(".", 1)[1]: gid for gid in groups if "." in gid and not gid.startswith(("name:", "team:"))}
    for gid in [g for g in groups if g.startswith("name:")]:
        n = gid[len("name:"):]
        for vendor, target in vendors.items():
            v = _norm(vendor)
            if len(v) >= 5 and (n.startswith(v) or v.startswith(n)):
                src = groups.pop(gid)
                dst = groups[target]
                dst["items"].extend(src["items"])
                dst["size"] += src["size"]
                dst["mtime"] = max(dst["mtime"], src["mtime"])
                break


def app_data(home: str, app: dict) -> List[dict]:
    """Library data that clearly belongs to one installed app (for uninstalling).

    Stricter than leftover detection: exact bundle ID (or its sub-IDs) and the
    exact app name only, so other apps from the same vendor are never touched.
    """
    bid = app["bundle_id"].lower()
    name = _norm(app["name"])
    found = []
    for loc in LIBRARY_LOCATIONS:
        base = os.path.join(home, loc)
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            kind, key = library_key(loc, entry)
            k = key.lower()
            hit = False
            if kind == "bundle" and bid and (k == bid or k.startswith(bid + ".")):
                hit = True
            elif kind == "team" and bid and key.split("|", 1)[1].lower() in (bid, "group." + bid):
                hit = True
            elif kind == "name" and name and _norm(key) == name and not loc.endswith(("Preferences", "Containers")):
                hit = True
            if hit:
                path = os.path.join(base, entry)
                found.append({"path": path, "location": loc.replace("Library/", ""), "size": measure(path)["bytes"]})
    found.sort(key=lambda i: i["size"], reverse=True)
    return found


def unused_apps(home: str, apps: List[dict], sizes: Dict[str, int], now: Optional[float] = None,
                days: int = UNUSED_AFTER_DAYS) -> List[dict]:
    """Apps not opened in ``days`` days (or never), with their data size."""
    import time

    now = now or time.time()
    out = []
    for a in apps:
        if a["path"].startswith("/System/"):
            continue
        lu = a.get("last_used")
        if lu is not None and now - lu < days * DAY:
            continue
        data = app_data(home, a)
        size = sizes.get(a["path"]) or measure(a["path"])["bytes"]
        out.append({**a, "size": size, "data": data, "data_size": sum(d["size"] for d in data),
                    "days_unused": int((now - lu) / DAY) if lu else None})
    out.sort(key=lambda x: x["size"] + x["data_size"], reverse=True)
    return out
