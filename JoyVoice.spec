# -*- mode: python ; coding: utf-8 -*-
# JoyVoice FREE (offline) build: bundles faster-whisper / ctranslate2 / PyAV /
# onnxruntime so Free Mode (local Whisper) works with no API key.
# CPU-oriented (no CUDA pip wheels collected); a GPU is still auto-used if present.

from PyInstaller.utils.hooks import collect_all

blocklist = ['torch', 'torchvision', 'torchaudio', 'transformers']

datas = []
binaries = []
hiddenimports = ['sounddevice', 'speech_recognition', 'numpy', 'pyperclip', 'keyboard', 'pycaw', 'comtypes', 'psutil']

for pkg in ['faster_whisper', 'ctranslate2', 'av', 'onnxruntime']:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ['app\\main.py'],
    pathex=[],
    binaries=binaries,
    datas=[('assets', 'assets')] + datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=blocklist,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='JoyVoice',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
