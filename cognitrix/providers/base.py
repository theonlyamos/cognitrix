"""
Provider-agnostic OpenAI-compatible LLM layer.

Runtime config (provider, base_url, api_key, model) from env or CLI.
Groq and Ollama: direct. Others: route through Helicone.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
from contextvars import ContextVar
from typing import Any, TypeAlias

import openai
from odbms import Model
from openai import AsyncOpenAI, OpenAI
from pydantic import Field

from cognitrix.errors import ExecutionControlError
from cognitrix.utils import file_to_image_data_uri, image_to_base64
from cognitrix.utils.llm_response import LLMResponse

# Cache async OpenAI clients by effective config to avoid per-request overhead.
# Async clients keep the server event loop free during the (long) provider call
# instead of blocking it for every socket read.
_client_cache: dict[str, tuple[asyncio.AbstractEventLoop | None, AsyncOpenAI]] = {}

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

# Defaults for optional fields (overridable via CLI)
DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_TOKENS = 8192

# Well-known base URLs when env does not provide them
_DEFAULT_BASE_URLS: dict[str, str] = {
    'groq': 'https://api.groq.com/openai/v1',
    'ollama': 'http://localhost:11434/v1',
    'openrouter': 'https://openrouter.ai/api/v1',
    'openai': 'https://api.openai.com/v1',
    'cerebras': 'https://api.cerebras.ai/v1',
    'google': 'https://generativelanguage.googleapis.com/v1beta/openai/v1',
    'anthropic': 'https://api.anthropic.com/v1/',
}

# Conservative per-provider context windows (tokens) used for prompt budgeting
# when LLM.context_window is not set explicitly.
_PROVIDER_CONTEXT_WINDOWS: dict[str, int] = {
    'google': 1_000_000,
    'gemini': 1_000_000,
    'anthropic': 200_000,
    'openai': 128_000,
    'groq': 128_000,
    'openrouter': 128_000,
    'ollama': 32_000,
}


def _env_key(provider: str, suffix: str) -> str:
    """Derive env key from provider: e.g. OPENROUTER_BASE_URL."""
    return f"{provider.upper()}_{suffix}"


# Optional per-request token tally, set by callers that report usage (the
# OpenAI-compatible /v1 endpoint). None means nobody is counting.
usage_sink: ContextVar[dict | None] = ContextVar('cognitrix_usage_sink', default=None)


def record_usage(usage: dict | None) -> None:
    sink = usage_sink.get()
    if sink is None or not usage:
        return
    for key in ('prompt_tokens', 'completion_tokens'):
        try:
            sink[key] = sink.get(key, 0) + int(usage.get(key) or 0)
        except (TypeError, ValueError):
            pass


class ProviderKeyDestinationError(ValueError):
    """A server-held provider key would be sent to a base URL the server does not trust."""


def _normalize_base_url(url: str | None) -> str:
    return (url or '').strip().rstrip('/').lower()


_KEY_ENV_SUFFIXES = ('_API_KEY', '_ACCESS_TOKEN', '_TOKEN')


def _server_held_keys() -> dict[str, set[str]]:
    """Map every provider key the server holds to the providers it belongs to.

    Covers <PROVIDER>_API_KEY style env vars and the keys known to settings,
    so a key is recognised however it was configured.
    """
    from cognitrix.config import settings

    held: dict[str, set[str]] = {}
    for name, value in os.environ.items():
        if not value or len(value) < 8:
            continue
        for suffix in _KEY_ENV_SUFFIXES:
            if name.endswith(suffix):
                provider = name[: -len(suffix)].lower()
                held.setdefault(value, set()).add(provider)
                break
    for provider in set(_DEFAULT_BASE_URLS) | {'together', 'cohere', 'groq', 'cerebras'}:
        try:
            value = settings.get_api_key(provider)
        except Exception:
            value = ''
        if value:
            held.setdefault(value, set()).add(provider)
    return held


def _trusted_base_urls(providers: set[str]) -> set[str]:
    """Base URLs a server-held key may be sent to.

    That is the operator's <PROVIDER>_BASE_URL (or the built-in default) for
    the key's own provider, plus any extra URLs listed in
    COGNITRIX_TRUSTED_BASE_URLS (comma-separated).
    """
    trusted: set[str] = set()
    for provider in providers:
        for candidate in (
            os.getenv(_env_key(provider, 'BASE_URL')),
            _DEFAULT_BASE_URLS.get(provider),
        ):
            if candidate:
                trusted.add(_normalize_base_url(candidate))
    extra = os.getenv('COGNITRIX_TRUSTED_BASE_URLS', '')
    trusted.update(_normalize_base_url(u) for u in extra.split(',') if u.strip())
    return trusted


def assert_key_destination_allowed(base_url: str, api_key: str) -> None:
    """Refuse to send a server-held provider key to an untrusted base URL.

    Agents can be created by any logged-in user with their own `base_url`.
    Without this check the server would attach its own provider key to a
    request to whatever host the user picked.
    """
    if not api_key:
        return
    owners = _server_held_keys().get(api_key)
    if not owners:
        return  # caller-supplied key: theirs to send wherever they like
    if _normalize_base_url(base_url) in _trusted_base_urls(owners):
        return
    raise ProviderKeyDestinationError(
        f"base_url '{base_url}' is not a trusted endpoint for the server's "
        f"{'/'.join(sorted(owners))} key. Supply your own api_key for a custom "
        "base_url, or ask the operator to add it to COGNITRIX_TRUSTED_BASE_URLS."
    )


def _resolve_runtime_config(provider: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Resolve runtime config from env and overrides.
    Provider name drives env keys: <PROVIDER>_BASE_URL, <PROVIDER>_API_KEY, <PROVIDER>_MODEL.
    AI_PROVIDER used when provider not explicitly passed.
    """
    from cognitrix.config import settings

    provider = str(provider).strip().lower()
    overrides = overrides or {}

    # Resolve provider: explicit or AI_PROVIDER
    effective_provider = provider or os.getenv('AI_PROVIDER', 'openrouter').lower()

    base_url = (
        overrides.get('base_url')
        or os.getenv(_env_key(effective_provider, 'BASE_URL'))
        or _DEFAULT_BASE_URLS.get(effective_provider, '')
    )
    api_key = (
        overrides.get('api_key')
        or os.getenv(_env_key(effective_provider, 'API_KEY'))
        or settings.get_api_key(effective_provider)
        or ''
    )
    model = (
        overrides.get('model')
        or os.getenv(_env_key(effective_provider, 'MODEL'))
        or settings.get_default_model(effective_provider)
    )
    temperature = overrides.get('temperature')
    if temperature is None:
        temp_env = os.getenv(_env_key(effective_provider, 'TEMPERATURE'))
        temperature = float(temp_env) if temp_env else DEFAULT_TEMPERATURE
    max_tokens = overrides.get('max_tokens')
    if max_tokens is None:
        tok_env = os.getenv(_env_key(effective_provider, 'MAX_TOKENS'))
        max_tokens = int(tok_env) if tok_env else DEFAULT_MAX_TOKENS

    # Validate required fields with clear errors
    if not base_url:
        raise ValueError(
            f"Missing base_url for provider '{effective_provider}'. "
            f"Set {_env_key(effective_provider, 'BASE_URL')} or pass base_url."
        )
    if not api_key:
        raise ValueError(
            f"Missing api_key for provider '{effective_provider}'. "
            f"Set {_env_key(effective_provider, 'API_KEY')} or pass api_key."
        )
    if not model:
        raise ValueError(
            f"Missing model for provider '{effective_provider}'. "
            f"Set {_env_key(effective_provider, 'MODEL')} or pass model."
        )

    assert_key_destination_allowed(base_url, api_key)

    config: dict[str, Any] = {
        'provider': effective_provider,
        'base_url': base_url,
        'api_key': api_key,
        'model': model,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }
    for k in ('extra_headers', 'extra_body', 'response_format'):
        if k in overrides and overrides[k] is not None:
            config[k] = overrides[k]
    return config


