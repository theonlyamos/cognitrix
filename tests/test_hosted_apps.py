"""Apps: live web apps agents publish.

- AppHost runs versions side by side: a new one goes live only when it answers
  HTTP; a broken one leaves the live one serving. Sleep, wake, roll back.
- Uploads leave dependency folders out and can't write outside the app folder.
- Publish App (local runtime) makes an app and adds versions; App Status shows logs.
- The proxy needs a viewer token for this app, checks sharing, sandboxes the
  page, tells the app who is looking, and shows a waking page while it starts.
- The sandbox supervisor's /apps endpoints and proxy work the same way.
- Publish App asks first on this machine, not in the sandboxed computer.
"""
import io
import json
import os
import sys
import tarfile
import textwrap
import time
from types import SimpleNamespace

import httpx
import pytest

APP_SRC = textwrap.dedent('''
    import http.server, json, os, sys
    if os.environ.get('BREAK'):
        sys.exit(3)
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/go'):
                self.send_response(302); self.send_header('Location', '/landing'); self.end_headers(); return
            body = json.dumps({'path': self.path, 'user': self.headers.get('X-Cognitrix-User-Name'),
                               'prefix': self.headers.get('X-Forwarded-Prefix'),
                               'version': os.environ['APP_VERSION'], 'data': os.environ['APP_DATA_DIR'],
                               'msg': open('msg.txt').read().strip(),
                               'api': bool(os.environ.get('COGNITRIX_API_KEY'))}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', 'x=1')
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            print('request', self.path, flush=True)
    print('listening', flush=True)
    http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()
''')


def make_app(folder, msg='hello', broken=False):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'app.py').write_text(APP_SRC)
    (folder / 'msg.txt').write_text(msg)
    (folder / 'node_modules').mkdir(exist_ok=True)
    (folder / 'node_modules' / 'big.js').write_text('x')
    cmd = f'{sys.executable} app.py'
    return ('BREAK=1 ' + cmd) if broken else cmd


@pytest.fixture
def host(tmp_path):
    from cognitrix.computer.sandbox.apps import AppHost

    h = AppHost(str(tmp_path / 'host'))
    yield h
    h.shutdown()


def _get(port, path='/'):
    return json.loads(httpx.get(f'http://127.0.0.1:{port}{path}', timeout=5).text)


# --- AppHost ------------------------------------------------------------------

def test_pack_skips_dependency_folders_and_unpack_stays_inside(tmp_path):
    from cognitrix.computer.sandbox.apps import AppHostError, pack_folder, unpack

    make_app(tmp_path / 'src')
    data = pack_folder(str(tmp_path / 'src'))
    names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
    assert 'app.py' in names and not any('node_modules' in n for n in names)

    evil = io.BytesIO()
    with tarfile.open(fileobj=evil, mode='w:gz') as tar:
        info = tarfile.TarInfo('../escape.txt')
        info.size = 1
        tar.addfile(info, io.BytesIO(b'x'))
    with pytest.raises(AppHostError):
        unpack(evil.getvalue(), str(tmp_path / 'dest'))
    assert not (tmp_path / 'escape.txt').exists()


def test_app_paths_stay_inside_the_apps_folder(host):
    from cognitrix.computer.sandbox.apps import AppHostError

    assert host.release_dir('board', 2).endswith(os.path.join('releases', 'board', 'v2'))
    for bad in ('../board', 'a/../../x', '', '.'):
        with pytest.raises(AppHostError):
            host.data_dir(bad)


def test_new_version_goes_live_only_when_it_answers(host, tmp_path):
    from cognitrix.computer.sandbox.apps import pack_folder

    cmd = make_app(tmp_path / 'src', 'one')
    first = host.deploy('demo', 1, pack_folder(str(tmp_path / 'src')), cmd)
    assert first['ok'] and first['status'] == 'online'
    got = _get(first['port'])
    assert got['msg'] == 'one' and got['version'] == '1' and got['data'].endswith(os.path.join('data', 'demo'))

    make_app(tmp_path / 'src', 'two')
    bad = host.deploy('demo', 2, pack_folder(str(tmp_path / 'src')), make_app(tmp_path / 'src', 'two', broken=True))
    assert not bad['ok'] and bad['status'] == 'failed' and 'Version 1 keeps serving' in bad['error']
    assert _get(host.port_for('demo'))['msg'] == 'one'

    good = host.deploy('demo', 3, pack_folder(str(tmp_path / 'src')), cmd)
    assert good['ok'] and _get(good['port'])['msg'] == 'two'
    assert 'starting v3' in host.logs('demo')

    back = host.promote('demo', 1)
    assert back['ok'] and _get(back['port'])['msg'] == 'one'


