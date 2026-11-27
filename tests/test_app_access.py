"""What an app's own server code may use (cognitrix/hosted_apps/access.py).

- Nothing works until the owner allows it; an agent can only ask.
- Secrets reach the app as environment variables when a version starts, are
  kept encrypted and are never sent back to the page.
- Saved sign-ins are filled in by Cognitrix on requests to their own hosts: the
  app gets the answer, never the value, and only for the sign-ins it was given.
- Connected apps: 'read' runs only tools that change nothing, 'all' runs the rest.
- The app's key opens only this; a person's session or another key doesn't.
- When someone else publishes the app's code, everything pauses for the owner.
"""
import base64
import json
import os
import sys
import textwrap
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from tests.test_hosted_apps import _as, _tool, _user, db, make_app  # noqa: F401  (fixture)

ENV_APP = textwrap.dedent('''
    import http.server, json, os
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({k: os.environ.get(k) for k in ('STRIPE_KEY', 'COGNITRIX_API_KEY', 'COGNITRIX_API_URL')}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a): pass
    http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()
''')


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


def _env_app(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'app.py').write_text(ENV_APP)
    return f'{sys.executable} app.py'


async def _env_of(app):
    from cognitrix.hosted_apps.runner import local_host

    port = local_host().port_for(app.slug, True)
    return json.loads(httpx.get(f'http://127.0.0.1:{port}/', timeout=5).text)


async def _caller(app):
    """What the app-access routes get for a request signed with the app's key."""
    from cognitrix.api.routes import hosted_apps as routes
    from cognitrix.hosted_apps import store

    app = await store.get_app(app.id)
    return await routes._calling_app(SimpleNamespace(api_key=SimpleNamespace(id=app.api_key_id)))


async def test_secrets_reach_the_app_on_restart_and_never_come_back(db, tmp_path):  # noqa: F811
    from cognitrix.api.routes import hosted_apps as routes
    from cognitrix.hosted_apps import store
    from cognitrix.hosted_apps.models import HostedAppAccess

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    cmd = _env_app(tmp_path / 'pay')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Pay', folder=str(tmp_path / 'pay'), command=cmd)
    ref = str(app.id)
    assert (await _env_of(app)) == {'STRIPE_KEY': None, 'COGNITRIX_API_KEY': None, 'COGNITRIX_API_URL': None}

    out = await routes.put_secrets(ref, routes.SecretsChange(put={'STRIPE_KEY': 'sk_live_verysecret1234'}), _ctx(amos))
    assert out['secrets'] == [{'name': 'STRIPE_KEY', 'hint': '…1234'}] and out['needs_restart'] is True
    assert 'sk_live' not in json.dumps(out)
    row = (await HostedAppAccess.find({'app_id': ref}))[0]
    assert row.secrets.startswith('enc:v1:') and 'sk_live' not in row.secrets
    # The running version doesn't have it yet.
    assert (await _env_of(app))['STRIPE_KEY'] is None

    await routes.redeploy_app(ref, None, _ctx(amos))
    assert (await _env_of(app))['STRIPE_KEY'] == 'sk_live_verysecret1234'
    assert (await routes.get_access(ref, _ctx(amos)))['needs_restart'] is False
    # Secrets alone don't give the app a Cognitrix key.
    assert (await _env_of(app))['COGNITRIX_API_KEY'] is None

    for bad in ('COGNITRIX_API_KEY', 'PORT', 'has space', '1ST'):
        with pytest.raises(HTTPException) as refused:
            await routes.put_secrets(ref, routes.SecretsChange(put={bad: 'x'}), _ctx(amos))
        assert refused.value.status_code == 400

    # Only the owner sees or changes any of this, even when the app is shared for editing.
    await store.set_sharing(await store.get_app(ref), people={str(bea.id): 'edit'})
    for call in (routes.get_access(ref, _ctx(bea)),
                 routes.put_secrets(ref, routes.SecretsChange(remove=['STRIPE_KEY']), _ctx(bea)),
                 routes.put_access(ref, routes.AccessChoice(credentials=[]), _ctx(bea))):
        with pytest.raises(HTTPException) as refused:
            await call
        assert refused.value.status_code == 403

    out = await routes.put_secrets(ref, routes.SecretsChange(remove=['STRIPE_KEY']), _ctx(amos))
    assert out['secrets'] == [] and out['needs_restart'] is True
    await store.delete_app(await store.get_app(ref))
    assert not await HostedAppAccess.find({'app_id': ref})


