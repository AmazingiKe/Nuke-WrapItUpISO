# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import json
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

project_root = Path(SPECPATH).resolve().parent
metadata = json.loads((project_root / 'version.json').read_text(encoding='utf-8'))
app_name, app_version = metadata['name'], metadata['version']
release_name = f'{app_name} {app_version}'
numeric_version = tuple(int(part) for part in app_version.split('.')) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numeric_version, prodvers=numeric_version),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('FileDescription', app_name),
        StringStruct('FileVersion', app_version),
        StringStruct('InternalName', app_name),
        StringStruct('OriginalFilename', release_name + '.exe'),
        StringStruct('ProductName', app_name),
        StringStruct('ProductVersion', app_version),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])],
)

a = Analysis(
    [str(project_root / 'app.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(project_root / 'version.json'), '.'), (str(project_root / 'assets'), 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'tkinterdnd2', 'PyQt5', 'PyQt6', 'PySide2'],
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
    name=release_name,
    icon=str(project_root / 'assets' / 'Icon' / 'app.ico'),
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
