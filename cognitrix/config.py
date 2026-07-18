import inspect
import logging
import math
import os
import secrets
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('cognitrix.log')

SQLITE_BUSY_TIMEOUT_MS = 15_000

def _resolve_version() -> str:
    """Version, sourced only from pyproject.toml. Read the source file directly
    when running from a checkout (an edit then shows up without reinstalling),
    otherwise fall back to installed package metadata (wheel installs, where
    pyproject.toml isn't shipped)."""
    pyproject = Path(__file__).resolve().parent.parent / 'pyproject.toml'
    try:
        import tomllib
        with pyproject.open('rb') as fh:
            return tomllib.load(fh)['tool']['poetry']['version']
    except (OSError, KeyError, ModuleNotFoundError):
        pass
    try:
        return _pkg_version('cognitrix')
    except PackageNotFoundError:  # source tree with no install and no tomllib
        return '0.0.0+dev'


VERSION = _resolve_version()
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

        try:
            self.task_recovery_interval_seconds = float(
                os.getenv('TASK_RECOVERY_INTERVAL_SECONDS', '30')
            )
        except ValueError as exc:
            raise ValueError(
                'TASK_RECOVERY_INTERVAL_SECONDS must be a positive number'
            ) from exc
        if (
            not math.isfinite(self.task_recovery_interval_seconds)
            or self.task_recovery_interval_seconds <= 0
        ):
            raise ValueError(
                'TASK_RECOVERY_INTERVAL_SECONDS must be a positive number'
            )

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
            'openrouter': 'google/gemini-3.5-flash-preview',
            'anthropic': 'claude-4-5-sonnet',
            'google': 'gemini-3.5-flash',
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
    await _ensure_schema()
    from cognitrix.tasks.repository import RunRepository
    repository = RunRepository()
    # TaskRun predates TaskRunHead.  Repair legacy projections before any API,
    # scheduler, or worker can admit another run for the same task.
    if getattr(DBMS.Database, 'dbms', '') in ('sqlite', 'postgresql', 'mysql'):
        await repository.reconcile_heads()
    await repository.recover_outboxes()


_TASKRUN_MIGRATION_COLUMNS = (
    ('result_data', 'TEXT'), ('requested_by', 'TEXT'),
    ('actor_key', 'TEXT'), ('authority_kind', 'TEXT'),
    ('authority_id', 'TEXT'), ('callback_url', 'TEXT'),
    ('acl_version', 'INTEGER'), ('acl_team_id', 'TEXT'),
    ('acl_agent_ids', 'TEXT'),
    ('callback_key_id', 'TEXT'),
    ('completion_notification_state', 'TEXT'),
    ('completion_notification_owner', 'TEXT'),
    ('completion_notification_expires_at', 'TEXT'),
    ('completion_notification_next_at', 'TEXT'),
    ('completion_notification_attempts', 'INTEGER'),
    ('completion_notified_at', 'TEXT'),
    ('resume_from_run_id', 'TEXT'),
    ('queue_job_id', 'TEXT'), ('queued_at', 'TEXT'),
    ('lease_owner', 'TEXT'), ('lease_generation', 'INTEGER'),
    ('heartbeat_at', 'TEXT'), ('lease_expires_at', 'TEXT'),
    ('cancel_requested_at', 'TEXT'), ('version', 'INTEGER'),
    ('next_event_sequence', 'INTEGER'), ('event_outbox', 'TEXT'),
    ('budget', 'TEXT'), ('usage', 'TEXT'), ('error_code', 'TEXT'),
)

_TASKRUN_HEAD_MIGRATION_COLUMNS = (
    ('deleted_at', 'TEXT'),
)

_TASK_MIGRATION_COLUMNS = (
    ('callback_url', 'TEXT'), ('callback_key_id', 'TEXT'),
    ('schedule_at', 'TEXT'), ('schedule_interval', 'INTEGER'),
    ('schedule_cron', 'TEXT'), ('next_run_at', 'TEXT'),
    ('schedule_enabled', 'BOOLEAN'), ('schedule_requested_by', 'TEXT'),
    ('schedule_authority_kind', 'TEXT'), ('schedule_authority_id', 'TEXT'),
    ('deleted_at', 'TEXT'),
)

