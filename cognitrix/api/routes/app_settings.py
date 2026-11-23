"""App-wide settings (cognitrix/app_settings.py) for the Settings pages.

Anyone signed in may read them (secrets never leave the engine: only
whether one is set); only an admin may change them.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix import app_settings
from cognitrix.agents.access import is_admin
from cognitrix.common.security import AuthContext, crud_scope, jwt_only

app_settings_api = APIRouter(
    prefix='/settings/app',
    dependencies=[Depends(crud_scope)],
)

PAGES = ('models', 'people', 'apps', 'computers', 'notifications', 'advanced')


def _page(page: str | None) -> str | None:
    if page is None or page == '':
        return None
    if page not in PAGES:
        raise HTTPException(status_code=404, detail='Unknown settings page')
    return page


@app_settings_api.get('')
async def read_app_settings(page: str | None = None, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    groups = await asyncio.to_thread(app_settings.describe, _page(page))
    return {'groups': groups, 'can_edit': await is_admin(ctx.user)}


class SettingsBody(BaseModel):
    values: dict[str, str | bool | int | float | None]


@app_settings_api.put('')
async def save_app_settings(body: SettingsBody, page: str | None = None,
                            ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    if not await is_admin(ctx.user):
        raise HTTPException(status_code=403, detail='Only an admin can change these settings.')
    try:
        await asyncio.to_thread(app_settings.set_many, body.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if any(n.endswith('_BASE_URL') or n in ('AI_PROVIDER', 'COGNITRIX_TRUSTED_BASE_URLS') for n in body.values):
        from cognitrix.providers.base import LLMManager

        LLMManager._llm_cache = {}
    groups = await asyncio.to_thread(app_settings.describe, _page(page))
    return {'groups': groups, 'can_edit': True}