_OPENAI_REASONING_MODEL = re.compile(r'^(gpt-(?:[5-9]|\d{2,})|o\d)')


def _is_openai_reasoning_model(model: str) -> bool:
    """GPT-5.x and o-series models reject a custom temperature."""
    name = (model or '').lower().rsplit('/', 1)[-1]
    return bool(_OPENAI_REASONING_MODEL.match(name)) and '-chat' not in name


_CLAUDE_VERSION = re.compile(r'^claude-(?:opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?!\d)')


def _anthropic_rejects_temperature(model: str) -> bool:
    """Claude Opus 4.7 and every later model reject `temperature` with a 400.

    Older names (claude-3-*, Sonnet/Haiku 4.5, Opus 4.6 and earlier) still
    accept it. Names we can't parse keep it, so behaviour is unchanged.
    """
    name = (model or '').lower().rsplit('/', 1)[-1]
    m = _CLAUDE_VERSION.match(name)
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2) or 0)
    return (major, minor) >= (4, 7)


def _apply_provider_param_rules(llm: 'LLM', params: dict[str, Any]) -> dict[str, Any]:
    """Adapt request params to what the provider's API accepts.

    OpenAI deprecated `max_tokens` in favour of `max_completion_tokens`, and
    GPT-5.x / o-series models reject `max_tokens` outright. They also only
    accept the default temperature, so it is left out for them. Newer Claude
    models reject `temperature` too, and Anthropic rejects `json_object`.
    """
    if llm.provider == 'anthropic':
        if _anthropic_rejects_temperature(llm.model):
            params.pop('temperature', None)
        # Anthropic's OpenAI-compatible endpoint only accepts strict
        # `json_schema` formats and rejects `json_object` with a 400. Callers
        # that ask for json_object already parse JSON out of the text, so
        # leaving it out is safe.
        fmt = params.get('response_format')
        if isinstance(fmt, dict) and fmt.get('type') == 'json_object':
            params.pop('response_format')
        return params
    if llm.provider != 'openai':
        return params
    if 'max_tokens' in params:
        params['max_completion_tokens'] = params.pop('max_tokens')
    if _is_openai_reasoning_model(llm.model):
        params.pop('temperature', None)
    return params


