"""Keys the agent never sees (the sandbox key proxy) and sandbox snapshots.

The proxy is exercised for real here: an HTTPS echo server on loopback, the
proxy in front of it and a client that trusts the proxy's CA. A real
container was used for the end-to-end check (curl, Python and Node in the
sandbox image); see the PR.
"""
from __future__ import annotations

import base64
import datetime as dt
import http.client
import ipaddress
import json
import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cognitrix.common import key_proxy as kp
from cognitrix.common import sandbox_work as sw
from cognitrix.common.key_proxy import HiddenValue


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    from cognitrix.config import settings
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    return tmp_path


def _entries(proxy: kp.KeyProxy, person: str) -> dict:
    return proxy.entries(person)


# -- placeholders and rewriting ------------------------------------------------

def test_placeholders_are_stable_keep_known_prefixes_and_differ_per_person(workdir):
    proxy = kp.KeyProxy()
    key = HiddenValue('sk-ant-api03-' + 'x' * 40, ['api.anthropic.com'], 'ANTHROPIC_API_KEY')
    one = proxy.placeholder('alice', 'ANTHROPIC_API_KEY', key)
    assert one.startswith('sk-ant-cgxph') and 'x' * 10 not in one
    assert proxy.placeholder('alice', 'ANTHROPIC_API_KEY', key) == one
    assert kp.KeyProxy().placeholder('alice', 'ANTHROPIC_API_KEY', key) == one  # same after a restart
    assert proxy.placeholder('bob', 'ANTHROPIC_API_KEY', key) != one
    short = proxy.placeholder('alice', 'PIN', HiddenValue('1234', ['x.example'], 'PIN'))
    assert short.startswith('cgxph')


def test_rewrite_fills_only_for_the_keys_own_sites(workdir):
    proxy = kp.KeyProxy()
    gh = proxy.placeholder('alice', 'GH', HiddenValue('ghp_' + 'r' * 36, ['api.github.com', '*.githubusercontent.com'], 'GH'))
    other = proxy.placeholder('alice', 'OAI', HiddenValue('sk-' + 'o' * 40, ['api.openai.com'], 'OAI'))
    head = (f'GET /repos?token={gh} HTTP/1.1\r\nHost: api.github.com\r\nAuthorization: Bearer {gh}\r\n'
            f'X-Other: {other}\r\nConnection: keep-alive\r\nProxy-Authorization: Basic abc\r\n\r\n').encode()
    out, used = kp.rewrite_head(head, _entries(proxy, 'alice'), 'api.github.com')
    text = out.decode()
    assert f'token=ghp_{"r" * 36}' in text and f'Bearer ghp_{"r" * 36}' in text
    assert other in text and 'o' * 40 not in text  # the OpenAI key never goes to GitHub
    assert 'Connection: close' in text and 'keep-alive' not in text and 'Proxy-Authorization' not in text
    assert used == {'GH'}
    # A different host gets nothing filled in.
    out, used = kp.rewrite_head(head, _entries(proxy, 'alice'), 'evil.example')
    assert 'r' * 36 not in out.decode() and not used
    # Another person's table doesn't know these placeholders.
    out, _ = kp.rewrite_head(head, _entries(proxy, 'bob'), 'api.github.com')
    assert 'r' * 36 not in out.decode()


def test_rewrite_fills_basic_auth_and_keeps_upgrades(workdir):
    proxy = kp.KeyProxy()
    ph = proxy.placeholder('alice', 'PW', HiddenValue('hunter2-long-password-value', ['git.example.com'], 'PW'))
    basic = base64.b64encode(f'me:{ph}'.encode()).decode()
    head = (f'GET /ws HTTP/1.1\r\nHost: git.example.com\r\nAuthorization: Basic {basic}\r\nUpgrade: websocket\r\n'
            'Connection: Upgrade\r\n\r\n').encode()
    out, used = kp.rewrite_head(head, _entries(proxy, 'alice'), 'git.example.com')
    text = out.decode()
    sent = text.split('Authorization: Basic ')[1].split('\r\n')[0]
    assert base64.b64decode(sent).decode() == 'me:hunter2-long-password-value'
    assert 'Connection: Upgrade' in text and used == {'PW'}


