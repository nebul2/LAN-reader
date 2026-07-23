#!/usr/bin/env bash
# Build the (currently unsigned) macOS app bundle: dist/LAN-reader.app
#
# Once the Apple Developer account is active, signing + notarization slot in
# after the pyinstaller step:
#   codesign --deep --force --options runtime -s "Developer ID Application: ..." dist/LAN-reader.app
#   ditto -c -k --keepParent dist/LAN-reader.app dist/LAN-reader.zip
#   xcrun notarytool submit dist/LAN-reader.zip --keychain-profile gos --wait
#   xcrun stapler staple dist/LAN-reader.app
set -euo pipefail
cd "$(dirname "$0")/.."

venv/bin/pip install -q -e '.[gui]' pyinstaller
venv/bin/pyinstaller packaging/LAN-reader.spec --noconfirm --distpath dist --workpath build
echo
echo "Built: dist/LAN-reader.app (unsigned)"
