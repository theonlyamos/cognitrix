import os
import logging
from pathlib import Path
from odbms import DBMS
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('cognitrix.log')

VERSION = '0.2.5'
API_VERSION = 'v1'
COGNITRIX_WORKDIR = Path.home() / '.cognitrix'

BASE_DIR = Path(__file__).parent
FRONTEND_BUILD_DIR = BASE_DIR.joinpath('..', 'frontend', 'dist')
FRONTEND_STATIC_DIR = FRONTEND_BUILD_DIR.joinpath('assets')

# MCP Configuration
COGNITRIX_HOME = Path.home() / '.cognitrix'
MCP_CONFIG_FILE = COGNITRIX_HOME / 'mcp.json'

def ensure_cognitrix_home():
    """Ensure the .cognitrix directory exists"""
    COGNITRIX_HOME.mkdir(exist_ok=True)
    return COGNITRIX_HOME

def initialize_database():
    db_type = os.getenv('DB_TYPE', default='sqlite')
    db_name = os.getenv('DB_NAME', str(Path.home() / '.cognitrix' / 'cognitrix.db'))
    # db_host = os.getenv('DB_HOST', 'localhost')
    # db_port = int(os.getenv('DB_PORT', 27017))
    # db_user = os.getenv('DB_USER', '')
    # db_password = os.getenv('DB_PASSWORD', '')

    DBMS.initialize(db_type, host="", port="", username="", password="", database=db_name) # type: ignore