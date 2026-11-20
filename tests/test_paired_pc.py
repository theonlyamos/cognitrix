"""Paired PCs (cognitrix/computer/paired): a web user's own PC as a computer
their agents can work on.

- A one-time code made on the web pairs a PC; the PC confirms and gets a token.
- The PC dials out and keeps one WebSocket open; tool calls go down it.
- The PC enforces its own limits: shared folders only, powers, pause.
- A chat set to a PC sends Bash and the file tools there, and every command
  there needs a person's approval.
- Unpairing on the web makes the PC forget its token.
"""
from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace

import pytest

from cognitrix.computer import paired
from cognitrix.computer.paired import store
from cognitrix.computer.paired.hub import DeviceUnavailable, hub
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'paired.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setenv('COGNITRIX_PAIRED_CONFIG', str(tmp_path / 'pc' / 'paired.json'))
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    store._codes.clear()
    store._tries.clear()
    yield
    set_execution_context(ToolExecutionContext())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
async def server(db):
    """A real HTTP + WebSocket server with the devices API, signed in as u1."""
    import uvicorn
    from fastapi import FastAPI

    from cognitrix.api.routes.computer_devices import devices_api
    from cognitrix.common.security import AuthContext, jwt_only

    app = FastAPI()
    app.include_router(devices_api, prefix='/api/v1')
    user = SimpleNamespace(id='u1', name='Kofi Boateng', email='kofi@example.com')
    app.dependency_overrides[jwt_only] = lambda: AuthContext(user=user)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='off'))
    task = asyncio.create_task(srv.serve())
    for _ in range(100):
        if srv.started:
            break
        await asyncio.sleep(0.05)
    yield SimpleNamespace(url=f'http://127.0.0.1:{port}', app=app)
    srv.should_exit = True
    await task


async def _wait(predicate, seconds: float = 10.0):
    for _ in range(int(seconds / 0.05)):
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def _paired_pc(server, tmp_path, *, powers=('files', 'commands')):
    from cognitrix.computer.paired.connector import Connector, link

    shared = tmp_path / 'project'
    shared.mkdir(exist_ok=True)
    (shared / 'notes.txt').write_text('first line\n', encoding='utf-8')
    item = store.make_code('u1', 'Kofi Boateng · kofi@example.com')
    view = await link(server.url, item.code, name='Kofi desktop', folders=[str(shared)], powers=list(powers))
    connector = Connector()
    connector.start()
    assert await _wait(lambda: connector.status == 'connected'), connector.last_error
    return view, connector, shared


def test_codes_are_short_unambiguous_and_normalized():
    code = paired.new_code()
    assert len(code) == 9 and code[4] == '-'
    assert not set(code.replace('-', '')) & set('01OIL')
    assert paired.normalize_code(code.lower().replace('-', ' ')) == code
    assert paired.normalize_code('abc') == ''


def test_powers_and_folders_are_cleaned():
    assert paired.clean_powers(['commands', 'root', 'files']) == ['files', 'commands']
    assert paired.clean_folders(['/a', {'path': '/b', 'writable': False}, 5, {'path': ''}]) == [
        {'path': '/a', 'writable': True}, {'path': '/b', 'writable': False}]
    assert paired.tool_power('Bash') == 'commands' and paired.tool_power('Read') == 'files'
    assert paired.tool_power('Web Fetch') is None


async def test_claim_shows_the_account_and_codes_are_single_use(db):
    item = store.make_code('u1', 'Kofi · kofi@example.com')
    assert store.claim(item.code, '1.2.3.4')['account'] == 'Kofi · kofi@example.com'
    device, token = await store.pair(item.code, '1.2.3.4', name='PC', platform='Linux', folders=['/tmp'],
                                     powers=['files'])
    assert token.startswith('cgxd_') and device.token_hash == paired.hash_token(token)
    assert store.code_status('u1', item.code)['status'] == 'paired'
    assert store.code_status('someone-else', item.code)['status'] == 'expired'
    with pytest.raises(store.PairingError) as used:
        store.claim(item.code, '1.2.3.4')
    assert used.value.code == 'used_code'
    assert (await store.device_for_token(token)).id == device.id
    assert await store.device_for_token('cgxd_wrong') is None


async def test_wrong_codes_are_rate_limited(db):
    for _ in range(store.CLAIM_TRIES):
        with pytest.raises(store.PairingError):
            store.claim('AAAA-BBBB', '5.6.7.8')
    with pytest.raises(store.PairingError) as limited:
        store.claim('AAAA-BBBB', '5.6.7.8')
    assert limited.value.status == 429


