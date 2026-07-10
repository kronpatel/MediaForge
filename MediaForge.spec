# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MediaForge Companion Application.

Usage (from project root):
    pyinstaller MediaForge.spec

Builds a one-file EXE at dist/MediaForge.exe containing:
  - MediaForge.exe (companion entry point)
  - All Python dependencies
  - Resource files (icons, assets)
"""

import os
import sys

# Build always runs from project root where MediaForge.spec lives
ROOT = os.getcwd()
COMPANION = os.path.join(ROOT, "companion")
RESOURCES = os.path.join(COMPANION, "resources")

block_cipher = None

a = Analysis(
    [os.path.join(COMPANION, "main.py")],
    pathex=[ROOT, COMPANION],
    binaries=[],
    datas=[
        (os.path.join(RESOURCES, "icon.ico"), "resources"),
        (os.path.join(RESOURCES, "icon.png"), "resources"),
        (os.path.join(RESOURCES, "tray.ico"), "resources"),
    ],
    hiddenimports=[
        "PIL",
        "PIL._tkinter_finder",
        "customtkinter",
        "pystray",
        "requests",
        "psutil",
        "queue",
        "threading",
        "json",
        "os",
        "sys",
        "time",
        "datetime",
        "subprocess",
        "shutil",
        "uuid",
        "glob",
        "pathlib",
        "logging",
        "traceback",
        "webbrowser",
        "socket",
        "io",
        "base64",
        "hashlib",
        "re",
        "tempfile",
        "atexit",
        "signal",
        "ctypes",
        "winreg",
        "configparser",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter.test",
        "unittest",
        "test",
        "setuptools",
        "pip",
        "distutils",
        "pygments",
        "IPython",
        "jedi",
        "matplotlib",
        "scipy",
        "numpy",
        "pandas",
        "PyQt5",
        "PySide2",
        "PySide6",
        "notebook",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
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
    name="MediaForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(RESOURCES, "icon.ico"),
)
