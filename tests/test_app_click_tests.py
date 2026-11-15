"""Test App: click-through tests for the apps agents build.

- Steps are checked before anything starts (one action each, known keys).
- Tests run on a throwaway copy of the live version: its own port and data
  folder (empty, or a copy), removed afterwards; the live app's data is untouched.
- With a browser: clicks, typing, drop-downs, Enter, dialogs, reloads and
  navigation work through the same stand-in proxy as Check App; a failed step
  stops the run with the reason, the page's buttons and fields, and a screenshot.
- The tool saves passing tests to the app's app-tests.json and re-runs them.
- The stand-in proxy answers CORS preflights like the real one.
"""
import json
import sys
import textwrap
import threading
import urllib.request
from types import SimpleNamespace

import pytest
from test_hosted_apps import AGENT, _as, _tool, _user, db, make_app  # noqa: F401 (db is a fixture)

from cognitrix.computer.sandbox import pagecheck

TODO_SRC = textwrap.dedent('''
    import http.server, json, os, sqlite3
    DB = sqlite3.connect(os.path.join(os.environ['APP_DATA_DIR'], 'todo.db'), check_same_thread=False)
    DB.execute('create table if not exists t (id integer primary key, title text)')
    DB.commit()
    PAGE = """<!doctype html><html><head><title>Todo</title></head><body>
    <h1>Todo</h1><p id=who>%s</p>
    <form id=f><label for=title>Title</label><input id=title placeholder="What needs doing?">
    <select id=pri aria-label=Priority><option value=1>Low</option><option value=2>High</option></select>
    <button type=submit>Add item</button></form>
    <ul id=list></ul><a href="about">About</a>
    <button id=broken onclick="notDefined()">Break</button>
    <script>
    async function load() {
      const items = await (await fetch('api/items')).json();
      document.getElementById('list').innerHTML = items.map(
        i => `<li>${i.title} <button class=del data-id=${i.id}>Delete</button></li>`).join('');
    }
    document.getElementById('f').addEventListener('submit', async e => {
      e.preventDefault();
      const t = document.getElementById('title');
      await fetch('api/items', {method: 'POST', body: JSON.stringify({title: t.value})});
      t.value = ''; load();
    });
    document.getElementById('list').addEventListener('click', async e => {
      if (!e.target.classList.contains('del') || !confirm('Delete it?')) return;
      await fetch('api/items/' + e.target.dataset.id, {method: 'DELETE'});
      load();
    });
    load();
    </script></body></html>"""
    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, body, kind='text/html', code=200):
            body = body.encode()
            self.send_response(code); self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if self.path == '/':
                return self._send(PAGE % (self.headers.get('X-Cognitrix-User-Name') or ''))
            if self.path == '/about':
                return self._send('<title>About</title><p>About this app</p><a href="./">Back</a>')
            if self.path == '/api/items':
                rows = [{'id': i, 'title': t} for i, t in DB.execute('select id, title from t')]
                return self._send(json.dumps(rows), 'application/json')
            self._send('nope', code=404)
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            DB.execute('insert into t (title) values (?)', (data.get('title'),)); DB.commit()
            self._send('{}', 'application/json')
        def do_DELETE(self):
            DB.execute('delete from t where id = ?', (int(self.path.rsplit('/', 1)[1]),)); DB.commit()
            self._send('{}', 'application/json')
        def log_message(self, *a):
            pass
    http.server.ThreadingHTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()
''')

FLOW = [
    {'see': 'Todo'},
    {'fill': 'Title', 'with': 'Buy milk'},
    {'select': 'Priority', 'option': 'High'},
    {'click': 'Add item'},
    {'expect': '#list li', 'count': 1},
    {'expect': 'Title', 'value': ''},
    {'fill': 'What needs doing?', 'with': 'Walk dog'},
    {'press': 'Enter'},
    {'expect': '#list li', 'count': 2},
    {'reload': True},
    {'see': 'Walk dog'},
    {'click': 'Delete', 'dialog': 'dismiss'},
    {'expect': '#list li', 'count': 2},
    {'click': 'Delete'},
    {'expect': '#list li', 'count': 1},
    {'not_see': 'Buy milk'},
    {'click': 'About'},
    {'url': 'about'},
    {'see': 'About this app'},
    {'click': 'Back'},
    {'js': "document.title === 'Todo'"},
    {'screenshot': 'home'},
]