async def test_idle_pcs_must_pair_again(db):
    from cognitrix.models.paired_device import PairedDevice

    item = store.make_code('u1', 'Kofi')
    device, token = await store.pair(item.code, 'x', name='PC', platform='', folders=[], powers=[])
    await PairedDevice.update_one({'id': device.id}, {'last_seen_at': '2020-01-01 00:00:00'})
    assert await store.device_for_token(token) is None


def test_link_needs_https_except_for_this_machine():
    from cognitrix.computer.paired.connector import LinkError, normalize_server

    assert normalize_server('cognitrix.example.com/') == 'https://cognitrix.example.com'
    assert normalize_server('https://x.example.com/api/v1') == 'https://x.example.com'
    assert normalize_server('http://localhost:8000') == 'http://localhost:8000'
    with pytest.raises(LinkError):
        normalize_server('http://cognitrix.example.com')


def test_protected_folders_cant_be_shared(tmp_path):
    from pathlib import Path

    from cognitrix.computer.paired.connector import LinkError, check_folders

    with pytest.raises(LinkError):
        check_folders([str(Path.home() / '.ssh')] if (Path.home() / '.ssh').exists() else ['/etc'])
    with pytest.raises(LinkError):
        check_folders([str(tmp_path / 'missing')])
    assert check_folders([str(tmp_path)])[0]['path'] == str(tmp_path.resolve())


async def test_pair_connect_and_run_tools_on_the_pc(server, tmp_path):
    view, connector, shared = await _paired_pc(server, tmp_path)
    try:
        device_id = view['device_id']
        assert view['account'] == 'Kofi Boateng · kofi@example.com'
        assert hub.online(device_id)
        conn = hub.get(device_id)
        assert conn.powers == ['files', 'commands'] and conn.folders[0]['path'] == str(shared.resolve())

        out = await hub.call(device_id, 'Write', {'file_path': 'hello.txt', 'content': 'hi from the web'},
                             power='files')
        assert 'hello.txt' in out
        assert (shared / 'hello.txt').read_text(encoding='utf-8') == 'hi from the web'

        out = await hub.call(device_id, 'Read', {'file_path': str(shared / 'notes.txt')}, power='files')
        assert 'first line' in out

        # Outside the shared folder: refused on the PC.
        outside = tmp_path / 'secret.txt'
        outside.write_text('nope', encoding='utf-8')
        out = await hub.call(device_id, 'Read', {'file_path': str(outside)}, power='files')
        assert 'outside the working folders' in out and 'nope' not in out
        out = await hub.call(device_id, 'Read', {'file_path': '../secret.txt'}, power='files')
        assert 'nope' not in out

        out = await hub.call(device_id, 'Bash', {'command': 'echo paired-ok', 'approved': True}, power='commands')
        assert 'paired-ok' in out

        # The web lists it online, with what the PC reported.
        import httpx
        async with httpx.AsyncClient(base_url=server.url) as http:
            listed = (await http.get('/api/v1/computer/devices')).json()['devices']
        assert listed[0]['online'] is True and listed[0]['name'] == 'Kofi desktop'
    finally:
        await connector.stop()


async def test_the_pc_enforces_pause_and_powers(server, tmp_path):
    from cognitrix.computer.paired.connector import load_config, run_call, save_config

    view, connector, shared = await _paired_pc(server, tmp_path, powers=('files',))
    try:
        device_id = view['device_id']
        with pytest.raises(DeviceUnavailable) as no_commands:
            await hub.call(device_id, 'Bash', {'command': 'echo hi', 'approved': True}, power='commands')
        assert str(no_commands.value) == 'no_commands'
        # Even if the server asked anyway, the PC refuses.
        reply = await run_call(load_config(), {'id': 'x', 'tool': 'Bash', 'args': {'command': 'echo hi'}})
        assert reply['ok'] is False and reply['error'] == 'no_commands'
        reply = await run_call(load_config(), {'id': 'y', 'tool': 'Web Fetch', 'args': {}})
        assert reply['error'] == 'not_allowed'

        cfg = load_config()
        cfg['paused'] = True
        save_config(cfg)
        await connector.push_state()
        assert await _wait(lambda: hub.get(device_id).paused)
        with pytest.raises(DeviceUnavailable):
            await hub.call(device_id, 'Read', {'file_path': 'notes.txt'}, power='files')
    finally:
        await connector.stop()