_ARTIFACT_MIGRATION_COLUMNS = (
    ('user_id', 'TEXT'), ('run_id', 'TEXT'),
    ('origin', 'TEXT'), ('vision_storage_key', 'TEXT'),
    ('thumbnail_storage_key', 'TEXT'), ('created_at', 'TEXT'),
)

_SESSION_MIGRATION_COLUMNS = (
    ('user_id', 'TEXT'), ('run_id', 'TEXT'),
    ('step_index', 'INTEGER'), ('step_title', 'TEXT'),
)

_TASKRUN_INDEXES = (
    ('ux_task_run_steps_run_step', 'taskrunsteps', 'run_id, step_index', True),
    ('ux_task_run_events_run_sequence', 'taskrunevents', 'run_id, sequence', True),
    ('ix_task_runs_task_created', 'taskruns', 'task_id, created_at', False),
    ('ix_task_runs_status_lease', 'taskruns', 'status, lease_expires_at', False),
    ('ix_task_runs_notification_due', 'taskruns',
     'completion_notification_state, completion_notification_next_at', False),
    ('ix_task_run_steps_run_status', 'taskrunsteps', 'run_id, status', False),
    ('ix_task_run_metrics_run_phase', 'taskrunphasemetrics', 'run_id, phase', False),
)


_TASK_EVENT_PAYLOAD_PARTITION = (
    'run_id, sequence, session_id, step_index, kind, agent_name, data'
)
_TASK_EVENT_MYSQL_PAYLOAD_PARTITION = (
    'BINARY run_id, sequence, BINARY session_id, step_index, '
    'BINARY kind, BINARY agent_name, BINARY data'
)


async def _cursor_rows(cursor) -> list:
    if cursor is None:
        return []
    rows = cursor.fetchall() if hasattr(cursor, 'fetchall') else cursor
    if inspect.isawaitable(rows):
        rows = await rows
    return list(rows or [])


async def _query_rows(database, statement: str, params: dict) -> list:
    """Fetch rows before an ODBMS pooled cursor leaves its connection lease."""
    pool = getattr(database, '_pool', None)
    if pool is None:
        return await _cursor_rows(await database.query(statement, params))

    async with pool.acquire() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(statement, params)
            return await _cursor_rows(cursor)


