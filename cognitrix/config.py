import logging
import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('cognitrix.log')

VERSION = '0.2.6'
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

        # Deployment environment marker (read early: gates JWT-secret behaviour).
        self.env = os.getenv('COGNITRIX_ENV', 'development')

        # JWT settings
        self.jwt_secret_key = self._resolve_jwt_secret()
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

        # Filesystem tool confinement: Read/Write/Edit may not escape this root.
        # Defaults to the current working directory.
        self.tools_root = Path(os.getenv('COGNITRIX_TOOLS_ROOT', Path.cwd())).expanduser().resolve()

        # CORS: comma-separated list of allowed origins for the web API.
        _cors = os.getenv('COGNITRIX_CORS_ORIGINS', 'http://localhost:8000,http://localhost:5173')
        self.cors_origins = [o.strip() for o in _cors.split(',') if o.strip()]

        # MCP Configuration
        self.mcp_config_file = self.workdir / 'mcp.json'

        # Ensure directories exist
        self._ensure_directories()

    def _resolve_jwt_secret(self) -> str:
        """Resolve a stable JWT secret.

        - If JWT_SECRET_KEY is set, use it.
        - In production (COGNITRIX_ENV=production) it is required: fail fast.
        - Otherwise persist a generated key under the workdir so tokens survive
          restarts (the old behaviour generated a new key every start, silently
          invalidating all sessions).
        """
        env_key = os.getenv('JWT_SECRET_KEY')
        if env_key:
            return env_key

        if self.env == 'production':
            raise RuntimeError(
                "JWT_SECRET_KEY must be set when COGNITRIX_ENV=production."
            )

        key_file = self.workdir / 'jwt_secret'
        try:
            if key_file.exists():
                return key_file.read_text(encoding='utf-8').strip()
            self.workdir.mkdir(parents=True, exist_ok=True)
            key = secrets.token_urlsafe(32)
            key_file.write_text(key, encoding='utf-8')
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            logger.warning(
                "JWT_SECRET_KEY not set; using a persisted development key at %s. "
                "Set JWT_SECRET_KEY (and COGNITRIX_ENV=production) for deployment.",
                key_file,
            )
            return key
        except OSError:
            # Filesystem unavailable — fall back to a process-local key.
            return secrets.token_urlsafe(32)

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

    def get_default_model(self, provider: str) -> str:
        """Get default model for a provider"""
        model_mapping = {
            'openai': 'gpt-4o',
            'openrouter': 'google/gemini-3.1-flash-lite-preview',
            'anthropic': 'claude-4-5-sonnet',
            'google': 'gemini-3.1-flash-lite-preview',
            'groq': 'llama-3.3-70b-versatile',
            'cohere': 'command-r-plus',
            'together': 'meta-llama/llama-3.3-70b-instruct',
            'huggingface': 'meta-llama/llama-3.3-70b-instruct',
            'clarifai': 'meta-llama/llama-3.3-70b-instruct',
            'aimlapi': 'meta-llama/llama-3.3-70b-instruct',
            'minds': 'meta-llama/llama-3.3-70b-instruct',
            'github': 'meta-llama/llama-3.3-70b-instruct',
            'tavily': 'meta-llama/llama-3.3-70b-instruct',
            'brave': 'meta-llama/llama-3.3-70b-instruct',
            'deepgram': 'meta-llama/llama-3.3-70b-instruct',
            'helicone': 'meta-llama/llama-3.3-70b-instruct',
        }
        return model_mapping.get(provider.lower(), '')

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

# Skills configuration
SKILLS_BUILTIN_DIR = BASE_DIR / 'skills' / 'builtin'
SKILLS_GLOBAL_DIR = Path.home() / '.agents' / 'skills'
SKILLS_CACHE_DIR = SKILLS_GLOBAL_DIR / '.cache'
SKILLS_REGISTRY_URL = 'https://github.com/theonlyamos/cognitrix-skills'


def get_skills_project_dir() -> Path:
    """Get project-scoped skills directory (.agents/skills/ relative to cwd)."""
    return Path.cwd() / '.agents' / 'skills'


def ensure_skills_dirs():
    """Ensure skills directories exist."""
    SKILLS_GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def ensure_cognitrix_home():
    """Ensure the .cognitrix directory exists"""
    settings.workdir.mkdir(exist_ok=True)
    return settings.workdir

async def initialize_database():
    """Initialize database with current settings"""
    config = settings.get_database_config()
    from odbms import DBMS
    kwargs = dict(
        host=config['host'],
        port=config['port'],
        username=config['user'],
        password=config['password'],
        database=config['name'],
    )
    # Older odbms releases only ship the sync initialize().
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async(config['type'], **kwargs)  # type: ignore
    else:
        DBMS.initialize(config['type'], **kwargs)
    _patch_odbms_sqlite()


def _patch_odbms_sqlite():
    """Compat shims for odbms<=0.5.2 on sqlite. ponytail: remove once odbms
    fixes both upstream.

    1. insert_one drops string ids, leaving the id column NULL, so a later
       update_one({'id': ...}) matches nothing and every re-save of the record
       is silently lost (agents/sessions never persist across runs).
       Shim: after a fresh insert, stamp a uuid into the row via its rowid.
    2. normalise serializes lists as '::'.join(str(v)) — irreversible for any
       list of dicts/models (Agent.tools, Session.chat).
       Shim: store lists as JSON; decode JSON list columns on read.
    """
    import json
    import types
    import uuid
    from typing import Union, get_args, get_origin

    from odbms import DBMS, Model

    if getattr(Model, '_cognitrix_sqlite_patch', False):
        return
    Model._cognitrix_sqlite_patch = True

    def _is_sqlite():
        return getattr(DBMS.Database, 'dbms', '') == 'sqlite'

    def _is_list_field(cls, key):
        field = getattr(cls, 'model_fields', {}).get(key)
        if field is None:
            return False
        ftype = field.annotation
        origin = get_origin(ftype)
        if origin in (Union, types.UnionType):
            args = [a for a in get_args(ftype) if a is not type(None)]
            if not args:
                return False
            ftype = args[0]
            origin = get_origin(ftype)
        return origin is list or ftype is list

    _orig_save = Model.save

    async def _save(self):
        is_new = not getattr(self, 'id', None)
        await _orig_save(self)
        if is_new and isinstance(self.id, int) and _is_sqlite():
            new_id = str(uuid.uuid4())
            await DBMS.Database.update_one(self.table_name(), {'rowid': self.id}, {'id': new_id})
            self.id = new_id
        return self

    Model.save = _save

    _orig_normalise = Model.normalise.__func__

    def _normalise(cls, content=None, optype='dbresult'):
        if content is None or not _is_sqlite():
            return _orig_normalise(cls, content, optype)
        if optype == 'params':
            out = _orig_normalise(cls, content, optype)
            for key, value in content.items():
                if isinstance(value, list):
                    out[key] = json.dumps(value, default=str)
            return out
        # dbresult: decode JSON list columns before the original '::'-split
        # logic can mangle them. Only fields the model declares as lists — a
        # text column whose value happens to look like JSON stays a string.
        content = dict(content)
        for key, value in content.items():
            if isinstance(value, str) and value[:1] == '[' and _is_list_field(cls, key):
                try:
                    decoded = json.loads(value)
                    if isinstance(decoded, list):
                        content[key] = decoded
                except json.JSONDecodeError:
                    pass
        return _orig_normalise(cls, content, optype)

    Model.normalise = classmethod(_normalise)

def get_settings() -> CognitrixSettings:
    """Get the global settings instance"""
    return settings
