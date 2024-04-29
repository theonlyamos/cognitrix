from pathlib import Path
import asyncio
import aiofiles
import json

VERSION = '0.2.2'
SPIRAL_WORKDIR = Path('~').expanduser() / '.cognitrix'

AGENTS_FILE = SPIRAL_WORKDIR / 'agents.json'
CONFIG_FILE = SPIRAL_WORKDIR / 'config.json'
SESSIONS_FILE = SPIRAL_WORKDIR / 'sessions.json'


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