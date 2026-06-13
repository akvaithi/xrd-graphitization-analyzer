# PyInstaller spec — builds the macOS .app and the Windows .exe (onedir).
#
#   pyinstaller packaging/xrd.spec --noconfirm     # run from the repo root
#
# Output: dist/"XRD Graphitization Analyzer.app"   (macOS)
#         dist/"XRD Graphitization Analyzer"/...exe (Windows)
import os
import sys
from PyInstaller.utils.hooks import collect_data_files

APP_NAME = "XRD Graphitization Analyzer"
# Spec lives in packaging/; the project (scripts, fonts/) is its parent. Anchor
# everything to ROOT so the build works regardless of the invocation cwd.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# Bundle matplotlib's data (mpl-data); numpy/scipy/matplotlib are pulled in
# automatically by PyInstaller's maintained hooks. No proprietary fonts shipped.
datas = collect_data_files("matplotlib")
hiddenimports = ["xrd_webgui", "xrd_analyzer", "run_parser"]

a = Analysis(
    [os.path.join(ROOT, "app_launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# A visible console on Windows guarantees a closeable window + the URL even if
# the tray icon can't start; on macOS the menu-bar icon (LSUIElement) handles it.
_console = sys.platform.startswith("win")

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME,
    console=_console,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=APP_NAME + ".app",
        bundle_identifier="edu.tamu.xrd.graphitization",
        info_plist={
            "LSUIElement": True,          # menu-bar app, no dock icon
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "NSHighResolutionCapable": True,
        },
    )