async def test_an_agent_can_only_ask_and_the_key_opens_nothing_else(db, tmp_path):  # noqa: F811
    from cognitrix.api.routes import hosted_apps as routes
    from cognitrix.hosted_apps import access, store
    from cognitrix.models.api_key import APIKey

    amos = await _user('Amos', 'amos@example.com')
    _as(amos, session_id='s1', agent_id='a1')
    build = store.apps_root() / 'digest'
    cmd = _env_app(build)
    out = await _tool('publish_app', title='Digest', start_command=cmd, connected_apps=['Gmail'],
                      credentials=['github token'], secrets=['SLACK_HOOK'])
    assert out.status == 'success', out.text
    assert 'connected apps: Gmail' in out.text and 'saved sign-ins: github token' in out.text
    assert 'secrets to fill in: SLACK_HOOK' in out.text and 'Access tab' in out.text
    app = await store.get_app(out.entities[0].id)

    # It was started with a key, so that a yes later needs no restart; the key has no scopes.
    env = await _env_of(app)
    assert env['COGNITRIX_API_KEY'].startswith('ctx_') and env['COGNITRIX_API_URL']
    assert (await APIKey.get(app.api_key_id)).scopes == []
    shown = await routes.get_app(str(app.id), _ctx(amos))
    assert shown['uses_api'] is False and shown['access_waiting'] == 3

    found = await _caller(app)
    summary = await routes.app_access(found)
    assert summary['connected_apps'] == [] and summary['credentials'] == []
    assert summary['waiting'] == {'apps': ['Gmail'], 'credentials': ['github token'], 'secrets': ['SLACK_HOOK']}
    for call in (routes.app_access_apps(found),
                 routes.app_access_call(routes.AppToolCall(app='Gmail', tool='search_mail'), found),
                 routes.app_access_http(routes.AppRequest(url='https://api.github.com/user',
                                                          credential='GITHUB_TOKEN'), found)):
        with pytest.raises(HTTPException) as refused:
            await call
        assert refused.value.status_code == 403 and 'Access' in refused.value.detail

    # A person's session, or a key that isn't an app's, is not an app.
    for ctx in (_ctx(amos), SimpleNamespace(api_key=SimpleNamespace(id='someone-elses-key'))):
        with pytest.raises(HTTPException) as refused:
            await routes._calling_app(ctx)
        assert refused.value.status_code == 403

    # Asking again replaces the ask; leaving a kind out keeps it.
    again = await _tool('publish_app', title='Digest', start_command=cmd, app_id=str(app.id), connected_apps=[])
    assert 'connected apps' not in again.text and 'SLACK_HOOK' in again.text
    row = await access.row_for(app)
    assert row.wants == {'apps': [], 'credentials': ['github token'], 'secrets': ['SLACK_HOOK']}
    bad = await _tool('publish_app', title='Digest', start_command=cmd, app_id=str(app.id), secrets=['COGNITRIX_X'])
    assert bad.status != 'success'


