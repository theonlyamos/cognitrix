"""Push notifications to a person's browsers and installed web apps.

``GET /push`` says whether push is on for this server, the key a browser
subscribes with, and which browsers this person already switched on.
``POST /push/subscriptions`` saves a browser, ``DELETE`` forgets one, and
``POST /push/test`` sends a test. Browser sessions only.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cognitrix.agents import access as agent_access
from cognitrix.common import webpush
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.models.push_subscription import PushSubscription

push_api = APIRouter(prefix='/push')


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class SubscriptionBody(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: SubscriptionKeys
    label: str = Field(default='', max_length=80)


class EndpointBody(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)


def _row(row: PushSubscription) -> dict[str, Any]:
    return {'id': str(row.id), 'label': row.label or 'Browser', 'endpoint': row.endpoint}


@push_api.get('')
async def push_state(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    if not webpush.enabled():
        return {'available': False, 'public_key': '', 'subscriptions': []}
    rows = await PushSubscription.find({'user_id': agent_access.owner_key(ctx.user)}) or []
    return {'available': True, 'public_key': webpush.public_key(), 'subscriptions': [_row(r) for r in rows]}


@push_api.post('/subscriptions')
async def subscribe(body: SubscriptionBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    if not webpush.enabled():
        raise HTTPException(status_code=400, detail='Push notifications are turned off on this server.')
    if not webpush.endpoint_allowed(body.endpoint):
        raise HTTPException(status_code=400, detail="This browser's push service isn't one Cognitrix knows.")
    try:
        if len(webpush.b64url_decode(body.keys.p256dh)) != 65 or len(webpush.b64url_decode(body.keys.auth)) != 16:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="The browser's push keys look wrong.") from None
    user_key = agent_access.owner_key(ctx.user)
    # The same browser subscribing again (or after another person signed in
    # on it) replaces the old row.
    await PushSubscription.delete_many({'endpoint': body.endpoint})
    rows = await PushSubscription.find({'user_id': user_key}) or []
    if len(rows) >= webpush.MAX_SUBSCRIPTIONS_PER_USER:
        raise HTTPException(status_code=400, detail='Too many browsers are switched on. Turn one off first.')
    row = PushSubscription(
        user_id=user_key, endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth,
        label=' '.join(body.label.split()))
    await row.save()
    return _row(row)


@push_api.post('/unsubscribe')
async def unsubscribe(body: EndpointBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, str]:
    await PushSubscription.delete_many({'user_id': agent_access.owner_key(ctx.user), 'endpoint': body.endpoint})
    return {'message': 'Turned off'}


@push_api.delete('/subscriptions/{subscription_id}')
async def remove(subscription_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, str]:
    row = await PushSubscription.get(subscription_id)
    if row is None or row.user_id != agent_access.owner_key(ctx.user):
        raise HTTPException(status_code=404, detail='Not found')
    await PushSubscription.delete_many({'id': row.id})
    return {'message': 'Turned off'}


@push_api.post('/test')
async def test(ctx: AuthContext = Depends(jwt_only)) -> dict[str, int]:
    sent = await webpush.send_to_user(
        agent_access.owner_key(ctx.user), 'Cognitrix', 'Push notifications are working.', url='/notifications', tag='test')
    if not sent:
        raise HTTPException(
            status_code=400,
            detail='No browser accepted the test. Turn push off and on again on this device.')
    return {'sent': sent}
