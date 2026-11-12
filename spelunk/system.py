"""macOS system information that a plain folder scan cannot see.

* APFS volumes in the boot container (System, Data, Preboot, Recovery, VM)
* Local snapshots (Time Machine and macOS update snapshots)
* Connected external drives that can receive moved files

Everything here shells out to Apple's own tools (``diskutil``, ``tmutil``)
and degrades gracefully on other platforms so the rest of the app and the
test-suite run on Linux too.
"""
from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
from typing import Dict, List, Optional

IS_MAC = platform.system() == "Darwin"


def _run(cmd: List[str], timeout: int = 20) -> Optional[bytes]:
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def home() -> str:
    return os.path.expanduser("~")


def default_roots() -> List[str]:
    """Folders worth scanning, in the order they are shown."""
    h = home()
    if not IS_MAC:
        return [h]
    roots = [h, "/Applications", "/Library", "/opt/homebrew", "/usr/local", "/Users/Shared", "/private/var"]
    return [r for r in roots if os.path.lexists(r)]


def disk_usage(path: str = "/") -> Dict[str, int]:
    u = shutil.disk_usage(path)
    return {"total": u.total, "used": u.used, "free": u.free}


def apfs_volumes() -> List[Dict]:
    """Volumes that share the boot drive's APFS container.

    ``Capacity Consumed`` per volume is the only honest source for how much
    space macOS itself (System, Preboot, Recovery, VM/swap) occupies.
    """
    if not IS_MAC:
        return []
    raw = _run(["diskutil", "apfs", "list", "-plist"])
    if not raw:
        return []
    try:
        data = plistlib.loads(raw)
    except Exception:
        return []
    boot = _boot_container_ref()
    result = []
    for container in data.get("Containers", []):
        if boot and container.get("ContainerReference") != boot:
            continue
        for vol in container.get("Volumes", []):
            result.append(
                {
                    "name": vol.get("Name", ""),
                    "roles": vol.get("Roles", []),
                    "device": vol.get("DeviceIdentifier", ""),
                    "used": int(vol.get("CapacityInUse", 0)),
                }
            )
        if boot:
            break
    return result


def _boot_container_ref() -> Optional[str]:
    raw = _run(["diskutil", "info", "-plist", "/"])
    if not raw:
        return None
    try:
        info = plistlib.loads(raw)
    except Exception:
        return None
    # "APFSContainerReference" is e.g. "disk3"
    return info.get("APFSContainerReference")


def snapshots() -> List[Dict]:
    """Local APFS snapshots on the boot volume.

    Snapshots are invisible in Finder but keep deleted data alive. Time
    Machine snapshots can be removed by the user; ``com.apple.os.update``
    snapshots belong to macOS and are released by finishing the update.
    """
    if not IS_MAC:
        return []
    out = _run(["diskutil", "apfs", "listSnapshots", "/"]) or b""
    names = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            names.append(line.split(":", 1)[1].strip())
    tm = _run(["tmutil", "listlocalsnapshots", "/"]) or b""
    for line in tm.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("com.apple.") and line not in names:
            names.append(line)
    result = []
    for name in names:
        if "TimeMachine" in name:
            kind, note = "timemachine", "Time Machine local snapshot. Safe to remove: sudo tmutil deletelocalsnapshots <date>"
        elif "MSUPrepareUpdate" in name:
            kind, note = "update-prep", "A downloaded macOS update waiting to install. Installing (or cancelling) the update frees it."
        elif "os.update" in name:
            kind, note = "os", "Snapshot of the running macOS. Managed by the system; keep."
        else:
            kind, note = "other", "Snapshot created by another app or backup tool."
        result.append({"name": name, "kind": kind, "note": note})
    return result


def external_volumes() -> List[Dict]:
    """Writable drives mounted under /Volumes (excluding the boot drive)."""
    base = "/Volumes" if IS_MAC else "/media"
    result = []
    if not os.path.isdir(base):
        return result
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue  # "Macintosh HD" is a symlink to /
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        fs = _filesystem(path)
        result.append(
            {
                "name": name,
                "path": path,
                "total": usage.total,
                "free": usage.free,
                "writable": os.access(path, os.W_OK),
                "filesystem": fs,
                "supports_symlinks": fs not in ("exfat", "msdos", "fat32", "ntfs"),
                "is_disk_image": _is_disk_image(path),
            }
        )
    return result


def filesystem(path: str) -> str:
    """Filesystem type of the volume holding ``path`` (e.g. apfs, exfat); '' if unknown."""
    return _filesystem(path)


def _filesystem(path: str) -> str:
    if not IS_MAC:
        return ""
    raw = _run(["diskutil", "info", "-plist", path], timeout=10)
    if not raw:
        return ""
    try:
        info = plistlib.loads(raw)
    except Exception:
        return ""
    return str(info.get("FilesystemType", "")).lower()


def _is_disk_image(path: str) -> bool:
    if not IS_MAC:
        return False
    raw = _run(["diskutil", "info", "-plist", path], timeout=10)
    if not raw:
        return False
    try:
        info = plistlib.loads(raw)
    except Exception:
        return False
    return "Disk Image" in str(info.get("BusProtocol", "")) or "Disk Image" in str(info.get("MediaName", ""))


def overview(scanned: Dict[str, int]) -> Dict:
    """Combine disk totals, APFS volumes and scan results.

    ``scanned`` maps root path -> bytes found by the scanner. Whatever the
    disk reports as used but the scan could not see is reported as
    ``hidden`` so the user knows how much is still unexplained.
    """
    usage = disk_usage("/")
    volumes = apfs_volumes()
    system_bytes = sum(v["used"] for v in volumes if not ({"Data", "User"} & set(v["roles"])))
    data_vol = next((v for v in volumes if "Data" in v["roles"]), None)
    data_used = data_vol["used"] if data_vol else usage["used"]
    seen = sum(scanned.values())
    hidden = max(0, data_used - seen)
    return {
        "platform": platform.system(),
        "mac_version": platform.mac_ver()[0] if IS_MAC else "",
        "disk": usage,
        "volumes": volumes,
        "system_bytes": system_bytes,
        "data_used": data_used,
        "scanned": scanned,
        "scanned_total": seen,
        "hidden": hidden,
        "snapshots": snapshots(),
    }


def full_disk_access(home: Optional[str] = None) -> Optional[bool]:
    """Does this process have Full Disk Access? ``None`` when not on macOS.

    The per-user TCC database is only readable with Full Disk Access, which
    makes it a reliable probe that doesn't touch any personal data.
    """
    if not IS_MAC:
        return None
    h = home or os.path.expanduser("~")
    probe = os.path.join(h, "Library", "Application Support", "com.apple.TCC", "TCC.db")
    try:
        with open(probe, "rb"):
            return True
    except PermissionError:
        return False
    except OSError:
        pass
    # Fallback: Safari's folder is protected the same way.
    try:
        os.listdir(os.path.join(h, "Library", "Safari"))
        return True
    except PermissionError:
        return False
    except OSError:
        return None


def open_full_disk_access_settings() -> bool:
    """Open System Settings at Privacy & Security > Full Disk Access."""
    if not IS_MAC:
        return False
    url = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
    return _run(["open", url]) is not None


def reveal_in_finder(path: str) -> bool:
    if not IS_MAC or not os.path.lexists(path):
        return False
    return _run(["open", "-R", path]) is not None
