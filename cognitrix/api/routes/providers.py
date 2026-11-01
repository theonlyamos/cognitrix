import asyncio
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cognitrix.agents.access import is_admin
from cognitrix.common.security import AuthContext, crud_scope, jwt_only, redact_secrets

from ...providers import LLM

providers_api = APIRouter(
    prefix='/providers',
    dependencies=[Depends(crud_scope)],
)

# Same list as the frontend's PROVIDERS and onboarding's SETUP_PROVIDERS.
KEY_PROVIDERS = ('openrouter', 'openai', 'anthropic', 'google', 'groq', 'cerebras', 'ollama')
KEYLESS = frozenset({'ollama'})
TEST_TIMEOUT = 12


def _llm_to_dict(llm) -> dict:
    """Serialize LLM to dict (Pydantic v1/v2 compatible)."""
    return getattr(llm, 'model_dump', getattr(llm, 'dict', lambda: {}))()


def _known(provider: str) -> str:
    provider = provider.strip().lower()
    if provider not in KEY_PROVIDERS:
        raise HTTPException(status_code=404, detail='Unknown provider')
    return provider


def _forget_cached_llms() -> None:
    """Drop this process's resolved LLMs so new ones pick up the change."""
    from cognitrix.providers.base import LLMManager

    LLMManager._llm_cache = {}


def _key_row(provider: str) -> dict[str, Any]:
    from cognitrix.providers.base import _DEFAULT_BASE_URLS, _env_key
    from cognitrix.providers.saved_keys import env_key, get_saved_key

    env_value = env_key(provider)
    saved = get_saved_key(provider)
    if provider in KEYLESS:
        source = 'local'
    elif env_value:
        source = 'env'
    elif saved:
        source = 'saved'
    else:
        source = 'none'
    return {
        'id': provider,
        'source': source,
        'env_var': _env_key(provider, 'API_KEY'),
        'hint': f'…{saved[-4:]}' if source == 'saved' else None,
        'base_url': os.getenv(_env_key(provider, 'BASE_URL')) or _DEFAULT_BASE_URLS.get(provider, ''),
    }


@providers_api.get('/keys')
async def list_provider_keys(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Which providers have a key, and where it comes from. Never the key itself."""
    return {
        'providers': [_key_row(p) for p in KEY_PROVIDERS],
        'can_edit': await is_admin(ctx.user),
    }


class KeyBody(BaseModel):
    api_key: str


@providers_api.put('/keys/{provider}')
async def save_provider_key(provider: str, body: KeyBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.providers.saved_keys import env_key, set_saved_key

    provider = _known(provider)
    if not await is_admin(ctx.user):
        raise HTTPException(status_code=403, detail='Only an admin can change the engine\'s provider keys.')
    if provider in KEYLESS:
        raise HTTPException(status_code=400, detail=f'{provider} runs locally and needs no key.')
    if env_key(provider):
        raise HTTPException(status_code=409, detail='This key comes from the engine\'s environment. Change it in .env instead.')
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail='Paste a key first.')
    try:
        set_saved_key(provider, body.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _forget_cached_llms()
    return _key_row(provider)


@providers_api.delete('/keys/{provider}')
async def remove_provider_key(provider: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.providers.saved_keys import set_saved_key

    provider = _known(provider)
    if not await is_admin(ctx.user):
        raise HTTPException(status_code=403, detail='Only an admin can change the engine\'s provider keys.')
    set_saved_key(provider, '')
    _forget_cached_llms()
    return _key_row(provider)


def _probe(provider: str) -> dict[str, Any]:
    """List the provider's models with the key the engine would use."""
    from openai import OpenAI

    from cognitrix.providers.base import _resolve_runtime_config

    try:
        config = _resolve_runtime_config(provider)
    except ValueError as exc:
        return {'ok': False, 'message': str(exc)}
    headers = {'x-api-key': config['api_key'], 'anthropic-version': '2023-06-01'} if provider == 'anthropic' else None
    client = OpenAI(api_key=config['api_key'], base_url=config['base_url'], timeout=TEST_TIMEOUT, max_retries=0,
                    default_headers=headers)
    try:
        models = client.models.list()
        count = len(list(getattr(models, 'data', []) or []))
    except Exception as exc:  # noqa: BLE001 - any failure is reported to the user
        status = getattr(exc, 'status_code', None)
        if status in (401, 403):
            return {'ok': False, 'message': 'The provider rejected this key.'}
        if provider in KEYLESS:
            return {'ok': False, 'message': f"Couldn't reach Ollama at {config['base_url']}. Is it running?"}
        return {'ok': False, 'message': f"Couldn't reach {provider}: {type(exc).__name__}."}
    return {'ok': True, 'message': f'Works. {count} models available.' if count else 'Works.'}


@providers_api.post('/keys/{provider}/test')
async def test_provider_key(provider: str, _ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    provider = _known(provider)
    return await asyncio.to_thread(_probe, provider)


@providers_api.get('/{provider_name}')
async def load_provider(provider_name: str):
    provider = LLM.load_llm(provider_name)
    response: dict = _llm_to_dict(provider) if provider else {}
    # Never return the provider api_key to the client.
    return JSONResponse(redact_secrets(response))
