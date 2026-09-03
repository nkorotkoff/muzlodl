# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for music-loader.

Builds a single-file executable. Run:
    pyinstaller music-loader.spec

Or use the wrapper: ./build.sh
"""
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = []
hiddenimports = []
excludes = ["tkinter", "matplotlib", "numpy", "pandas", "PyQt5", "PyQt6", "PySide2", "PySide6"]

# yt-dlp ships extractors as separate modules and a data file
for pkg in ("yt_dlp",):
    d, h, _ = collect_all(pkg)
    datas += d
    hiddenimports += h

# mutagen, requests are pure-python or small - default discovery works
# But collect them anyway to be safe with submodules
for pkg in ("mutagen", "requests"):
    try:
        d, h, _ = collect_all(pkg)
        datas += d
        hiddenimports += h
    except Exception:
        pass

# Optional: yandex_music, spotdl - only include if importable
for pkg in ("yandex_music", "spotdl"):
    try:
        __import__(pkg)
        d, h, _ = collect_all(pkg)
        datas += d
        hiddenimports += h
    except ImportError:
        pass

from pathlib import Path as _Path
# Bundle Svelte dist when present (Vite outDir: loader/web/static/dist)
_dist = _Path("loader/web/static/dist")
if _dist.exists():
    datas += [(str(_dist), "loader/web/static/dist")]

# Bundle legacy Jinja/static for Frozen fallback (serve when dist not yet built)
for _p in ["loader/web/static/dist"]:
    _pp = _Path(_p)
    if _pp.exists():
        datas += [(str(_pp), _p)]
for _p in ["loader/web/static/login.html", "loader/web/static/setup.html"]:
    _pp = _Path(_p)
    if _pp.exists():
        datas += [(str(_pp), _p)]

a = Analysis(
    ["loader/__main__.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="music-loader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
