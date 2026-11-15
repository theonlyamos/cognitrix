"""People on this server: who has an account, their role, invites. Admins
only, and browser sessions only (an API key can never manage accounts).
"""
from __future__ import annotations

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
    # Swarm caps for this person; 0 means the server's own limit.
    swarm_concurrency: int | None = Field(default=None, ge=0, le=64)
    swarm_max_workers: int | None = Field(default=None, ge=0, le=500)


class InviteBody(BaseModel):
    email: str = ''
    role: Literal['admin', 'member'] = 'member'
    days: int = Field(default=7, ge=1, le=INVITE_DAYS_MAX)


class EmailTest(BaseModel):
    to: str


def _audit(ctx: AuthContext, action: str, target: str, detail: str = '') -> None:
    from cognitrix import audit

    logger.info('admin %s: %s %s', getattr(ctx.user, 'email', '?'), action, target)
    audit.record('people', action[:1].upper() + action[1:], user_id=agent_access.owner_key(ctx.user),
                 actor=str(getattr(ctx.user, 'email', '') or ''), target=target, detail=detail, outcome='ok')


async def _admins(users) -> list:
    return [u for u in users if not getattr(u, 'disabled', None) and await agent_access.is_admin(u)]


async def _project(user, me: str) -> dict:
    from cognitrix.swarms import service as swarms
    from cognitrix.usage import caps

    totals = (await usage.summary(agent_access.owner_key(user), 30))['total']
    is_admin = await agent_access.is_admin(user)
    try:
        cap_state = await caps.status(agent_access.owner_key(user), is_admin=is_admin)
        own = await caps.get_row(agent_access.owner_key(user))
        spending = {
            'mode': own.mode if own is not None and own.mode in caps.MODES else 'default',
            'own_cost': float(own.monthly_cost or 0) if own is not None else 0.0,
            'own_tokens': int(own.monthly_tokens or 0) if own is not None else 0,
            **cap_state,
        }
    except Exception:
        logger.debug('Could not read the spending cap', exc_info=True)
        spending = None
    try:
        caps = await swarms.get_settings(agent_access.owner_key(user))
        swarm_caps = (int(caps.admin_concurrency or 0), int(caps.admin_max_workers or 0))
    except Exception:
        logger.debug('Could not read swarm limits', exc_info=True)
        swarm_caps = (0, 0)
    return {
        'swarm_concurrency': swarm_caps[0],
        'swarm_max_workers': swarm_caps[1],
        'id': str(user.id),
        'name': user.name,
        'email': user.email,
        'role': 'admin' if is_admin else 'member',
        'spending': spending,
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
    caps = {key: value for key, value in (('admin_concurrency', body.swarm_concurrency),
                                          ('admin_max_workers', body.swarm_max_workers)) if value is not None}
    if caps:
        from cognitrix.swarms import service as swarms

        await swarms.save_settings(agent_access.owner_key(user), caps, admin=True)
        _audit(ctx, 'changed swarm limits for', user.email)
    if changes:
        await User.update_one({'id': user.id}, changes)
        _audit(ctx, 'changed ' + ', '.join(k for k in changes if k not in ('tokens_valid_after',)), user.email)
        if changes.get('disabled'):
            # Their linked chat apps act as them without a password.
            from cognitrix.channels.service import get_service

            service = get_service()
            if service is not None:
                await service.cancel_user_turns(agent_access.owner_key(user))
    return await _project(await User.get(user_id), str(ctx.user.id))


def _hash(token: str) -> str:
    from cognitrix.api.routes.auth import _hash_token

    return _hash_token(token)


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


# --- spending caps ---------------------------------------------------------------

class CapBody(BaseModel):
    mode: Literal['default', 'custom', 'none'] = 'custom'
    monthly_cost: float = Field(default=0, ge=0, le=1_000_000)
    monthly_tokens: int = Field(default=0, ge=0, le=100_000_000_000)


def _cap_text(body: CapBody) -> str:
    if body.mode == 'default':
        return 'follows the default'
    if body.mode == 'none' or not (body.monthly_cost or body.monthly_tokens):
        return 'no limit'
    parts = []
    if body.monthly_cost:
        parts.append(f'${body.monthly_cost:,.2f} a month')
    if body.monthly_tokens:
        parts.append(f'{body.monthly_tokens:,} tokens a month')
    return ', '.join(parts)


@admin_api.get('/spending-cap')
async def get_default_cap(_ctx: AuthContext = Depends(admin_only)):
    from cognitrix.usage import caps

    row = await caps.get_row(caps.DEFAULT_KEY)
    return {
        'monthly_cost': float(row.monthly_cost or 0) if row else 0.0,
        'monthly_tokens': int(row.monthly_tokens or 0) if row else 0,
    }


@admin_api.put('/spending-cap')
async def set_default_cap(body: CapBody, ctx: AuthContext = Depends(admin_only)):
    """The cap for every member who has none of their own."""
    from cognitrix.usage import caps

    row = await caps.save(caps.DEFAULT_KEY, mode='custom', monthly_cost=body.monthly_cost,
                          monthly_tokens=body.monthly_tokens, by=str(ctx.user.email))
    _audit(ctx, 'set the default spending limit', 'everyone without their own',
           _cap_text(CapBody(mode='custom', monthly_cost=body.monthly_cost, monthly_tokens=body.monthly_tokens)))
    return {'monthly_cost': float(row.monthly_cost or 0), 'monthly_tokens': int(row.monthly_tokens or 0)}


@admin_api.put('/users/{user_id}/spending-cap')
async def set_user_cap(user_id: str, body: CapBody, ctx: AuthContext = Depends(admin_only)):
    from cognitrix.usage import caps

    user = await User.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail='Person not found')
    await caps.save(agent_access.owner_key(user), mode=body.mode, monthly_cost=body.monthly_cost,
                    monthly_tokens=body.monthly_tokens, by=str(ctx.user.email))
    _audit(ctx, 'changed the spending limit for', user.email, _cap_text(body))
    return await _project(user, str(ctx.user.id))


