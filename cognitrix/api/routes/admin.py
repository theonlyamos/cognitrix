"""People on this server: who has an account, their role, invites. Admins
only, and browser sessions only (an API key can never manage accounts).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cognitrix import usage
from cognitrix.agents import access as agent_access
from cognitrix.common import email as mailer
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.common.utils import Utils
from cognitrix.models import User
from cognitrix.models.invite import Invite

logger = logging.getLogger('cognitrix.audit')

admin_api = APIRouter(prefix='/admin')

INVITE_DAYS_MAX = 30


async def admin_only(ctx: AuthContext = Depends(jwt_only)) -> AuthContext:
    if not await agent_access.is_admin(ctx.user):
        raise HTTPException(status_code=403, detail='Only an admin can manage people')
    return ctx


class UserUpdate(BaseModel):
    role: Literal['admin', 'member'] | None = None
    disabled: bool | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=200)


class InviteBody(BaseModel):
    email: str = ''
    role: Literal['admin', 'member'] = 'member'
    days: int = Field(default=7, ge=1, le=INVITE_DAYS_MAX)


class EmailTest(BaseModel):
    to: str


def _audit(ctx: AuthContext, action: str, target: str) -> None:
    logger.info('admin %s: %s %s', getattr(ctx.user, 'email', '?'), action, target)


async def _admins(users) -> list:
    return [u for u in users if not getattr(u, 'disabled', None) and await agent_access.is_admin(u)]


async def _project(user, me: str) -> dict:
    totals = (await usage.summary(agent_access.owner_key(user), 30))['total']
    return {
        'id': str(user.id),
        'name': user.name,
        'email': user.email,
        'role': 'admin' if await agent_access.is_admin(user) else 'member',
        'disabled': bool(getattr(user, 'disabled', None)),
        'you': str(user.id) == me,
        'created': str(getattr(user, 'created_at', '') or '')[:10],
        'usage_30d': {
            'calls': totals['calls'],
            'tokens': totals['prompt_tokens'] + totals['completion_tokens'],
            'cost': totals['cost'],
        },
    }


@admin_api.get('/users')
async def list_users(ctx: AuthContext = Depends(admin_only)):
    users = sorted(await User.all(), key=lambda u: str(getattr(u, 'created_at', '') or ''))
    return {
        'users': [await _project(user, str(ctx.user.id)) for user in users],
        'email_configured': mailer.configured(),
    }


@admin_api.patch('/users/{user_id}')
async def update_user(user_id: str, body: UserUpdate, ctx: AuthContext = Depends(admin_only)):
    user = await User.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail='Person not found')
    changes: dict = {}
    is_self = str(user.id) == str(ctx.user.id)
    losing_admin = (body.role == 'member' or body.disabled is True) and await agent_access.is_admin(user)
    if losing_admin:
        if is_self:
            raise HTTPException(status_code=400, detail="You can't remove your own admin access or disable yourself")
        others = [u for u in await _admins(await User.all()) if str(u.id) != str(user.id)]
        if not others:
            raise HTTPException(status_code=400, detail='There has to be at least one admin')
    if body.role is not None:
        changes['role'] = body.role
    if body.disabled is not None:
        changes['disabled'] = body.disabled
        if body.disabled:
            changes['tokens_valid_after'] = int(time.time())
    if body.new_password is not None:
        changes['password'] = Utils.hash_password(body.new_password)
        changes['tokens_valid_after'] = int(time.time())
        changes['reset_token_hash'] = None
        changes['reset_expires'] = None
    if changes:
        await User.update_one({'id': user.id}, changes)
        _audit(ctx, 'changed ' + ', '.join(k for k in changes if k not in ('tokens_valid_after',)), user.email)
    return await _project(await User.get(user_id), str(ctx.user.id))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _invite_row(invite: Invite) -> dict:
    now = int(time.time())
    return {
        'id': str(invite.id),
        'email': invite.email or None,
        'role': invite.role or 'member',
        'expires_at': invite.expires_at,
        'state': 'used' if invite.used_at else 'expired' if invite.expires_at < now else 'open',
        'used_by': invite.used_by or None,
    }


@admin_api.get('/invites')
async def list_invites(_ctx: AuthContext = Depends(admin_only)):
    rows = sorted(await Invite.all(), key=lambda i: -int(i.expires_at or 0))
    return [_invite_row(row) for row in rows[:100]]


@admin_api.post('/invites')
async def create_invite(body: InviteBody, ctx: AuthContext = Depends(admin_only)):
    email = body.email.strip()
    if email and not Utils.email_is_valid(email):
        raise HTTPException(status_code=400, detail='That email address is not valid')
    if email and await User.find_one({'email': email}):
        raise HTTPException(status_code=400, detail='That address already has an account')
    token = secrets.token_urlsafe(32)
    invite = Invite(token_hash=_hash(token), email=email, role=body.role,
                    created_by=str(ctx.user.email), expires_at=int(time.time()) + body.days * 86400)
    await invite.save()
    _audit(ctx, f'invited ({body.role})', email or 'anyone with the link')
    saved = await Invite.find_one({'token_hash': _hash(token)}) or invite
    # The token is returned this once; only its hash is kept.
    return {**_invite_row(saved), 'token': token, 'path': f'/signup?invite={token}'}


@admin_api.delete('/invites/{invite_id}')
async def delete_invite(invite_id: str, ctx: AuthContext = Depends(admin_only)):
    await Invite.delete_many({'id': invite_id})
    _audit(ctx, 'withdrew invite', invite_id)
    return {'message': 'Invite withdrawn'}


@admin_api.post('/email-test')
async def email_test(body: EmailTest, _ctx: AuthContext = Depends(admin_only)):
    try:
        await mailer.send(body.to.strip(), 'Cognitrix test email', 'Email from Cognitrix is working.\n')
    except mailer.EmailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {'message': f'Sent to {body.to.strip()}'}
