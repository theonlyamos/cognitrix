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
from pathlib import Path
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
    monkeypatch.setenv('COGNITRIX_PAIRED_KEYCHAIN', '0')
    from cognitrix.computer.paired import connector as _connector
    monkeypatch.setattr(_connector, '_keychain_token', None)
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


async def test_commands_never_start_in_a_read_only_folder(server, tmp_path):
    from cognitrix.computer.paired.connector import load_config, run_call, save_config

    view, connector, shared = await _paired_pc(server, tmp_path)
    try:
        cfg = load_config()
        cfg['folders'] = [{'path': str(shared), 'writable': False}]
        save_config(cfg)
        reply = await run_call(load_config(), {'id': 'a', 'tool': 'Bash',
                                               'args': {'command': 'echo hi > made.txt', 'approved': True}})
        assert reply['ok'] is False and reply['error'] == 'read_only'
        assert not (shared / 'made.txt').exists()
        # Reading still works there.
        reply = await run_call(load_config(), {'id': 'b', 'tool': 'Read', 'args': {'file_path': 'notes.txt'}})
        assert reply['ok'] and 'first line' in reply['text']
    finally:
        await connector.stop()


async def test_a_helper_agent_only_looks_at_the_pc(server, tmp_path):
    from cognitrix.computer.paired.turn import device_view, run_remote, tools_for
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.utils import delegated_execution_context

    view, connector, shared = await _paired_pc(server, tmp_path)
    try:
        parent = ToolExecutionContext(user_id='u1', device=view['device_id'])
        child = delegated_execution_context(parent)
        assert child.device == view['device_id'] and child.device_read_only
        token = set_execution_context(child)
        try:
            assert 'first line' in await run_remote(ToolManager.get_by_name('Read'), {'file_path': 'notes.txt'})
            out = await run_remote(ToolManager.get_by_name('Write'), {'file_path': 'x.txt', 'content': 'no'})
            assert out.startswith('Error') and not (shared / 'x.txt').exists()
            out = await run_remote(ToolManager.get_by_name('Bash'), {'command': 'echo hi', 'approved': True})
            assert out.startswith('Error')
        finally:
            reset_execution_context(token)
        device = await device_view('u1', view['device_id'], read_only=True)
        assert set(tools_for(device)) == {'read', 'glob', 'grep'}
    finally:
        await connector.stop()


async def test_apps_on_the_pc_use_its_own_screen_and_send_screenshots_back(server, tmp_path, monkeypatch):
    import httpx

    from cognitrix.computer.paired import connector as pc
    from cognitrix.tools import base as tools_base
    from cognitrix.tools.misc import end_image_collection, queue_image, start_image_collection

    seen = {}

    class FakeWindowTool:
        name = 'Look At Window'

        async def run(self, **kw):
            seen.update(kw)
            queue_image('data:image/png;base64,AAAA')
            return 'window 7: Notepad'

    real = tools_base.ToolManager.get_by_name
    monkeypatch.setattr(tools_base.ToolManager, 'get_by_name',
                        classmethod(lambda cls, n: FakeWindowTool() if n == 'Look At Window' else real(n)))
    monkeypatch.setattr(pc, '_screen_image', lambda max_width=1280: 'data:image/jpeg;base64,SCREEN')

    view, connector, shared = await _paired_pc(server, tmp_path, powers=('files',))
    try:
        device_id = view['device_id']
        async with httpx.AsyncClient(base_url=server.url) as http:
            refused = await http.get(f'/api/v1/computer/devices/{device_id}/screen')
            assert refused.status_code == 409 and "doesn't allow apps" in refused.json()['detail']

        cfg = pc.load_config()
        cfg['powers'] = ['files', 'apps']
        pc.save_config(cfg)
        await connector.push_state()
        assert await _wait(lambda: 'apps' in hub.get(device_id).powers)

        token, images = start_image_collection()
        try:
            text = await hub.call(device_id, 'Look At Window', {'window_id': 7, 'computer': 'agent'}, power='apps')
        finally:
            end_image_collection(token)
        assert text == 'window 7: Notepad'
        assert seen['computer'] == 'person'  # always the PC's own screen
        assert images == ['data:image/png;base64,AAAA']

        async with httpx.AsyncClient(base_url=server.url) as http:
            shot = (await http.get(f'/api/v1/computer/devices/{device_id}/screen')).json()
        assert shot['image'] == 'data:image/jpeg;base64,SCREEN'
    finally:
        await connector.stop()


def test_opening_an_app_on_a_pc_needs_a_person():
    from cognitrix.safety.permissions import needs_person
    from cognitrix.tools.base import ToolManager

    open_app = ToolManager.get_by_name('Open App')
    assert not needs_person((open_app,), {'target': 'notepad'})
    token = set_execution_context(ToolExecutionContext(user_id='u1', device='d1'))
    try:
        assert needs_person((open_app,), {'target': 'notepad'})
    finally:
        reset_execution_context(token)