async def _reconcile_task_event_sequences(database, dbms: str) -> None:
    """Make legacy ``(run_id, sequence)`` collisions safe before indexing.

    A byte-equivalent replay is one whose public event payload columns match;
    only those rows are collapsed. Distinct payloads that reused a process-
    local sequence are retained and deterministically moved above that run's
    existing maximum. Any unsupported/failed reconciliation raises so startup
    never creates an index after silently discarding conflicting events.
    """
    payload = _TASK_EVENT_PAYLOAD_PARTITION
    if dbms == 'sqlite':
        await database.query(
            'DELETE FROM taskrunevents WHERE rowid IN ('
            'SELECT event_rowid FROM ('
            'SELECT rowid AS event_rowid, ROW_NUMBER() OVER ('
            f'PARTITION BY {payload} ORDER BY rowid'
            ') AS duplicate_rank FROM taskrunevents'
            ') exact_duplicates WHERE duplicate_rank > 1)'
        )
        await database.query(
            'WITH ranked AS ('
            'SELECT rowid AS event_rowid, run_id, sequence, '
            'ROW_NUMBER() OVER (PARTITION BY run_id, sequence ORDER BY rowid) '
            'AS collision_rank, '
            'MAX(sequence) OVER (PARTITION BY run_id) AS max_sequence '
            'FROM taskrunevents'
            '), conflicts AS ('
            'SELECT event_rowid, max_sequence + ROW_NUMBER() OVER ('
            'PARTITION BY run_id ORDER BY sequence, event_rowid'
            ') AS new_sequence FROM ranked WHERE collision_rank > 1'
            ') UPDATE taskrunevents SET sequence = ('
            'SELECT new_sequence FROM conflicts '
            'WHERE conflicts.event_rowid = taskrunevents.rowid'
            ') WHERE rowid IN (SELECT event_rowid FROM conflicts)'
        )
        return

    if dbms == 'postgresql':
        await database.query(
            'WITH ranked AS ('
            'SELECT ctid AS event_rowid, ROW_NUMBER() OVER ('
            f'PARTITION BY {payload} ORDER BY id NULLS LAST, ctid'
            ') AS duplicate_rank FROM taskrunevents'
            ') DELETE FROM taskrunevents event USING ranked duplicate '
            'WHERE event.ctid = duplicate.event_rowid '
            'AND duplicate.duplicate_rank > 1'
        )
        await database.query(
            'WITH ranked AS ('
            'SELECT ctid AS event_rowid, ctid::text AS locator_order, id, '
            'run_id, sequence, ROW_NUMBER() OVER ('
            'PARTITION BY run_id, sequence ORDER BY id NULLS LAST, ctid'
            ') AS collision_rank, MAX(sequence) OVER ('
            'PARTITION BY run_id) AS max_sequence FROM taskrunevents'
            '), conflicts AS ('
            'SELECT event_rowid, max_sequence + ROW_NUMBER() OVER ('
            'PARTITION BY run_id ORDER BY sequence, id NULLS LAST, locator_order'
            ') AS new_sequence FROM ranked WHERE collision_rank > 1'
            ') UPDATE taskrunevents event SET sequence = conflict.new_sequence '
            'FROM conflicts conflict WHERE event.ctid = conflict.event_rowid'
        )
        return

    if dbms == 'mysql':
        binary_payload = _TASK_EVENT_MYSQL_PAYLOAD_PARTITION
        await database.query(
            'DELETE event FROM taskrunevents event JOIN ('
            'SELECT id FROM ('
            'SELECT id, ROW_NUMBER() OVER ('
            f'PARTITION BY {binary_payload} ORDER BY id'
            ') AS duplicate_rank FROM taskrunevents'
            ') ranked_exact WHERE duplicate_rank > 1'
            ') duplicate ON duplicate.id = event.id'
        )
        # The extra derived-table layer forces materialization, avoiding
        # MySQL's target-table restriction without relying on a temporary
        # table (odbms may use a different pooled connection per query).
        await database.query(
            'UPDATE taskrunevents event JOIN ('
            'SELECT * FROM ('
            'SELECT id, max_sequence + ROW_NUMBER() OVER ('
            'PARTITION BY run_id ORDER BY sequence, id'
            ') AS new_sequence FROM ('
            'SELECT id, run_id, sequence, ROW_NUMBER() OVER ('
            'PARTITION BY run_id, sequence ORDER BY id'
            ') AS collision_rank, MAX(sequence) OVER ('
            'PARTITION BY run_id) AS max_sequence FROM taskrunevents'
            ') ranked_collisions WHERE collision_rank > 1'
            ') planned_conflicts'
            ') conflict ON conflict.id = event.id '
            'SET event.sequence = conflict.new_sequence'
        )
        return

    raise RuntimeError(f'Unsupported relational event reconciliation for {dbms}')


async def _backfill_taskrun_counters(database, dbms: str) -> None:
    greatest = 'MAX' if dbms == 'sqlite' else 'GREATEST'
    await database.query(
        'UPDATE taskruns SET '
        'lease_generation = COALESCE(lease_generation, 0), '
        'version = COALESCE(version, 0), '
        'completion_notification_attempts = '
        'COALESCE(completion_notification_attempts, 0), '
        f'next_event_sequence = {greatest}('
        'COALESCE(next_event_sequence, 0), COALESCE(('
        'SELECT MAX(sequence) FROM taskrunevents '
        'WHERE taskrunevents.run_id = taskruns.id), 0))'
    )


def _index_columns(value) -> list[str]:
    if isinstance(value, str):
        value = value.strip('{}')
        return [column.strip().lower() for column in value.split(',') if column.strip()]
    return [str(column).lower() for column in (value or [])]