def _client_cache_key(base_url: str, api_key: str, headers: dict[str, str] | None) -> str:
    """Stable cache key for OpenAI client config."""
    payload = {'base_url': base_url, 'api_key': api_key, 'headers': headers or {}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _get_or_create_client(
    base_url: str, api_key: str, default_headers: dict[str, str] | None = None
) -> AsyncOpenAI:
    """Reuse an async OpenAI client per effective config and event loop.

    An async client's connection pool is bound to the loop that first used it;
    openai 3.x fails with "Event loop is closed" if it is reused after that
    loop ends (CLI helpers and tests run separate asyncio.run() calls).
    """
    key = _client_cache_key(base_url, api_key, default_headers)
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    cached = _client_cache.get(key)
    if cached is None or cached[0] is not loop:
        # The explicit, budget-accounted retry loop owns provider retries.
        kwargs: dict[str, Any] = {
            'api_key': api_key,
            'base_url': base_url,
            'max_retries': 0,
        }
        if default_headers:
            kwargs['default_headers'] = default_headers
        _client_cache[key] = (loop, AsyncOpenAI(**kwargs))
    return _client_cache[key][1]


def _transient_errors() -> tuple[type[BaseException], ...]:
    # Looked up per call so tests (and SDK upgrades) can swap the classes.
    return (
        openai.RateLimitError, openai.APITimeoutError,
        openai.APIConnectionError, openai.InternalServerError,
    )

_MAX_ATTEMPTS = 3
_MAX_RETRY_AFTER_SECONDS = 30.0


def _retry_delay(error: Exception, attempt: int) -> float:
    """Seconds to wait before the next attempt.

    Honours the provider's Retry-After header (capped), otherwise uses
    jittered exponential backoff.
    """
    import random

    response = getattr(error, 'response', None)
    headers = getattr(response, 'headers', None) or {}
    try:
        retry_after = headers.get('retry-after')
    except Exception:
        retry_after = None
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), _MAX_RETRY_AFTER_SECONDS)
        except (TypeError, ValueError):
            pass
    base = 2 ** (attempt - 1)
    return base + random.uniform(0, base / 2)


# Models that refused function tools while reasoning on /chat/completions
# (e.g. gpt-6.x). Later calls send reasoning_effort='none' up front.
_NO_REASONING_WITH_TOOLS: set[str] = set()


def _rejects_tools_with_reasoning(err: BaseException) -> bool:
    text = str(err).lower()
    return 'reasoning_effort' in text and 'function tools' in text


def _reasoning_effort_set(params: dict[str, Any]) -> bool:
    extra = params.get('extra_body') or {}
    return 'reasoning_effort' in params or (isinstance(extra, dict) and 'reasoning_effort' in extra)


