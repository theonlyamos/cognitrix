from pathlib import Path
import json

VERSION = '0.2.0'
SPIRAL_WORKDIR = Path('~').expanduser() / '.cognitrix'

AGENTS_FILE = SPIRAL_WORKDIR / 'agents.json'
CONFIG_FILE = SPIRAL_WORKDIR / 'config.json'

if not SPIRAL_WORKDIR.exists() and not SPIRAL_WORKDIR.is_dir():
    SPIRAL_WORKDIR.mkdir()
    
if not AGENTS_FILE.exists() and not AGENTS_FILE.is_file():
    with open(AGENTS_FILE, 'w') as file:
        pass
        
if not CONFIG_FILE.exists() and not CONFIG_FILE.is_file():
    with open(CONFIG_FILE, 'w') as file:
        pass