async def test_saved_sign_ins_are_filled_in_for_the_app_not_given_to_it(db, tmp_path, monkeypatch):  # noqa: F811
    from cognitrix.api.routes import hosted_apps as routes
    from cognitrix.hosted_apps import store
    from tests.test_credentials import _add_github, _fake_http

    amos = await _user('Amos', 'amos@example.com')
    await _add_github(amos)
    await _add_github(amos, name='other token', value='oth_anothersecret999', allowed_hosts=['api.other.com'])
    cmd = _env_app(tmp_path / 'gh')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Stars', folder=str(tmp_path / 'gh'), command=cmd)
    ref = str(app.id)

    listed = await routes.get_access(ref, _ctx(amos))
    assert [(c['name'], c['allowed']) for c in listed['credentials']] == [('GITHUB_TOKEN', False), ('OTHER_TOKEN', False)]
    with pytest.raises(HTTPException) as unknown:
        await routes.put_access(ref, routes.AccessChoice(credentials=['NOPE']), _ctx(amos))
    assert unknown.value.status_code == 404
    out = await routes.put_access(ref, routes.AccessChoice(credentials=['github token']), _ctx(amos))
    assert [(c['name'], c['allowed']) for c in out['credentials']] == [('GITHUB_TOKEN', True), ('OTHER_TOKEN', False)]
    # The app was started without a key: it needs one restart.
    assert out['needs_restart'] is True
    await routes.redeploy_app(ref, None, _ctx(amos))
    assert (await routes.get_access(ref, _ctx(amos)))['needs_restart'] is False
    assert 'ghp_' not in json.dumps(await _env_of(app))

    seen = []
    echoes = {
        '/utf16': (json.dumps({'t': 'ghp_supersecretvalue123'}).encode('utf-16'), 'application/json; charset=utf-16'),
        '/bin': (b'\xff\xfe\x00\x81' + 'ghp_supersecretvalue123'.encode('utf-16-le') + b'\x81ghp_supersecretvalue123',
                 'application/octet-stream'),
    }


    def handler(request):
        seen.append(request)
        if request.url.path in echoes:
            body, kind = echoes[request.url.path]
            return httpx.Response(200, content=body, headers={'content-type': kind})
        if request.url.path == '/hop':
            return httpx.Response(302, headers={'location': 'https://evil.example/steal'})
        return httpx.Response(200, json={'you_sent': request.headers.get('authorization'), 'q': str(request.url)},
                              headers={'x-echo': request.headers.get('authorization', ''), 'set-cookie': 'a=1'})

    _fake_http(monkeypatch, handler)
    found = await _caller(app)
    summary = await routes.app_access(found)
    assert [c['name'] for c in summary['credentials']] == ['GITHUB_TOKEN'] and 'ghp_' not in json.dumps(summary)

    got = await routes.app_access_http(routes.AppRequest(url='https://api.github.com/user', credential='GITHUB_TOKEN'),
                                       found)
    assert seen[-1].headers['authorization'] == 'Bearer ghp_supersecretvalue123'
    assert got['status'] == 200 and got['used'] == ['GITHUB_TOKEN']
    assert json.loads(got['body'])['you_sent'] == 'Bearer [GITHUB_TOKEN hidden]'
    assert got['headers']['x-echo'] == 'Bearer [GITHUB_TOKEN hidden]' and 'set-cookie' not in got['headers']
    assert 'ghp_' not in json.dumps(got)

    got = await routes.app_access_http(routes.AppRequest.model_validate({
        'url': 'https://api.github.com/gists?key={{secret:GITHUB_TOKEN}}', 'method': 'post',
        'json': {'token': '{{secret:GITHUB_TOKEN}}'}}), found)
    assert json.loads(seen[-1].content) == {'token': 'ghp_supersecretvalue123'} and seen[-1].method == 'POST'
    assert 'ghp_' not in json.dumps(got)

    # An answer in another encoding, or one that isn't text, hides the value just the same.
    got = await routes.app_access_http(routes.AppRequest(url='https://api.github.com/utf16', credential='GITHUB_TOKEN'),
                                       found)
    assert json.loads(got['body']) == {'t': '[GITHUB_TOKEN hidden]'}
    got = await routes.app_access_http(routes.AppRequest(url='https://api.github.com/bin', credential='GITHUB_TOKEN'),
                                       found)
    raw = base64.b64decode(got['body_base64'])
    assert got['body'] == '' and b'ghp_' not in raw and 'ghp_'.encode('utf-16-le') not in raw
    assert raw.count(b'[GITHUB_TOKEN hidden]') == 1
    sent = len(seen)
    refused_calls = (
        # Another host than the sign-in was saved for.
        (routes.AppRequest(url='https://evil.example/x', credential='GITHUB_TOKEN'), 403),
        # A sign-in the owner did not give this app.
        (routes.AppRequest(url='https://api.other.com/x', credential='OTHER_TOKEN'), 403),
        (routes.AppRequest(url='https://api.other.com/x', headers={'X-Key': '{{secret:OTHER_TOKEN}}'}), 403),
        # No sign-in at all: not a free proxy.
        (routes.AppRequest(url='https://api.github.com/user'), 400),
    )
    for body, status in refused_calls:
        with pytest.raises(HTTPException) as refused:
            await routes.app_access_http(body, found)
        assert refused.value.status_code == status, refused.value.detail
        assert 'ghp_' not in str(refused.value.detail) and 'oth_' not in str(refused.value.detail)
    assert len(seen) == sent

    # The sign-in never follows a redirect to another host.
    got = await routes.app_access_http(routes.AppRequest(url='https://api.github.com/hop', credential='GITHUB_TOKEN'),
                                       found)
    assert got['status'] == 302 and got['redirect_not_followed'] == 'https://evil.example/steal'
    assert seen[-1].url.host == 'api.github.com'

    # Taking it away works at once, with no restart.
    await routes.put_access(ref, routes.AccessChoice(credentials=[]), _ctx(amos))
    with pytest.raises(HTTPException) as refused:
        await routes.app_access_http(routes.AppRequest(url='https://api.github.com/user', credential='GITHUB_TOKEN'),
                                     await _caller(app))
    assert refused.value.status_code == 403