async def _create_with_retry(client: AsyncOpenAI, params: dict[str, Any]):
    """chat.completions.create with retries on 429/5xx/timeouts.

    Used by both paths. For streaming this covers failures before the first
    chunk, which is where rate limits and overload errors surface; a stream
    that breaks midway is not replayed.
    """
    from cognitrix.tasks.accounting import current_task_accounting

    accounting = current_task_accounting()
    model = str(params.get('model') or '')
    if params.get('tools') and model in _NO_REASONING_WITH_TOOLS and not _reasoning_effort_set(params):
        params = {**params, 'reasoning_effort': 'none'}
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if attempt > 1 and accounting is not None:
            retry_output_tokens = await accounting.begin_provider_retry()
            if retry_output_tokens is not None:
                # Re-apply the live token clamp after the failed attempt
                # has consumed budget. Keep all other provider params.
                token_key = 'max_completion_tokens' if 'max_completion_tokens' in params else 'max_tokens'
                params = {**params, token_key: retry_output_tokens}
        try:
            return await client.chat.completions.create(**params)
        except openai.BadRequestError as e:
            # Some OpenAI models only take function tools on /chat/completions
            # with reasoning turned off. Retry once that way rather than fail
            # every chat with tools. A reasoning_effort the agent sets itself
            # is left alone.
            if not (params.get('tools') and _rejects_tools_with_reasoning(e)) or _reasoning_effort_set(params):
                raise
            logger.info("%s refuses tools while reasoning; retrying with reasoning_effort='none'", model)
            _NO_REASONING_WITH_TOOLS.add(model)
            params = {**params, 'reasoning_effort': 'none'}
            return await client.chat.completions.create(**params)
        except _transient_errors() as e:
            if accounting is not None:
                # The request may be billable even when it failed before
                # returning usage. Close this attempt conservatively
                # before reserving any subsequent request.
                await accounting.finish_failed_provider_attempt()
            if attempt == _MAX_ATTEMPTS:
                raise
            delay = _retry_delay(e, attempt)
            logger.warning("Transient LLM error (attempt %s, retrying in %.1fs): %s", attempt, delay, e)
            if accounting is not None:
                await accounting.wait_within_wall(asyncio.sleep(delay))
            else:
                await asyncio.sleep(delay)
    raise RuntimeError('unreachable')


def _malformed_arguments_error(name: str, raw: Any, truncated: bool) -> str:
    snippet = str(raw)
    if len(snippet) > 200:
        snippet = snippet[:200] + '...'
    reason = (
        'the response hit the output token limit, so the arguments were cut off'
        if truncated else 'the arguments were not valid JSON'
    )
    return (
        f"Error: tool call '{name}' was not run because {reason}. "
        f"Received: {snippet}. Send the call again with complete, valid JSON arguments"
        + (' (split large content into smaller calls).' if truncated else '.')
    )


