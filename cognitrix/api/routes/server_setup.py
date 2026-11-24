"""The first-start Server setup screen: where the database and job queue are.

Shown on a server (not the desktop app) before anyone has an account, so the
person setting it up can point it at PostgreSQL or Redis without editing
environment variables. Most people keep the built-in SQLite and queue and
just press Continue. The values go to the app settings file, which is read
before the database opens, and stay editable in Settings > Advanced.

These endpoints are public, like sign-up, but only until the first account
exists or setup is finished; after that they answer 403.
"""
from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cognitrix import app_settings

server_setup_api = APIRouter(prefix='/setup/server')

DB_TYPES = ('sqlite', 'postgresql', 'mysql', 'mongodb')
SERVER_NAMES = ('DB_TYPE', 'DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'CELERY_BROKER_URL')


async def setup_pending() -> bool:
    """True while the Server setup screen should be offered."""
    if os.environ.get('COGNITRIX_DESKTOP') == '1':
        return False
    if await asyncio.to_thread(app_settings.server_setup_done):
        return False
    from cognitrix.models import User

    return not await User.find_one({})


async def _require_pending() -> None:
    if not await setup_pending():
        raise HTTPException(status_code=403, detail='This server is already set up. Change it in Settings > Advanced.')


def _sqlite_default() -> str:
    return str(Path.home() / '.cognitrix' / 'cognitrix.db')


@server_setup_api.get('')
async def read_server_setup() -> dict[str, Any]:
    await _require_pending()
    get = app_settings.get
    db_type = get('DB_TYPE', 'sqlite') or 'sqlite'
    return {
        'database': {
            'type': db_type,
            'host': get('DB_HOST', ''),
            'port': get('DB_PORT', ''),
            'name': get('DB_NAME', ''),
            'user': get('DB_USER', ''),
            'password_set': bool(get('DB_PASSWORD', '')),
        },
        'queue': {'redis': bool(get('CELERY_BROKER_URL', ''))},
        'sqlite_path': _sqlite_default(),
    }


class ServerSetupBody(BaseModel):
    db_type: str = 'sqlite'
    db_host: str = ''
    db_port: str = ''
    db_name: str = ''
    db_user: str = ''
    db_password: str = ''       # empty keeps the saved one
    queue: str = 'builtin'      # builtin | redis
    broker_url: str = ''        # empty keeps the saved one when queue is redis


def _reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _sign_in_problem(db_type: str, host: str, port: int, name: str, user: str, password: str) -> str:
    """Sign in to the database the way the app will on its next start, so a
    wrong user, password or name is fixed here, not after a restart that
    fails. '' when it worked (or the driver isn't installed to check)."""
    async def attempt() -> None:
        if db_type == 'postgresql':
            import aiopg

            base = f'user={user} password={password} host={host} port={port}'
            try:
                conn = await aiopg.connect(f'dbname={name} {base}', timeout=5)
            except Exception as exc:
                if 'database' not in str(exc):
                    raise
                conn = await aiopg.connect(base, timeout=5)  # the app makes the database
            conn.close()
        elif db_type == 'mysql':
            import aiomysql

            try:
                conn = await aiomysql.connect(host=host, port=port, user=user, password=password, db=name,
                                              connect_timeout=5)
            except Exception as exc:
                if 'Unknown database' not in str(exc):
                    raise
                conn = await aiomysql.connect(host=host, port=port, user=user, password=password,
                                              connect_timeout=5)
            conn.close()
        elif db_type == 'mongodb':
            from motor.motor_asyncio import AsyncIOMotorClient

            client = AsyncIOMotorClient(host=host, port=port, serverSelectionTimeoutMS=5000)
            try:
                await client.admin.command('ping')
            finally:
                client.close()

    try:
        asyncio.run(asyncio.wait_for(attempt(), timeout=10))
    except ImportError:
        return ''
    except Exception as exc:  # the driver's own message says what is wrong
        return str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
    return ''


def _changes(body: ServerSetupBody) -> dict[str, str]:
    """The values to save, or a 400 that says what is wrong."""
    from cognitrix.config import DEFAULT_DB_PORTS

    db_type = body.db_type.strip().lower()
    if db_type not in DB_TYPES:
        raise HTTPException(status_code=400, detail='Pick one of the databases.')
    changes: dict[str, str] = {'DB_TYPE': db_type}
    if db_type == 'sqlite':
        name = body.db_name.strip()
        folder = Path(name).expanduser().parent if name else Path.home() / '.cognitrix'
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not os.access(folder, os.W_OK):
            raise HTTPException(status_code=400, detail=f"The server can't write to {folder}.")
        changes.update({'DB_HOST': '', 'DB_PORT': '', 'DB_NAME': name, 'DB_USER': '', 'DB_PASSWORD': ''})
    else:
        host = body.db_host.strip()
        if not host:
            raise HTTPException(status_code=400, detail='Type the address of the database server.')
        port_text = body.db_port.strip() or DEFAULT_DB_PORTS.get(db_type, '')
        if not port_text.isdigit() or not 0 < int(port_text) < 65536:
            raise HTTPException(status_code=400, detail='The database port must be a number from 1 to 65535.')
        if not _reachable(host, int(port_text)):
            raise HTTPException(status_code=400, detail=f"Couldn't reach a database at {host}:{port_text}. "
                                                        'Check the address and that it accepts connections.')
        changes.update({'DB_HOST': host, 'DB_PORT': body.db_port.strip(), 'DB_NAME': body.db_name.strip(),
                        'DB_USER': body.db_user.strip()})
        if body.db_password:
            changes['DB_PASSWORD'] = body.db_password
        problem = _sign_in_problem(db_type, host, int(port_text), body.db_name.strip() or 'cognitrix',
                                   body.db_user.strip(), body.db_password or app_settings.get('DB_PASSWORD', ''))
        if problem:
            raise HTTPException(status_code=400, detail=f"Couldn't sign in to the database: {problem}")
    if body.queue == 'redis':
        url = body.broker_url.strip() or app_settings.get('CELERY_BROKER_URL', '')
        parsed = urlparse(url)
        if parsed.scheme not in ('redis', 'rediss') or not parsed.hostname:
            raise HTTPException(status_code=400, detail='The job queue address looks like redis://host:6379/0.')
        if not _reachable(parsed.hostname, parsed.port or 6379):
            raise HTTPException(status_code=400, detail=f"Couldn't reach Redis at {parsed.hostname}:{parsed.port or 6379}.")
        changes['CELERY_BROKER_URL'] = url
    elif body.queue == 'builtin':
        changes['CELERY_BROKER_URL'] = ''
    else:
        raise HTTPException(status_code=400, detail='Pick a job queue.')
    return changes


def _effective() -> dict[str, str]:
    values = {name: app_settings.get(name, '') for name in SERVER_NAMES}
    values['DB_TYPE'] = values['DB_TYPE'] or 'sqlite'
    return values


@server_setup_api.post('')
async def save_server_setup(body: ServerSetupBody) -> dict[str, Any]:
    """Save where the database and job queue are and finish setup. A change
    takes effect after the server restarts."""
    await _require_pending()
    changes = await asyncio.to_thread(_changes, body)
    before = _effective()
    try:
        await asyncio.to_thread(app_settings.set_many, changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    after = _effective()
    await asyncio.to_thread(app_settings.mark_server_setup_done)
    return {'restart_needed': before != after}