async def _run(tool_name, **params):
    from cognitrix.tools.base import ToolManager

    return (await ToolManager.get_by_name(tool_name).run(**params, parent=AGENT)).outcome


HAS_BROWSER = pagecheck.find_browser() is not None
needs_browser = pytest.mark.skipif(not HAS_BROWSER, reason='no Chromium-family browser installed')


def make_todo(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'app.py').write_text(TODO_SRC, encoding='utf-8')
    return f'{sys.executable} app.py'


def _skip_if_browser_cannot_start(out):
    if not out.get('ok') and 'browser' in str(out.get('error', '')).lower():
        # e.g. CI runners where Chrome's own sandbox can't start.
        pytest.skip(f'browser could not start here: {out.get("error")}')


# --- steps -----------------------------------------------------------------------

def test_steps_are_checked_before_anything_runs():
    from cognitrix.computer.sandbox.apptest import check_steps, describe

    assert check_steps('[{"click": "Add"}]') == [{'click': 'Add'}]
    assert check_steps({'steps': [{'see': 'x'}]}) == [{'see': 'x'}]
    for bad, why in [([], 'non-empty'), ('nope', 'not valid JSON'), (['click Add'], 'is text'),
                     ([{'click': 'a', 'see': 'b'}], 'exactly one action'), ([{'tap': 'a'}], 'exactly one action'),
                     ([{'fill': 'Title'}], '"with"'), ([{'select': 'P'}], '"option"'),
                     ([{'press': 'Hyper'}], 'unknown key'), ([{'see': 'x'}] * 61, 'At most')]:
        with pytest.raises(ValueError, match=why):
            check_steps(bad)
    assert describe({'fill': 'Title', 'with': 'Milk'}) == 'fill "Title" with "Milk"'
    assert describe({'expect': '#list li', 'count': 2}) == 'expect "#list li" (count 2)'


# --- the throwaway copy ----------------------------------------------------------

def test_scratch_copy_has_its_own_port_and_data(tmp_path):
    from cognitrix.computer.sandbox.apps import AppHost, AppHostError, pack_folder

    host = AppHost(str(tmp_path / 'host'))
    try:
        cmd = make_app(tmp_path / 'src', 'hi')
        assert host.deploy('demo', 1, pack_folder(str(tmp_path / 'src')), cmd, '', {}, 0)['ok']
        live = host.data_dir('demo')
        with open(f'{live}/keep.txt', 'w') as fh:
            fh.write('real data')
        with host.scratch('demo') as copy:
            assert copy.port != host.port_for('demo') and copy.version == 1
            got = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{copy.port}/').read())
            assert got['msg'] == 'hi' and got['data'] != live and not (tmp_path / got['data'] / 'keep.txt').exists()
            fresh_dir = got['data']
        assert not (tmp_path / fresh_dir).exists()  # removed afterwards
        with host.scratch('demo', 'copy') as copy:
            got = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{copy.port}/').read())
            with open(f'{got["data"]}/keep.txt') as fh:
                assert fh.read() == 'real data'
            with open(f'{got["data"]}/keep.txt', 'w') as fh:
                fh.write('changed by a test')
        with open(f'{live}/keep.txt') as fh:
            assert fh.read() == 'real data'
        assert host.status('demo')['status'] == 'online'  # the live one kept running
        with pytest.raises(AppHostError):
            with host.scratch('missing'):
                pass
    finally:
        host.shutdown()


def test_stand_in_proxy_answers_preflights(tmp_path):
    """Opaque-origin pages send OPTIONS before DELETE/PUT; the real proxy answers them, so the stand-in must too."""
    from cognitrix.computer.sandbox.apps import AppHost, pack_folder

    host = AppHost(str(tmp_path / 'host'))
    try:
        cmd = make_app(tmp_path / 'src')
        assert host.deploy('demo', 1, pack_folder(str(tmp_path / 'src')), cmd, '', {}, 0)['ok']
        hosting = pagecheck._Hosting(host.port_for('demo'))
        try:
            req = urllib.request.Request(hosting.url + 'api/items/1', method='OPTIONS')
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 204 and resp.headers['Access-Control-Allow-Methods'] == '*'
        finally:
            hosting.close()
    finally:
        host.shutdown()


# --- clicking through (needs a browser) ------------------------------------------

