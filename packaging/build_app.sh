#!/usr/bin/env bash
# Build Spelunk.app and a drag-to-install DMG. Run on macOS:
#
#   packaging/build_app.sh
#
# Output: dist/Spelunk.app and dist/Spelunk-macOS-<arch>.dmg (arch: arm64 or x86_64)
#
# Optional environment:
#   PYTHON=python3.12                       Python to build with (3.9-3.13)
#   CODESIGN_IDENTITY="Developer ID Application: Name (TEAMID)"
#                                           sign for distribution (default: ad-hoc "-")
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Spelunk.app must be built on macOS." >&2
  exit 1
fi

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  for c in python3.12 python3.13 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
echo "Using $("$PY" --version) at $(command -v "$PY")"

VERSION=$("$PY" -c 'import spelunk; print(spelunk.__version__)')
ARCH=$(uname -m)
IDENTITY="${CODESIGN_IDENTITY:--}"

rm -rf build dist .build-venv
"$PY" -m venv .build-venv
.build-venv/bin/python -m pip install --quiet --upgrade pip
.build-venv/bin/python -m pip install --quiet -r packaging/requirements-build.txt

CODESIGN_IDENTITY="$IDENTITY" .build-venv/bin/pyinstaller --noconfirm --clean \
  --distpath dist --workpath build packaging/spelunk.spec

APP="dist/Spelunk.app"
if [[ "$IDENTITY" == "-" ]]; then
  codesign --force --deep --sign - "$APP"
else
  codesign --force --options runtime --timestamp \
    --entitlements packaging/entitlements.plist --sign "$IDENTITY" "$APP"
fi
codesign --verify --deep --strict "$APP"

# Drag-to-Applications disk image
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
# Stable name so README can link to releases/latest/download/Spelunk-macOS-arm64.dmg
DMG="dist/Spelunk-macOS-$ARCH.dmg"
hdiutil create -quiet -volname "Spelunk" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
if [[ "$IDENTITY" != "-" ]]; then
  codesign --force --sign "$IDENTITY" "$DMG"
fi

echo
echo "Built $APP"
echo "Built $DMG"
echo "Try it:  open $APP"
