import asyncio
import aiofiles
from pathlib import Path

VERSION = '0.2.5'
API_VERSION = 'v1'
SPIRAL_WORKDIR = Path.home() / '.cognitrix'

AGENTS_FILE = SPIRAL_WORKDIR / 'agents.json'
CONFIG_FILE = SPIRAL_WORKDIR / 'config.json'
SESSIONS_FILE = SPIRAL_WORKDIR / 'sessions.json'
BASE_DIR = Path(__file__).parent
FRONTEND_BUILD_DIR = BASE_DIR.joinpath('..', 'frontend', 'dist')
FRONTEND_STATIC_DIR = FRONTEND_BUILD_DIR.joinpath('assets')

async def configure():
    if not SPIRAL_WORKDIR.exists() and not SPIRAL_WORKDIR.is_dir():
        SPIRAL_WORKDIR.mkdir()
        
    if not AGENTS_FILE.exists() and not AGENTS_FILE.is_file():
        async with aiofiles.open(AGENTS_FILE, 'w') as file:
            pass
            
    if not CONFIG_FILE.exists() and not CONFIG_FILE.is_file():
        async with aiofiles.open(CONFIG_FILE, 'w') as file:
            pass
        
    if not SESSIONS_FILE.exists() and not SESSIONS_FILE.is_file():
        async with aiofiles.open(SESSIONS_FILE, 'w') as file:
            pass
        
asyncio.run(configure())