class LLM(Model):
    """
    Provider-agnostic LLM backed by OpenAI-compatible API.
    Config: provider, base_url, api_key, model (+ optional temperature, max_tokens).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: str = Field(default='openrouter')
    model: str = Field(default='')
    temperature: float = Field(default=DEFAULT_TEMPERATURE)
    api_key: str = Field(default='')
    base_url: str = ''
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS)
    context_window: int = Field(default=0)
    """Model context window in tokens; 0 = use the provider default."""
    is_multimodal: bool = Field(default=True)
    supports_tool_use: bool = Field(default=True)
    client: Any = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    response_format: dict[str, Any] | None = None

    def __init__(self, provider: str | None = None, **data: Any):
        if provider is not None or any(k in data for k in ('base_url', 'api_key', 'model')):
            overrides = {k: v for k, v in data.items() if v is not None and v != ''}
            resolved = _resolve_runtime_config(provider or data.get('provider', 'openrouter'), overrides)
            data = {**resolved, **overrides}
        super().__init__(**data)
        if provider and 'provider' not in data:
            self.provider = provider.lower()

    def get_context_window(self) -> int:
        if self.context_window > 0:
            return self.context_window
        return _PROVIDER_CONTEXT_WINDOWS.get(self.provider, 128_000)

    def format_query(self, messages: list[dict[str, Any]]) -> list:
        return LLMManager.format_query(self, messages)

    @staticmethod
    def load_llm(provider: str | dict[str, Any]) -> 'LLM | None':
        return LLMManager.load_llm(provider)

    def get_supported_models(self):
        return LLMManager.get_supported_models(self)

    async def __call__(self, prompt: list[dict[str, Any]], stream: bool = False, tools: Any = None, **kwds: Any):
        if tools is None:
            tools = []
        # Imported lazily to keep the provider layer usable without the task
        # runtime and to avoid a module cycle through provider limiters.
        from cognitrix.tasks.accounting import wrap_llm_result

        return await wrap_llm_result(
            self,
            prompt,
            lambda runtime_llm: LLMManager.generate_response(
                runtime_llm,
                prompt,
                stream,
                tools,
                **kwds,
            ),
            stream=stream,
        )


LLMList: TypeAlias = list[LLM]


class LLMManager:
    """Manager for provider-agnostic LLM logic."""

    @staticmethod
    def _normalize_role(role: str) -> str:
        """Map role variants to canonical system/user/assistant/tool for API compatibility."""
        r = (role or '').strip().lower()
        if r in ('system',):
            return 'system'
        if r in ('user',):
            return 'user'
        if r in ('tool',):
            return 'tool'
        # Assistant, agent names, and any other non-user role -> assistant
        return 'assistant'

    @staticmethod
    def _image_url_from_content(content: Any) -> str | None:
        """Resolve `type:'image'` content to an image_url string, or None if unusable.

        Handles the three shapes that reach the formatter: a PIL image (screenshot
        tool), an already-built `data:` URI, and a path to an uploaded image on disk.
        """
        try:
            if isinstance(content, str):
                if content.startswith('data:'):
                    return content
                if os.path.isfile(content):
                    return file_to_image_data_uri(content)
                return None
            return f'data:image/jpeg;base64,{image_to_base64(content)}'
        except Exception:
            return None

    @staticmethod
    def format_query(llm: LLM, messages: list[dict[str, Any]]) -> list:
        formatted_messages = []
        for fm in messages:
            role = LLMManager._normalize_role(fm.get('role', 'user'))
            content = fm.get('content', '')
            msg_type = fm.get('type', 'text')
            tool_call_id = fm.get('tool_call_id')
            tool_calls = fm.get('tool_calls')
            if tool_calls:
                # Assistant message that issued native tool calls. Reconstruct the
                # OpenAI-spec shape from the parsed form so the tool-result messages
                # that follow have a valid preceding assistant.tool_calls.
                formatted_messages.append({
                    'role': 'assistant',
                    'content': content or None,
                    'tool_calls': [
                        {
                            'id': tc.get('tool_call_id') or f'call_{i}',
                            'type': 'function',
                            'function': {
                                'name': str(tc.get('name', '')).replace(' ', '_'),
                                'arguments': json.dumps(tc.get('arguments', {})),
                            },
                            # Echo provider extras (Gemini's thought_signature) on
                            # the re-prompt — but only to Gemini; other providers
                            # can reject unknown fields in tool_calls.
                            **({'extra_content': tc['extra_content']}
                               if tc.get('extra_content') and llm.provider in ('google', 'gemini') else {}),
                        }
                        for i, tc in enumerate(tool_calls)
                    ],
                })
            elif msg_type in ('text', 'summary', 'media_context'):
                # Summaries and turn-local media instructions are text messages
                # with distinct internal types so context shaping can preserve them.
                msg = {'role': role, 'content': content}
                # Add tool_call_id for tool role messages (OpenAI format)
                if role == 'tool' and tool_call_id:
                    msg['tool_call_id'] = tool_call_id
                formatted_messages.append(msg)
            elif msg_type == 'image' and llm.is_multimodal:
                # content may be a PIL image (screenshot tool), a data: URI, or a
                # path to a user-uploaded image on disk.
                url = LLMManager._image_url_from_content(content)
                if url:
                    caption = ('This is the result of the latest screenshot'
                               if not isinstance(content, str) else 'User-provided image')
                    formatted_messages.append({
                        'role': role,
                        'content': [
                            {'type': 'text', 'text': caption},
                            {'type': 'image_url', 'image_url': {'url': url}},
                        ],
                    })
                else:
                    formatted_messages.append({
                        'role': role,
                        'content': '[An image was provided but could not be loaded.]',
                    })
            elif msg_type == 'image':
                # Non-multimodal model: don't silently drop the image — leave a text
                # placeholder so the turn keeps context, and warn.
                logger.warning("Dropping image content for non-multimodal model '%s'", llm.model)
                formatted_messages.append({
                    'role': role,
                    'content': '[An image/screenshot was provided but this model cannot view images.]',
                })
        return formatted_messages

    @staticmethod
    def load_llm(provider: str | dict[str, Any]) -> LLM | None:
        """Load LLM with caching to avoid repeated instantiation."""
        cache_key = json.dumps(provider, sort_keys=True) if isinstance(provider, dict) else provider

        if not hasattr(LLMManager, '_llm_cache'):
            LLMManager._llm_cache = {}

        if cache_key in LLMManager._llm_cache:
            cached = LLMManager._llm_cache[cache_key]
            if isinstance(cached, LLM):
                # Deep copy: callers mutate the returned instance (temperature/model,
                # and potentially the extra_headers/extra_body dicts). A shallow copy
                # would share those nested dicts with the cached instance.
                return cached.model_copy(deep=True)

        try:
            if isinstance(provider, dict):
                llm = LLM(**provider)
            else:
                llm = LLM(provider=str(provider))
            LLMManager._llm_cache[cache_key] = llm
            return llm.model_copy(deep=True)
        except Exception as e:
            logging.exception(e)
            return None

    @staticmethod
    def get_supported_models(llm: LLM):
        if llm.provider == 'openrouter':
            return _openrouter_list_models(llm.api_key)
        assert_key_destination_allowed(llm.base_url, llm.api_key)
        client = OpenAI(api_key=llm.api_key, base_url=llm.base_url)
        return client.models.list()

    @staticmethod
    async def generate_response(
        llm: LLM, prompt: list[dict[str, Any]], stream: bool = False, tools: Any = None, **kwds: Any
    ):
        if tools is None:
            tools = []
        try:
            from cognitrix.config import settings

            # Groq and Ollama: direct. Others: Helicone.
            use_helicone = (
                llm.provider not in ('groq', 'ollama')
                and settings.has_api_key('helicone')
                and (settings.helicone_base_url or 'https://gateway.helicone.ai/v1')
            )
            helicone_base = settings.helicone_base_url or 'https://gateway.helicone.ai/v1'

            if llm.provider == 'openrouter':
                helicone_base = helicone_base.replace('v1', 'api/v1')

            if llm.provider in ('google', 'gemini'):
                helicone_base = helicone_base + 'beta/openai/v1'

            # Re-checked here because an LLM's fields can change after it
            # was constructed (and stored agents predate the check).
            assert_key_destination_allowed(llm.base_url, llm.api_key)

            if use_helicone:
                headers: dict[str, str] = {
                    'Helicone-Auth': f'Bearer {settings.get_api_key("helicone")}',
                    'Helicone-Target-Url': llm.base_url,
                    'Helicone-Target-Provider': llm.provider.upper(),
                }
                if llm.extra_headers:
                    for k, v in llm.extra_headers.items():
                        headers[k] = str(v)
                client = _get_or_create_client(helicone_base, llm.api_key, headers)
            else:
                default_headers = dict(llm.extra_headers) if llm.extra_headers else None
                client = _get_or_create_client(llm.base_url, llm.api_key, default_headers)
            formatted_messages = LLMManager.format_query(llm, prompt)
            formatted_tools = tools if tools else None

            completion_params: dict[str, Any] = {
                'model': llm.model,
                'messages': formatted_messages,
                'max_tokens': llm.max_tokens,
                'temperature': llm.temperature,
                'stream': stream,
            }
            if stream:
                # Ask for real token usage on the final stream chunk.
                completion_params['stream_options'] = {'include_usage': True}
            if formatted_tools:
                completion_params['tools'] = formatted_tools
            response_format = kwds.get('response_format', llm.response_format)
            if response_format:
                completion_params['response_format'] = response_format
            if llm.extra_body:
                completion_params['extra_body'] = llm.extra_body
            completion_params = _apply_provider_param_rules(llm, completion_params)

            if stream:
                return LLMManager._handle_streaming_response(client, completion_params)
            return await LLMManager._handle_non_streaming_response(client, completion_params)

        except ExecutionControlError:
            raise
        except openai.OpenAIError as e:
            logger.error(f"OpenAI API error: {str(e)}")
            msg = f"Error: OpenAI API encountered an issue - {str(e)}"
            return LLMResponse(llm_response=msg, error=msg)
        except TimeoutError:
            logger.error("Request to OpenAI API timed out")
            msg = "Error: Request timed out. Please try again later."
            return LLMResponse(llm_response=msg, error=msg)
        except Exception as e:
            logger.exception(f"Unexpected error in LLM generate_response: {str(e)}")
            msg = f"An unexpected error occurred: {str(e)}"
            return LLMResponse(llm_response=msg, error=msg)

    @staticmethod
    def _get_reasoning_from_delta(delta) -> str | None:
        """Extract reasoning from delta (provider-agnostic)."""
        if delta is None:
            return None
        for key in ('reasoning_content', 'reasoning', 'thinking'):
            val = getattr(delta, key, None)
            if val is not None and val != '':
                return str(val)
        return None

    @staticmethod
    def _parse_native_tool_calls(tool_calls: list[Any] | None, truncated: bool = False) -> list[dict[str, Any]]:
        """
        Map OpenAI-style message.tool_calls to [{name, arguments, tool_call_id}] for agent.call_tools.
        arguments is parsed from JSON string to dict.
        """
        if not tool_calls:
            return []
        result: list[dict[str, Any]] = []
        for tc in tool_calls:
            fn = getattr(tc, 'function', None) or (tc.get('function') if isinstance(tc, dict) else None)
            if not fn:
                continue
            name = getattr(fn, 'name', None) or (fn.get('name') if isinstance(fn, dict) else None)
            args_raw = getattr(fn, 'arguments', None) or (fn.get('arguments') if isinstance(fn, dict) else None)
            tool_call_id = getattr(tc, 'id', None) or (tc.get('id') if isinstance(tc, dict) else None)
            if not name:
                continue
            args: dict[str, Any] = {}
            args_error = None
            if args_raw:
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                except json.JSONDecodeError:
                    logger.warning("Tool call '%s' had malformed JSON arguments. Raw: %r", name, args_raw)
                    args_error = _malformed_arguments_error(name, args_raw, truncated)
            if not isinstance(args, dict) and args_error is None:
                args_error = _malformed_arguments_error(name, args_raw, truncated)
                args = {}
            parsed = {'name': name, 'arguments': args if args_error is None else {}, 'tool_call_id': tool_call_id}
            if args_error:
                # The tool is not run; the model gets this error instead of a
                # misleading "Missing required parameter" from empty args.
                parsed['arguments_error'] = args_error
            # Gemini's OpenAI-compat layer attaches a thought_signature under
            # extra_content that MUST be echoed back on the re-prompt.
            extra = getattr(tc, 'extra_content', None) or (tc.get('extra_content') if isinstance(tc, dict) else None)
            if extra:
                parsed['extra_content'] = extra
            result.append(parsed)
        return result

    @staticmethod
    async def _handle_streaming_response(client: AsyncOpenAI, params: dict[str, Any]):
        try:
            stream = await _create_with_retry(client, params)
            response = LLMResponse()
            in_reasoning = False
            finish_reason = None
            # Accumulate partial tool calls by index for streaming
            tool_call_accum: dict[int, dict[str, Any]] = {}
            async for chunk in stream:
                chunk_usage = getattr(chunk, 'usage', None)
                if chunk_usage:
                    response.usage = {
                        'prompt_tokens': chunk_usage.prompt_tokens or 0,
                        'completion_tokens': chunk_usage.completion_tokens or 0,
                    }
                if not chunk.choices:
                    continue
                finish_reason = getattr(chunk.choices[0], 'finish_reason', None) or finish_reason
                delta = chunk.choices[0].delta
                reasoning = LLMManager._get_reasoning_from_delta(delta)
                content = delta.content if hasattr(delta, 'content') else None
                # Accumulate delta.tool_calls
                delta_tc = getattr(delta, 'tool_calls', None) or []
                for dtc in delta_tc:
                    idx = getattr(dtc, 'index', None)
                    if idx is None and isinstance(dtc, dict):
                        idx = dtc.get('index')
                    tc_id = getattr(dtc, 'id', None) or (dtc.get('id') if isinstance(dtc, dict) else None)
                    if idx is None:
                        # Some OpenAI-compat endpoints (e.g. Gemini) stream tool
                        # calls without an index: a delta carrying an id starts a
                        # new call; one without continues the last.
                        idx = len(tool_call_accum) if (tc_id or not tool_call_accum) else max(tool_call_accum)
                    if idx not in tool_call_accum:
                        tool_call_accum[idx] = {'name': '', 'arguments': '', 'tool_call_id': None}
                    extra = getattr(dtc, 'extra_content', None) or (dtc.get('extra_content') if isinstance(dtc, dict) else None)
                    if extra:
                        tool_call_accum[idx]['extra_content'] = extra
                    fn = getattr(dtc, 'function', None) or (dtc.get('function') if isinstance(dtc, dict) else None)
                    if fn:
                        n = getattr(fn, 'name', None) or (fn.get('name') if isinstance(fn, dict) else None)
                        a = getattr(fn, 'arguments', None) or (fn.get('arguments') if isinstance(fn, dict) else None)
                        if n:
                            tool_call_accum[idx]['name'] = (tool_call_accum[idx]['name'] or '') + (n or '')
                        if a:
                            tool_call_accum[idx]['arguments'] = (tool_call_accum[idx]['arguments'] or '') + (a or '')
                    if tc_id:
                        tool_call_accum[idx]['tool_call_id'] = tc_id
                if reasoning:
                    response.add_reasoning_chunk(reasoning)
                    if not in_reasoning:
                        response.current_chunk = '<think>' + reasoning
                        in_reasoning = True
                    else:
                        response.current_chunk = reasoning
                    yield response
                if content:
                    emit_chunk = content
                    if in_reasoning:
                        emit_chunk = '\n</think>\n' + content
                        in_reasoning = False
                    response.add_chunk(content)
                    response.current_chunk = emit_chunk
                    yield response
            # Finalize native tool calls from accumulated fragments
            if tool_call_accum:
                sorted_items = sorted(tool_call_accum.items())
                parsed = []
                for _idx, acc in sorted_items:
                    name = (acc.get('name') or '').strip()
                    args_raw = acc.get('arguments') or '{}'
                    args_error = None
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                    except json.JSONDecodeError:
                        args = {}
                        if name:
                            logger.warning("Streamed tool call '%s' had malformed JSON arguments. Raw: %r", name, args_raw)
                            args_error = _malformed_arguments_error(name, args_raw, finish_reason == 'length')
                    if not isinstance(args, dict):
                        args_error = _malformed_arguments_error(name, args_raw, finish_reason == 'length')
                        args = {}
                    if name:
                        item = {'name': name, 'arguments': args, 'tool_call_id': acc.get('tool_call_id')}
                        if args_error:
                            item['arguments_error'] = args_error
                        if acc.get('extra_content'):
                            item['extra_content'] = acc['extra_content']
                        parsed.append(item)
                response.tool_calls = parsed
            response.finish_reason = finish_reason
            record_usage(getattr(response, 'usage', None))
            # Final yield delivers tool_calls/finalization only — clear
            # current_chunk so consumers don't print the last chunk twice.
            response.current_chunk = '\n</think>\n' if in_reasoning else ''
            yield response
        except ExecutionControlError:
            raise
        except Exception as e:
            logger.exception(f"Error in streaming response: {str(e)}")
            msg = f"Streaming error: {str(e)}"
            err = LLMResponse(llm_response=msg, error=msg)
            # Streaming consumers print current_chunk — without it the error
            # is invisible to the user.
            err.current_chunk = msg
            yield err

    @staticmethod
    def _get_reasoning_from_message(msg) -> str | None:
        """Extract reasoning from message (provider-agnostic)."""
        if msg is None:
            return None
        for key in ('reasoning_content', 'reasoning', 'thinking'):
            val = getattr(msg, key, None)
            if val is not None and val != '':
                return str(val)
        return None

    @staticmethod
    async def _handle_non_streaming_response(client: AsyncOpenAI, params: dict[str, Any]):
        try:
            # Retry transient errors (rate limit / timeout / connection / 5xx) with
            # backoff; other errors (e.g. 400 invalid request) fail fast.
            response = await _create_with_retry(client, params)
            msg = response.choices[0].message
            finish_reason = getattr(response.choices[0], 'finish_reason', None)
            content = msg.content or ""
            reasoning = LLMManager._get_reasoning_from_message(msg)
            native_tool_calls = LLMManager._parse_native_tool_calls(
                getattr(msg, 'tool_calls', None), truncated=finish_reason == 'length'
            )
            llm_resp = LLMResponse()
            llm_resp.finish_reason = finish_reason
            if reasoning:
                llm_resp.add_reasoning_chunk(reasoning)
                llm_resp.add_chunk(f'<think>{reasoning}</think>\n\n{content}')
            else:
                llm_resp.add_chunk(content)
            if native_tool_calls:
                llm_resp.tool_calls = native_tool_calls
            resp_usage = getattr(response, 'usage', None)
            if resp_usage:
                llm_resp.usage = {
                    'prompt_tokens': resp_usage.prompt_tokens or 0,
                    'completion_tokens': resp_usage.completion_tokens or 0,
                }
                record_usage(llm_resp.usage)
            return llm_resp
        except ExecutionControlError:
            raise
        except Exception as e:
            logger.exception(f"Error in non-streaming response: {str(e)}")
            msg = f"Response error: {str(e)}"
            return LLMResponse(llm_response=msg, error=msg)


def _openrouter_list_models(api_key: str) -> list[str]:
    """Fetch model ids from OpenRouter API."""
    try:
        import requests
        r = requests.get(
            'https://openrouter.ai/api/v1/models',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            timeout=10,
        )
        if r.status_code == 200:
            return [e['id'] for e in r.json().get('data', [])]
    except Exception as e:
        logger.exception(e)
    return []
