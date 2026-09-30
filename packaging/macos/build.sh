#!/bin/sh
# Builds dist/Keypad.app (universal) and dist/Keypad-<version>.dmg.
# Signing: set CODESIGN_ID="Developer ID Application: ..." to sign for
# distribution (then notarize the DMG with `xcrun notarytool`). Without it
# the app is ad-hoc signed, which is fine on the machine that built it.
set -eu
cd "$(dirname "$0")/../.."
VERSION=${VERSION:-dev}
APP=dist/Keypad.app
rm -rf "$APP" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

for arch in arm64 amd64; do
  (cd host && CGO_ENABLED=1 GOOS=darwin GOARCH=$arch go build -trimpath -ldflags "${LDFLAGS:-}" -o "../dist/keypad-$arch" ./cmd/keypad)
done
lipo -create -output "$APP/Contents/MacOS/keypad" dist/keypad-arm64 dist/keypad-amd64
rm dist/keypad-arm64 dist/keypad-amd64

sed "s/__VERSION__/${VERSION#v}/g" packaging/macos/Info.plist > "$APP/Contents/Info.plist"
[ -f packaging/macos/Keypad.icns ] && cp packaging/macos/Keypad.icns "$APP/Contents/Resources/"

codesign --force --options runtime --timestamp=none --sign "${CODESIGN_ID:--}" "$APP"

DMG="dist/Keypad-${VERSION}.dmg"
rm -f "$DMG"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname Keypad -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "built $APP and $DMG"