async def test_a_chat_turn_on_the_pc_sends_file_tools_there(server, tmp_path):
    from cognitrix.computer.paired.turn import chat_device, device_prompt, run_remote
    from cognitrix.tools.base import ToolManager

    view, connector, shared = await _paired_pc(server, tmp_path)
    try:
        session = SimpleNamespace(computer=view['device_id'])
        device = await chat_device(session, 'u1')
        assert device and device['online']
        assert await chat_device(session, 'someone-else') is None
        prompt = device_prompt(device)
        assert 'Kofi desktop' in prompt and str(shared.resolve()) in prompt and 'asks the person' in prompt

        read = ToolManager.get_by_name('Read')
        fetch = ToolManager.get_by_name('Web Fetch') or SimpleNamespace(name='Web Fetch')
        token = set_execution_context(ToolExecutionContext(user_id='u1', device=view['device_id']))
        try:
            assert 'first line' in await run_remote(read, {'file_path': 'notes.txt'})
            assert await run_remote(fetch, {'url': 'https://example.com'}) is None  # runs on the server
        finally:
            reset_execution_context(token)
        # Not a PC turn: nothing is sent.
        assert await run_remote(read, {'file_path': 'notes.txt'}) is None
    finally:
        await connector.stop()

    # Offline: a clear error, no silent move to another computer.
    assert await _wait(lambda: not hub.online(view['device_id']))
    token = set_execution_context(ToolExecutionContext(user_id='u1', device=view['device_id']))
    try:
        out = await run_remote(ToolManager.get_by_name('Read'), {'file_path': 'notes.txt'})
    finally:
        reset_execution_context(token)
    assert 'offline' in out and "Don't switch" in out


async def test_unpairing_on_the_web_makes_the_pc_forget(server, tmp_path):
    import httpx

    from cognitrix.computer.paired.connector import load_config

    view, connector, shared = await _paired_pc(server, tmp_path)
    try:
        async with httpx.AsyncClient(base_url=server.url) as http:
            resp = await http.delete(f"/api/v1/computer/devices/{view['device_id']}")
            assert resp.status_code == 200
            assert (await http.get('/api/v1/computer/devices')).json()['devices'] == []
        assert await _wait(lambda: connector.status == 'unpaired')
        assert not load_config().get('token')
    finally:
        await connector.stop()


async def test_pair_code_flow_over_http(server, tmp_path):
    import httpx

    async with httpx.AsyncClient(base_url=server.url) as http:
        made = (await http.post('/api/v1/computer/devices/pair-codes')).json()
        assert made['expires_in'] > 500
        assert (await http.get(f"/api/v1/computer/devices/pair-codes/{made['code']}")).json()['status'] == 'waiting'
        bad = await http.post('/api/v1/computer/devices/claim', json={'code': 'ZZZZ-ZZZZ'})
        assert bad.status_code == 404 and bad.json()['detail']['code'] == 'bad_code'
        claimed = (await http.post('/api/v1/computer/devices/claim', json={'code': made['code']})).json()
        assert claimed['account'] == 'Kofi Boateng · kofi@example.com'
        paired_out = (await http.post('/api/v1/computer/devices/pair', json={
            'code': made['code'], 'name': 'Laptop', 'folders': [str(tmp_path)], 'powers': ['files']})).json()
        assert paired_out['token'].startswith('cgxd_')
        status = (await http.get(f"/api/v1/computer/devices/pair-codes/{made['code']}")).json()
        assert status['status'] == 'paired' and status['device']['name'] == 'Laptop'
        renamed = (await http.patch(f"/api/v1/computer/devices/{paired_out['device_id']}",
                                    json={'name': 'Work laptop'})).json()
        assert renamed['name'] == 'Work laptop' and renamed['online'] is False


def test_every_command_on_a_pc_needs_a_person():
    from cognitrix.safety.permissions import needs_person
    from cognitrix.tools.base import ToolManager

    bash = ToolManager.get_by_name('Bash')
    token = set_execution_context(ToolExecutionContext(user_id='u1', device='d1'))
    try:
        assert needs_person((bash, bash), {'command': 'ls'}) is True
    finally:
        reset_execution_context(token)


def test_folder_block_names_the_pc_not_the_servers_folders():
    from cognitrix.sessions.base import _with_folder_block

    device = {'id': 'd1', 'name': 'Kofi desktop', 'platform': 'Windows 11', 'online': False,
              'folders': [{'path': 'C:\\code\\app', 'writable': True}], 'powers': ['files']}
    prompt = _with_folder_block([{'role': 'system', 'content': 'base'}], (), {'device': device})
    text = prompt[0]['content']
    assert 'Kofi desktop' in text and 'C:\\code\\app' in text and 'Windows' in text
    assert 'offline' in text and 'Bash will be refused' in text


def test_the_wire_format_is_plain_json():
    from cognitrix.computer.paired.turn import _jsonable

    assert _jsonable({'command': 'ls', 'approved': True, 'parent': object(), 'interface': 'web'}) == {
        'command': 'ls', 'approved': True}
    json.dumps(_jsonable({'a': [1, 2], 'b': object()}))
