"""Connected-app screens beside the chat (MCP Apps). See ``cognitrix/mcp/app_views.py``.

Browser sessions only (``jwt_only``), except the sandboxed frame the screen
is shown in, which is reached with a short-lived token instead.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.mcp import app_views
from cognitrix.session_ownership import principal_key

app_views_api = APIRouter(prefix='/app-views')
app_view_frame_api = APIRouter()


def _me(ctx: AuthContext) -> str:
    return principal_key(ctx.user)


def _bad(exc: app_views.ViewError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


async def _view(view_id: str, ctx: AuthContext) -> app_views.AppView:
    view = await app_views.get_view(view_id, _me(ctx))
    if view is None:
        raise HTTPException(status_code=404, detail='This app screen was not found')
    return view


def _row(view: app_views.AppView) -> dict[str, Any]:
    return {
        'id': str(view.id), 'app': view.app_name, 'tool': view.tool_name, 'title': view.title,
        'session_id': view.session_id, 'agent_id': view.agent_id, 'version': view.version,
        'created': view.created, 'updated': view.updated,
    }


@app_views_api.get('')
async def list_views(session_id: str, ctx: AuthContext = Depends(jwt_only)):
    """The app screens a chat opened, newest first (the chat's side panel)."""
    return [_row(v) for v in await app_views.views_for(_me(ctx), session_id)]


@app_views_api.get('/{view_id}')
async def get_view(view_id: str, ctx: AuthContext = Depends(jwt_only)):
    """What a screen shows, and the frame to show it in."""
    view = await _view(view_id, ctx)
    try:
        _html, ui = await app_views.screen(view)
    except app_views.ViewError as exc:
        return {**_row(view), 'error': exc.message, 'frame_url': None,
                'tool_input': view.tool_input, 'tool_result': view.tool_result}
    return {
        **_row(view),
        'tool_input': view.tool_input,
        'tool_result': view.tool_result,
        'frame_url': f'/api/v1/app-view-frame/{app_views.frame_token(str(view.id), _me(ctx))}',
        'prefers_border': bool(ui.get('prefersBorder')) if isinstance(ui, dict) else False,
        'error': None,
    }


class CallBody(BaseModel):
    name: str
    arguments: dict[str, Any] = {}
    confirmed: bool = False


@app_views_api.post('/{view_id}/call')
async def call_tool(view_id: str, body: CallBody, ctx: AuthContext = Depends(jwt_only)):
    """``tools/call`` from the screen, on its own app only.

    409 ``confirm`` means the tool can change things: the panel asks the
    person and calls again with ``confirmed``."""
    view = await _view(view_id, ctx)
    try:
        return await app_views.call_from_screen(view, body.name, body.arguments, confirmed=body.confirmed)
    except app_views.ViewError as exc:
        raise _bad(exc)


class ReadBody(BaseModel):
    uri: str


@app_views_api.post('/{view_id}/read')
async def read_resource(view_id: str, body: ReadBody, ctx: AuthContext = Depends(jwt_only)):
    """``resources/read`` from the screen, on its own app only."""
    view = await _view(view_id, ctx)
    try:
        return await app_views.read_from_screen(view, body.uri)
    except app_views.ViewError as exc:
        raise _bad(exc)


class ContextBody(BaseModel):
    content: list[Any] | None = None
    structuredContent: Any = None


@app_views_api.post('/{view_id}/context')
async def update_context(view_id: str, body: ContextBody, ctx: AuthContext = Depends(jwt_only)):
    """``ui/update-model-context``: what the agent should know on its next turn."""
    view = await _view(view_id, ctx)
    app_views.set_context(view, body.content, body.structuredContent)
    return {}


@app_view_frame_api.get('/app-view-frame/{token}', include_in_schema=False)
async def view_frame(token: str):
    claims = app_views.read_token(token)
    if not claims:
        raise HTTPException(status_code=404, detail='This link expired. Reload the page.')
    view = await app_views.get_view(str(claims.get('a') or ''), str(claims.get('u') or ''))
    if view is None:
        raise HTTPException(status_code=404, detail='This app screen was not found')
    try:
        html, ui = await app_views.screen(view)
    except app_views.ViewError as exc:
        raise _bad(exc)
    return HTMLResponse(html, headers={
        'Content-Security-Policy': app_views.content_security_policy(ui),
        'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
    })