async def test_the_task_worker_reaches_a_pc_through_the_web_process(server, tmp_path, monkeypatch):
    import httpx

    from cognitrix.computer.paired import relay
    from cognitrix.computer.paired.hub import status

    view, connector, shared = await _paired_pc(server, tmp_path)
    try:
        monkeypatch.setenv('COGNITRIX_INTERNAL_URL', server.url)
        # What a worker does: no connection here, so it relays.
        text, _ = await hub._relay_call(view['device_id'], 'Read', {'file_path': 'notes.txt'}, power='files',
                                        ctx={}, timeout=20)
        assert 'first line' in text
        assert (await relay.send('status', {'device_id': view['device_id']}))['online'] is True
        async with httpx.AsyncClient(base_url=server.url) as http:
            forged = await http.post(relay.PATH, json={'op': 'status', 'device_id': view['device_id']},
                                     headers={relay.HEADER: '1:abc'})
        assert forged.status_code == 403
        monkeypatch.setattr(hub, 'serving', False)
        assert (await status('not-connected-here'))['online'] is False  # asked the web process
    finally:
        await connector.stop()


async def test_a_task_waits_for_an_offline_pc_then_tells_the_person(db, monkeypatch):
    from cognitrix.computer.paired import turn
    from cognitrix.tasks import automation
    from cognitrix.tools.base import ToolManager

    told = []

    async def fake_notify(user_id, title, message, **kw):
        told.append((user_id, title, message, kw.get('task_id')))

    monkeypatch.setattr(automation, 'create_notification', fake_notify)
    monkeypatch.setenv('COGNITRIX_PAIRED_TASK_WAIT_S', '0.5')
    monkeypatch.setattr(hub, 'serving', True)
    turn._gave_up.clear()
    token = set_execution_context(ToolExecutionContext(user_id='u1', device='gone', task_id='t1'))
    try:
        out = await turn.run_remote(ToolManager.get_by_name('Read'), {'file_path': 'x'})
        assert 'offline' in out
        assert told and told[0][0] == 'u1' and told[0][3] == 't1'
        # Once it gave up, the task's next call doesn't wait again.
        await turn.run_remote(ToolManager.get_by_name('Read'), {'file_path': 'x'})
        assert len(told) == 1
    finally:
        reset_execution_context(token)


async def test_a_pc_can_hand_tasks_to_the_web_account_only_when_allowed(server, tmp_path, monkeypatch):
    from cognitrix.agents import access as agent_access
    from cognitrix.api.routes import tasks as tasks_routes
    from cognitrix.computer.paired import connector as pc
    from cognitrix.models.user import User
    from cognitrix.tasks.base import Task

    async def fake_user(user_id):
        return SimpleNamespace(id=user_id, disabled=False, email='kofi@example.com', name='Kofi')

    async def fake_agents(owner):
        return [SimpleNamespace(id='a1', name='Assistant'), SimpleNamespace(id='a2', name='Coder')]

    async def no_check(ctx, task):
        return None

    started = []

    async def fake_start(task_id, body=None, ctx=None):
        started.append((task_id, str(ctx.user.id)))
        return {'task_id': task_id, 'run_id': 'r1', 'status': 'queued'}

    monkeypatch.setattr(User, 'get', staticmethod(fake_user))
    monkeypatch.setattr(agent_access, 'visible_agents', fake_agents)
    monkeypatch.setattr(tasks_routes, '_check_task_references', no_check)
    monkeypatch.setattr(tasks_routes, 'start_task_run', fake_start)

    view, connector, shared = await _paired_pc(server, tmp_path)
    try:
        with pytest.raises(pc.LinkError) as refused:
            await pc.remote('GET', '/link/agents')
        assert 'Send tasks' in refused.value.message

        cfg = pc.load_config()
        cfg['powers'] = ['files', 'tasks']
        pc.save_config(cfg)
        await connector.push_state()
        assert await _wait(lambda: 'tasks' in hub.get(view['device_id']).powers)
        assert await _wait_async(lambda: _powers_saved(view['device_id'], 'tasks'))

        agents = (await pc.remote('GET', '/link/agents'))['agents']
        assert [a['name'] for a in agents] == ['Assistant', 'Coder']
        sent = await pc.remote('POST', '/link/tasks', {'title': 'Summarize the logs', 'description': 'From today',
                                                       'steps': ['Read them', ''], 'agent_id': 'a2'})
        task = await Task.get(sent['task_id'])
        assert task.owner_id == 'u1' and task.assigned_agents == ['a2'] and list(task.step_instructions) == ['0']
        assert started == [(sent['task_id'], 'u1')]
        listed = (await pc.remote('GET', '/link/tasks'))['tasks']
        assert listed[0]['id'] == sent['task_id'] and listed[0]['title'] == 'Summarize the logs'
    finally:
        await connector.stop()


