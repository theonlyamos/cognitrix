"""
Provider-agnostic OpenAI-compatible LLM layer.

Runtime config (provider, base_url, api_key, model) from env or CLI.
Groq and Ollama: direct. Others: route through Helicone.
"""
import hashlib
import json
import logging
import os
import uuid
from typing import Any, TypeAlias

import openai
from odbms import Model
from openai import OpenAI
from pydantic import Field

from cognitrix.utils import image_to_base64
from cognitrix.utils.llm_response import LLMResponse

# Cache OpenAI clients by effective config to avoid per-request overhead
_client_cache: dict[str, OpenAI] = {}

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
    'cerebras': 'https://api.cerebras.com/v1',
    'google': 'https://generativelanguage.googleapis.com/v1beta/',
}


def _env_key(provider: str, suffix: str) -> str:
    """Derive env key from provider: e.g. OPENROUTER_BASE_URL."""
    return f"{provider.upper()}_{suffix}"


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
        or ''
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


def _client_cache_key(base_url: str, api_key: str, headers: dict[str, str] | None) -> str:
    """Stable cache key for OpenAI client config."""
    payload = {'base_url': base_url, 'api_key': api_key, 'headers': headers or {}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _get_or_create_client(
    base_url: str, api_key: str, default_headers: dict[str, str] | None = None
) -> OpenAI:
    """Reuse OpenAI client per effective config."""
    key = _client_cache_key(base_url, api_key, default_headers)
    if key not in _client_cache:
        kwargs: dict[str, Any] = {'api_key': api_key, 'base_url': base_url}
        if default_headers:
            kwargs['default_headers'] = default_headers
        _client_cache[key] = OpenAI(**kwargs)
    return _client_cache[key]


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

    def format_query(self, messages: list[dict[str, Any]]) -> list:
        return LLMManager.format_query(self, messages)

    def format_tools(self, tools: list[dict[str, Any]]):
        return LLMManager.format_tools(tools)

    @staticmethod
    def load_llm(provider: str | dict[str, Any]) -> 'LLM | None':
        return LLMManager.load_llm(provider)

    def get_supported_models(self):
        return LLMManager.get_supported_models(self)

    async def __call__(self, prompt: list[dict[str, Any]], stream: bool = False, tools: Any = None, **kwds: Any):
        if tools is None:
            tools = []
        return await LLMManager.generate_response(self, prompt, stream, tools, **kwds)


LLMList: TypeAlias = list[LLM]


class LLMManager:
    """Manager for provider-agnostic LLM logic."""

    @staticmethod
    def _normalize_role(role: str) -> str:
        """Map role variants to canonical system/user/assistant for API compatibility."""
        r = (role or '').strip().lower()
        if r in ('system',):
            return 'system'
        if r in ('user',):
            return 'user'
        # Assistant, agent names, and any other non-user role -> assistant
        return 'assistant'

    @staticmethod
    def format_query(llm: LLM, messages: list[dict[str, Any]]) -> list:
        formatted_messages = []
        for fm in messages:
            role = LLMManager._normalize_role(fm.get('role', 'user'))
            content = fm.get('content', '')
            msg_type = fm.get('type', 'text')
            if msg_type == 'text':
                formatted_messages.append({'role': role, 'content': content})
            elif msg_type == 'image' and llm.is_multimodal:
                base64_image = image_to_base64(content)  # type: ignore
                formatted_messages.append({
                    'role': role,
                    'content': [
                        {'type': 'text', 'text': 'This is the result of the latest screenshot'},
                        {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{base64_image}'}},
                    ],
                })
        return formatted_messages

    @staticmethod
    def format_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatted_tools = []
        for tool in tools:
            f_tool = {
                'type': 'function',
                'function': {
                    'name': tool['function']['name'].replace(' ', '_'),
                    'description': tool['function']['description'][:1024],
                    'parameters': {
                        'type': 'object',
                        'properties': {},
                        'required': tool['function']['parameters']['required'],
                    },
                },
            }
            for key, value in tool['function']['parameters']['properties'].items():
                f_tool['function']['parameters']['properties'][key] = {'type': value}
            formatted_tools.append(f_tool)
        return formatted_tools

    @staticmethod
    def load_llm(provider: str | dict[str, Any]) -> LLM | None:
        try:
            if isinstance(provider, dict):
                return LLM(**provider)
            return LLM(provider=str(provider))
        except Exception as e:
            logging.exception(e)
            return None

    @staticmethod
    def get_supported_models(llm: LLM):
        if llm.provider == 'openrouter':
            return _openrouter_list_models(llm.api_key)
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
                helicone_base = helicone_base + 'beta'

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
            formatted_tools = LLMManager.format_tools(tools) if tools else None

            completion_params: dict[str, Any] = {
                'model': llm.model,
                'messages': formatted_messages,
                'max_tokens': llm.max_tokens,
                'temperature': llm.temperature,
                'stream': stream,
            }
            if formatted_tools:
                completion_params['tools'] = formatted_tools
            if llm.response_format:
                completion_params['response_format'] = llm.response_format
            if llm.extra_body:
                completion_params['extra_body'] = llm.extra_body

            if stream:
                return LLMManager._handle_streaming_response(client, completion_params)
            return await LLMManager._handle_non_streaming_response(client, completion_params)

        except openai.OpenAIError as e:
            logger.error(f"OpenAI API error: {str(e)}")
            return LLMResponse(llm_response=f"Error: OpenAI API encountered an issue - {str(e)}")
        except TimeoutError:
            logger.error("Request to OpenAI API timed out")
            return LLMResponse(llm_response="Error: Request timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in LLM generate_response: {str(e)}")
            return LLMResponse(llm_response=f"An unexpected error occurred: {str(e)}")

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
    async def _handle_streaming_response(client: OpenAI, params: dict[str, Any]):
        try:
            stream = client.chat.completions.create(**params)
            response = LLMResponse()
            in_reasoning = False
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = LLMManager._get_reasoning_from_delta(delta)
                content = delta.content if hasattr(delta, 'content') else None
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
            if in_reasoning:
                response.current_chunk = '\n</think>\n'
                yield response
        except Exception as e:
            logger.exception(f"Error in streaming response: {str(e)}")
            yield LLMResponse(llm_response=f"Streaming error: {str(e)}")

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
    async def _handle_non_streaming_response(client: OpenAI, params: dict[str, Any]):
        try:
            response = client.chat.completions.create(**params)
            msg = response.choices[0].message
            content = msg.content or ""
            reasoning = LLMManager._get_reasoning_from_message(msg)
            if reasoning:
                llm_resp = LLMResponse(llm_response=f'<think>{reasoning}</think>\n\n{content}')
                llm_resp.reasoning = reasoning
            else:
                llm_resp = LLMResponse(llm_response=content)
            return llm_resp
        except Exception as e:
            logger.exception(f"Error in non-streaming response: {str(e)}")
            return LLMResponse(llm_response=f"Response error: {str(e)}")


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
