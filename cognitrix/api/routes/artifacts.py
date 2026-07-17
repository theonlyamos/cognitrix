"""Authenticated binary delivery for tool-generated artifacts."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from cognitrix.artifacts import Artifact, absolute_path
from cognitrix.common.security import AuthContext, get_auth_context, require

artifacts_api = APIRouter(prefix='/artifacts', dependencies=[Depends(require('chat'))])


@artifacts_api.get('/{artifact_id}')
async def get_artifact(artifact_id: str, ctx: AuthContext = Depends(get_auth_context)):
    artifact = await Artifact.get(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail='Artifact not found')
    # A durable TaskRun artifact is a run resource, not a chat-session
    # resource.  Its owner id and producing agent are insufficient authority:
    # API keys are also narrowed by the run's immutable team/agent ACL and the
    # artifact must be present in an authoritative persisted result.  Enforce
    # both checks exclusively on /tasks/{task}/runs/{run}/artifacts/{artifact}.
    if artifact.run_id is not None:
        raise HTTPException(status_code=404, detail='Artifact not found')
    if artifact.user_id != str(ctx.user.id):
        raise HTTPException(status_code=404, detail='Artifact not found')
    if artifact.agent_id and not ctx.agent_allowed(artifact.agent_id):
        raise HTTPException(status_code=403, detail='API key not allowed for this artifact')
    try:
        path = absolute_path(artifact)
    except ValueError:
        raise HTTPException(status_code=404, detail='Artifact not found')
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Artifact data is unavailable')
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.filename)
