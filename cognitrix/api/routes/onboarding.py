"""First-run setup: give each new user their own default assistant.

After a user signs in for the first time the app asks
``GET /onboarding``. When ``needs_setup`` is true it shows a setup dialog
where the user names the assistant and picks a provider and model, then
calls ``POST /onboarding/assistant``. ``POST /onboarding/skip`` closes the
dialog for good, creating the assistant with the suggested settings when the
user has no agent at all to chat with.

The assistant is an ordinary agent owned by the user, so it can be renamed,
re-pointed at another model or deleted from the Agents page later.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents import Agent
from cognitrix.agents import access as agent_access
from cognitrix.agents.templates import default_assistant_prompt
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only, redact_secrets
from cognitrix.models import User

from ...providers.base import (
    _DEFAULT_BASE_URLS,
    LLM,
    ProviderKeyDestinationError,
    normalize_reasoning_effort,
)

logger = logging.getLogger('cognitrix.log')

onboarding_api = APIRouter(prefix='/onboarding', dependencies=[Depends(jwt_only)])

DEFAULT_ASSISTANT_NAME = 'Assistant'
MAX_NAME_LENGTH = 60

# Providers offered in the setup dialog, in the order we suggest them when
# more than one has a key. Keep in step with PROVIDERS in
# frontend/src/lib/providers.ts.
SETUP_PROVIDERS = ('anthropic', 'openai', 'google', 'openrouter', 'groq', 'cerebras', 'ollama')
# Providers that run without an API key.
KEYLESS_PROVIDERS = frozenset({'ollama'})


class AssistantSetup(BaseModel):
    name: str = DEFAULT_ASSISTANT_NAME
    provider: str
    model: str
    api_key: str | None = None
    reasoning_effort: str | None = None


def _has_server_key(provider: str) -> bool:
    from cognitrix.config import settings

    if os.getenv(f'{provider.upper()}_API_KEY'):
        return True
    try:
        return bool(settings.get_api_key(provider))
    except Exception:
        return False


def _default_model(provider: str) -> str:
    from cognitrix.config import settings

    return os.getenv(f'{provider.upper()}_MODEL') or settings.get_default_model(provider)


def _provider_options() -> list[dict[str, Any]]:
    options = []
    for provider in SETUP_PROVIDERS:
        needs_key = provider not in KEYLESS_PROVIDERS
        options.append({
            'id': provider,
            'needs_key': needs_key,
            'has_key': needs_key and _has_server_key(provider),
            'default_model': _default_model(provider),
        })
    return options


def _suggested_provider(options: list[dict[str, Any]]) -> str:
    from cognitrix.config import settings

    with_key = [o['id'] for o in options if o['has_key']]
    preferred = str(getattr(settings, 'ai_provider', '') or '').lower()
    if preferred in with_key:
        return preferred
    if with_key:
        return with_key[0]
    return preferred if preferred in SETUP_PROVIDERS else SETUP_PROVIDERS[0]


async def _owned_agents(user_key: str) -> list[Agent]:
    agents = await Agent.find({'owner_id': user_key}) or []
    return [a for a in agents if not getattr(a, 'is_sub_agent', False)]


async def _needs_setup(user: User) -> bool:
    if getattr(user, 'onboarded_at', None):
        return False
    # Someone who already made agents before this existed doesn't need a
    # setup dialog.
    return not await _owned_agents(agent_access.owner_key(user))


async def _mark_onboarded(user: User) -> None:
    now = int(time.time())
    await User.update_one({'id': user.id}, {'onboarded_at': now})
    try:
        user.onboarded_at = now
    except Exception:
        pass


def _clean_name(raw: str | None) -> str:
    name = ' '.join(str(raw or '').split())
    if not name:
        return DEFAULT_ASSISTANT_NAME
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(status_code=400, detail=f'Keep the name under {MAX_NAME_LENGTH} characters.')
    return name


def _build_llm(setup: AssistantSetup) -> LLM:
    provider = setup.provider.strip().lower()
    if provider not in SETUP_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{setup.provider}'.")
    model = setup.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail='Choose a model.')

    llm_data: dict[str, Any] = {
        'provider': provider,
        'model': model,
        'base_url': os.getenv(f'{provider.upper()}_BASE_URL') or _DEFAULT_BASE_URLS.get(provider, ''),
    }
    api_key = (setup.api_key or '').strip()
    if api_key:
        llm_data['api_key'] = api_key
    elif provider not in KEYLESS_PROVIDERS and not _has_server_key(provider):
        raise HTTPException(
            status_code=400,
            detail=f'No API key for {provider}. Paste one, or pick a provider that has a key.',
        )
    try:
        if setup.reasoning_effort:
            llm_data['reasoning_effort'] = normalize_reasoning_effort(setup.reasoning_effort)
        llm = LLM(**llm_data)
    except (ProviderKeyDestinationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    llm.provider = provider
    return llm


async def _create_assistant(user: User, name: str, llm: LLM) -> Agent:
    from cognitrix.tools.base import ToolManager

    agent = Agent(
        name=name,
        llm=llm,
        system_prompt=default_assistant_prompt(getattr(user, 'name', None)),
        tools=list(ToolManager.list_all_tools()),
        owner_id=agent_access.owner_key(user),
    )
    await agent.save()
    logger.info('Created default assistant %s for user %s', agent.id, agent.owner_id)
    return agent


@onboarding_api.get('')
async def onboarding_status(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    options = _provider_options()
    provider = _suggested_provider(options)
    model = next((o['default_model'] for o in options if o['id'] == provider), '')
    return {
        'needs_setup': await _needs_setup(ctx.user),
        'has_agents': bool(await agent_access.visible_agents(agent_access.owner_key(ctx.user))),
        'suggested': {'name': DEFAULT_ASSISTANT_NAME, 'provider': provider, 'model': model},
        'providers': options,
    }


@onboarding_api.post('/assistant')
async def create_default_assistant(
    setup: AssistantSetup, ctx: AuthContext = Depends(get_auth_context)
) -> dict[str, Any]:
    if getattr(ctx.user, 'onboarded_at', None):
        raise HTTPException(
            status_code=409,
            detail='Setup is already done. Make more agents from the Agents page.',
        )
    name = _clean_name(setup.name)
    llm = _build_llm(setup)
    agent = await _create_assistant(ctx.user, name, llm)
    await _mark_onboarded(ctx.user)
    return {'agent': redact_secrets(agent.json())}


@onboarding_api.post('/skip')
async def skip_setup(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    """Close the setup dialog. A user with no agent to chat with still gets
    the assistant, using the suggested provider and model."""
    user = ctx.user
    if getattr(user, 'onboarded_at', None):
        return {'agent': None}
    agent = None
    if not await agent_access.visible_agents(agent_access.owner_key(user)):
        options = _provider_options()
        provider = _suggested_provider(options)
        model = next((o['default_model'] for o in options if o['id'] == provider), '')
        try:
            llm = _build_llm(AssistantSetup(provider=provider, model=model))
        except HTTPException:
            # No provider key yet: point it at a local Ollama, which needs
            # none, so the user still has an assistant to open and re-point
            # from the Agents page.
            llm = _build_llm(AssistantSetup(provider='ollama', model=_default_model('ollama')))
        agent = await _create_assistant(user, DEFAULT_ASSISTANT_NAME, llm)
    await _mark_onboarded(user)
    return {'agent': redact_secrets(agent.json()) if agent is not None else None}
