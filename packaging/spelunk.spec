# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for Spelunk.app. Build with:  packaging/build_app.sh
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH is set by PyInstaller)
sys.path.insert(0, ROOT)
from spelunk import __version__  # noqa: E402

IDENTITY = os.environ.get("CODESIGN_IDENTITY") or None
if IDENTITY == "-":
    IDENTITY = None  # ad-hoc signing happens after the build

a = Analysis(  # noqa: F821
    [os.path.join(SPECPATH, "launch.py")],  # noqa: F821
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "spelunk", "rules"), "spelunk/rules"),
        (os.path.join(ROOT, "spelunk", "web"), "spelunk/web"),
    ],
    hiddenimports=["spelunk.desktop", "webview"],
    excludes=["tkinter", "test", "unittest", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Spelunk",
    console=False,
    argv_emulation=False,
    codesign_identity=IDENTITY,
    entitlements_file=os.path.join(SPECPATH, "entitlements.plist") if IDENTITY else None,  # noqa: F821
)

coll = COLLECT(exe, a.binaries, a.datas, name="Spelunk")  # noqa: F821

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="Spelunk.app",
        icon=os.path.join(SPECPATH, "icon.icns"),  # noqa: F821
        bundle_identifier="io.github.mazzyy.spelunk",
        version=__version__,
        info_plist={
            "CFBundleName": "Spelunk",
            "CFBundleDisplayName": "Spelunk",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "12.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "© 2026 Abdul Musawar Soomro. MIT License.",
            "NSAppleEventsUsageDescription": "Spelunk uses Finder to show items you select.",
        },
    )
