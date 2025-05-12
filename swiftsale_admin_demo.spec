# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['swiftsale_admin_demo.py'],
    pathex=[],
    binaries=[],
    datas=[('SwiftSale.png', '.'), ('C:\\Program Files\\Tesseract-OCR\\tesseract.exe', 'tesseract')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='swiftsale_admin_demo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
