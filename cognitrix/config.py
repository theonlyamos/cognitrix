import asyncio
import aiofiles
from pathlib import Path

VERSION = '0.2.5'
API_VERSION = 'v1'
COGNITRIX_WORKDIR = Path.home() / '.cognitrix'

TASKS_FILE = COGNITRIX_WORKDIR / 'tasks.json'
AGENTS_FILE = COGNITRIX_WORKDIR / 'agents.json'
CONFIG_FILE = COGNITRIX_WORKDIR / 'config.json'
SESSIONS_FILE = COGNITRIX_WORKDIR / 'sessions.json'
BASE_DIR = Path(__file__).parent
FRONTEND_BUILD_DIR = BASE_DIR.joinpath('..', 'frontend', 'dist')
FRONTEND_STATIC_DIR = FRONTEND_BUILD_DIR.joinpath('assets')

async def configure():
    if not COGNITRIX_WORKDIR.exists() and not COGNITRIX_WORKDIR.is_dir():
        COGNITRIX_WORKDIR.mkdir()
        
    for file_path in [TASKS_FILE, AGENTS_FILE, CONFIG_FILE, SESSIONS_FILE]:
        if not file_path.exists():
            async with aiofiles.open(file_path, 'w') as file:
                pass
        
asyncio.run(configure())