async def _connect(owner, name='Gmail'):
    from cognitrix.models.mcp_server import MCPServer

    await MCPServer(name=name, owner_id=str(owner.id), transport='http', url='https://mcp.example/mcp').save()
    return str((await MCPServer.find({'name': name, 'owner_id': str(owner.id)}))[0].id)


def _fake_connected_apps(monkeypatch):
    from cognitrix.mcp import apps, client, registry

    calls = []

    async def call_tool(_client, config, name, args):
        calls.append((config.name, name, args))
        return '{"done": true}'

    async def nothing(*_a, **_k):
        return None

    async def tools(*_a, **_k):
        return [{'name': 'search_mail', 'description': 'Find mail'}, {'name': 'send_email', 'description': 'Send'}]

    monkeypatch.setattr(client, 'get_dynamic_client', nothing)
    monkeypatch.setattr(registry, 'prepare', nothing)
    monkeypatch.setattr(apps, 'list_tools', tools)
    monkeypatch.setattr(apps, 'call_tool', call_tool)
    return calls


async def test_connected_apps_follow_the_owners_allow_list(db, tmp_path, monkeypatch):  # noqa: F811
    from cognitrix.api.routes import hosted_apps as routes
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    gmail = await _connect(amos, 'Gmail')
    await _connect(amos, 'Notion')
    beas = await _connect(bea, 'Slack')
    calls = _fake_connected_apps(monkeypatch)
    cmd = _env_app(tmp_path / 'sync')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Sync', folder=str(tmp_path / 'sync'), command=cmd,
                                    wants={'apps': ['gmail', 'Linear']})
    ref = str(app.id)

    shown = await routes.get_access(ref, _ctx(amos))
    assert [(a['name'], a['level'], a['asked']) for a in shown['apps']] == [('Gmail', '', True), ('Notion', '', False)]
    assert shown['asked']['apps'] == ['Linear']  # asked for, but not connected yet

    for bad in ({beas: 'read'}, {gmail: 'everything'}):
        with pytest.raises(HTTPException):
            await routes.put_access(ref, routes.AccessChoice(apps=bad), _ctx(amos))

    out = await routes.put_access(ref, routes.AccessChoice(apps={gmail: 'read'}), _ctx(amos))
    assert out['needs_restart'] is False  # it asked, so it already has its key
    found = await _caller(app)
    assert (await routes.app_access(found))['connected_apps'] == [{'app': 'Gmail', 'changes': False}]
    assert (await routes.app_access_apps(found))['apps'] == [
        {'app': 'Gmail', 'connected': True, 'tools': [{'name': 'search_mail', 'description': 'Find mail'}]}]
    got = await routes.app_access_call(routes.AppToolCall(app='gmail', tool='search_mail', arguments={'q': 'x'}), found)
    assert got == {'value': {'done': True}} and calls == [('Gmail', 'search_mail', {'q': 'x'})]

    with pytest.raises(HTTPException) as refused:
        await routes.app_access_call(routes.AppToolCall(app='Gmail', tool='send_email'), found)
    assert refused.value.status_code == 403 and 'may only read' in refused.value.detail
    with pytest.raises(HTTPException) as refused:
        await routes.app_access_call(routes.AppToolCall(app='Notion', tool='search_mail'), found)
    assert refused.value.status_code == 403 and len(calls) == 1

    await routes.put_access(ref, routes.AccessChoice(apps={gmail: 'all'}), _ctx(amos))
    found = await _caller(app)
    listed = (await routes.app_access_apps(found))['apps'][0]['tools']
    assert {'name': 'send_email', 'description': 'Send', 'changes': True} in listed
    await routes.app_access_call(routes.AppToolCall(app='Gmail', tool='send_email', arguments={'to': 'a'}), found)
    assert calls[-1] == ('Gmail', 'send_email', {'to': 'a'})

    await routes.put_access(ref, routes.AccessChoice(apps={gmail: ''}), _ctx(amos))
    with pytest.raises(HTTPException) as refused:
        await routes.app_access_call(routes.AppToolCall(app='Gmail', tool='search_mail'), await _caller(app))
    assert refused.value.status_code == 403


