"""Agent email: connect a mailbox so each agent gets its own address.

``GET /email`` returns the mailbox (password never included) and every agent's
address. ``PUT /email`` saves the mailbox; when the servers or sign-in change
they are checked first (IMAP and SMTP). ``PUT /email/agents/{agent_id}`` changes
an agent's address name or turns it off, ``POST /email/test`` sends a test
mail to the person, and ``DELETE /email`` disconnects.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agent_email import mail
from cognitrix.agent_email import service as email_service
from cognitrix.agent_email.models import ADDRESSING, SECURITY, AgentAddress, EmailAccount, EmailLink
from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only

email_api = APIRouter(prefix='/email', dependencies=[Depends(jwt_only)])

MAX_FIELD = 300


class EmailUpdate(BaseModel):
    address: str | None = None
    addressing: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_security: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: str | None = None
    username: str | None = None
    password: str | None = None
    folder: str | None = None
    agent_id: str | None = None
    allowed_senders: str | None = None
    require_auth: bool | None = None
    answer_others: bool | None = None
    enabled: bool | None = None


class AddressUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None


def _user_key(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


async def _load(user_key: str) -> EmailAccount | None:
    return await EmailAccount.find_one({'user_id': user_key})


async def _agents(user_key: str) -> list:
    from cognitrix.channels.service import ChannelService

    return await ChannelService()._agents(user_key)


async def _ensure_addresses(account: EmailAccount, agents: list) -> dict[str, AgentAddress]:
    """Every agent gets an address the first time the mailbox is shown."""
    rows = {str(r.agent_id): r for r in await AgentAddress.find({'account_id': account.id}) or []}
    taken = {r.name for r in rows.values()}
    for agent in agents:
        if str(agent.id) in rows:
            continue
        base = mail.slug(agent.name)
        name, n = base, 2
        while name in taken:
            name = f'{base[:36]}-{n}'
            n += 1
        row = AgentAddress(account_id=account.id, user_id=account.user_id, agent_id=str(agent.id), name=name)
        await row.save()
        rows[str(agent.id)] = row
        taken.add(name)
    return rows


async def _payload(user_key: str, account: EmailAccount | None) -> dict[str, Any]:
    own = await email_service.owner_email(user_key)
    base = {'available': email_service.email_enabled(), 'owner_email': own}
    if account is None:
        return {**base, 'account': None, 'agents': []}
    agents = await _agents(user_key)
    rows = await _ensure_addresses(account, agents)
    service = email_service.get_service()
    listed = []
    for agent in agents:
        row = rows.get(str(agent.id))
        if row is None:
            continue
        listed.append({
            'agent_id': str(agent.id), 'agent_name': agent.name, 'name': row.name, 'enabled': bool(row.enabled),
            'address': mail.agent_address(account.address, account.addressing, row.name),
        })
    return {
        **base,
        'account': {
            'address': account.address, 'addressing': account.addressing,
            'imap_host': account.imap_host, 'imap_port': account.imap_port, 'imap_security': account.imap_security,
            'smtp_host': account.smtp_host, 'smtp_port': account.smtp_port, 'smtp_security': account.smtp_security,
            'username': account.username, 'password_set': bool(account.password), 'folder': account.folder,
            'agent_id': account.agent_id, 'allowed_senders': account.allowed_senders,
            'require_auth': bool(account.require_auth), 'answer_others': bool(account.answer_others),
            'enabled': bool(account.enabled), 'status': account.status, 'last_error': account.last_error,
            'last_checked_at': account.last_checked_at,
            'running': bool(service and service.is_running(account.id)),
        },
        'agents': listed,
    }


def _text(value: str | None, label: str, *, allow_space: bool = False) -> str:
    text = (value or '').strip()
    if len(text) > MAX_FIELD or (not allow_space and any(c.isspace() for c in text)):
        raise HTTPException(status_code=400, detail=f'That {label} looks wrong.')
    return text


def _port(value: int | None) -> int:
    if value is None or not 1 <= int(value) <= 65535:
        raise HTTPException(status_code=400, detail='Ports are numbers from 1 to 65535.')
    return int(value)


@email_api.get('')
async def get_email(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    user_key = _user_key(ctx)
    return await _payload(user_key, await _load(user_key))


@email_api.put('')
async def update_email(body: EmailUpdate, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    from cognitrix.common.secret_box import seal, unseal_or_empty

    user_key = _user_key(ctx)
    row = await _load(user_key)
    current: dict[str, Any] = row.model_dump() if row is not None else {}
    data: dict[str, Any] = {}

    if body.address is not None:
        address = _text(body.address, 'address').lower()
        if not mail.valid_address(address):
            raise HTTPException(status_code=400, detail='Enter the mailbox address, like agents@example.com.')
        data['address'] = address
    if body.addressing is not None:
        if body.addressing not in ADDRESSING:
            raise HTTPException(status_code=400, detail='Unknown addressing.')
        data['addressing'] = body.addressing
    for host in ('imap_host', 'smtp_host'):
        if getattr(body, host) is not None:
            data[host] = _text(getattr(body, host), 'server name').lower()
    for port in ('imap_port', 'smtp_port'):
        if getattr(body, port) is not None:
            data[port] = _port(getattr(body, port))
    for security in ('imap_security', 'smtp_security'):
        if getattr(body, security) is not None:
            if getattr(body, security) not in SECURITY:
                raise HTTPException(status_code=400, detail='Security is ssl, starttls or none.')
            data[security] = getattr(body, security)
    if body.username is not None:
        data['username'] = _text(body.username, 'user name')
    if body.password:
        if len(body.password) > MAX_FIELD:
            raise HTTPException(status_code=400, detail='That password looks wrong.')
        data['password'] = seal(body.password)
    if body.folder is not None:
        data['folder'] = _text(body.folder, 'folder', allow_space=True) or 'INBOX'
    if body.agent_id is not None:
        agent_id = body.agent_id.strip()
        if agent_id and await agent_access.find_visible_agent(user_key, agent_id=agent_id) is None:
            raise HTTPException(status_code=404, detail='Agent not found')
        data['agent_id'] = agent_id
    if body.allowed_senders is not None:
        entries = mail.allowed_list(body.allowed_senders)
        bad = [e for e in entries if not (mail.valid_address(e) or (e.startswith('@') and '.' in e))]
        if bad or len(entries) > 50:
            raise HTTPException(status_code=400, detail=f'Not an address or @domain: {", ".join(bad[:3])}')
        data['allowed_senders'] = '\n'.join(entries)
    for flag in ('require_auth', 'answer_others', 'enabled'):
        if getattr(body, flag) is not None:
            data[flag] = bool(getattr(body, flag))

    merged = {**current, **data}
    server_keys = ('address', 'imap_host', 'imap_port', 'imap_security', 'smtp_host', 'smtp_port',
                   'smtp_security', 'username', 'password', 'folder')
    servers_changed = row is None or any(k in data and data[k] != current.get(k) for k in server_keys)
    if servers_changed:
        if not merged.get('address') or not merged.get('imap_host') or not merged.get('smtp_host'):
            raise HTTPException(status_code=400, detail='Fill in the address and both servers.')
        if not merged.get('password'):
            raise HTTPException(status_code=400, detail='Enter the password (an app password for Gmail).')
        other = await EmailAccount.find_one({'address': merged['address']})
        if other is not None and (row is None or other.id != row.id):
            raise HTTPException(status_code=409, detail='This mailbox is already connected to another account.')
        login = {'username': merged.get('username') or merged['address'],
                 'password': unseal_or_empty(merged['password'])}
        defaults = {'imap_port': 993, 'imap_security': 'ssl', 'smtp_port': 465, 'smtp_security': 'ssl'}
        merged = {**defaults, **{k: v for k, v in merged.items() if v not in (None, '')}}
        try:
            await asyncio.to_thread(
                mail.check_imap, host=merged['imap_host'], port=merged['imap_port'],
                security=merged['imap_security'], folder=merged.get('folder') or 'INBOX', **login)
            await asyncio.to_thread(
                mail.check_smtp, host=merged['smtp_host'], port=merged['smtp_port'],
                security=merged['smtp_security'], **login)
        except mail.MailError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        # A different mailbox starts fresh: only mail from now on is read.
        data.update({'uid_validity': '', 'last_uid': 0, 'status': '', 'last_error': ''})

    if row is None:
        row = EmailAccount(user_id=user_key, **{k: v for k, v in merged.items() if k in EmailAccount.model_fields
                                                and k not in ('id', 'created_at', 'updated_at')},
                           uid_validity='', last_uid=0)
        await row.save()
    elif data:
        await EmailAccount.update_one({'id': row.id}, data)
    row = await _load(user_key)
    if row is not None and data:
        if servers_changed:
            await EmailLink.delete_many({'account_id': row.id})
        service = email_service.get_service()
        if service is not None:
            await service.reload(row.id)
    return await _payload(user_key, row)


@email_api.put('/agents/{agent_id}')
async def update_address(agent_id: str, body: AddressUpdate,
                         ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    user_key = _user_key(ctx)
    account = await _load(user_key)
    if account is None:
        raise HTTPException(status_code=404, detail='Connect a mailbox first')
    await _ensure_addresses(account, await _agents(user_key))
    row = await AgentAddress.find_one({'account_id': account.id, 'agent_id': agent_id})
    if row is None:
        raise HTTPException(status_code=404, detail='Agent not found')
    data: dict[str, Any] = {}
    if body.name is not None:
        name = body.name.strip().lower()
        if not mail.valid_name(name):
            raise HTTPException(status_code=400, detail='Use letters, numbers, dots or dashes (up to 40).')
        other = await AgentAddress.find_one({'account_id': account.id, 'name': name})
        if other is not None and other.id != row.id:
            raise HTTPException(status_code=409, detail='Another agent already has that address.')
        data['name'] = name
    if body.enabled is not None:
        data['enabled'] = bool(body.enabled)
    if data:
        await AgentAddress.update_one({'id': row.id}, data)
    return await _payload(user_key, account)


@email_api.post('/test')
async def test_email(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    user_key = _user_key(ctx)
    account = await _load(user_key)
    if account is None:
        raise HTTPException(status_code=404, detail='Connect a mailbox first')
    to = await email_service.owner_email(user_key)
    if not mail.valid_address(to):
        raise HTTPException(status_code=409, detail='Your account has no email address to send to.')
    message = mail.build(sender=account.address, sender_name='Cognitrix', to=to,
                         subject='Test from Cognitrix agent email',
                         body='Agent email works. Reply to an agent\'s address to give it work.', auto=False)
    try:
        await asyncio.to_thread(mail.send, message, host=account.smtp_host, port=account.smtp_port,
                                security=account.smtp_security, **email_service.server_settings(account))
    except mail.MailError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return {'status': 'sent', 'to': to}


@email_api.delete('')
async def delete_email(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    user_key = _user_key(ctx)
    account = await _load(user_key)
    if account is None:
        raise HTTPException(status_code=404, detail='Not connected')
    await EmailAccount.delete_many({'id': account.id})
    await AgentAddress.delete_many({'account_id': account.id})
    await EmailLink.delete_many({'account_id': account.id})
    service = email_service.get_service()
    if service is not None:
        await service.reload(account.id)
    return {'status': 'deleted'}
