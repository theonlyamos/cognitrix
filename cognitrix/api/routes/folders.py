"""Working folders for chats: check a folder, and read or change a chat's folders.

Agent default folders are saved with the agent (POST /agents). Every path is
checked here on the server; the client's list is never trusted.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.common.folders import (
    FolderError,
    allowed_roots,
    folders_to_dicts,
    normalize_folders,
    validate_folder,
)
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only

folders_api = APIRouter(prefix='/folders', dependencies=[Depends(jwt_only)])


class FolderCheck(BaseModel):
    path: str
    access: str = 'read'


class FolderList(BaseModel):
    folders: list[dict[str, Any]]


@folders_api.get('/info')
async def folder_info():
    """What the folder picker should offer on this install."""
    from cognitrix.config import settings

    roots = allowed_roots()
    return {
        'desktop': os.environ.get('COGNITRIX_DESKTOP') == '1',
        'allowed_roots': None if roots is None else [str(r) for r in roots],
        'working_folder': str(settings.tools_root),
    }


@folders_api.post('/check')
async def check_folder(body: FolderCheck):
    """Validate one folder before adding it; returns it in its stored shape."""
    try:
        return validate_folder(body.path, body.access).to_dict()
    except FolderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


async def _owned_session(session_id: str, ctx: AuthContext):
    from cognitrix.api.routes.sessions import _load_authorized_session

    binding, session = await _load_authorized_session(session_id, ctx)
    # TaskRun-only access (no owner binding) may read but not change folders.
    return binding, session


@folders_api.get('/sessions/{session_id}')
async def get_session_folders(session_id: str, ctx: AuthContext = Depends(get_auth_context)):
    _, session = await _owned_session(session_id, ctx)
    return {'folders': session.folders}


@folders_api.put('/sessions/{session_id}')
async def set_session_folders(
    session_id: str,
    body: FolderList,
    ctx: AuthContext = Depends(get_auth_context),
):
    """Replace a chat's folders. Applies from the chat's next message."""
    _, session = await _owned_session(session_id, ctx)
    if session.run_id:
        raise HTTPException(status_code=400, detail="A task run's folders can't be changed")
    try:
        folders = normalize_folders(body.folders)
    except FolderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    session.folders = folders_to_dicts(folders)
    await session.save()
    return {'folders': session.folders}