async def _verify_relational_event_index(database, dbms: str) -> None:
    name = 'ux_task_run_events_run_sequence'
    if dbms == 'postgresql':
        rows = await _query_rows(
            database,
            'SELECT index_meta.indisunique, ARRAY_AGG(attribute.attname '
            'ORDER BY key_column.ordinality) FROM pg_class table_meta '
            'JOIN pg_index index_meta ON index_meta.indrelid = table_meta.oid '
            'JOIN pg_class index_name ON index_name.oid = index_meta.indexrelid '
            'JOIN LATERAL UNNEST(index_meta.indkey) WITH ORDINALITY '
            'AS key_column(attnum, ordinality) ON TRUE '
            'JOIN pg_attribute attribute ON attribute.attrelid = table_meta.oid '
            'AND attribute.attnum = key_column.attnum '
            'WHERE table_meta.oid = TO_REGCLASS(%(table_name)s) '
            'AND index_name.relname = %(index_name)s '
            'AND index_meta.indpred IS NULL AND index_meta.indisvalid '
            'AND index_meta.indnkeyatts = 2 AND index_meta.indnatts = 2 '
            'GROUP BY index_meta.indisunique',
            {'table_name': 'taskrunevents', 'index_name': name},
        )
        valid = (
            len(rows) == 1
            and bool(rows[0][0])
            and _index_columns(rows[0][1]) == ['run_id', 'sequence']
        )
    elif dbms == 'mysql':
        rows = await _query_rows(
            database,
            'SELECT non_unique, seq_in_index, column_name '
            'FROM information_schema.statistics '
            'WHERE table_schema = DATABASE() AND table_name = %(table_name)s '
            'AND index_name = %(index_name)s AND sub_part IS NULL '
            'ORDER BY seq_in_index',
            {'table_name': 'taskrunevents', 'index_name': name},
        )
        valid = (
            len(rows) == 2
            and all(int(row[0]) == 0 for row in rows)
            and [str(row[2]).lower() for row in rows] == ['run_id', 'sequence']
            and [int(row[1]) for row in rows] == [1, 2]
        )
    else:
        raise RuntimeError(f'Cannot verify relational indexes for {dbms}')

    if not valid:
        raise RuntimeError(
            f'{dbms} requires an exact unique index on taskrunevents(run_id, sequence)'
        )


async def _migrate_relational_task_schema(database) -> None:
    """Upgrade durable-task tables on PostgreSQL/MySQL or fail startup.

    ODBMS only creates missing tables; it does not safely add fields to an
    existing relational table. Each statement is idempotent for PostgreSQL.
    MySQL lacks portable ``IF NOT EXISTS`` support across maintained versions,
    so duplicate-column/index errors are the only ignored failures.
    """
    dbms = getattr(database, 'dbms', '')
    if dbms not in ('postgresql', 'mysql'):
        return

    for table, columns in (
        ('taskruns', _TASKRUN_MIGRATION_COLUMNS),
        ('taskrunheads', _TASKRUN_HEAD_MIGRATION_COLUMNS),
        ('tasks', _TASK_MIGRATION_COLUMNS),
        ('artifacts', _ARTIFACT_MIGRATION_COLUMNS),
        ('sessions', _SESSION_MIGRATION_COLUMNS),
    ):
        for column, ctype in columns:
            clause = (
                'ADD COLUMN IF NOT EXISTS'
                if dbms == 'postgresql'
                else 'ADD COLUMN'
            )
            try:
                await database.query(
                    f'ALTER TABLE {table} {clause} {column} {ctype}'
                )
            except Exception as exc:
                duplicate = dbms == 'mysql' and any(
                    marker in str(exc).lower()
                    for marker in ('duplicate column', 'already exists')
                )
                if not duplicate:
                    raise RuntimeError(
                        f'Could not migrate {table}.{column} on {dbms}'
                    ) from exc

    await database.query(
        "UPDATE taskruns SET authority_kind = 'system' "
        "WHERE authority_kind IS NULL OR authority_kind = ''"
    )

    await _reconcile_task_event_sequences(database, dbms)
    await _backfill_taskrun_counters(database, dbms)

    for name, table, columns, unique in _TASKRUN_INDEXES:
        prefix = 'CREATE UNIQUE INDEX' if unique else 'CREATE INDEX'
        if dbms == 'postgresql':
            prefix += ' IF NOT EXISTS'
        try:
            await database.query(f'{prefix} {name} ON {table} ({columns})')
        except Exception as exc:
            duplicate = dbms == 'mysql' and any(
                marker in str(exc).lower()
                for marker in ('duplicate key name', 'already exists')
            )
            if not duplicate:
                raise RuntimeError(f'Could not create {name} on {dbms}') from exc

    await _verify_relational_event_index(database, dbms)


