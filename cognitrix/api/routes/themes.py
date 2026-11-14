"""Settings > Appearance: the person's themes and which are in use.

GET  /api/theme                  the state (mode, light, dark, themes)
PUT  /api/theme                  pick themes / mode
PUT  /api/theme/themes/{id}      add or replace one of your themes
DELETE /api/theme/themes/{id}    delete one (picks fall back to Signal)
PUT  /api/theme/artwork/{id}     choose a theme's artwork ({id} built-in or 'none', or {svg, name})
DELETE /api/theme/artwork/{id}   go back to the theme's own artwork

See ``cognitrix/themes.py``. Agents change the same state with Set Theme.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from cognitrix import themes
from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, jwt_only

themes_api = APIRouter(prefix='/theme')


class Selection(BaseModel):
    mode: str | None = None
    light: str | None = None
    dark: str | None = None


def _owner(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


def _bad(exc: themes.ThemeError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@themes_api.get('')
async def get_theme(ctx: AuthContext = Depends(jwt_only)):
    return themes.project(await themes.get_settings(_owner(ctx)))


@themes_api.put('')
async def put_theme(body: Selection, ctx: AuthContext = Depends(jwt_only)):
    try:
        return await themes.set_selection(_owner(ctx), mode=body.mode, light=body.light, dark=body.dark)
    except themes.ThemeError as exc:
        raise _bad(exc) from None


@themes_api.put('/themes/{theme_id}')
async def put_custom_theme(theme_id: str, body: dict[str, Any] = Body(...), ctx: AuthContext = Depends(jwt_only)):
    try:
        theme, state = await themes.save_theme(_owner(ctx), body, theme_id=theme_id)
    except themes.ThemeError as exc:
        raise _bad(exc) from None
    return {'theme': theme, **state}


@themes_api.delete('/themes/{theme_id}')
async def delete_custom_theme(theme_id: str, ctx: AuthContext = Depends(jwt_only)):
    return await themes.delete_theme(_owner(ctx), theme_id)


@themes_api.put('/artwork/{theme_id}')
async def put_artwork(theme_id: str, body: dict[str, Any] = Body(...), ctx: AuthContext = Depends(jwt_only)):
    try:
        return await themes.set_artwork(_owner(ctx), theme_id, body)
    except themes.ThemeError as exc:
        raise _bad(exc) from None


@themes_api.delete('/artwork/{theme_id}')
async def delete_artwork(theme_id: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        return await themes.set_artwork(_owner(ctx), theme_id, None)
    except themes.ThemeError as exc:
        raise _bad(exc) from None