async def _powers_saved(device_id, power):
    from cognitrix.computer.paired.store import powers_of
    from cognitrix.models.paired_device import PairedDevice
    device = await PairedDevice.find_one({'id': device_id})
    return power in powers_of(device)


async def _wait_async(make, seconds: float = 10.0):
    for _ in range(int(seconds / 0.05)):
        if await make():
            return True
        await asyncio.sleep(0.05)
    return False


def test_the_pc_token_goes_to_the_keychain_when_there_is_one(tmp_path, monkeypatch):
    from cognitrix.computer.paired import connector, secret_store

    monkeypatch.setenv('COGNITRIX_PAIRED_CONFIG', str(tmp_path / 'paired.json'))
    monkeypatch.setattr(connector, '_keychain_token', None)
    vault: dict[str, str] = {}
    monkeypatch.setattr(secret_store, 'where', lambda: 'Keychain')
    monkeypatch.setattr(secret_store, 'save', lambda token: vault.update(t=token) or True)
    monkeypatch.setattr(secret_store, 'load', lambda: vault.get('t'))
    monkeypatch.setattr(secret_store, 'delete', lambda: vault.clear())

    connector.save_config({'server': 'https://x', 'token': 'cgxd_secret', 'name': 'pc'})
    on_disk = json.loads((tmp_path / 'paired.json').read_text())
    assert 'token' not in on_disk and on_disk['token_store'] == 'keychain'
    assert vault['t'] == 'cgxd_secret'

    monkeypatch.setattr(connector, '_keychain_token', None)  # a fresh process
    cfg = connector.load_config()
    assert cfg['token'] == 'cgxd_secret'
    assert connector.public_config(cfg)['token_kept_in'] == 'Keychain'

    # Saving the loaded config again keeps the token out of the file.
    connector.save_config({**cfg, 'paused': True})
    assert 'token' not in json.loads((tmp_path / 'paired.json').read_text())

    connector.forget_config()
    assert not vault and not (tmp_path / 'paired.json').exists()


def test_the_pc_token_stays_in_the_file_when_the_keychain_refuses(tmp_path, monkeypatch):
    from cognitrix.computer.paired import connector, secret_store

    monkeypatch.setenv('COGNITRIX_PAIRED_CONFIG', str(tmp_path / 'paired.json'))
    monkeypatch.setattr(connector, '_keychain_token', None)
    monkeypatch.setattr(secret_store, 'save', lambda token: False)
    connector.save_config({'server': 'https://x', 'token': 'cgxd_secret'})
    assert json.loads((tmp_path / 'paired.json').read_text())['token'] == 'cgxd_secret'
    assert connector.load_config()['token'] == 'cgxd_secret'


async def test_edits_on_the_pc_can_be_undone_from_the_chat(server, tmp_path, monkeypatch):
    import httpx

    from cognitrix.api.routes import sessions as sessions_routes

    view, connector, shared = await _paired_pc(server, tmp_path)
    device_id = view['device_id']
    chat = {'session_id': 's1'}
    try:
        await hub.call(device_id, 'Edit', {'file_path': 'notes.txt', 'old_string': 'first line',
                                           'new_string': 'agent was here'}, power='files', ctx=chat)
        await hub.call(device_id, 'Write', {'file_path': 'notes.txt', 'content': 'and again'}, power='files', ctx=chat)
        await hub.call(device_id, 'Write', {'file_path': 'new.txt', 'content': 'brand new'}, power='files', ctx=chat)
        # Another chat's edit is its own.
        await hub.call(device_id, 'Write', {'file_path': 'other.txt', 'content': 'x'}, power='files',
                       ctx={'session_id': 's2'})
        assert (shared / 'notes.txt').read_text(encoding='utf-8') == 'and again'

        async def owned(session_id, ctx):
            return None, SimpleNamespace(id=session_id, computer=device_id)
        monkeypatch.setattr(sessions_routes, '_load_authorized_session', owned)
        async with httpx.AsyncClient(base_url=server.url) as http:
            listed = (await http.get('/api/v1/computer/devices/chats/s1/changes')).json()
            kinds = {Path(c['path']).name: c['kind'] for c in listed['changes']}
            assert kinds == {'notes.txt': 'changed', 'new.txt': 'created'}
            assert listed['device']['name'] == 'Kofi desktop'

            # One file back: the version from before the chat touched it.
            out = (await http.post('/api/v1/computer/devices/chats/s1/undo',
                                   json={'paths': [str((shared / 'notes.txt').resolve())]})).json()
            assert out['restored'][0]['ok'] is True
            assert (shared / 'notes.txt').read_text(encoding='utf-8') == 'first line\n'

            # The rest: the new file goes away; s2's file stays.
            out = (await http.post('/api/v1/computer/devices/chats/s1/undo', json={})).json()
            assert [r['ok'] for r in out['restored']] == [True]
            assert not (shared / 'new.txt').exists() and (shared / 'other.txt').exists()
            assert (await http.get('/api/v1/computer/devices/chats/s1/changes')).json()['changes'] == []
    finally:
        await connector.stop()