async def _ensure_schema():
    """Additive schema upkeep odbms can't do itself on sqlite.

    - Creates the taskruns table (new model; odbms only creates tables via an
      explicit create_table call).
    - Adds new nullable Session columns to an EXISTING sessions table — odbms
      alter-table is a no-op stub on sqlite, so raw ALTER is the only way. A
      fresh database (no sessions table yet) is skipped: the regular
      create_table pass builds the full schema from model_fields.
    - Switches sqlite to WAL + a generous busy timeout: parallel task steps,
      API polling and the cancel endpoint are genuinely concurrent writers,
      each on its own connection (odbms opens one per operation).

    Idempotent — safe on every init (API, celery worker, CLI).
    """
    import logging as _logging

    from odbms import DBMS

    log = _logging.getLogger('cognitrix.log')

    from cognitrix.artifacts import Artifact, DocumentArtifact
    from cognitrix.models.api_key import APIKey
    from cognitrix.session_ownership import SessionOwnership
    from cognitrix.tasks.events import TaskRunEvent
    from cognitrix.tasks.metrics import TaskRunPhaseMetric
    from cognitrix.tasks.run import TaskRun, TaskRunHead
    from cognitrix.tasks.step import TaskRunStep

    for model in (
        TaskRun,
        TaskRunHead,
        TaskRunStep,
        TaskRunEvent,
        TaskRunPhaseMetric,
        APIKey,
        Artifact,
    ):
        try:
            create = getattr(model, '_create_table_async', None) or getattr(model, 'create_table', None)
            if create is not None:
                result = create()
                if hasattr(result, '__await__'):
                    await result
        except Exception:
            log.exception("Could not create %s table", model.__name__)
            if model is TaskRunHead:
                raise RuntimeError(
                    'TaskRunHead is required for cross-process run uniqueness'
                )

    dbms = getattr(DBMS.Database, 'dbms', '')
    # These tables are authorization and cleanup journals. Starting without
    # either would turn fail-closed ownership/recovery into silent data loss.
    for model in (SessionOwnership, DocumentArtifact):
        create = (
            getattr(model, '_create_table_async', None)
            or getattr(model, 'create_table', None)
        )
        if create is None:
            raise RuntimeError(f'{model.__name__} schema hook is unavailable')
        result = create()
        if hasattr(result, '__await__'):
            await result

    if dbms in ('postgresql', 'mysql'):
        await _migrate_relational_task_schema(DBMS.Database)
        return
    if dbms != 'sqlite':
        return

    try:
        await DBMS.Database.query('PRAGMA journal_mode=WAL')
        await DBMS.Database.query(
            f'PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}'
        )
    except Exception:
        log.exception("Could not set sqlite pragmas")

    for table, columns in (
        ('sessions', _SESSION_MIGRATION_COLUMNS),
        ('artifacts', _ARTIFACT_MIGRATION_COLUMNS),
        ('tasks', _TASK_MIGRATION_COLUMNS),
        ('taskruns', _TASKRUN_MIGRATION_COLUMNS),
        ('taskrunheads', _TASKRUN_HEAD_MIGRATION_COLUMNS),
    ):
        try:
            cursor = await DBMS.Database.query(f'PRAGMA table_info({table})')
            rows = cursor.fetchall() if hasattr(cursor, 'fetchall') else (cursor or [])
            existing = {r[1] for r in rows}
            if not existing:
                continue  # fresh DB — create_table builds the full schema later
            for column, ctype in columns:
                if column not in existing:
                    await DBMS.Database.query(f'ALTER TABLE {table} ADD COLUMN {column} {ctype}')
        except Exception:
            log.exception("Could not migrate %s schema", table)

    try:
        await DBMS.Database.query(
            "UPDATE taskruns SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )
        await DBMS.Database.query(
            "UPDATE taskruns SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"
        )
        await DBMS.Database.query(
            "UPDATE taskruns SET authority_kind = 'system' "
            "WHERE authority_kind IS NULL OR authority_kind = ''"
        )
        await _reconcile_task_event_sequences(DBMS.Database, 'sqlite')
        await _backfill_taskrun_counters(DBMS.Database, 'sqlite')
        for name, table, columns, unique in _TASKRUN_INDEXES:
            prefix = 'CREATE UNIQUE INDEX' if unique else 'CREATE INDEX'
            await DBMS.Database.query(
                f'{prefix} IF NOT EXISTS {name} ON {table} ({columns})'
            )

        for name, table, _columns, _unique in _TASKRUN_INDEXES:
            cursor = await DBMS.Database.query(f'PRAGMA index_list({table})')
            rows = cursor.fetchall() if hasattr(cursor, 'fetchall') else (cursor or [])
            if name not in {row[1] for row in rows}:
                raise RuntimeError(f'durable schema invariant missing index {name}')
        event_indexes = await _cursor_rows(
            await DBMS.Database.query('PRAGMA index_list(taskrunevents)')
        )
        event_index = next(
            (row for row in event_indexes if row[1] == 'ux_task_run_events_run_sequence'),
            None,
        )
        event_columns = await _cursor_rows(
            await DBMS.Database.query(
                'PRAGMA index_info(ux_task_run_events_run_sequence)'
            )
        )
        if (
            event_index is None
            or not bool(event_index[2])
            or [row[2] for row in event_columns] != ['run_id', 'sequence']
        ):
            raise RuntimeError(
                'sqlite requires an exact unique index on '
                'taskrunevents(run_id, sequence)'
            )
    except Exception:
        log.exception("Could not establish durable task-run invariants")
        raise


def _patch_odbms_sqlite():
    """Compat shims for odbms<=0.5.2 on sqlite. ponytail: remove once odbms
    fixes both upstream.

    1. insert_one drops string ids, leaving the id column NULL, so a later
       update_one({'id': ...}) matches nothing and every re-save of the record
       is silently lost (agents/sessions never persist across runs). Newer
       Pydantic also exposes odbms' inherited ``id`` alias as a dynamic field,
       so ``self.id`` stays None even after SQLite returns a rowid.
       Shim: recover that rowid, stamp a uuid into the row and real model field,
       and route loaded SQL ids through the alias while retaining public ``id``.
    2. normalise serializes lists as '::'.join(str(v)) — irreversible for any
       list of dicts/models (Agent.tools, Session.chat).
       Shim: store lists as JSON; decode JSON list columns on read.
    3. Every operation opens a fresh SQLite connection, so a one-off PRAGMA at
       startup does not propagate. Shim the connection factory to apply the
       configured busy timeout to each connection before it is used.
    """
    import inspect
    import json
    import types
    import uuid
    from typing import Union, get_args, get_origin

    from odbms import DBMS, Model
    from pydantic import BaseModel

    database = DBMS.Database
    if getattr(database, 'dbms', '') == 'sqlite':
        database_type = type(database)
        if not getattr(
            database_type,
            '_cognitrix_busy_timeout_patch',
            False,
        ):
            original_get_connection = database_type._get_connection

            def _get_connection(self):
                connection = original_get_connection(self)
                try:
                    connection.execute(
                        f'PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}'
                    )
                except Exception:
                    connection.close()
                    raise
                return connection

            database_type._get_connection = _get_connection
            database_type._cognitrix_busy_timeout_patch = True

        existing_connection = getattr(database, '_connection', None)
        if existing_connection is not None:
            existing_connection.execute(
                f'PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}'
            )

    # odbms 0.5.2 changed Model.create_table() from an awaitable operation to
    # a synchronous wrapper that merely schedules _create_table_async().  The
    # rest of Cognitrix intentionally awaits schema creation before using a
    # table, so restore that ordering contract instead of allowing startup and
    # tests to race a background CREATE TABLE task.
    base_create_table = Model.create_table.__func__
    if not inspect.iscoroutinefunction(base_create_table):
        base_create_table_async = getattr(Model, '_create_table_async', None)
        if base_create_table_async is None:
            raise RuntimeError('ODBMS does not expose an awaitable schema hook')

        async def _create_table(cls):
            await base_create_table_async.__func__(cls)

        Model.create_table = classmethod(_create_table)

    if getattr(Model, '_cognitrix_sqlite_patch', False):
        return
    Model._cognitrix_sqlite_patch = True

    def _is_sqlite():
        return getattr(DBMS.Database, 'dbms', '') == 'sqlite'

    def _is_relational():
        return getattr(DBMS.Database, 'dbms', '') in ('sqlite', 'postgresql', 'mysql')

    def _is_json_field(cls, key):
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
        if origin in (list, tuple, dict) or ftype in (list, tuple, dict):
            return True
        try:
            return isinstance(ftype, type) and issubclass(ftype, BaseModel)
        except TypeError:
            return False

    def _uses_inherited_id_alias(cls):
        field = getattr(cls, 'model_fields', {}).get('id')
        return getattr(field, 'alias', None) == '_id'

    _orig_init = Model.__init__
    _orig_setattr = Model.__setattr__

    def _init(self, **data):
        # odbms declares ``id`` on the base model with the ``_id`` alias, but
        # its custom initializer only checks the concrete class annotations.
        # Consequently public ``id=`` input is treated as a dynamic field and
        # the real Pydantic field remains None. Route it through the alias.
        prepared = dict(data)
        if 'id' in prepared and _uses_inherited_id_alias(type(self)):
            prepared.setdefault('_id', prepared.pop('id'))
        _orig_init(self, **prepared)
        dynamic = getattr(self, '_dynamic_fields', {})
        if _uses_inherited_id_alias(type(self)):
            dynamic.pop('_id', None)
            if getattr(self, 'id', None) is not None:
                dynamic['id'] = self.id

    def _setattr(self, name, value):
        # The same inherited-annotation check misroutes later ``self.id =``
        # assignments. Keep the real field and public dynamic representation
        # in sync; delegate every other attribute to odbms unchanged.
        if name == 'id' and _uses_inherited_id_alias(type(self)):
            object.__setattr__(self, name, value)
            dynamic = self.__dict__.get('_dynamic_fields')
            if isinstance(dynamic, dict):
                dynamic['id'] = value
            return
        _orig_setattr(self, name, value)

    Model.__init__ = _init
    Model.__setattr__ = _setattr

    _orig_save = Model.save

    async def _save(self):
        is_new = not getattr(self, 'id', None)
        await _orig_save(self)
        dynamic = getattr(self, '_dynamic_fields', {})
        inserted_rowid = getattr(self, 'id', None) or dynamic.get('id')
        if is_new and _is_sqlite() and (
            isinstance(inserted_rowid, int)
            or (isinstance(inserted_rowid, str) and inserted_rowid.isdigit())
        ):
            new_id = str(uuid.uuid4())
            await DBMS.Database.update_one(
                self.table_name(), {'rowid': int(inserted_rowid)}, {'id': new_id}
            )
            self.id = new_id
        return self

    Model.save = _save

    _orig_normalise = Model.normalise.__func__

    def _normalise(cls, content=None, optype='dbresult'):
        if content is None or not _is_relational():
            return _orig_normalise(cls, content, optype)
        if optype == 'params':
            out = _orig_normalise(cls, content, optype)
            out.pop('_id', None)
            if content.get('id') is not None:
                # odbms maps inherited string ids to Mongo's ``_id`` and then
                # drops them for SQL. Preserve an explicitly supplied primary
                # key so repository reservation/insert operations are atomic.
                out['id'] = content['id']
            for key, value in content.items():
                if _is_json_field(cls, key) and isinstance(
                    value, (list, tuple, dict, BaseModel)
                ):
                    if isinstance(value, BaseModel):
                        value = value.model_dump(mode='json')
                    out[key] = json.dumps(value, default=str)
            return out
        # dbresult: decode JSON list columns before the original '::'-split
        # logic can mangle them. Only fields the model declares as lists — a
        # text column whose value happens to look like JSON stays a string.
        content = dict(content)
        if content.get('id') is not None and _uses_inherited_id_alias(cls):
            # Populate Pydantic's actual inherited id field via its alias while
            # retaining the established public ``id`` key as a dynamic field.
            content['_id'] = content['id']
        for key, value in content.items():
            if (
                isinstance(value, str)
                and value[:1] in ('[', '{')
                and _is_json_field(cls, key)
            ):
                try:
                    decoded = json.loads(value)
                    if isinstance(decoded, (list, dict)):
                        content[key] = decoded
                except json.JSONDecodeError:
                    pass
        return _orig_normalise(cls, content, optype)

    Model.normalise = classmethod(_normalise)

def get_settings() -> CognitrixSettings:
    """Get the global settings instance"""
    return settings