def test_blocked_addresses():
    for addr in ('127.0.0.1', '::1', '169.254.169.254', '0.0.0.0', '::ffff:127.0.0.1', 'not-an-ip'):
        assert kp._blocked_address(addr), addr
    for addr in ('172.17.0.1', '93.184.216.34', '2606:4700::1111'):
        assert not kp._blocked_address(addr), addr


# -- the proxy for real ----------------------------------------------------------

def _echo_server(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'echo')])
    now = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256()))
    (tmp_path / 'echo.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / 'echo.key').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))

    class Echo(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            body = json.dumps({'path': self.path, 'headers': dict(self.headers)}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(tmp_path / 'echo.pem', tmp_path / 'echo.key')
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _get(proxy, token, port, path, header, cafile):
    ctx = ssl.create_default_context(cafile=cafile)
    conn = http.client.HTTPSConnection('127.0.0.1', proxy.port, context=ctx, timeout=10)
    auth = base64.b64encode(f'cognitrix:{token}'.encode()).decode()
    conn.set_tunnel('127.0.0.1', port, headers={'Proxy-Authorization': f'Basic {auth}'})
    conn.request('GET', path, headers={'Authorization': header})
    return json.loads(conn.getresponse().read())


def test_proxy_fills_keys_end_to_end(workdir, tmp_path, monkeypatch):
    monkeypatch.setattr(kp, '_blocked_address', lambda addr: False)  # the test server is on loopback
    echo = _echo_server(tmp_path)
    port = echo.server_address[1]
    monkeypatch.setenv(kp.UPSTREAM_CA_ENV, str(tmp_path / 'echo.pem'))
    proxy = kp.KeyProxy()
    try:
        proxy.ensure_started('127.0.0.1')
        token = proxy.token_for('alice')
        ph = proxy.placeholder('alice', 'API', HiddenValue('real-secret-value-0123456789', [f'127.0.0.1:{port}'], 'API'))
        ca = tmp_path / 'proxy-ca.pem'
        ca.write_bytes(proxy.ca.pem)
        got = _get(proxy, token, port, f'/x?k={ph}', f'Bearer {ph}', str(ca))
        assert got['headers']['Authorization'] == 'Bearer real-secret-value-0123456789'
        assert got['path'] == '/x?k=real-secret-value-0123456789'
        # The CA's private key stays in the engine's folder, readable only here.
        assert (workdir / 'sandbox-proxy' / 'ca.key').exists()
        assert b'PRIVATE KEY' not in proxy.ca.pem
        # A wrong password gets 407.
        with pytest.raises(OSError, match='407'):
            _get(proxy, 'wrong', port, '/', 'x', str(ca))
    finally:
        proxy.stop()
        echo.shutdown()


def test_proxy_refuses_loopback(workdir, tmp_path):
    proxy = kp.KeyProxy()
    try:
        proxy.ensure_started('127.0.0.1')
        token = proxy.token_for('alice')
        with pytest.raises(OSError, match='403'):
            _get(proxy, token, 8000, '/', 'x', None)
    finally:
        proxy.stop()


# -- where hidden values come from -------------------------------------------------

def test_env_vars_with_sites_are_hidden_and_plain_ones_stay_plain(workdir):
    sw.save_sandbox_env('alice', [{'name': 'NODE_ENV', 'value': 'test'},
                                  {'name': 'GH_TOKEN', 'value': 'ghp_x', 'hosts': ['API.GitHub.com']}])
    env = sw.sandbox_env('alice')
    assert env == {'NODE_ENV': 'test', 'GH_TOKEN': 'ghp_x'}
    assert not isinstance(env['NODE_ENV'], HiddenValue)
    assert isinstance(env['GH_TOKEN'], HiddenValue) and env['GH_TOKEN'].hosts == ('api.github.com',)
    # Leaving value and hosts out keeps both; hosts=[] makes it plain again.
    sw.save_sandbox_env('alice', [{'name': 'GH_TOKEN'}])
    assert sw.sandbox_env('alice')['GH_TOKEN'].hosts == ('api.github.com',)
    sw.save_sandbox_env('alice', [{'name': 'GH_TOKEN', 'hosts': []}])
    assert not isinstance(sw.sandbox_env('alice')['GH_TOKEN'], HiddenValue)
    assert 'ghp_x' not in (workdir / 'sandbox-env.json').read_text()
    with pytest.raises(ValueError):
        sw.save_sandbox_env('alice', [{'name': 'X', 'value': 'v', 'hosts': ['not a host!']}])


def test_sign_ins_and_cli_keys_are_hidden(monkeypatch):
    out = sw.credential_vars('GH', 'token', {'value': 'v'}, ['api.github.com'])
    assert isinstance(out['GH'], HiddenValue) and out['GH'].hosts == ('api.github.com',)
    # Sandbox-only sign-ins are for things inside the sandbox: passed as they are.
    out = sw.credential_vars('DB', 'basic', {'username': 'u', 'password': 'p'}, [sw.SANDBOX_ONLY_HOST])
    assert not isinstance(out['DB_PASSWORD'], HiddenValue)
    out = sw.credential_vars('DB', 'basic', {'username': 'u', 'password': 'p'}, ['db.example.com'])
    assert not isinstance(out['DB_USERNAME'], HiddenValue) and isinstance(out['DB_PASSWORD'], HiddenValue)

    import cognitrix.providers.saved_keys as keys
    monkeypatch.setattr(keys, 'env_key', lambda provider: 'sk-ant-real' if provider == 'anthropic' else None)
    monkeypatch.setattr(keys, 'get_saved_key', lambda provider: None)
    env = sw.cli_env('claude')
    assert isinstance(env['ANTHROPIC_API_KEY'], HiddenValue)
    assert env['ANTHROPIC_API_KEY'].hosts == ('api.anthropic.com',)


def test_run_sends_placeholders_and_proxy_settings(workdir, monkeypatch):
    from cognitrix.computer.sandbox import client

    class Manager:
        def key_proxy_env(self, user_id):
            return {'HTTPS_PROXY': 'http://cognitrix:t@host:1', 'SSL_CERT_FILE': '/b.pem'}

    class Box:
        def call(self, method, path, body, **kw):
            sent.append(body)
            return {'ok': True}

    sent: list = []
    monkeypatch.setattr(client, 'get_manager', lambda: Manager())
    sw.save_sandbox_env('alice', [{'name': 'TOKEN', 'value': 'real-token-value', 'hosts': ['api.example.com']},
                                  {'name': 'MODE', 'value': 'dev'}])
    work = sw.SandboxWork('alice', 'agent-1')
    work._box = Box()
    work.run('true', env={'GH': sw.credential_vars('GH', 'token', {'value': 'gh-real'}, ['api.github.com'])['GH']})
    env = sent[0]['env']
    assert 'real-token-value' not in json.dumps(sent) and 'gh-real' not in json.dumps(sent)
    assert kp.MARK in env['TOKEN'] and kp.MARK in env['GH'] and env['MODE'] == 'dev'
    assert env['HTTPS_PROXY'].startswith('http://cognitrix:')
    # Nothing hidden: no proxy settings at all.
    sent.clear()
    sw.save_sandbox_env('alice', [{'name': 'MODE', 'value': 'dev'}])
    work.run('true')
    assert 'HTTPS_PROXY' not in sent[0]['env']
    # Switched off: real values, no proxy.
    sent.clear()
    monkeypatch.setenv(kp.HIDE_ENV, '0')
    work.run('true', env={'GH': HiddenValue('gh-real', ['api.github.com'], 'GH')})
    assert sent[0]['env']['GH'] == 'gh-real' and 'HTTPS_PROXY' not in sent[0]['env']


# -- snapshots ------------------------------------------------------------------------

needs_gnu_tar = pytest.mark.skipif(
    os.name == 'nt' or shutil.which('tar') is None
    or 'GNU tar' not in subprocess.run(['tar', '--version'], capture_output=True, text=True).stdout,
    reason='needs GNU tar and a POSIX shell (as in the sandbox image)')


@needs_gnu_tar
def test_agent_setup_scripts_save_restore_and_copy(tmp_path):
    from cognitrix.computer.sandbox import snapshots as snap
    home = tmp_path / 'home'
    (home / 'work/a1/proj').mkdir(parents=True)
    (home / 'work/a1/proj/f.txt').write_text('from a1')
    (home / 'work/a10').mkdir(parents=True)
    (home / 'work/a10/other.txt').write_text('not a1')
    profile = home / '.profiles/a1/chromium'
    (profile / 'Default/Cache/Cache_Data').mkdir(parents=True)
    (profile / 'Default/Cookies').write_text('cookies')
    (profile / 'Default/Cache/Cache_Data/blob').write_text('cache')
    os.symlink('host-123', profile / 'SingletonLock')
    (home / 'work/b2').mkdir(parents=True)
    (home / 'work/b2/old.txt').write_text('old')
    env = {**os.environ, 'HOME': str(home)}

    archive = subprocess.run(['sh', '-c', snap.agent_save_script('a1')], env=env, capture_output=True, check=True).stdout
    names = subprocess.run(['tar', '-tzf', '-'], input=archive, capture_output=True, check=True).stdout.decode().split()
    assert 'agent/work/proj/f.txt' in names and 'agent/profiles/chromium/Default/Cookies' in names
    assert not any('Cache_Data/' in n or 'Singleton' in n or 'other.txt' in n for n in names)

    subprocess.run(['sh', '-c', snap.agent_restore_script('b2')], env=env, input=archive, check=True)
    assert (home / 'work/b2/proj/f.txt').read_text() == 'from a1'
    assert not (home / 'work/b2/old.txt').exists()
    assert (home / '.profiles/b2/chromium/Default/Cookies').read_text() == 'cookies'

    subprocess.run(['sh', '-c', snap.copy_script('a1', 'c3')], env=env, check=True)
    assert (home / 'work/c3/proj/f.txt').read_text() == 'from a1'
    assert not (home / '.profiles/c3/chromium/SingletonLock').is_symlink()
    # Nothing to copy, or an archive that won't unpack: the target keeps its own setup.
    assert subprocess.run(['sh', '-c', snap.copy_script('nobody', 'c3')], env=env).returncode == 3
    assert (home / 'work/c3/proj/f.txt').read_text() == 'from a1'
    bad = subprocess.run(['sh', '-c', snap.agent_restore_script('c3')], env=env, input=b'not a tar.gz')
    assert bad.returncode == 4 and (home / 'work/c3/proj/f.txt').read_text() == 'from a1'
    assert not (home / '.cognitrix-sandbox/staging/c3').exists()
    with pytest.raises(snap.SnapshotError):
        snap.copy_script('a1', 'a1')
    with pytest.raises(ValueError):
        snap.agent_save_script('../etc')


def test_snapshot_index_create_list_delete(workdir, monkeypatch):
    from cognitrix.computer.sandbox import snapshots as snap

    class Box:
        name = 'cognitrix-sandbox-x'
        image_id = 'sha256:1'

    class Manager:
        docker = 'docker'

        def sandbox(self, user_id):
            return Box()

    scripts = []

    def fake_exec(mgr, container, script, target):
        scripts.append(script)
        target.write_bytes(b'archive')
        return 7

    monkeypatch.setattr(snap, '_exec_to_file', fake_exec)
    monkeypatch.setattr(snap, 'MAX_SNAPSHOTS', 2)
    one = snap.create_snapshot(Manager(), 'alice', '  Set   up  ', None)
    two = snap.create_snapshot(Manager(), 'alice', None, 'agent-1')
    assert one['name'] == 'Set up' and one['kind'] == 'computer' and two['kind'] == 'agent'
    assert 'agent/work' in scripts[1] and 'agent/work' not in scripts[0]
    assert [s['id'] for s in snap.list_snapshots('alice')] == [two['id'], one['id']]
    assert snap.list_snapshots('bob') == []
    with pytest.raises(snap.SnapshotError, match='most'):
        snap.create_snapshot(Manager(), 'alice', None, None)
    snap.delete_snapshot('alice', one['id'])
    assert [s['id'] for s in snap.list_snapshots('alice')] == [two['id']]
    with pytest.raises(snap.SnapshotError):
        snap.delete_snapshot('alice', one['id'])