def test_sleep_and_wake(host, tmp_path):
    from cognitrix.computer.sandbox.apps import pack_folder

    cmd = make_app(tmp_path / 'src')
    host.deploy('nap', 1, pack_folder(str(tmp_path / 'src')), cmd)
    host.stop('nap')
    assert host.status('nap')['status'] == 'asleep' and host.port_for('nap', wake=False) is None
    port = host.port_for('nap', wake=True)
    assert port and _get(port)['msg'] == 'hello'


def test_crashed_app_is_restarted(host, tmp_path):
    from cognitrix.computer.sandbox.apps import pack_folder

    cmd = make_app(tmp_path / 'src')
    out = host.deploy('crashy', 1, pack_folder(str(tmp_path / 'src')), cmd)
    host.running['crashy'].proc.kill()
    deadline = time.time() + 20
    while time.time() < deadline:
        run = host.running.get('crashy')
        if run and run.proc.poll() is None and run.port != out['port'] or (run and run.restarts):
            break
        time.sleep(0.5)
    assert host.status('crashy')['status'] == 'online'


# --- store, tools and proxy (local runtime) ----------------------------------------

@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings
    from cognitrix.hosted_apps import runner

    db_file = str(tmp_path / 'apps.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'off')
    monkeypatch.delenv('COGNITRIX_APPS_LOCAL', raising=False)
    runner._local_host = None
    yield tmp_path
    if runner._local_host is not None:
        runner._local_host.shutdown()
        runner._local_host = None


async def _user(name, email):
    from cognitrix.models import User

    await User(name=name, email=email, password='x' * 20).save()
    return await User.find_one({'email': email})


def _as(user, **kw):
    from cognitrix.tools.utils import ToolExecutionContext, set_execution_context

    return set_execution_context(ToolExecutionContext(user_id=str(user.id), **kw))


AGENT = SimpleNamespace(id='a1', name='Builder')


async def _tool(name, **params):
    from cognitrix.tools.base import ToolManager

    return (await ToolManager.get_by_name(name).run(**params, parent=AGENT)).outcome


def _proxy_client():
    from fastapi import FastAPI

    from cognitrix.api.routes.hosted_apps import app_proxy

    api = FastAPI()
    api.include_router(app_proxy)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test')


async def test_publish_app_tool_and_versions(db, tmp_path):
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, session_id='s1', agent_id='a1')
    cmd = make_app(tmp_path / 'build', 'v-one')
    out = await _tool('publish_app', title='Support queue', folder=str(tmp_path / 'build'), start_command=cmd)
    assert out.status == 'success', out.text
    app = await store.get_app(out.entities[0].id)
    assert app.slug == 'support-queue' and app.status == 'online' and app.version == 1 and app.runtime == 'local'

    bad = make_app(tmp_path / 'build', 'v-two', broken=True)
    failed = await _tool('publish_app', title='Support queue', folder=str(tmp_path / 'build'), start_command=bad)
    assert failed.status != 'success' and 'Version 1 keeps serving' in failed.text
    app = await store.get_app(app.id)
    assert app.version == 1 and app.latest == 2 and app.status == 'online'

    # Same title from the same chat updates the app (no app_id needed).
    again = await _tool('publish_app', title='Support queue', folder=str(tmp_path / 'build'), start_command=cmd,
                        note='fixed')
    assert again.status == 'success' and 'version 3' in again.text
    rows = await store.versions(await store.get_app(app.id))
    assert [(r.number, r.status) for r in rows] == [(3, 'live'), (2, 'failed'), (1, 'ok')]

    status = await _tool('app_status', app_id='support-queue')
    assert 'live version 3' in status.text and 'starting v3' in status.text


async def test_proxy_access_sandboxing_and_headers(db, tmp_path):
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Board', folder=str(tmp_path / 'b'),
                                    command=make_app(tmp_path / 'b', 'hi'))
    async with _proxy_client() as client:
        # No token: off to the sign-in page that fetches one.
        r = await client.get('/a/board/')
        assert r.status_code == 302 and r.headers['location'] == '/apps/open/board'
        r = await client.get('/a/board/~nonsense/')
        assert r.status_code == 403
        # Names that aren't app names never become redirects.
        assert (await client.get('/a/Bad%5Cevil.com/')).status_code == 404
        assert (await client.get('/a/board/~bad%5Ctoken')).status_code == 404

        token = store.open_token('board', str(amos.id))
        r = await client.get(f'/a/board/~{token}/items?q=1', headers={'Accept': 'application/json'})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['path'] == '/items?q=1' and body['user'] == 'Amos' and body['prefix'] == f'/a/board/~{token}'
        assert 'sandbox allow-scripts' in r.headers['content-security-policy']
        assert 'allow-same-origin' not in r.headers['content-security-policy']
        assert 'set-cookie' not in r.headers
        r = await client.get(f'/a/board/~{token}/go')
        assert r.status_code == 302 and r.headers['location'] == f'/a/board/~{token}/landing'

        # Bea can't open it until it's shared; a token for another app doesn't work either.
        bea_token = store.open_token('board', str(bea.id))
        assert (await client.get(f'/a/board/~{bea_token}/')).status_code == 403
        await store.set_sharing(app, everyone='view')
        assert (await client.get(f'/a/board/~{bea_token}/')).json()['user'] == 'Bea'
        other = store.open_token('other', str(amos.id))
        assert (await client.get(f'/a/board/~{other}/')).status_code == 403

        # Page views are counted for navigations.
        await client.get(f'/a/board/~{token}/', headers={'Accept': 'text/html'})
        assert sum((await store.get_app(app.id)).visits.values()) >= 1