@needs_browser
def test_a_whole_flow_passes_on_a_fresh_copy_and_leaves_live_data_alone(tmp_path):
    from cognitrix.computer.sandbox.apps import AppHost, pack_folder
    from cognitrix.computer.sandbox.apptest import test_app_copy

    host = AppHost(str(tmp_path / 'host'))
    try:
        cmd = make_todo(tmp_path / 'src')
        assert host.deploy('todo', 1, pack_folder(str(tmp_path / 'src')), cmd, '', {}, 0)['ok']
        out = test_app_copy(host, 'todo', FLOW, headers={'X-Cognitrix-User-Name': 'Amos'}, no_sandbox=True)
        _skip_if_browser_cannot_start(out)
        assert out['ok'] and out['passed'], json.dumps(out.get('steps'), indent=1)
        steps = out['steps']
        assert len(steps) == len(FLOW) and all(s['ok'] for s in steps)
        assert steps[11]['dialogs'] == ['Delete it?'] and steps[13]['dialogs'] == ['Delete it?']
        assert steps[17]['note'] == '/about'
        assert [s['label'] for s in out['shots']] == ['home'] and out['shots'][0]['data']
        assert not out['errors'] and not out['failed']
        # The live app never saw any of it.
        live = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{host.port_for("todo")}/api/items').read())
        assert live == []
        # A second run starts empty again, so the same steps pass again.
        again = test_app_copy(host, 'todo', FLOW[:5], no_sandbox=True)
        assert again['passed'], again['steps']
        # On a phone too (the page has no viewport tag, so it is zoomed out).
        phone = test_app_copy(host, 'todo', FLOW, phone=True, no_sandbox=True)
        assert phone['passed'] and phone['width'] == 390, phone['steps']
    finally:
        host.shutdown()


@needs_browser
def test_a_failed_step_says_why_and_what_is_on_the_page(tmp_path):
    from cognitrix.computer.sandbox.apps import AppHost, pack_folder
    from cognitrix.computer.sandbox.apptest import test_app_copy

    host = AppHost(str(tmp_path / 'host'))
    try:
        cmd = make_todo(tmp_path / 'src')
        assert host.deploy('todo', 1, pack_folder(str(tmp_path / 'src')), cmd, '', {}, 0)['ok']
        out = test_app_copy(host, 'todo', [{'see': 'Todo'}, {'click': 'Save changes', 'timeout': 1}, {'see': 'x'}],
                            no_sandbox=True)
        _skip_if_browser_cannot_start(out)
        assert out['ok'] and not out['passed'] and out['total'] == 3 and len(out['steps']) == 2
        assert 'Nothing on the page matches "Save changes"' in out['steps'][1]['error']
        assert 'button "Add item"' in out['controls'] and 'Todo' in out['page_text']
        assert out['screenshot'] and 'log' in out
        # Script errors are tied to the step that caused them and fail the test.
        broken = test_app_copy(host, 'todo', [{'click': 'Break'}], no_sandbox=True)
        assert broken['steps'][0]['ok'] and not broken['passed']
        assert any('notDefined' in e for e in broken['steps'][0]['errors'])
        # A disabled or covered button is not clicked.
        covered = test_app_copy(host, 'todo', [
            {'js': "(() => { const d = document.createElement('div'); d.style.cssText = "
                   "'position:fixed;inset:0;background:#000'; d.id = 'veil'; document.body.append(d); return true })()"},
            {'click': 'Add item', 'timeout': 1}], no_sandbox=True)
        assert 'is covered by div#veil' in covered['steps'][1]['error']
    finally:
        host.shutdown()


@needs_browser
def test_the_sandbox_supervisor_runs_tests_too(tmp_path, monkeypatch):
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
        cmd = make_todo(tmp_path / 'src')
        assert runner.deploy('todo', 1, pack_folder(str(tmp_path / 'src')), cmd, '', {}, 0)['ok']
        out = runner.test('todo', [{'see': 'Amos'}, {'fill': 'Title', 'with': 'Milk'}, {'click': 'Add item'},
                                   {'see': 'Milk'}], '', False, {'X-Cognitrix-User-Name': 'Amos'})
        _skip_if_browser_cannot_start(out)
        assert out['passed'], out['steps']
        bad = runner.test('todo', [{'tap': 'x'}])
        assert not bad['ok'] and bad['bad_steps']
    finally:
        server.shutdown()
        apps.shutdown()


