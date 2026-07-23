# PyInstaller spec for the LAN-reader desktop app.
#
# macOS:   venv/bin/pyinstaller packaging/LAN-reader.spec --noconfirm  -> dist/LAN-reader.app
# Windows: same spec on a Windows machine/runner                       -> dist/LAN-reader/LAN-reader.exe
#
# The bundle is unsigned; signing/notarization is applied to the finished
# .app once the Apple Developer (GREENING OF STREAMING) account is active.

import os
import sys

SRC = os.path.join(SPECPATH, "..", "src")

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[SRC],
    binaries=[],
    datas=[],
    # tapo (Rust extension) and the device modules are imported lazily via the
    # DEVICE_TYPES registry, so PyInstaller's static analysis misses them.
    hiddenimports=[
        "tapo",
        "measure.devices.tapo",
        "measure.devices.fake",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LAN-reader",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LAN-reader",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="LAN-reader.app",
        icon=None,
        bundle_identifier="org.greeningofstreaming.lan-reader",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.1.0",
            "NSHumanReadableCopyright": "Greening of Streaming",
        },
    )