async def test_idle_apps_sleep_and_wake_on_visit(db, tmp_path):
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Nap', folder=str(tmp_path / 'n'),
                                    command=make_app(tmp_path / 'n'))
    await store._set(app, sleep_after=1)
    store._last_active['nap'] = time.time() - 120
    assert await store.sleep_idle() == ['nap']
    app = await store.get_app(app.id)
    assert app.status == 'asleep'

    token = store.open_token('nap', str(amos.id))
    async with _proxy_client() as client:
        r = await client.get(f'/a/nap/~{token}/', headers={'Accept': 'text/html'})
        assert r.status_code == 503 and 'Waking up Nap' in r.text
        # A request that isn't a page load waits for it to start.
        r = await client.get(f'/a/nap/~{token}/x', headers={'Accept': 'application/json'})
        assert r.status_code == 200 and r.json()['msg'] == 'hello'
    assert (await store.get_app(app.id)).status == 'online'


async def test_app_api_key_rotates_only_when_live(db, tmp_path):
    from cognitrix.hosted_apps import store
    from cognitrix.models.api_key import APIKey

    amos = await _user('Amos', 'amos@example.com')
    cmd = make_app(tmp_path / 'k')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Keyed', folder=str(tmp_path / 'k'), command=cmd,
                                    use_cognitrix_api=True)
    first = app.api_key_id
    assert first and (await APIKey.get(first)).scopes == store.API_SCOPES
    app, row, _ = await store.publish(user_key=str(amos.id), title='Keyed', folder=str(tmp_path / 'k'),
                                      command=make_app(tmp_path / 'k', broken=True), app_id=str(app.id))
    assert row.status == 'failed' and app.api_key_id == first and not (await APIKey.get(first)).revoked
    app, row, _ = await store.publish(user_key=str(amos.id), title='Keyed', folder=str(tmp_path / 'k'), command=cmd,
                                      app_id=str(app.id))
    assert row.status == 'live' and app.api_key_id != first and (await APIKey.get(first)).revoked
    await store.delete_app(app)
    assert (await APIKey.get(app.api_key_id)).revoked


# --- the sandbox supervisor's endpoints ------------------------------------------

def test_supervisor_apps_endpoints_and_proxy(tmp_path, monkeypatch):
    import threading

    from cognitrix.computer.sandbox import supervisor
    from cognitrix.computer.sandbox.apps import AppHost, free_port, pack_folder
    from cognitrix.computer.sandbox.client import Sandbox
    from cognitrix.hosted_apps.runner import SandboxRunner

    monkeypatch.setenv('COGNITRIX_SANDBOX_TOKEN', 'secret-token')
    apps = AppHost(str(tmp_path / 'box'))
    port = free_port()
    server = supervisor.make_server(SimpleNamespace(desktops={}), port=port, host='127.0.0.1', apps=apps)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        runner = SandboxRunner.__new__(SandboxRunner)
        runner.box = Sandbox('box', 'secret-token', port, 0)
        cmd = make_app(tmp_path / 'src', 'boxed')
        out = runner.deploy('boxed', 1, pack_folder(str(tmp_path / 'src')), cmd, '', {}, 0)
        assert out['ok'], out
        base, headers = runner.target('boxed')
        r = httpx.get(f'{base}/hello?x=1', headers=headers, timeout=10)
        assert r.json()['msg'] == 'boxed' and r.json()['path'] == '/hello?x=1'
        assert httpx.get(f'{base}/hello', timeout=10).status_code == 401  # no token, no app
        runner.stop('boxed')
        assert runner.status('boxed')['status'] == 'asleep'
        assert httpx.get(f'{base}/', headers=headers, timeout=30).json()['msg'] == 'boxed'  # woken by the request
        assert 'starting v1' in runner.logs('boxed')
    finally:
        server.shutdown()
        apps.shutdown()


# --- approvals ---------------------------------------------------------------------

def test_publish_app_asks_only_on_this_machine(monkeypatch):
    from cognitrix.safety.destructive_ops import DestructiveOpDetector

    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'off')
    local = DestructiveOpDetector().analyze('Publish App', {'start_command': 'python3 app.py'})
    assert local.risk_level.value == 'high'
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    boxed = DestructiveOpDetector().analyze('Publish App', {'start_command': 'python3 app.py'})
    assert boxed.risk_level.value == 'low'
    assert DestructiveOpDetector().analyze('App Status', {}).risk_level.value == 'low'