# --- removing people -------------------------------------------------------------

class RemoveBody(BaseModel):
    keep_work: bool = True
    """True moves their agents, tasks, teams, apps and pages to you."""
    confirm_email: str


@admin_api.get('/users/{user_id}/removal')
async def removal_preview(user_id: str, _ctx: AuthContext = Depends(admin_only)):
    from cognitrix import people_removal

    user = await User.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail='Person not found')
    return await people_removal.preview(agent_access.owner_key(user))


@admin_api.post('/users/{user_id}/remove')
async def remove_user(user_id: str, body: RemoveBody, ctx: AuthContext = Depends(admin_only)):
    """Delete someone's account. Cannot be undone."""
    from cognitrix import people_removal

    user = await User.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail='Person not found')
    if str(user.id) == str(ctx.user.id):
        raise HTTPException(status_code=400, detail="You can't remove yourself")
    if body.confirm_email.strip().lower() != str(user.email or '').strip().lower():
        raise HTTPException(status_code=400, detail='Type their email address to confirm')
    if await agent_access.is_admin(user):
        others = [u for u in await _admins(await User.all()) if str(u.id) != str(user.id)]
        if not others:
            raise HTTPException(status_code=400, detail='There has to be at least one admin')
    result = await people_removal.remove(
        user, new_owner=agent_access.owner_key(ctx.user) if body.keep_work else None)
    moved = sum(result['moved'].values())
    _audit(ctx, 'removed', user.email,
           f'moved {moved} items to {ctx.user.email}' if body.keep_work else 'deleted their work')
    message = f'{user.email} was removed.'
    if result.get('paused'):
        message += (f" {result['paused']} of their schedules or triggers moved to you paused;"
                    ' turn them back on from the task page.')
    return {'message': message, **result}
