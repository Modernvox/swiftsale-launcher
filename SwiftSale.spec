# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['SwiftSaleApp.py'],
    pathex=[],
    binaries=[('tesseract/tesseract.exe', 'tesseract')],
    datas=[('SwiftSale.png', '.'), ('config.ini', '.'), ('templates', 'templates'), ('tesseract', 'tesseract'), ('.env', '.')],
    hiddenimports=['pytesseract', 'pdfplumber', 'fitz', 'telegram', 'flask', 'flask_socketio', 'socketio', 'engineio', 'engineio.async_drivers.threading', 'pyperclip', 'dotenv', 'urllib3', 'urllib3.util.request', 'urllib3.util.response', 'urllib3.util.retry', 'urllib3.util.url', 'urllib3.util.ssltransport', 'urllib3.util.ssl_', 'urllib3.util.timeout', 'urllib3.util.proxy', 'urllib3.util.ssl_match_hostname', 'urllib3.util.queue', 'httpx', 'cryptography', 'werkzeug', 'jinja2', 'itsdangerous', 'blinker'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=True,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('v', None, 'OPTION')],
    name='SwiftSale',
    debug=True,
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
    icon=['SwiftSale.ico'],
)