async def test_someone_elses_version_pauses_everything_until_the_owner_looks(db, tmp_path, monkeypatch):  # noqa: F811
    from cognitrix.api.routes import hosted_apps as routes
    from cognitrix.hosted_apps import store

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    gmail = await _connect(amos)
    calls = _fake_connected_apps(monkeypatch)
    cmd = _env_app(tmp_path / 'team')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Team', folder=str(tmp_path / 'team'), command=cmd)
    ref = str(app.id)
    await routes.put_access(ref, routes.AccessChoice(apps={gmail: 'all'}), _ctx(amos))
    await routes.put_secrets(ref, routes.SecretsChange(put={'STRIPE_KEY': 'sk_live_verysecret1234'}), _ctx(amos))
    await routes.redeploy_app(ref, None, _ctx(amos))
    assert (await _env_of(app))['STRIPE_KEY'] == 'sk_live_verysecret1234'
    await routes.app_access_call(routes.AppToolCall(app='Gmail', tool='send_email'), await _caller(app))
    assert len(calls) == 1

    # Bea may publish new versions. Her code must not act as Amos or read his secrets.
    await store.set_sharing(await store.get_app(ref), people={str(bea.id): 'edit'})
    await store.publish(user_key=str(bea.id), title='Team', folder=str(tmp_path / 'team'), command=cmd, app_id=ref)
    assert (await _env_of(app))['STRIPE_KEY'] is None
    with pytest.raises(HTTPException) as refused:
        await routes.app_access_call(routes.AppToolCall(app='Gmail', tool='send_email'), await _caller(app))
    assert refused.value.status_code == 403 and 'paused' in refused.value.detail and len(calls) == 1
    shown = await routes.get_access(ref, _ctx(amos))
    assert shown['paused'] is True
    assert (await routes.app_access(await _caller(app)))['connected_apps'] == []

    out = await routes.put_access(ref, routes.AccessChoice(paused=False), _ctx(amos))
    assert out['paused'] is False and out['needs_restart'] is True  # its secrets come back on restart
    await routes.app_access_call(routes.AppToolCall(app='Gmail', tool='send_email'), await _caller(app))
    assert len(calls) == 2
    await routes.redeploy_app(ref, None, _ctx(amos))
    assert (await _env_of(app))['STRIPE_KEY'] == 'sk_live_verysecret1234'


async def test_apps_that_run_agents_keep_their_full_key(db, tmp_path):  # noqa: F811
    from cognitrix.api.routes import hosted_apps as routes
    from cognitrix.hosted_apps import store
    from cognitrix.models.api_key import APIKey

    amos = await _user('Amos', 'amos@example.com')
    cmd = _env_app(tmp_path / 'bot')
    app, _, _ = await store.publish(user_key=str(amos.id), title='Bot', folder=str(tmp_path / 'bot'), command=cmd,
                                    use_cognitrix_api=True)
    assert (await APIKey.get(app.api_key_id)).scopes == store.API_SCOPES
    # A redeploy from its page (which doesn't say use_cognitrix_api) keeps the same kind of key.
    await routes.redeploy_app(str(app.id), None, _ctx(amos))
    app = await store.get_app(app.id)
    assert (await APIKey.get(app.api_key_id)).scopes == store.API_SCOPES
    assert (await routes.get_app(str(app.id), _ctx(amos)))['uses_api'] is True
    assert os.environ.get('COGNITRIX_API_KEY') is None
