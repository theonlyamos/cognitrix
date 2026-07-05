"""API key management. JWT-only by design: a key must never be able to mint
or manage keys (privilege escalation). Responses are explicit projections —
the key hash and secrets never leave the server after creation."""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.models.api_key import (
    VALID_SCOPES,
    APIKey,
    generate_secret,
    hash_secret,
    normalize_expiry,
)

api_keys_api = APIRouter(prefix='/api-keys')


def _project(key: APIKey) -> dict:
    return {
        'id': key.id,
        'name': key.name,
        'prefix': key.prefix,
        'scopes': key.scopes,
        'allowed_agents': key.allowed_agents,
        'allowed_teams': key.allowed_teams,
        'rate_limit': key.rate_limit,
        'expires_at': key.expires_at,
        'last_used_at': key.last_used_at,
        'revoked': key.revoked,
        'created_at': str(getattr(key, 'created_at', '') or ''),
    }


class CreateKeyRequest(BaseModel):
    name: str
    scopes: list[str]
    allowed_agents: list[str] = []
    allowed_teams: list[str] = []
    expires_at: str | None = None
    rate_limit: int | None = None


@api_keys_api.get('')
async def list_keys(ctx: AuthContext = Depends(jwt_only)):
    keys = await APIKey.find({'user_id': ctx.user.id})
    keys.sort(key=lambda k: str(getattr(k, 'created_at', '') or ''), reverse=True)
    return [_project(k) for k in keys]


@api_keys_api.post('')
async def create_key(body: CreateKeyRequest, ctx: AuthContext = Depends(jwt_only)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Key name is required")
    scopes = sorted(set(body.scopes))
    if not scopes or not set(scopes) <= VALID_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"Scopes must be a non-empty subset of: {', '.join(sorted(VALID_SCOPES))}",
        )
    try:
        expires_at = normalize_expiry(body.expires_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="expires_at must be an ISO datetime")
    if body.rate_limit is not None and body.rate_limit < 1:
        raise HTTPException(status_code=400, detail="rate_limit must be a positive integer")

    secret = generate_secret()
    key = APIKey(
        name=body.name.strip(),
        user_id=ctx.user.id,
        key_hash=hash_secret(secret),
        prefix=secret[:12],
        scopes=scopes,
        allowed_agents=body.allowed_agents,
        allowed_teams=body.allowed_teams,
        webhook_secret=secrets.token_hex(32),
        rate_limit=body.rate_limit,
        expires_at=expires_at,
    )
    await key.save()

    # The only response that ever carries the secrets.
    return {**_project(key), 'key': secret, 'webhook_secret': key.webhook_secret}


@api_keys_api.delete('/{key_id}')
async def revoke_key(key_id: str, ctx: AuthContext = Depends(jwt_only)):
    key = await APIKey.get(key_id)
    if key is None or key.user_id != ctx.user.id:
        raise HTTPException(status_code=404, detail="API key not found")
    if not key.revoked:
        await APIKey.update_one({'id': key.id}, {'revoked': True})
    return {'message': 'API key revoked'}
