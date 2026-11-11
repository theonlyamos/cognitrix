"""Background computer use: a person's standing answers per app.

``GET /computer/apps`` lists the apps a person set to "always allow" or
"never", and says whether background computer use works on this machine.
``PUT /computer/apps`` saves one answer ('ask' forgets it). Browser sessions only.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.computer import apps as computer_apps

computer_api = APIRouter(prefix='/computer')

_APP_NAME = re.compile(r'^[\w .+\-()]{1,120}$')


class AppLevelBody(BaseModel):
    app: str = Field(min_length=1, max_length=120)
    level: str


def _owner(ctx: AuthContext) -> str:
    # Same key the chat turn uses for approvals (see routes/agents._user_key).
    user = ctx.user
    return str(getattr(user, 'id', None) or getattr(user, 'email', 'anon'))


def _available() -> bool:
    try:
        from cognitrix.computer import get_driver

        get_driver()
        return True
    except Exception:
        return False


@computer_api.get('/apps')
async def list_apps(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    return {'available': _available(), 'apps': await computer_apps.list_levels(_owner(ctx))}


@computer_api.put('/apps')
async def set_app(body: AppLevelBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    app = computer_apps.app_key(body.app)
    if not _APP_NAME.match(app):
        raise HTTPException(status_code=400, detail="That doesn't look like an app name, e.g. notepad.exe.")
    try:
        await computer_apps.set_level(_owner(ctx), app, body.level)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {'apps': await computer_apps.list_levels(_owner(ctx))}
