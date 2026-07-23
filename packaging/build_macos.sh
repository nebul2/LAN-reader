#!/usr/bin/env bash
# Build the (currently unsigned) macOS app bundle: dist/LEM.app
#
# Once the Apple Developer account is active, signing + notarization slot in
# after the pyinstaller step:
#   codesign --deep --force --options runtime -s "Developer ID Application: ..." dist/LEM.app
#   ditto -c -k --keepParent dist/LEM.app dist/LEM.zip
#   xcrun notarytool submit dist/LEM.zip --keychain-profile gos --wait
#   xcrun stapler staple dist/LEM.app
set -euo pipefail
cd "$(dirname "$0")/.."

venv/bin/pip install -q -e '.[gui]' pyinstaller
venv/bin/pyinstaller packaging/lem.spec --noconfirm --distpath dist --workpath build
echo
echo "Built: dist/LEM.app (unsigned)"
