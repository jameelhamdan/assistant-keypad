#!/bin/sh
# Builds dist/Keypad.app and dist/Keypad-<version>.dmg (PyInstaller, one folder).
# Signing: set CODESIGN_ID="Developer ID Application: ..." to sign for
# distribution. Without it the app is ad-hoc signed, which is fine on the
# machine that built it but shows Gatekeeper's "can't verify" prompt on any
# other Mac. Also set NOTARY_PROFILE (a keychain profile made once with
# `xcrun notarytool store-credentials`) to notarize and staple the DMG, which
# removes that prompt entirely.
# KEYPAD_ARCH=universal2 builds for Intel and Apple Silicon (needs a universal2 Python).
set -eu
cd "$(dirname "$0")/../.."
VERSION=${VERSION:-dev}
PY=host/.venv/bin/python
WORK=$(mktemp -d)

$PY packaging/mkicon.py "$WORK/Keypad.iconset"
iconutil -c icns -o "$WORK/Keypad.icns" "$WORK/Keypad.iconset"

printf '%s\n' "${VERSION#v}" > host/keypad/data/version
trap 'rm -f host/keypad/data/version; rm -rf "$WORK"' EXIT

# PyInstaller writes into a scratch folder: its "Keypad" output folder would
# otherwise collide with anything named "keypad" in dist/ (macOS ignores case).
KEYPAD_VERSION=$VERSION KEYPAD_ICON="$WORK/Keypad.icns" $PY -m PyInstaller --noconfirm --clean \
  --distpath "$WORK/dist" --workpath "$WORK/build" packaging/keypad.spec
mkdir -p dist
rm -rf dist/Keypad.app
mv "$WORK/dist/Keypad.app" dist/
if [ -n "${CODESIGN_ID:-}" ]; then
  # Hardened runtime needs one Team ID across the app and its libraries: sign everything with it.
  codesign --force --deep --options runtime --timestamp --sign "$CODESIGN_ID" dist/Keypad.app
fi  # otherwise PyInstaller's ad-hoc signature stands (fine on the machine that built it)

DMG="dist/Keypad-${VERSION}.dmg"
rm -f "$DMG"
STAGE="$WORK/dmg"
mkdir -p "$STAGE"
cp -R dist/Keypad.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname Keypad -srcfolder "$STAGE" -ov -format UDZO "$DMG"

if [ -n "${CODESIGN_ID:-}" ] && [ -n "${NOTARY_PROFILE:-}" ]; then
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi  # without NOTARY_PROFILE, a signed (or ad-hoc) build still shows "Open Anyway" on first launch

echo "built dist/Keypad.app and $DMG"
