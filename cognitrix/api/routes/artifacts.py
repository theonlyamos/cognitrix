"""Authenticated binary delivery for exact retained artifact variants."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from cognitrix.artifacts import Artifact
from cognitrix.common.security import AuthContext, get_auth_context, require
from cognitrix.media import MediaError, MediaOwnership, media_assets
from cognitrix.session_ownership import principal_key

artifacts_api = APIRouter(prefix='/artifacts', dependencies=[Depends(require('chat'))])


@artifacts_api.get('/{artifact_id}')
async def get_artifact(
    artifact_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    variant: Literal['original', 'vision', 'thumbnail'] = 'original',
):
    artifact = await Artifact.get(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail='Artifact not found')
    user_key = principal_key(ctx.user)
    if artifact.user_id != user_key:
        raise HTTPException(status_code=404, detail='Artifact not found')
    if artifact.agent_id and not ctx.agent_allowed(artifact.agent_id):
        raise HTTPException(status_code=403, detail='API key not allowed for this artifact')
    try:
        resolved = await media_assets.resolve_variant_file(
            artifact_id,
            MediaOwnership(
                session_id=artifact.session_id,
                user_id=user_key,
                agent_id=artifact.agent_id,
            ),
            variant,
        )
    except (MediaError, OSError, ValueError):
        raise HTTPException(status_code=404, detail='Artifact not found')
    return FileResponse(
        resolved.path,
        media_type=resolved.mime_type,
        filename=resolved.filename,
    )
