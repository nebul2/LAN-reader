# PyInstaller spec for the LEM desktop app.
#
# macOS:   venv/bin/pyinstaller packaging/lem.spec --noconfirm  -> dist/LEM.app
# Windows: same spec on a Windows machine/runner                -> dist/LEM/LEM.exe
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
        "lem.devices.tapo",
        "lem.devices.fake",
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
    name="LEM",
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
    name="LEM",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="LEM.app",
        icon=None,
        bundle_identifier="org.greeningofstreaming.lem",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.2.0",
            "NSHumanReadableCopyright": "Greening of Streaming",
            # Without this, macOS silently blocks all LAN connections (no
            # prompt), and scans/measurements find nothing.
            "NSLocalNetworkUsageDescription":
                "LEM talks to smart plugs on your local network to "
                "discover them and measure their power consumption.",
        },
    )
