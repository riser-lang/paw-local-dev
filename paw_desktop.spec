# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec for PAW Personal Work OS v2.5.0 (Windows 本地化版)

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

project_root = Path(__file__).resolve().parent if '__file__' in globals() else Path.cwd().resolve()
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'backend'))
sys.path.insert(0, str(project_root / 'backend' / 'runtime'))
sys.path.insert(0, str(project_root / 'backend' / 'runtime' / 'core'))

datas = [
    (str(project_root / 'frontend' / 'dist'), 'frontend/dist'),
    (str(project_root / 'template' / 'workspace_template.db'), 'template'),
    (str(project_root / 'backend' / 'runtime' / 'core'), 'backend/runtime/core'),
    (str(project_root / 'backend' / 'runtime' / '__init__.py'), 'backend/runtime'),
    (str(project_root / 'backend' / 'repair_v238.py'), 'backend'),
    (str(project_root / 'backend' / 'pm' / 'dossier_sections.json'), 'backend/pm'),
    (str(project_root / 'backend' / 'pm' / 'dossier_views.json'), 'backend/pm'),
    (str(project_root / 'backend' / 'pm' / 'contracts'), 'backend/pm/contracts'),
]

# Collect tzdata if available
try:
    datas += collect_data_files('tzdata')
except Exception:
    pass

hiddenimports = [
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespans',
    'uvicorn.lifespans.on',
    'fastapi',
    'starlette',
    'pydantic',
    'docx',
    'openpyxl',
    'pptx',
    'pypdf',
    'PIL',
    'watchdog',
    'httpx',
    'lxml',
    'lxml.html',
    'tzdata',
    'zoneinfo',
    'sqlite3',
    'backend.search',
    'backend.search.contract',
    'backend.search.service',
    'backend.search.ssrf',
    'backend.search.gatekeeper',
    'backend.search.assistant_bridge',
    'backend.search.backends',
    'backend.search.backends.bing',
    'backend.search.backends.duckduckgo',
    'backend.search.backends.baidu',
    'backend.search.backends.sogou',
    'backend.main',
    'backend.repair_v238',
    'backend.core.config',
    'backend.governance.service',
    'backend.governance.committer',
    'backend.governance.router',
    'backend.workspace_api.router',
    'backend.reader.dispatch',
    'backend.runtime_core.scheduler',
    'backend.runtime_core.planner',
    'backend.runtime_core.event_bus',
    'backend.runtime_core.conflict_analyzer',
    'backend.runtime_core.evolution_engine',
    'backend.runtime_core.harness_adapter',
    'backend.watcher.observer',
    'pystray',
    'pystray._win32',
    'backend.tray',
    'backend.tray.tray_app',
]

# Collect backend submodules dynamically, strictly excluding legacy & experimental
backend_submodules = collect_submodules('backend')
hiddenimports += [
    m for m in backend_submodules 
    if not m.startswith('backend.runtime.legacy') and not m.startswith('backend.runtime.experimental')
]

a = Analysis(
    ['run.py'],
    pathex=[str(project_root), str(project_root / 'backend'), str(project_root / 'backend' / 'runtime'), str(project_root / 'backend' / 'runtime' / 'core')],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'numpy', 'pytest', 'backend.runtime.legacy', 'backend.runtime.experimental'],
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
    name='PAW_Personal_Work_OS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Set to False so NO black console window appears on desktop
    disable_windowed_traceback=False,
    argv_emulation=False,
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
    upx=True,
    upx_exclude=[],
    name='PAW_Personal_Work_OS',
)
