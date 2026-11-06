# -*- mode: python ; coding: utf-8 -*-
"""Empaqueta Sound como aplicación de escritorio en dist/Sound (onedir + Inno Setup)."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()

datas = collect_data_files("ytmusicapi")
# El .ico va dentro del ejecutable para la barra de tareas/menú, pero Tk necesita además el
# archivo para ponerlo en la ventana y en los diálogos; el .png es el respaldo en otros Tk.
datas += [
    (str(ROOT / "packaging" / "sound.ico"), "packaging"),
    (str(ROOT / "packaging" / "sound.png"), "packaging"),
]
hiddenimports = (
    # La ventana importa páginas y el servicio de descargas de forma diferida; PyInstaller no
    # las ve en el análisis estático.
    collect_submodules("app")
    + collect_submodules("yt_dlp")
    + collect_submodules("yt_dlp_ejs")
    + [
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
        "winrt.windows.media.core",
        "winrt.windows.media.playback",
        "winrt.windows.storage.streams",
    ]
)

node = ROOT / "packaging" / "node" / "node.exe"
binaries = [(str(node), "node")] if node.exists() else []

a = Analysis(
    [str(ROOT / "packaging" / "entry_gui.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests", "ruff", "tkinter.test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sound",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "packaging" / "sound.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Sound",
)
