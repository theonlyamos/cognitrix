"""App-wide settings changed in Settings, not in environment variables.

Anyone who downloads Cognitrix should be able to set it up from the app, so
everything an admin might want to change (email sending, the GitHub App, the
cloud computers on Fly, web search keys, limits and timeouts...) lives here
and is edited from Settings. Each setting keeps the name of the environment
variable it replaces, so the code and the docs read the same.

Values live in ``<data folder>/app_settings.json``; secrets are sealed with
``secret_box`` and the file is readable by its owner only. A file (not a DB
row) because some values are read in sync code at import time, and every
process (web server, Celery worker, desktop engine) shares the data folder.

Environment variables (and ``.env``) are not read for any of these, not
even once on upgrade: an admin sets them again in Settings. Model provider
keys and OAuth clients live in ``saved_keys`` the same way.

Even the server's own setup is here: the database and job queue (chosen on
the first-start Server setup screen; this file is read before the database
opens) and the web server's allowed addresses. The keys that sign sign-ins
and unlock saved secrets are made by the server and kept in the data folder
(see ``secret_box``). What stays environment-only is what one process tells
another: what the desktop app tells its own engine, and what the engine
tells the sandbox container. ``ENV_ONLY`` lists them for the docs and for a test that keeps
new ``os.getenv`` reads honest.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger('cognitrix.log')

MAX_VALUE_LENGTH = 20_000
VALUES = 'values'


@dataclass(frozen=True)
class Setting:
    name: str                 # the environment variable it replaces
    page: str                 # Settings section it shows on
    group: str                # heading inside that section
    label: str
    help: str = ''
    kind: str = 'text'        # text | secret | number | toggle | select | textarea | list
    default: str = ''
    options: tuple[tuple[str, str], ...] = ()   # (value, label) for select
    restart: bool = False     # read once at start, so a change needs a restart
    placeholder: str = ''
    multiline_secret: bool = field(default=False)


ON_OFF = 'toggle'

# Providers whose address can be changed (same list as KEY_PROVIDERS).
_ADDRESS_PROVIDERS = (
    ('openrouter', 'OpenRouter'), ('openai', 'OpenAI'), ('anthropic', 'Anthropic'),
    ('google', 'Google Gemini'), ('groq', 'Groq'), ('cerebras', 'Cerebras'), ('ollama', 'Ollama'),
)


def _registry() -> list[Setting]:
    s: list[Setting] = []
    add = s.append

    # ---- Models and keys ------------------------------------------------------------
    g = 'Model providers'
    add(Setting('AI_PROVIDER', 'models', g, 'Default provider',
                'Used when an agent or the command line does not name one.',
                kind='select', default='openrouter',
                options=tuple((p, label) for p, label in _ADDRESS_PROVIDERS)))
    for provider, label in _ADDRESS_PROVIDERS:
        add(Setting(f'{provider.upper()}_BASE_URL', 'models', g, f'{label} address',
                    'Leave empty for the usual address. Change it for a proxy or a self-hosted server.'))
    add(Setting('COGNITRIX_TRUSTED_BASE_URLS', 'models', g, 'Other trusted addresses',
                "Extra addresses the engine's own keys may be sent to, one per line. Agents with "
                'a custom address otherwise need their own key.', kind='list'))
    add(Setting('COGNITRIX_ANTHROPIC_NATIVE', 'models', g, "Use Anthropic's own API",
                "Talk to Claude models in Anthropic's format (prompt caching, thinking). Off uses the "
                'OpenAI-compatible endpoint.', kind=ON_OFF, default='1'))
    add(Setting('HELICONE_API_KEY', 'models', g, 'Helicone key',
                'Send model calls through Helicone for logging. Leave empty to call providers directly.',
                kind='secret'))
    add(Setting('HELICONE_BASE_URL', 'models', g, 'Helicone address',
                placeholder='https://gateway.helicone.ai/v1'))

    g = 'Tools and services'
    add(Setting('TAVILY_API_KEY', 'models', g, 'Tavily key (web search)', kind='secret'))
    add(Setting('BRAVE_SEARCH_API_KEY', 'models', g, 'Brave Search key', kind='secret'))
    add(Setting('GITHUB_TOKEN', 'models', g, 'GitHub token',
                'A personal access token for the GitHub tools.', kind='secret'))
    add(Setting('COGNITRIX_IMAGE_PROVIDER', 'models', g, 'Image generation',
                kind='select', default='', options=(('', 'Automatic'), ('google', 'Google Gemini'),
                                                   ('openai', 'OpenAI'))))
    add(Setting('COGNITRIX_OPENAI_IMAGE_MODEL', 'models', g, 'OpenAI image model', placeholder='gpt-image-2'))
    add(Setting('COGNITRIX_OPENAI_SPEECH_MODEL', 'models', g, 'OpenAI voice model',
                placeholder='gpt-realtime-2.1-mini'))
    add(Setting('COGNITRIX_GEMINI_SPEECH_MODEL', 'models', g, 'Gemini voice model',
                placeholder='gemini-3.8-flash-tts'))
    add(Setting('COGNITRIX_MEMORY_EMBEDDINGS', 'models', g, 'Search memories by meaning',
                'Downloads a small model (about 80 MB) the first time.', kind=ON_OFF, default='1'))
    add(Setting('COGNITRIX_PRICE_LOOKUP', 'models', g, 'Look up model prices',
                'Fetch a public price list once a day so Usage can show costs.', kind=ON_OFF, default='1'))
    add(Setting('COGNITRIX_MODEL_PRICING_JSON', 'models', g, 'Custom model prices',
                'JSON prices per million tokens, e.g. {"my-model": {"input": 1, "output": 2}}. '
                'Used for task budgets.', kind='textarea'))

    # ---- People ---------------------------------------------------------------------
    g = 'Sign-up and sign-in'
    add(Setting('COGNITRIX_ALLOW_SIGNUP', 'people', g, 'Who can create an account',
                kind='select', default='',
                options=(('', 'Only the first person (then invites)'), ('1', 'Anyone'), ('0', 'Nobody'))))
    add(Setting('COGNITRIX_ADMIN_EMAILS', 'people', g, 'Admin email addresses',
                'One per line. When set, only these people are admins (roles set on this page still win).',
                kind='list'))
    add(Setting('COGNITRIX_CODE_SIGNIN', 'people', g, 'Sign in with a code from another device',
                kind=ON_OFF, default='1'))
    add(Setting('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', 'people', g, 'Stay signed in for (minutes)',
                'On a server. The desktop app always keeps you signed in for at least 30 days.',
                kind='number', default='30', restart=True))
    add(Setting('COGNITRIX_AUDIT_DAYS', 'people', g, 'Keep the activity log for (days)',
                '0 keeps it forever.', kind='number', default='90'))

    g = 'Email sending'
    add(Setting('COGNITRIX_SMTP_HOST', 'people', g, 'SMTP server',
                'For invites and password resets. Leave empty to turn email off.',
                placeholder='smtp.gmail.com'))
    add(Setting('COGNITRIX_SMTP_SECURITY', 'people', g, 'Security', kind='select', default='starttls',
                options=(('starttls', 'STARTTLS (usually port 587)'), ('ssl', 'SSL/TLS (usually port 465)'),
                         ('none', 'None'))))
    add(Setting('COGNITRIX_SMTP_PORT', 'people', g, 'Port', 'Leave empty for the usual port.', kind='number'))
    add(Setting('COGNITRIX_SMTP_USER', 'people', g, 'User name'))
    add(Setting('COGNITRIX_SMTP_PASSWORD', 'people', g, 'Password', 'An app password for Gmail.',
                kind='secret'))
    add(Setting('COGNITRIX_SMTP_FROM', 'people', g, 'Send as', 'Leave empty to use the user name.',
                placeholder='Cognitrix <you@example.com>'))

    # ---- Connected apps ---------------------------------------------------------------
    g = 'GitHub App (pull request updates)'
    add(Setting('GITHUB_APP_ID', 'apps', g, 'App ID',
                'Lets GitHub tell Cognitrix about pull request changes right away instead of timed checks.'))
    add(Setting('GITHUB_APP_PRIVATE_KEY', 'apps', g, 'Private key', 'The .pem file GitHub gave you, pasted in.',
                kind='secret', multiline_secret=True))
    add(Setting('GITHUB_APP_WEBHOOK_SECRET', 'apps', g, 'Webhook secret', kind='secret'))
    add(Setting('COGNITRIX_GITHUB_WEBHOOKS', 'apps', g, 'Use repository webhooks',
                'When this server has a public https address, watch pull requests with webhooks.',
                kind=ON_OFF, default='1'))
    add(Setting('COGNITRIX_GITHUB_CHECK_SECONDS', 'apps', g, 'Timed check every (seconds)',
                kind='number', placeholder='300'))

    g = 'Background checks'
    add(Setting('COGNITRIX_CHANNELS', 'apps', g, 'Answer chat apps (Telegram, Slack, SMS)',
                'Turn off on all but one server when several share the same bots.', kind=ON_OFF, default='1'))
    add(Setting('COGNITRIX_EMAIL', 'apps', g, "Check agents' mailboxes", kind=ON_OFF, default='1'))
    add(Setting('COGNITRIX_EMAIL_POLL_SECONDS', 'apps', g, 'Mailbox check every (seconds)',
                kind='number', placeholder='30'))
    add(Setting('COGNITRIX_MAIL_CHECK_SECONDS', 'apps', g, 'Email triggers check every (seconds)',
                kind='number', placeholder='300'))
    add(Setting('COGNITRIX_CALENDAR_CHECK_SECONDS', 'apps', g, 'Calendar check every (seconds)',
                kind='number', placeholder='300'))

    # ---- Computers --------------------------------------------------------------------
    g = "Agents' computer"
    add(Setting('COGNITRIX_COMPUTER_SANDBOX', 'computers', g, 'Where agents get a computer',
                'Automatic follows the switch above (on when Docker is installed).',
                kind='select', default='',
                options=(('', 'Automatic'), ('docker', 'Docker on this machine'), ('fly', 'Fly.io (cloud)'),
                         ('off', 'Off'))))
    add(Setting('COGNITRIX_SANDBOX_IMAGE', 'computers', g, 'Computer image', restart=True,
                placeholder='ghcr.io/theonlyamos/cognitrix-desktop:latest'))
    add(Setting('COGNITRIX_SANDBOX_MEMORY', 'computers', g, 'Memory limit (Docker)', placeholder='4g'))
    add(Setting('COGNITRIX_SANDBOX_IDLE_MINUTES', 'computers', g, 'Stop an idle computer after (minutes)',
                '0 never stops it.', kind='number', placeholder='15'))
    add(Setting('COGNITRIX_SANDBOX_UPDATE_CHECK_HOURS', 'computers', g, 'Check for a new image every (hours)',
                '0 turns the check off.', kind='number', placeholder='6'))
    add(Setting('COGNITRIX_SANDBOX_AUTO_UPDATE_IDLE_S', 'computers', g,
                'Update an idle computer after (seconds)', '0 waits for you to update it.',
                kind='number', placeholder='600', restart=True))
    add(Setting('COGNITRIX_SANDBOX_HIDE_KEYS', 'computers', g, 'Hide keys from the computer',
                'Programs in the computer get placeholders; a proxy fills real keys in for the right hosts only.',
                kind=ON_OFF, default='1'))
    add(Setting('COGNITRIX_SANDBOX_MAX_COPY_MB', 'computers', g, 'Largest folder copied in (MB)',
                kind='number', default='400', restart=True))
    add(Setting('COGNITRIX_SANDBOX_SHELL', 'computers', g, 'Full shell without asking',
                'Only when this engine itself runs inside a sandbox or container.', kind=ON_OFF, default='0'))
    add(Setting('COGNITRIX_MAC_BACKGROUND', 'computers', g, 'Background computer use on macOS (experimental)',
                kind=ON_OFF, default='0'))
    add(Setting('COGNITRIX_CHECK_BROWSER', 'computers', g, 'Browser for page checks',
                'Path to a Chrome, Edge or Chromium. Leave empty to find one.'))

    g = 'Cloud computers on Fly.io'
    add(Setting('COGNITRIX_FLY_API_TOKEN', 'computers', g, 'Fly API token', kind='secret'))
    add(Setting('COGNITRIX_SANDBOX_FLY_APP', 'computers', g, 'Fly app name'))
    add(Setting('COGNITRIX_SANDBOX_FLY_REGION', 'computers', g, 'Region', placeholder='iad'))
    add(Setting('COGNITRIX_SANDBOX_FLY_CPU_KIND', 'computers', g, 'CPU kind', kind='select', default='shared',
                options=(('shared', 'Shared'), ('performance', 'Performance'))))
    add(Setting('COGNITRIX_SANDBOX_FLY_CPUS', 'computers', g, 'CPUs', kind='number', placeholder='2'))
    add(Setting('COGNITRIX_SANDBOX_FLY_MEMORY_MB', 'computers', g, 'Memory (MB)', kind='number',
                placeholder='4096'))
    add(Setting('COGNITRIX_SANDBOX_FLY_VOLUME_GB', 'computers', g, 'Disk (GB)', kind='number', placeholder='10'))
    add(Setting('COGNITRIX_SANDBOX_FLY_IDLE', 'computers', g, 'When idle', kind='select', default='stop',
                options=(('stop', 'Stop it'), ('suspend', 'Suspend it (faster to wake)'))))

    # ---- Notifications ----------------------------------------------------------------
    g = 'Browser push'
    add(Setting('COGNITRIX_WEB_PUSH', 'notifications', g, 'Push alerts to browsers', kind=ON_OFF, default='1'))
    add(Setting('COGNITRIX_VAPID_PRIVATE_KEY', 'notifications', g, 'Push key (VAPID private key)',
                'Leave empty and Cognitrix makes one.', kind='secret', multiline_secret=True))
    add(Setting('COGNITRIX_VAPID_SUBJECT', 'notifications', g, 'Push contact',
                'A mailto: or https: address push services can reach you at.', placeholder='mailto:you@example.com'))
    g = 'Approval alerts'
    add(Setting('COGNITRIX_APPROVAL_ALERT_DELAY', 'notifications', g, 'Alert after (seconds)',
                'How long an approval waits before an alert goes out.', kind='number', placeholder='10'))
    add(Setting('COGNITRIX_APPROVAL_AWAY_DELAY', 'notifications', g, 'Count me as away after (seconds)',
                kind='number', placeholder='60'))

    # ---- Advanced ---------------------------------------------------------------------
    g = 'Web address'
    add(Setting('COGNITRIX_PUBLIC_URL', 'advanced', g, 'Public address',
                'The address people use to reach this server, for links in emails, sign-in with Google or '
                'GitHub, SMS and webhooks. Leave empty on the desktop app.', placeholder='https://cognitrix.example.com'))

    g = 'Database and job queue'
    add(Setting('DB_TYPE', 'advanced', g, 'Database',
                'SQLite keeps everything in one file in the data folder. Pick another to use a database server.',
                kind='select', default='sqlite', restart=True,
                options=(('sqlite', 'SQLite (built in)'), ('postgresql', 'PostgreSQL'), ('mysql', 'MySQL'),
                         ('mongodb', 'MongoDB'))))
    add(Setting('DB_HOST', 'advanced', g, 'Database server', 'Not used with SQLite.', placeholder='localhost',
                restart=True))
    add(Setting('DB_PORT', 'advanced', g, 'Database port', 'Leave empty for the usual port.', kind='number',
                restart=True))
    add(Setting('DB_NAME', 'advanced', g, 'Database name',
                'With SQLite, the path of the database file.', placeholder='cognitrix', restart=True))
    add(Setting('DB_USER', 'advanced', g, 'Database user', restart=True))
    add(Setting('DB_PASSWORD', 'advanced', g, 'Database password', kind='secret', restart=True))
    add(Setting('CELERY_BROKER_URL', 'advanced', g, 'Job queue (Redis)',
                'Where task runs wait for a worker, e.g. redis://localhost:6379/0. Leave empty to use the '
                'built-in queue in the data folder.', kind='secret', restart=True))
    add(Setting('TASK_LIMIT_REDIS_URL', 'advanced', g, 'Redis for shared limits',
                'Keeps model-call limits across several workers. Leave empty to use the job queue Redis.',
                kind='secret', restart=True))

    g = 'Web server'
    add(Setting('COGNITRIX_CORS_ORIGINS', 'advanced', g, 'Other addresses allowed to call the API',
                'One per line. Only needed when the app is opened from another address than the server.',
                kind='list', placeholder='http://localhost:8000\nhttp://localhost:5173', restart=True))
    add(Setting('COGNITRIX_CSP', 'advanced', g, 'Content security policy',
                'Leave empty for the built-in policy. Type off to send none.'))

    g = 'Safety'
    add(Setting('COGNITRIX_REVIEWER', 'advanced', g, 'Action reviewer',
                'A second model checks risky tool calls. Agents can override it.', kind='select', default='off',
                options=(('off', 'Off'), ('unattended', 'When nobody is watching'), ('always', 'Always'))))
    add(Setting('COGNITRIX_REVIEWER_PROVIDER', 'advanced', g, 'Reviewer provider',
                'Leave empty for the default provider.'))
    add(Setting('COGNITRIX_REVIEWER_MODEL', 'advanced', g, 'Reviewer model'))
    add(Setting('COGNITRIX_REVIEWER_MAX_ALLOW', 'advanced', g, 'Reviewer may allow up to', kind='select',
                default='medium', options=(('medium', 'Medium risk'), ('high', 'High risk'))))
    add(Setting('COGNITRIX_REVIEWER_TIMEOUT', 'advanced', g, 'Reviewer time limit (seconds)', kind='number',
                placeholder='60'))
    add(Setting('COGNITRIX_SHELL_MODE', 'advanced', g, 'Commands outside the safe list', kind='select',
                default='ask', options=(('ask', 'Run once approved'), ('allowlist', 'Refuse them'))))
    add(Setting('COGNITRIX_SHELL', 'advanced', g, 'Shell program',
                "Path to the shell agents' commands run in, e.g. Git Bash on Windows. Leave empty to pick one."))
    add(Setting('COGNITRIX_AUTO_APPROVE', 'advanced', g, 'Approve every tool call',
                'Dangerous: agents act without asking. Only for a throwaway machine.', kind=ON_OFF, default='0'))
    add(Setting('COGNITRIX_ALLOWED_FOLDERS', 'advanced', g, 'Folders people may add',
                'One per line. Empty: anywhere on the desktop app, the working folder on a server.', kind='list'))
    add(Setting('COGNITRIX_WEBFETCH_ALLOW_PRIVATE', 'advanced', g, 'Let agents fetch private network pages',
                'localhost and LAN addresses.', kind=ON_OFF, default='0'))
    add(Setting('COGNITRIX_WEBHOOK_ALLOW_PRIVATE', 'advanced', g, 'Let webhooks call private network addresses',
                kind=ON_OFF, default='0'))
    add(Setting('COGNITRIX_WEBHOOK_BUDGET', 'advanced', g, 'Webhook time limit (seconds)', kind='number',
                default='15', restart=True))

    g = 'Restarts'
    add(Setting('COGNITRIX_RESUME_CHATS', 'advanced', g, 'Carry on replies after a restart',
                'A reply that was running when the app or server stopped is picked up again '
                'where it left off.', kind=ON_OFF, default='1'))

    g = 'Sharing and apps'
    add(Setting('COGNITRIX_PUBLIC_AGENT_LINKS', 'advanced', g, 'Public links to agents', kind=ON_OFF, default='1'))
    add(Setting('COGNITRIX_ARTIFACT_PUBLIC_LINKS', 'advanced', g, 'Public links to artifacts', kind=ON_OFF,
                default='1'))
    add(Setting('COGNITRIX_APPS', 'advanced', g, 'Apps agents build', kind=ON_OFF, default='1'))
    add(Setting('COGNITRIX_APPS_LOCAL', 'advanced', g, 'Run apps on this machine when the computer is off',
                'Servers people share usually turn this off.', kind=ON_OFF, default='1'))

    g = 'Limits and timeouts'
    num = [
        ('COGNITRIX_API_RATE_LIMIT', 'API requests per minute (per key)', '60', False),
        ('COGNITRIX_API_CHAT_TIMEOUT', 'API chat reply time limit (seconds)', '300', True),
        ('COGNITRIX_MAX_TOOL_ROUNDS', 'Most tool rounds in one reply', '100', True),
        ('COGNITRIX_MAX_CONCURRENT_TOOL_CALLS', 'Tool calls at once (per agent)', '', True),
        ('COGNITRIX_MAX_PARALLEL_STEPS', 'Task steps at once', '3', True),
        ('COGNITRIX_STEP_TIMEOUT', 'Task step time limit (seconds)', '600', True),
        ('COGNITRIX_GATE_THRESHOLD', 'Task review pass mark (out of 10)', '7', True),
        ('COGNITRIX_DONE_WHEN_FIXES', 'Fix rounds when a result misses "Done when"', '2', False),
        ('COGNITRIX_TASK_APPROVAL_TIMEOUT', 'Task approval wait (seconds)', '1800', False),
        ('COGNITRIX_TASK_APPROVAL_POLL', 'Task approval check every (seconds)', '1.0', False),
        ('COGNITRIX_GROUP_MAX_TURNS', 'Agent turns per group chat message', '6', True),
        ('COGNITRIX_CODE_MODE_TIMEOUT', 'Run Code time limit (seconds)', '600', True),
        ('COGNITRIX_CODE_MODE_MAX_CALLS', 'Tool calls per Run Code script', '200', True),
        ('COGNITRIX_ON_DEMAND_MIN_TOOLS', 'Load tools on demand from (tools)', '12', True),
        ('COGNITRIX_CODING_AGENT_TIMEOUT', 'Coding agent time limit (seconds)', '', False),
        ('COGNITRIX_SWARM_MAX_CONCURRENCY', 'Most swarm workers at once (per person)', '8', False),
        ('COGNITRIX_SWARM_MAX_WORKERS', 'Most workers in a swarm', '50', False),
        ('COGNITRIX_SWARM_MAX_TOKENS', 'Most tokens a swarm may use', '20000000', False),
        ('COGNITRIX_SWARM_WORKER_TIMEOUT', 'Swarm worker time limit (seconds)', '900', False),
        ('COGNITRIX_SWARM_STALL_SECONDS', 'Swarm worker counts as stuck after (seconds)', '300', False),
        ('COGNITRIX_SWARM_WAIT_SECONDS', 'Wait for swarm results (seconds)', '45', False),
        ('COGNITRIX_AUTOMATION_MAX_RUNS_PER_DAY', 'Automation runs per day', '48', False),
        ('COGNITRIX_MEDIA_PROCESSING_CONCURRENCY', 'Media files processed at once', '', True),
        ('COGNITRIX_PRODUCED_FILE_MAX_MB', 'Largest output file kept (MB)', '500', False),
        ('COGNITRIX_PRODUCED_PC_FILE_MAX_MB', 'Largest file from a paired PC (MB)', '100', False),
        ('COGNITRIX_PRODUCED_FILES_QUOTA_MB', 'Output files kept per person (MB)', '5000', False),
        ('COGNITRIX_PAIRED_TASK_WAIT_S', 'Wait for an offline paired PC (seconds)', '', False),
        ('TASK_PROVIDER_CONCURRENCY', 'Task model calls at once (per provider)', '4', True),
        ('TASK_ACTOR_CONCURRENCY', 'Task model calls at once (per person)', '4', True),
        ('TASK_RECOVERY_INTERVAL_SECONDS', 'Look for stuck tasks every (seconds)', '30', True),
    ]
    for name, label, default, restart in num:
        add(Setting(name, 'advanced', g, label, kind='number', placeholder=default, restart=restart))
    return s


def _default_base_url(provider: str) -> str:
    try:
        from cognitrix.providers.base import _DEFAULT_BASE_URLS
        return _DEFAULT_BASE_URLS.get(provider, '')
    except Exception:
        return ''


_registry_cache: dict[str, Setting] | None = None


def registry() -> dict[str, Setting]:
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = {s.name: s for s in _registry()}
    return _registry_cache


# Values that stay environment variables: deployment plumbing, keys that
# unlock saved secrets, and what one Cognitrix process tells another.
ENV_ONLY: dict[str, str] = {
    'CELERY_WORKER_MODE / CELERY_FORCE_FILESYSTEM': 'Set by the app for the worker it starts itself.',
    'COGNITRIX_DESKTOP / COGNITRIX_ENGINE_PID / COGNITRIX_DESKTOP_TOKEN / COGNITRIX_TOOLS_ROOT / COGNITRIX_CWD_FILE':
        'Set by the desktop app for its own engine.',
    'COGNITRIX_SANDBOX_* inside the computer image, COGNITRIX_API_URL / COGNITRIX_API_KEY, '
    'COGNITRIX_SANDBOX_CLI with the model key placeholders for the CLI there':
        'Set by the engine when it starts a sandbox computer.',
    'COGNITRIX_INTERNAL_URL / FLY_APP_NAME / FLY_PRIVATE_IP / FLY_REGION': 'Set by the hosting platform.',
    'COGNITRIX_PAIRED_CONFIG / COGNITRIX_PAIRED_KEYCHAIN / COGNITRIX_PAIRED_ALLOW_HTTP': 'The pairing helper on a paired PC.',
    'COGNITRIX_<CLI>_PATH': 'Developer override for where a coding agent CLI is installed.',
    'COGNITRIX_FLY_API_URL / COGNITRIX_TELEGRAM_API_BASE / COGNITRIX_TWILIO_API_BASE / COGNITRIX_APPS_API_URL / COMPOSIO_BASE_URL':
        'Test doubles for outside APIs.',
    'DISABLE_VECTOR_STORE': 'Test switch.',
}


# ---------------------------------------------------------------- the file

_lock = threading.Lock()
_cache: tuple[int, dict[str, str]] | None = None
_overrides: dict[str, str] = {}

# Tests set this so ``monkeypatch.setenv`` keeps working for values that are
# not saved; the app itself never reads these settings from the environment.
_env_fallback = False


def settings_file() -> Path:
    # Same folder as settings.workdir, without importing config (which reads us).
    return Path.home() / '.cognitrix' / 'app_settings.json'


def _read_file() -> dict:
    path = settings_file()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Couldn't read app settings at %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(raw: dict) -> None:
    global _cache
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.app_settings.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(raw, fh, indent=2, sort_keys=True)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _cache = None


def _is_secret(name: str) -> bool:
    spec = registry().get(name)
    return bool(spec and spec.kind == 'secret')


def _unseal(name: str, value: str) -> str:
    if not _is_secret(name):
        return value
    from cognitrix.common.secret_box import unseal_or_empty

    return unseal_or_empty(value)


def _values() -> dict[str, str]:
    """Saved values, unsealed. Re-read only when the file changes."""
    global _cache
    path = settings_file()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = -1
    with _lock:
        if _cache is not None and _cache[0] == mtime:
            return _cache[1]
        raw = _read_file().get(VALUES)
        raw = raw if isinstance(raw, dict) else {}
        values: dict[str, str] = {}
        for name, value in raw.items():
            if isinstance(value, str) and value:
                plain = _unseal(str(name), value)
                if plain:
                    values[str(name)] = plain
        _cache = (mtime, values)
        return values


def get(name: str, default: str = '') -> str:
    """A setting's value, or ``default`` when it isn't set. Drop-in for
    ``os.getenv(name, default)`` with values from Settings instead."""
    if name in _overrides:
        return _overrides[name]
    value = _values().get(name)
    if value:
        return value
    if _env_fallback:
        env = os.environ.get(name)
        if env is not None:
            return env
    return default


def get_bool(name: str, default: bool) -> bool:
    raw = get(name, '').strip().lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'on')


def override(name: str, value: str | None) -> None:
    """Set a value for this process only (command line flags). None clears it."""
    if value is None:
        _overrides.pop(name, None)
    else:
        _overrides[name] = value


# Numbers the code reads with int(), so a fraction would not load.
_WHOLE_NUMBERS = frozenset({
    'COGNITRIX_SMTP_PORT', 'COGNITRIX_GITHUB_CHECK_SECONDS', 'COGNITRIX_MAIL_CHECK_SECONDS',
    'COGNITRIX_CALENDAR_CHECK_SECONDS', 'COGNITRIX_AUDIT_DAYS', 'JWT_ACCESS_TOKEN_EXPIRE_MINUTES',
    'COGNITRIX_SANDBOX_MAX_COPY_MB', 'COGNITRIX_SANDBOX_FLY_CPUS', 'COGNITRIX_SANDBOX_FLY_MEMORY_MB',
    'COGNITRIX_SANDBOX_FLY_VOLUME_GB', 'COGNITRIX_API_RATE_LIMIT', 'COGNITRIX_MAX_TOOL_ROUNDS',
    'COGNITRIX_MAX_CONCURRENT_TOOL_CALLS', 'COGNITRIX_MAX_PARALLEL_STEPS', 'COGNITRIX_STEP_TIMEOUT',
    'COGNITRIX_DONE_WHEN_FIXES',
    'COGNITRIX_GROUP_MAX_TURNS', 'COGNITRIX_CODE_MODE_MAX_CALLS', 'COGNITRIX_ON_DEMAND_MIN_TOOLS',
    'COGNITRIX_CODING_AGENT_TIMEOUT', 'COGNITRIX_SWARM_MAX_CONCURRENCY', 'COGNITRIX_SWARM_MAX_WORKERS',
    'COGNITRIX_SWARM_MAX_TOKENS', 'COGNITRIX_AUTOMATION_MAX_RUNS_PER_DAY', 'COGNITRIX_MEDIA_PROCESSING_CONCURRENCY',
    'COGNITRIX_PRODUCED_FILE_MAX_MB', 'COGNITRIX_PRODUCED_PC_FILE_MAX_MB', 'COGNITRIX_PRODUCED_FILES_QUOTA_MB',
    'TASK_PROVIDER_CONCURRENCY', 'TASK_ACTOR_CONCURRENCY', 'DB_PORT',
})


def _clean(spec: Setting | None, name: str, value: Any) -> str:
    text = '' if value is None else str(value)
    if spec is not None and spec.kind == 'toggle' and isinstance(value, bool):
        text = '1' if value else '0'
    if spec is None or not (spec.kind in ('textarea', 'list') or spec.multiline_secret):
        text = text.strip()
    else:
        text = text.strip('\n\r ')
    if len(text) > MAX_VALUE_LENGTH:
        raise ValueError(f'{spec.label if spec else name} is too long.')
    if spec is not None and spec.kind == 'number' and text:
        try:
            number = float(text)
        except ValueError:
            raise ValueError(f'{spec.label} must be a number.') from None
        if number != number or number < 0 or number in (float('inf'), float('-inf')):
            raise ValueError(f'{spec.label} must be 0 or more.')
        if number.is_integer():
            text = str(int(number))
        elif name in _WHOLE_NUMBERS:
            raise ValueError(f'{spec.label} must be a whole number.')
    if spec is not None and spec.kind == 'select' and spec.options and text not in {v for v, _ in spec.options}:
        raise ValueError(f'{spec.label}: pick one of the choices.')
    if spec is not None and spec.kind == 'list':
        # Stored the way the code always read it: comma separated (folders: os.pathsep).
        sep = os.pathsep if name == 'COGNITRIX_ALLOWED_FOLDERS' else ','
        parts = [p.strip() for line in text.splitlines() for p in (line.split(sep) if sep == ',' else [line])]
        text = sep.join(p for p in parts if p)
    return text


def set_many(changes: dict[str, Any]) -> None:
    """Save values from Settings. An empty value clears the setting (back to
    its default). Unknown names are refused."""
    from cognitrix.common.secret_box import seal

    specs = registry()
    cleaned: dict[str, str] = {}
    for name, value in changes.items():
        spec = specs.get(name)
        if spec is None:
            raise ValueError(f'Unknown setting: {name}')
        cleaned[name] = _clean(spec, name, value)
    with _lock:
        raw = _read_file()
        values = raw.get(VALUES) if isinstance(raw.get(VALUES), dict) else {}
        for name, value in cleaned.items():
            if value:
                values[name] = seal(value) if specs[name].kind == 'secret' else value
            else:
                values.pop(name, None)
        raw[VALUES] = values
        _write_file(raw)


SETUP_DONE = 'server_setup_done'


def server_setup_done() -> bool:
    """Whether the first-start Server setup screen was finished (or skipped)."""
    return bool(_read_file().get(SETUP_DONE))


def mark_server_setup_done() -> None:
    with _lock:
        raw = _read_file()
        raw[SETUP_DONE] = True
        _write_file(raw)


# ---------------------------------------------------------------- for the API

def describe(page: str | None = None) -> list[dict[str, Any]]:
    """Settings for one Settings section, grouped, with secrets hidden."""
    values = _values()
    groups: dict[str, list[dict[str, Any]]] = {}
    for spec in registry().values():
        if page and spec.page != page:
            continue
        value = values.get(spec.name, '')
        row: dict[str, Any] = {
            'name': spec.name, 'label': spec.label, 'help': spec.help, 'kind': spec.kind,
            'default': spec.default, 'restart': spec.restart,
            'options': [{'value': v, 'label': lab} for v, lab in spec.options],
            'placeholder': spec.placeholder or (
                _default_base_url(spec.name[:-len('_BASE_URL')].lower()) if spec.name.endswith('_BASE_URL') else ''),
            'multiline': spec.multiline_secret or spec.kind in ('textarea', 'list'),
        }
        if spec.kind == 'secret':
            row['is_set'] = bool(value)
            row['hint'] = f'…{value[-4:]}' if len(value) >= 12 and not spec.multiline_secret else ''
            row['value'] = ''
        elif spec.kind == 'list':
            sep = os.pathsep if spec.name == 'COGNITRIX_ALLOWED_FOLDERS' else ','
            row['value'] = '\n'.join(p for p in value.split(sep) if p)
        else:
            row['value'] = value
        groups.setdefault(spec.group, []).append(row)
    return [{'title': title, 'settings': rows} for title, rows in groups.items()]