async def test_an_older_database_gets_the_sent_tasks_column(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'old.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await DBMS.Database.query('CREATE TABLE paireddevices (id TEXT PRIMARY KEY, user_id TEXT, name TEXT)')
    await _ensure_schema()
    cursor = await DBMS.Database.query('PRAGMA table_info(paireddevices)')
    rows = cursor.fetchall() if hasattr(cursor, 'fetchall') else cursor
    assert 'sent_tasks' in {r[1] for r in rows}


async def test_a_task_step_waits_for_its_pc_before_the_step_clock_starts(db, monkeypatch):
    from cognitrix.computer.paired import turn

    monkeypatch.setenv('COGNITRIX_PAIRED_TASK_WAIT_S', '0.4')
    turn._gave_up.clear()
    why = await turn.wait_before_step('pc-gone', task_id='t9', user_id='u1')
    assert why and 'offline' in why

    online = {'online': False}

    async def status(device_id):
        return dict(online)
    monkeypatch.setattr('cognitrix.computer.paired.hub.status', status)
    turn._gave_up.clear()

    async def come_back():
        await asyncio.sleep(0.1)
        online['online'] = True
    asyncio.get_running_loop().create_task(come_back())
    monkeypatch.setenv('COGNITRIX_PAIRED_TASK_WAIT_S', '5')
    assert await turn.wait_before_step('pc-back', task_id='t10', user_id='u1') is None


async def _account():
    from cognitrix.models import User
    await User(name='Kofi Boateng', email='kofi@example.com', password='x').save()
    return await User.find_one({'email': 'kofi@example.com'})


async def test_a_sign_in_code_links_a_pc_and_signing_it_out_unpairs_it(db, server, tmp_path, monkeypatch):
    from cognitrix import signins
    from cognitrix.computer.paired.connector import link, preview
    from cognitrix.models.paired_device import PairedDevice
    from cognitrix.models.sign_in import SignIn

    monkeypatch.delenv('COGNITRIX_CODE_SIGNIN', raising=False)
    signins._tries.clear()
    user = await _account()
    made = await signins.make_code(user)
    shared = tmp_path / 'work'
    shared.mkdir()

    assert (await preview(server.url, made['code']))['account'] == 'Kofi Boateng · kofi@example.com'
    view = await link(server.url, made['code'], name='Kofi laptop', folders=[str(shared)], powers=['files'])
    assert view['account'] == 'Kofi Boateng · kofi@example.com'

    device = (await PairedDevice.find({'user_id': str(user.id)}))[0]
    row = await SignIn.find_one({'sid': device.sign_in_sid})
    assert (row.method, row.kind, row.name) == ('code', 'cli', 'Kofi laptop (cognitrix connect)')
    listed = await signins.listing(user)
    assert [(s['name'], s['linked_pc']) for s in listed] == [('Kofi laptop (cognitrix connect)', True)]

    # The code worked once.
    with pytest.raises(Exception):
        await link(server.url, made['code'], name='Other', folders=[str(shared)], powers=[])

    # Signing it out in Settings, Sign-ins unpairs the PC.
    assert await signins.end(str(user.id), device.sign_in_sid)
    assert await PairedDevice.find({'user_id': str(user.id)}) in ([], None)


async def test_a_pairing_code_pc_shows_in_sign_ins_and_unpairing_signs_it_out(db, server, tmp_path):
    from cognitrix import signins
    from cognitrix.api.routes import computer_devices
    from cognitrix.common.security import AuthContext
    from cognitrix.models.sign_in import SignIn

    user = await _account()
    item = store.make_code(str(user.id), 'Kofi')
    device, _ = await store.pair(item.code, '1.2.3.4', name='Studio PC', platform='Windows', folders=[], powers=[],
                                 client='desktop')
    row = await SignIn.find_one({'sid': device.sign_in_sid})
    assert (row.method, row.kind, row.name) == ('pairing', 'desktop', 'Studio PC (desktop app)')
    assert [s['linked_pc'] for s in await signins.listing(user)] == [True]

    await computer_devices.unpair_device(str(device.id), AuthContext(user=user))
    assert (await SignIn.find_one({'sid': device.sign_in_sid})).ended_at
    assert await signins.listing(user) == []
