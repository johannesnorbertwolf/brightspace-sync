# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the native Brightspace Sync menu bar app.

Deliberately excludes Qt and Playwright so the bundle stays small: the GUI
uses macOS's own AppKit through PyObjC, and Chrome is driven over the
DevTools Protocol instead of through a bundled browser engine.
"""

import re
from pathlib import Path

ROOT = Path(SPECPATH)
version = re.search(
    r'__version__ = "([^"]+)"', (ROOT / "brightspace_sync" / "__init__.py").read_text()
).group(1)

hiddenimports = [
    "objc",
    "AppKit",
    "Foundation",
    "PyObjCTools",
    "PyObjCTools.AppHelper",
    "websocket",
]

excludes = [
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "playwright",
    "numpy",
    "pandas",
    "matplotlib",
    "scipy",
    "PIL",
    "setuptools",
    "pip",
    "pytest",
]

a = Analysis(
    [str(ROOT / "run_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BrightspaceSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BrightspaceSync",
)

app = BUNDLE(
    coll,
    name="Brightspace Sync.app",
    icon=str(ROOT / "build" / "AppIcon.icns"),
    bundle_identifier="com.brightspace-sync.app",
    info_plist={
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "CFBundleName": "Brightspace Sync",
        "CFBundleDisplayName": "Brightspace Sync",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
    },
)
