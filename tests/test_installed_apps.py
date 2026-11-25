"""Installed Apps: what an app may ask the person for (window.cognitrix).

- The proxy puts the runtime first in an app's HTML pages, and leaves other
  responses (JSON, scripts) alone.
- Notifications and connected apps are the person's own answer, per app;
  nothing works until they say yes, and an app's code can't answer for them.
- Connected apps: reads run; a tool that changes something needs the person's
  yes for that one call (409 'confirm' until then).
- Deleting an app drops everyone's answers.
"""
import sys
import textwrap
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_hosted_apps import _proxy_client, _user, db  # noqa: F401  (fixture)

HTML_APP = textwrap.dedent('''
    import http.server, os
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/data'):
                body, kind = b'{"ok": true}', 'application/json'
            else:
                body, kind = b'<!doctype html><html><head><title>T</title></head><body>hi</body></html>', 'text/html; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()
''')


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


def test_runtime_goes_first_in_the_head():
    from cognitrix.hosted_apps.bridge import SCRIPT, inject

    page = b'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"></head><body><header>x</header></body></html>'
    out = inject(page)
    assert out.index(SCRIPT) == page.index(b'<head>') + len(b'<head>')
    # No <head>: after <html>; only a doctype: after it (never before, which would mean quirks mode).
    assert inject(b'<html><body>x</body></html>').startswith(b'<html>' + SCRIPT)
    assert inject(b'<!doctype html><p>x').startswith(b'<!doctype html>' + SCRIPT)
    assert inject(b'<p>x').startswith(SCRIPT)
    # <header> is not <head>, and a page that already has it isn't changed twice.
    assert inject(b'<body><header>x</header>').startswith(SCRIPT)
    assert inject(out) == out


async def test_proxy_adds_the_runtime_to_pages_only(db, tmp_path):  # noqa: F811
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    folder = tmp_path / 'h'
    folder.mkdir()
    (folder / 'app.py').write_text(HTML_APP)
    await store.publish(user_key=str(amos.id), title='Pager', folder=str(folder), command=f'{sys.executable} app.py')
    token = store.open_token('pager', str(amos.id))
    async with _proxy_client() as client:
        page = await client.get(f'/a/pager/~{token}/', headers={'Accept': 'text/html'})
        assert page.status_code == 200 and 'window.cognitrix' in page.text
        assert page.text.index('data-cognitrix-runtime') < page.text.index('<title>')
        assert 'sandbox allow-scripts' in page.headers['content-security-policy']
        data = await client.get(f'/a/pager/~{token}/data')
        assert data.json() == {'ok': True}


async def _app_for(owner):
    from cognitrix.hosted_apps.models import HostedApp

    app = HostedApp(owner_id=str(owner.id), slug='inbox', title='Inbox', status='online', version=1)
    await app.save()
    return (await HostedApp.find({'slug': 'inbox'}))[0]


async def test_permissions_are_the_persons_own_answer(db):  # noqa: F811
    from cognitrix.api.routes import hosted_apps as routes
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    app = await _app_for(amos)
    ref = str(app.id)

    assert await routes.get_permissions(ref, _ctx(amos)) == {'allowed': [], 'asked': [], 'powers': ['notify', 'apps']}
    out = await routes.set_permission(ref, routes.PermissionAnswer(power='notify', allowed=True), _ctx(amos))
    assert out['allowed'] == ['notify'] and out['asked'] == ['notify']
    out = await routes.set_permission(ref, routes.PermissionAnswer(power='apps', allowed=False), _ctx(amos))
    assert out['allowed'] == ['notify'] and out['asked'] == ['notify', 'apps']
    with pytest.raises(HTTPException) as bad:
        await routes.set_permission(ref, routes.PermissionAnswer(power='camera', allowed=True), _ctx(amos))
    assert bad.value.status_code == 400

    # Someone the app isn't shared with can't see it at all; once shared, they start from nothing.
    with pytest.raises(HTTPException):
        await routes.get_permissions(ref, _ctx(bea))
    await store.set_sharing(app, people={str(bea.id): 'view'})
    assert (await routes.get_permissions(ref, _ctx(bea)))['allowed'] == []

    # Deleting the app drops every answer.
    from cognitrix.hosted_apps.models import HostedAppGrant

    assert len(await HostedAppGrant.find({'app_id': ref}) or []) == 1
    await store.delete_app(app)
    assert not await HostedAppGrant.find({'app_id': ref})


async def test_connected_apps_read_freely_and_ask_before_each_change(db, monkeypatch):  # noqa: F811
    from cognitrix.api.routes import artifact_pages
    from cognitrix.api.routes import hosted_apps as routes
    from cognitrix.mcp import apps, client, registry

    amos = await _user('Amos', 'amos@example.com')
    app = await _app_for(amos)
    ref = str(app.id)
    calls = []

    async def my_apps(me):
        assert me == str(amos.id)
        return [SimpleNamespace(name='Gmail', key='gmail')]

    async def call_tool(_client, _config, name, args):
        calls.append((name, args))
        return '{"done": true}'

    async def nothing(*_a, **_k):
        return None

    async def tools(*_a, **_k):
        return [{'name': 'search_mail', 'description': 'Find mail'}, {'name': 'send_email', 'description': 'Send'}]

    monkeypatch.setattr(artifact_pages, '_my_apps', my_apps)
    monkeypatch.setattr(client, 'get_dynamic_client', nothing)
    monkeypatch.setattr(registry, 'prepare', nothing)
    monkeypatch.setattr(registry, 'matches', lambda c, name: c.name == name)
    monkeypatch.setattr(apps, 'list_tools', tools)
    monkeypatch.setattr(apps, 'call_tool', call_tool)

    listing = routes.BridgeCall(kind='apps.list')
    with pytest.raises(HTTPException) as refused:
        await routes.app_bridge(ref, listing, _ctx(amos))
    assert refused.value.status_code == 403 and not calls

    await routes.set_permission(ref, routes.PermissionAnswer(power='apps', allowed=True), _ctx(amos))
    listed = (await routes.app_bridge(ref, listing, _ctx(amos)))['value']
    assert listed == [{'app': 'Gmail', 'connected': True, 'tools': [
        {'name': 'search_mail', 'description': 'Find mail', 'changes': False},
        {'name': 'send_email', 'description': 'Send', 'changes': True}]}]

    read = routes.BridgeCall(kind='apps.call', payload={'app': 'Gmail', 'tool': 'search_mail', 'arguments': {'q': 'x'}})
    assert (await routes.app_bridge(ref, read, _ctx(amos)))['value'] == {'done': True}

    send = routes.BridgeCall(kind='apps.call', payload={'app': 'Gmail', 'tool': 'send_email', 'arguments': {'to': 'a'}})
    with pytest.raises(HTTPException) as ask:
        await routes.app_bridge(ref, send, _ctx(amos))
    assert ask.value.status_code == 409 and ask.value.detail['code'] == 'confirm'
    assert calls == [('search_mail', {'q': 'x'})]
    send.confirmed = True
    assert (await routes.app_bridge(ref, send, _ctx(amos)))['value'] == {'done': True}
    assert calls[-1] == ('send_email', {'to': 'a'})

    # Artifact pages still may only read.
    from cognitrix.artifact_pages.store import PageError

    with pytest.raises(PageError) as page_refused:
        await artifact_pages._apps_call(str(amos.id), 'Gmail', 'send_email', {})
    assert page_refused.value.status == 403

    with pytest.raises(HTTPException) as unknown:
        await routes.app_bridge(ref, routes.BridgeCall(kind='data.get'), _ctx(amos))
    assert unknown.value.status_code == 400
