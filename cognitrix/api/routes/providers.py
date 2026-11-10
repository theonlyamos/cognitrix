import asyncio
import logging
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

# Same list as the frontend's PROVIDERS and onboarding's SETUP_PROVIDERS, less
# 'chatgpt', which signs in instead of taking a key (the /chatgpt/plan routes).
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


# Model ids that aren't chat models (OpenAI and OpenRouter list everything).
_NOT_CHAT = ('embed', 'whisper', 'tts', 'dall-e', 'davinci', 'babbage', 'moderation',
             'transcribe', 'audio', 'realtime', 'image', 'imagen', 'veo', 'sora', 'aqa', 'guard')
_MODELS_TTL = 600
_MAX_LISTED_MODELS = 80
_models_cache: dict[str, tuple[float, list[str]]] = {}


def _list_models(provider: str) -> list[str]:
    """Chat models the provider offers this key, newest first."""
    from openai import OpenAI

    from cognitrix.providers.base import _resolve_runtime_config

    config = _resolve_runtime_config(provider)
    headers = {'x-api-key': config['api_key'], 'anthropic-version': '2023-06-01'} if provider == 'anthropic' else None
    client = OpenAI(api_key=config['api_key'], base_url=config['base_url'], timeout=TEST_TIMEOUT, max_retries=0,
                    default_headers=headers)
    rows = list(getattr(client.models.list(), 'data', []) or [])

    def created(row) -> float:
        value = getattr(row, 'created', None) or getattr(row, 'created_at', None) or 0
        if isinstance(value, str):
            from datetime import datetime
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
            except ValueError:
                return 0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0

    rows.sort(key=created, reverse=True)
    out: list[str] = []
    for row in rows:
        model_id = str(getattr(row, 'id', '') or '').strip()
        if provider == 'google' and model_id.startswith('models/'):
            model_id = model_id[len('models/'):]
        if not model_id or model_id in out or any(word in model_id.lower() for word in _NOT_CHAT):
            continue
        out.append(model_id)
        if len(out) >= _MAX_LISTED_MODELS:
            break
    return out


@providers_api.get('/{provider}/models')
async def provider_models(provider: str, refresh: bool = False,
                          _ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Models the provider lists for the engine's key, for the model pickers.

    Empty (with a reason) when there's no key or the provider can't be reached;
    the picker then shows its built-in list only.
    """
    import time

    provider = provider.strip().lower()
    if provider == 'chatgpt':
        return await _plan_models(refresh)
    provider = _known(provider)
    cached = _models_cache.get(provider)
    if cached and not refresh and time.monotonic() - cached[0] < _MODELS_TTL:
        return {'models': cached[1], 'live': True}
    try:
        models = await asyncio.to_thread(_list_models, provider)
    except Exception:  # noqa: BLE001 - the picker falls back to its own list
        # Keep error details (which can include keys or URLs) in the log only.
        logging.getLogger(__name__).info('Listing %s models failed', provider, exc_info=True)
        return {'models': [], 'live': False, 'reason': f"Couldn't list {provider} models"}
    _models_cache[provider] = (time.monotonic(), models)
    return {'models': models, 'live': True}


# --- ChatGPT plan (Sign in with ChatGPT) ---------------------------------------
# A ChatGPT Plus/Pro plan in place of an API key; see providers/chatgpt_plan.py.

def _plan_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


async def _plan_admin(ctx: AuthContext) -> None:
    if not await is_admin(ctx.user):
        raise HTTPException(status_code=403, detail='Only an admin can connect a ChatGPT plan to this engine.')


async def _plan_models(refresh: bool) -> dict[str, Any]:
    import time

    from cognitrix.providers import chatgpt_plan

    cached = _models_cache.get('chatgpt')
    if cached and not refresh and time.monotonic() - cached[0] < _MODELS_TTL:
        return {'models': cached[1], 'live': True}
    try:
        models = [m['slug'] for m in await chatgpt_plan.list_models()]
    except Exception:  # noqa: BLE001 - the picker falls back to its own list
        logging.getLogger(__name__).info('Listing ChatGPT plan models failed', exc_info=True)
        return {'models': [], 'live': False, 'reason': "Couldn't list your ChatGPT plan's models"}
    _models_cache['chatgpt'] = (time.monotonic(), models)
    return {'models': models, 'live': True}


@providers_api.get('/chatgpt/plan')
async def chatgpt_plan_status(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.agents.access import owner_key
    from cognitrix.providers import chatgpt_plan

    info = await asyncio.to_thread(chatgpt_plan.status)
    owner = info.pop('owner_id', None)
    info['yours'] = bool(owner) and owner == owner_key(ctx.user)
    info['can_edit'] = await is_admin(ctx.user)
    return info


class PlanSignIn(BaseModel):
    new_account: bool = False


@providers_api.post('/chatgpt/plan/sign-in')
async def chatgpt_plan_sign_in(body: PlanSignIn, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.agents.access import owner_key
    from cognitrix.providers import chatgpt_plan

    await _plan_admin(ctx)
    try:
        return await chatgpt_plan.start_sign_in(owner_key(ctx.user), new_account=body.new_account)
    except chatgpt_plan.ChatGPTPlanError as exc:
        raise _plan_error(exc) from exc


@providers_api.get('/chatgpt/plan/flows/{flow_id}')
async def chatgpt_plan_flow(flow_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.agents.access import owner_key
    from cognitrix.providers import chatgpt_plan

    found = chatgpt_plan.flow_status(flow_id, owner_key(ctx.user))
    if found is None:
        raise HTTPException(status_code=404, detail='That sign-in has ended. Start again.')
    return found


class PastedAddress(BaseModel):
    url: str


@providers_api.post('/chatgpt/plan/flows/{flow_id}/address')
async def chatgpt_plan_pasted(flow_id: str, body: PastedAddress, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Finish a sign-in from the address the browser ended up on.

    Used when the engine runs on another computer, so the browser's
    127.0.0.1 page can't reach it.
    """
    from cognitrix.agents.access import owner_key
    from cognitrix.providers import chatgpt_plan

    try:
        found = await chatgpt_plan.finish_from_pasted_url(flow_id, owner_key(ctx.user), body.url)
    except chatgpt_plan.ChatGPTPlanError as exc:
        raise _plan_error(exc) from exc
    if found is None:
        raise HTTPException(status_code=404, detail='That sign-in has ended. Start again.')
    return found


@providers_api.delete('/chatgpt/plan')
async def chatgpt_plan_sign_out(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.providers import chatgpt_plan

    await _plan_admin(ctx)
    warning = None
    try:
        await chatgpt_plan.sign_out()
    except chatgpt_plan.ChatGPTPlanError as exc:
        warning = str(exc)
    _models_cache.pop('chatgpt', None)
    return {'ok': True, 'warning': warning}


@providers_api.post('/chatgpt/plan/test')
async def chatgpt_plan_test(_ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.providers import chatgpt_plan

    try:
        models = await chatgpt_plan.list_models()
    except chatgpt_plan.ChatGPTPlanError as exc:
        return {'ok': False, 'message': str(exc)}
    except Exception as exc:  # noqa: BLE001 - reported to the person
        return {'ok': False, 'message': f"Couldn't reach ChatGPT: {type(exc).__name__}."}
    count = len(models)
    return {'ok': True, 'message': f'Works. {count} models on your plan.' if count else 'Works.'}


@providers_api.get('/{provider_name}')
async def load_provider(provider_name: str):
    provider = LLM.load_llm(provider_name)
    response: dict = _llm_to_dict(provider) if provider else {}
    # Never return the provider api_key to the client.
    return JSONResponse(redact_secrets(response))
