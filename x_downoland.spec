# PyInstaller spec — builds X-downoland.exe (Windows) / .app (mac) as one-folder bundle.
# Run: pyinstaller --noconfirm x_downoland.spec
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = []
# browser-cookie3 pulls in optional encryption deps + sqlite — make sure they ship
for mod in ("browser_cookie3", "Cryptodome", "lz4", "jeepney", "secretstorage"):
    try:
        hidden.extend(collect_submodules(mod))
    except Exception:
        pass

a = Analysis(
    ["x_scraper.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="X-downoland",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="X-downoland",
)
