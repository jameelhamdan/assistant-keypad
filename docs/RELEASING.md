# Releasing

1. Make sure `master` is green (the `ci` workflow: host tests and lint on macOS and Windows, firmware tests and build).
2. Tag it and push the tag:

   ```sh
   git tag v2.2.0
   git push origin v2.2.0
   ```

3. The `ci` workflow then, for the tag:
   - builds the firmware with the tag as its version and bundles it into the app (so *Update firmware* in the tray works);
   - builds `Keypad-2.2.0.dmg` (macOS) and `Keypad-2.2.0-setup.exe` (Windows);
   - publishes both on the GitHub release with generated notes.

The app and firmware version come from the tag, not from `pyproject.toml` (which is only the development version). Builds are not code-signed: macOS shows *Open Anyway* once and Windows shows SmartScreen (see the README's Install section). To sign and notarize the DMG, set `CODESIGN_ID` and `NOTARY_PROFILE` for `packaging/macos/build.sh`.

A failed release can be re-run from the Actions tab. To redo a tag: delete the release and the tag, then tag again.
