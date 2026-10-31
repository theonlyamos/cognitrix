# PyInstaller spec for the desktop app's engine (the API server + task worker).
# Build with:  python desktop/scripts/build_engine.py
# Output:      desktop/src-tauri/engine/  (one folder, bundled as a Tauri resource)
#
# --onedir rather than --onefile: a one-file build unpacks itself to a temp
# folder on every start, and the task worker re-runs this same executable, so
# that cost would be paid twice per task run.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / 'frontend' / 'dist'), 'frontend/dist'),
    (str(ROOT / 'pyproject.toml'), '.'),
]
# Built-in skills ship .md files and helper scripts that are read as files.
datas += collect_data_files('cognitrix', include_py_files=True, excludes=['**/__pycache__'])
binaries = []
hiddenimports = collect_submodules('cognitrix')

# Packages that load plugins, drivers or data files by name at runtime.
for package in ('celery', 'kombu', 'billiard', 'uvicorn', 'chromadb', 'onnxruntime',
                'tokenizers', 'odbms', 'mcp', 'sse_starlette', 'tiktoken', 'fitz', 'pymupdf'):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

for dist in ('cognitrix', 'celery', 'kombu', 'fastapi', 'uvicorn', 'chromadb', 'openai', 'mcp'):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

a = Analysis(
    [str(ROOT / 'desktop' / 'scripts' / 'engine_entry.py')],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=['tkinter', 'matplotlib', 'IPython', 'pytest'],
    noarchive=False,
    # inflect decorates functions with typeguard's @typechecked at import,
    # which reads their source; ship the .py files next to the bytecode.
    module_collection_mode={'inflect': 'pyz+py', 'typeguard': 'pyz+py'},
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='cognitrix-engine',
    # Console build: the app starts it with CREATE_NO_WINDOW, so no window
    # shows, and stdout/stderr stay real streams that reach the engine log
    # (a windowed build sets them to None, which breaks uvicorn's logging).
    console=True,
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name='engine', upx=False)