# --- the tool --------------------------------------------------------------------

@needs_browser
async def test_test_app_tool_saves_and_reruns_tests(db, tmp_path):  # noqa: F811
    from cognitrix.hosted_apps import store
    from cognitrix.tools import misc

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, session_id='s1', agent_id='a1')
    cmd = make_todo(store.apps_root() / 'todo')
    assert (await _tool('publish_app', title='Todo', start_command=cmd)).status == 'success'
    token, images = misc.start_image_collection()
    try:
        out = await _run('Test App', app_id='todo', name='add an item', save=True,
                          steps=[{'see': 'Amos'}, {'fill': 'Title', 'with': 'Milk'}, {'click': 'Add item'},
                                 {'see': 'Milk'}, {'reload': True}, {'see': 'Milk'}])
    finally:
        misc.end_image_collection(token)
    if 'could not run' in out.text and 'browser' in out.text.lower():
        pytest.skip(out.text)
    assert out.status == 'success', out.text
    assert 'PASSED, 6 of 6 steps passed' in out.text and 'on a fresh copy of "Todo" v1 (desktop)' in out.text
    assert '2. ✓ fill "Title" with "Milk"' in out.text and 'Saved as "add an item"' in out.text
    assert images and images[0].startswith('data:image/png;base64,')
    app = await store.by_slug('todo')
    saved = json.loads((store.apps_root() / 'todo' / 'app-tests.json').read_text())
    assert saved['tests'][0]['name'] == 'add an item' and len(saved['tests'][0]['steps']) == 6

    # A failing test is reported and not saved.
    failed = await _run('Test App', app_id='todo', name='edit', save=True,
                         steps=[{'click': 'Edit', 'timeout': 1}])
    assert 'FAILED, 0 of 1 steps passed' in failed.text and 'Not saved, because it failed' in failed.text
    assert 'Buttons, links and fields there:' in failed.text and "Don't tell the user" in failed.text
    assert len(store.saved_tests(app)) == 1

    # No steps: every saved test runs again.
    rerun = await _run('Test App', app_id='todo')
    assert 'Test "add an item"' in rerun.text and 'PASSED' in rerun.text
    nothing = await _run('Test App', app_id='todo', name='missing')
    assert nothing.status != 'success' and 'No saved test called "missing"' in nothing.text
    # Publishing again points at the saved tests.
    again = await _tool('publish_app', title='Todo', start_command=cmd, app_id=str(app.id))
    assert 'no steps re-runs its saved tests' in again.text


async def test_test_app_tool_without_a_browser_or_with_bad_steps(db, monkeypatch):  # noqa: F811
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, session_id='s1', agent_id='a1')
    cmd = make_todo(store.apps_root() / 'todo')
    assert (await _tool('publish_app', title='Todo', start_command=cmd)).status == 'success'
    bad = await _run('Test App', app_id='todo', steps=[{'tap': 'Add'}])
    assert bad.status != 'success' and 'exactly one action' in bad.text
    none = await _run('Test App', app_id='todo')
    assert none.status != 'success' and 'No saved tests' in none.text
    (store.apps_root() / 'todo' / 'app-tests.json').write_text('{"tests": [{"name": "x", "steps": "oops"}]}')
    broken = await _run('Test App', app_id='todo')
    assert broken.status != 'success' and 'app-tests.json' in broken.text
    monkeypatch.setenv('COGNITRIX_CHECK_BROWSER', 'off')
    out = await _run('Test App', app_id='todo', steps=[{'see': 'Todo'}])
    assert out.status != 'success' and 'No Edge, Chrome or Chromium' in out.text
    # People the app is only shared with can't run tests (they'd see its log).
    bea = await _user('Bea', 'bea@example.com')
    app = await store.by_slug('todo')
    await store.set_sharing(app, people={str(bea.id): 'view'})
    _as(bea, session_id='s2', agent_id='a2')
    assert 'not test it' in (await _run('Test App', app_id=str(app.id), steps=[{'see': 'Todo'}])).text


def test_test_app_never_asks_for_approval():
    from cognitrix.safety.destructive_ops import DestructiveOpDetector

    assert DestructiveOpDetector().analyze('Test App', {'app_id': 'x', 'steps': []}).risk_level.value == 'low'
