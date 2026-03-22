import logging
import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('cognitrix.log')

VERSION = '0.2.5'
API_VERSION = 'v1'

class CognitrixSettings:
    """Centralized configuration management for Cognitrix"""

    def __init__(self):
        # Core settings
        self.version = VERSION
        self.api_version = API_VERSION
        self.workdir = Path.home() / '.cognitrix'

        # Database settings
        self.db_type = os.getenv('DB_TYPE', 'sqlite')
        self.db_name = os.getenv('DB_NAME', str(Path.home() / '.cognitrix' / 'cognitrix.db'))
        self.db_host = os.getenv('DB_HOST', 'localhost')
        self.db_port = int(os.getenv('DB_PORT', '27017'))
        self.db_user = os.getenv('DB_USER', '')
        self.db_password = os.getenv('DB_PASSWORD', '')

        # JWT settings
        self.jwt_secret_key = os.getenv('JWT_SECRET_KEY', secrets.token_urlsafe(32))
        self.jwt_algorithm = os.getenv('JWT_ALGORITHM', 'HS256')
        self.jwt_access_token_expire_minutes = int(os.getenv('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', '30'))

        # API Keys for LLM providers
        self.openai_api_key = os.getenv('OPENAI_API_KEY', '')
        self.anthropic_api_key = os.getenv('ANTHROPIC_API_KEY', '')
        self.google_api_key = os.getenv('GOOGLE_API_KEY', '')
        self.groq_api_key = os.getenv('GROQ_API_KEY', '')
        self.cohere_api_key = os.getenv('CO_API_KEY', '')
        self.together_api_key = os.getenv('TOGETHER_API_KEY', '')
        self.huggingface_access_token = os.getenv('HUGGINGFACE_ACCESS_TOKEN', '')
        self.clarifai_access_token = os.getenv('CLARIFAI_ACCESS_TOKEN', '')
        self.aimlapi_api_key = os.getenv('AIMLAPI_API_KEY', '')
        self.minds_api_key = os.getenv('MINDS_API_KEY', '')
        self.github_token = os.getenv('GITHUB_TOKEN', '')

        # Tool API Keys
        self.tavily_api_key = os.getenv('TAVILY_API_KEY', '')
        self.brave_search_api_key = os.getenv('BRAVE_SEARCH_API_KEY', '')
        self.deepgram_api_key = os.getenv('DEEPGRAM_API_KEY', '')

        # Monitoring and Analytics
        self.helicone_api_key = os.getenv('HELICONE_API_KEY', '')
        self.helicone_base_url = os.getenv('HELICONE_BASE_URL', 'https://gateway.helicone.ai/v1')

        # LLM provider config (AI_PROVIDER, OPENROUTER_*, OPENAI_*, etc.)
        self.ai_provider = os.getenv('AI_PROVIDER', 'openrouter')

        # MCP Configuration
        self.mcp_config_file = self.workdir / 'mcp.json'

        # Ensure directories exist
        self._ensure_directories()

    def _ensure_directories(self):
        """Ensure required directories exist"""
        self.workdir.mkdir(exist_ok=True)
        self.mcp_config_file.parent.mkdir(exist_ok=True)

    def get_api_key(self, provider: str) -> str:
        """Get API key for a specific provider"""
        key_mapping = {
            'openai': self.openai_api_key or os.getenv('OPENAI_API_KEY', ''),
            'openrouter': os.getenv('OPENROUTER_API_KEY', ''),
            'anthropic': self.anthropic_api_key,
            'google': self.google_api_key or os.getenv('GOOGLE_API_KEY', ''),
            'groq': self.groq_api_key,
            'cohere': self.cohere_api_key,
            'together': self.together_api_key,
            'huggingface': self.huggingface_access_token,
            'clarifai': self.clarifai_access_token,
            'aimlapi': self.aimlapi_api_key,
            'minds': self.minds_api_key,
            'github': self.github_token,
            'tavily': self.tavily_api_key,
            'brave': self.brave_search_api_key,
            'deepgram': self.deepgram_api_key,
            'helicone': self.helicone_api_key,
        }
        return key_mapping.get(provider.lower(), '')

    def has_api_key(self, provider: str) -> bool:
        """Check if API key is available for a provider"""
        return bool(self.get_api_key(provider))

    def get_database_config(self) -> dict[str, Any]:
        """Get database configuration as a dictionary"""
        return {
            'type': self.db_type,
            'name': self.db_name,
            'host': self.db_host,
            'port': self.db_port,
            'user': self.db_user,
            'password': self.db_password,
        }

    def get_jwt_config(self) -> dict[str, Any]:
        """Get JWT configuration as a dictionary"""
        return {
            'secret_key': self.jwt_secret_key,
            'algorithm': self.jwt_algorithm,
            'expire_minutes': self.jwt_access_token_expire_minutes,
        }

    def list_available_providers(self) -> list[str]:
        """List providers that have API keys configured"""
        providers = ['openai', 'anthropic', 'google', 'groq', 'cohere', 'together',
                    'huggingface', 'clarifai', 'aimlapi', 'minds', 'github']
        return [p for p in providers if self.has_api_key(p)]

    def validate_required_settings(self) -> dict[str, bool]:
        """Validate that required settings are configured"""
        validations = {
            'database_configured': bool(self.db_type and self.db_name),
            'jwt_configured': bool(self.jwt_secret_key),
            'at_least_one_llm_provider': len(self.list_available_providers()) > 0,
            'workdir_exists': self.workdir.exists(),
        }
        return validations

    def get_config_summary(self) -> dict[str, Any]:
        """Get a summary of current configuration (without sensitive data)"""
        return {
            'version': self.version,
            'api_version': self.api_version,
            'workdir': str(self.workdir),
            'database_type': self.db_type,
            'available_providers': self.list_available_providers(),
            'validation_status': self.validate_required_settings(),
        }

# Global settings instance
settings = CognitrixSettings()

# Legacy compatibility - maintain existing constants and functions
COGNITRIX_WORKDIR = settings.workdir
BASE_DIR = Path(__file__).parent
FRONTEND_BUILD_DIR = BASE_DIR.joinpath('..', 'frontend', 'dist')
FRONTEND_STATIC_DIR = FRONTEND_BUILD_DIR.joinpath('assets')
COGNITRIX_HOME = settings.workdir
MCP_CONFIG_FILE = settings.mcp_config_file

def ensure_cognitrix_home():
    """Ensure the .cognitrix directory exists"""
    settings.workdir.mkdir(exist_ok=True)
    return settings.workdir

async def initialize_database():
    """Initialize database with current settings"""
    config = settings.get_database_config()
    from odbms import DBMS
    await DBMS.initialize_async(
        config['type'],
        host=config['host'],
        port=config['port'],
        username=config['user'],
        password=config['password'],
        database=config['name']
    ) # type: ignore

def get_settings() -> CognitrixSettings:
    """Get the global settings instance"""
    return settings
