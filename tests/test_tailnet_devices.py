"""Agent tools for tailnet devices (cognitrix/tailscale/devices.py and
cognitrix/tools/tailnet.py): the list an admin keeps, how a command is run,
and that a person approves every one. Tailscale and ssh are stand-ins."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from cognitrix import app_settings
from cognitrix.safety import auto_approve_all, command_rules
from cognitrix.safety.approval_gate import ApprovalGate, ApprovalResult, ToolCall, resolve_web_approval, web_turn_ctx
from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel
from cognitrix.safety.permissions import needs_person
from cognitrix.tailscale import cli, daemon, devices, service
from cognitrix.tools.base import ToolManager
from tests.test_hosted_apps import _as, _user, db  # noqa: F401 - db is a fixture

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='the stand-in ssh is a shell script')

TAG = 'tag:cognitrix-sandbox'


def _peer(node, name, ip, os_='linux', online=True, tags=(), ssh=True):
    return {'ID': node, 'HostName': name, 'DNSName': f'{name}.tail1234.ts.net.', 'OS': os_, 'Online': online,
            'TailscaleIPs': [ip, 'fd7a:115c:a1e0::9'], 'Tags': list(tags), **({'sshHostKeys': ['ssh-ed25519 AAA']} if ssh else {})}


@pytest.fixture
def tailnet(monkeypatch):
    peers = {
        'k1': _peer('nNAS', 'nas', '100.64.0.11'),
        'k2': _peer('nWIN', 'Office-PC', '100.64.0.12', os_='windows', ssh=False),
        'k3': _peer('nOLD', 'attic', '100.64.0.13', online=False),
        'k4': _peer('nBOX', 'cognitrix-sandbox-abc123', '100.64.0.14', tags=(TAG,)),
    }
    state = SimpleNamespace(peers=peers, backend='Running')
    monkeypatch.setattr(cli, 'status', lambda: {'BackendState': state.backend, 'Peer': state.peers,
                                                'Self': {'TailscaleIPs': ['100.64.0.7']}})
    monkeypatch.setattr(service, 'mode', lambda: service.SYSTEM)
    monkeypatch.setattr(daemon, 'running', lambda: False)
    monkeypatch.setattr(app_settings, '_env_fallback', False)
    app_settings.set_many({'COGNITRIX_TAILSCALE': True, 'COGNITRIX_TAILSCALE_DEVICES': 'everyone'})
    return state


SSH = r'''#!/bin/sh
# A stand-in for ssh: records how it was called, then answers by the command.
printf '%s\n' "$@" > "$FAKE_SSH_LOG"
for last; do :; done
case "$last" in
  "uptime") echo " 10:00 up 3 days"; exit 0 ;;
  "fail") echo "partial"; echo "disk full" >&2; exit 3 ;;
  "denied") echo "ada@100.64.0.11: Permission denied (publickey)." >&2; exit 255 ;;
  "refused") echo "ssh: connect to host 100.64.0.11 port 22: Connection refused" >&2; exit 255 ;;
  "check") printf '# Tailscale SSH requires an additional check.\r\n# To authenticate, visit: https://login.tailscale.com/a/abc123\r\n' >&2; sleep 30 ;;
  "slow") echo "started"; exec sleep 30 ;;
  "first") echo "Warning: Permanently added 'tailnet-nNAS' (ED25519) to the list of known hosts." >&2; echo ok; exit 0 ;;
  "key") for a; do if [ "$prev" = "-i" ]; then head -n 1 "$a"; fi; prev=$a; done; exit 0 ;;
esac
echo "ran: $last"
'''


@pytest.fixture
def ssh(tmp_path, monkeypatch):
    program = tmp_path / 'ssh'
    program.write_text(SSH)
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / 'ssh.log'
    monkeypatch.setenv('FAKE_SSH_LOG', str(log))
    monkeypatch.setattr(devices, 'find_ssh', lambda: str(program))
    monkeypatch.setattr(devices, '_CHECK_GRACE_S', 0.2)
    return lambda: log.read_text().splitlines()


def _tool(name):
    return next(t for t in ToolManager.list_all_tools() if t.name == name)


async def _call(name, **params):
    out = await _tool(name).run(**params)
    return out.outcome


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------

async def test_nothing_is_allowed_until_an_admin_adds_it(db, tailnet):  # noqa: F811
    found = await devices.listing()
    assert [d['name'] for d in found] == ['nas', 'Office-PC', 'attic']  # online first; never an agents' computer
    assert all(not d['allowed'] and d['users'] == [] for d in found)
    assert [d['tailscale_ssh'] for d in found] == [True, False, True]
    assert 'ip' not in found[0]

    saved = await devices.allow('nNAS', ['ada', 'deploy', 'ada'], 'tailscale', by='u1')
    assert saved['allowed'] and saved['users'] == ['ada', 'deploy'] and saved['path'] == 'tailscale'
    await devices.allow('nNAS', 'ada', 'key')  # changed in place
    (nas,) = (d for d in await devices.listing() if d['allowed'])
    assert nas['users'] == ['ada'] and nas['path'] == 'key'

    for node in ('nNOPE', 'nBOX'):  # not on the tailnet; an agents' computer
        with pytest.raises(devices.DeviceError) as unknown:
            await devices.allow(node, ['ada'], 'tailscale')
        assert unknown.value.code == 'unknown'
    for bad in (['-oProxyCommand=x'], ['a b'], ['ada;id'], ['$(id)'], ['ada@host']):
        with pytest.raises(devices.DeviceError) as refused:
            await devices.allow('nNAS', bad, 'tailscale')
        assert refused.value.code == 'bad_user'
    with pytest.raises(devices.DeviceError):
        await devices.allow('nNAS', ['ada'], 'telnet')

    # A machine that left the tailnet still shows, so it can be removed.
    del tailnet.peers['k1']
    (gone,) = (d for d in await devices.listing() if d['allowed'])
    assert gone['name'] == 'nas' and gone['present'] is False
    assert await devices.remove('nNAS') is True and await devices.remove('nNAS') is False


def test_this_servers_key_is_made_once_and_sealed():
    line = devices.public_key()
    assert line.startswith('ssh-ed25519 AAAA') and line.endswith(' cognitrix')
    assert devices.public_key() == line
    kept = json.loads(devices._key_file().read_text())
    assert 'PRIVATE KEY' not in kept['private'] and 'PRIVATE KEY' in devices._key()[0]
    if os.name != 'nt':
        assert stat.S_IMODE(devices._key_file().stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# Running a command
# ---------------------------------------------------------------------------

def test_the_ssh_command_line(tailnet, ssh, monkeypatch):
    from cognitrix.tailscale import outbound

    machine = next(m for m in devices.machines() if m['node'] == 'nNAS')
    argv = devices.ssh_argv(machine, 'ada', 'ls -la; rm x', 'tailscale')
    assert argv[-3:] == ['ada@100.64.0.11', '--', 'ls -la; rm x']  # the address Tailscale gives, one argument
    options = [argv[i + 1] for i, a in enumerate(argv) if a == '-o']
    for wanted in ('BatchMode=yes', 'StrictHostKeyChecking=accept-new', 'HostKeyAlias=tailnet-nNAS',
                   'IdentityAgent=none', 'ForwardAgent=no', 'PasswordAuthentication=no', 'PubkeyAuthentication=no'):
        assert wanted in options
    assert '-i' not in argv and not any(o.startswith('ProxyCommand') for o in options)
    assert any(o.startswith('UserKnownHostsFile=') and 'known_hosts' in o for o in options)

    keyed = devices.ssh_argv(machine, 'ada', 'id', 'key', '/tmp/k')
    assert keyed[keyed.index('-i') + 1] == '/tmp/k' and 'PubkeyAuthentication=no' not in keyed

    # The built-in Tailscale has no network device: ssh goes through its client.
    monkeypatch.setattr(outbound, 'proxy_port', lambda: 1055)
    monkeypatch.setattr(cli, 'base_argv', lambda: ['/usr/local/bin/tailscale', '--socket=/data/ts.sock'])
    proxied = devices.ssh_argv(machine, 'ada', 'id', 'tailscale')
    assert '-o' in proxied and 'ProxyCommand=/usr/local/bin/tailscale --socket=/data/ts.sock nc %h %p' in proxied


async def test_a_command_runs_and_says_how_it_ended(db, tailnet, ssh):  # noqa: F811
    await devices.allow('nNAS', ['ada'], 'tailscale')
    ok = await devices.perform('nas', 'ada', 'uptime')
    assert ok == {'ok': True, 'code': '', 'text': 'ada@nas exit code 0\n10:00 up 3 days', 'denied': False}
    assert ssh()[-3:] == ['ada@100.64.0.11', '--', 'uptime']
    assert (await devices.perform('NAS.tail1234.ts.net', 'ada', 'uptime'))['ok']  # by its tailnet name too

    failed = await devices.perform('nas', 'ada', 'fail')
    assert failed['code'] == 'command_failed' and failed['text'] == 'ada@nas exit code 3\npartial\n[stderr]\ndisk full'
    first = await devices.perform('nas', 'ada', 'first')
    assert first['ok'] and 'Permanently added' not in first['text']

    denied = await devices.perform('nas', 'ada', 'denied')
    assert denied['code'] == 'ssh_failed' and 'tailnet policy' in denied['text'] and 'tagged' in denied['text']
    refused = await devices.perform('nas', 'ada', 'refused')
    assert refused['code'] == 'ssh_failed' and 'Tailscale SSH turned on' in refused['text']

    slow = await devices.perform('nas', 'ada', 'slow', timeout=1)
    assert slow['code'] == 'timeout' and 'started' in slow['text']


async def test_tailscales_confirm_it_is_you_link_is_handed_back(db, tailnet, ssh):  # noqa: F811
    await devices.allow('nNAS', ['ada'], 'tailscale')
    out = await asyncio.wait_for(devices.perform('nas', 'ada', 'check', timeout=20), 10)
    assert out['code'] == 'check_needed' and 'https://login.tailscale.com/a/abc123' in out['text']


async def test_this_servers_key_is_used_from_a_file_that_is_removed(db, tailnet, ssh):  # noqa: F811
    await devices.allow('nWIN', ['Ada'], 'key')
    out = await devices.perform('office-pc', 'Ada', 'key')
    assert out['ok'] and 'BEGIN OPENSSH PRIVATE KEY' in out['text']
    key_file = ssh()[ssh().index('-i') + 1]
    assert not os.path.exists(key_file) and not os.path.exists(os.path.dirname(key_file))
    denied = await devices.perform('office-pc', 'Ada', 'denied')
    assert 'authorized_keys' in denied['text']


async def test_only_listed_machines_and_names(db, tailnet, ssh):  # noqa: F811
    await devices.allow('nNAS', ['ada'], 'tailscale')
    await devices.allow('nOLD', ['ada'], 'tailscale')
    await devices.allow('nWIN', [], 'key')
    not_listed = await devices.perform('cognitrix-sandbox-abc123', 'ada', 'uptime')
    assert not_listed['code'] == 'unknown'
    wrong_user = await devices.perform('nas', 'root', 'uptime')
    assert wrong_user['code'] == 'user_not_allowed' and wrong_user['denied'] and 'Allowed: ada' in wrong_user['text']
    assert (await devices.perform('office-pc', 'Ada', 'uptime'))['code'] == 'no_user'
    assert (await devices.perform('attic', 'ada', 'uptime'))['code'] == 'offline'
    await devices.remove('nNAS')
    removed = await devices.perform('nas', 'ada', 'uptime')
    assert removed['code'] == 'not_allowed' and removed['denied'] and 'Settings, Tailscale' in removed['text']
    assert not os.path.exists(os.environ['FAKE_SSH_LOG'])  # nothing was ever run


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------

async def test_the_tools_are_off_until_an_admin_turns_them_on(db, tailnet, ssh):  # noqa: F811
    ada = await _user('Ada', 'ada@example.com')     # the first account: an admin
    bob = await _user('Bob', 'bob@example.com')
    await devices.allow('nNAS', ['ada'], 'tailscale')

    app_settings.set_many({'COGNITRIX_TAILSCALE_DEVICES': 'off'})
    token = _as(bob)
    try:
        for name, params in (('List Tailnet Devices', {}),
                             ('Run On Tailnet Device', {'device': 'nas', 'user': 'ada', 'command': 'uptime',
                                                        'approved': True})):
            off = await _call(name, **params)
            assert off.error.code == 'tailnet_off' and 'Settings, Tailscale' in off.text
        app_settings.set_many({'COGNITRIX_TAILSCALE_DEVICES': 'admins'})
        assert (await _call('List Tailnet Devices')).error.code == 'tailnet_not_allowed'
    finally:
        from cognitrix.tools.utils import reset_execution_context
        reset_execution_context(token)

    token = _as(ada)
    try:
        listed = await _call('List Tailnet Devices')
        assert listed.status == 'success' and listed.text.splitlines() == [
            '- nas (linux, online): you may run commands as ada (Tailscale SSH)',
            '- Office-PC (windows, online): not allowed',
            '- attic (linux, offline): not allowed']
        # No person approved it: nothing runs, whatever the model sends.
        unapproved = await _call('Run On Tailnet Device', device='nas', user='ada', command='uptime')
        assert unapproved.error.code == 'approval_required' and not os.path.exists(os.environ['FAKE_SSH_LOG'])
        ran = await _call('Run On Tailnet Device', device='nas', user='ada', command='uptime', approved=True)
        assert ran.status == 'success' and ran.text.startswith('ada@nas exit code 0')
    finally:
        reset_execution_context(token)


def test_the_tools_are_not_in_the_first_assistants_set():
    names = {t.name for t in ToolManager.list_all_tools() if t.category == 'tailnet'}
    assert names == {'List Tailnet Devices', 'Run On Tailnet Device'}
    assert 'approved' in _tool('Run On Tailnet Device').internal_params
    assert 'approved' not in json.dumps(_tool('Run On Tailnet Device').parameters_schema()
                                        if hasattr(_tool('Run On Tailnet Device'), 'parameters_schema') else {})


# ---------------------------------------------------------------------------
# A person approves every command
# ---------------------------------------------------------------------------

def test_every_command_needs_a_person():
    run, listing = _tool('Run On Tailnet Device'), _tool('List Tailnet Devices')
    params = {'device': 'nas', 'user': 'ada', 'command': 'systemctl restart nginx'}
    assert needs_person((run, run), params) is True
    assert needs_person((listing, listing), {}) is False
    risk = DestructiveOpDetector().analyze('Run On Tailnet Device', params)
    assert risk.risk_level == RiskLevel.HIGH and risk.categories == ['tailnet_device']
    assert risk.details == ("Runs on nas as ada, a real machine on your tailnet (not the agent's sandbox): "
                            'systemctl restart nginx')
    assert DestructiveOpDetector().analyze('List Tailnet Devices', {}).risk_level == RiskLevel.LOW
    assert 'tailnet_device' in auto_approve_all.NEVER_CATEGORIES


def test_auto_approve_everything_still_shows_the_card():
    auto_approve_all.forget()
    auto_approve_all.remember('u1', True)
    risk = DestructiveOpDetector().analyze('Run On Tailnet Device', {'device': 'nas', 'user': 'ada', 'command': 'id'})

    async def run():
        emitted = []

        async def emit(payload):
            emitted.append(payload)
            resolve_web_approval(payload['request_id'], approved=False, user_key='u1')

        token = web_turn_ctx.set({'emit': emit, 'session_id': 's1', 'bypass': True, 'user_key': 'u1'})
        try:
            result = await ApprovalGate(cache_dir=None).check_approval(
                ToolCall('Run On Tailnet Device', {'device': 'nas', 'user': 'ada', 'command': 'id'}), risk,
                interface='web', timeout=0.5, scope='a1', person_only=True)
        finally:
            web_turn_ctx.reset(token)
        return result, emitted

    try:
        result, emitted = asyncio.run(run())
    finally:
        auto_approve_all.forget()
    assert result.approved is False                             # asked, and the person said no
    assert [e['type'] for e in emitted] == ['approval_request', 'approval_resolved']
    assert emitted[0]['full_shell'] is False                    # no "allow all commands in this chat"


async def test_always_allow_is_for_one_login_on_one_machine(db, tailnet):  # noqa: F811
    await devices.allow('nNAS', ['ada', 'deploy'], 'tailscale')
    await devices.allow('nOLD', ['ada'], 'tailscale')
    gate = ApprovalGate(cache_dir=None)
    gate.permanent_cache.clear()
    asked: list = []

    async def fake_web(tool_call, risk, timeout, person_only=False, must_ask=False, scope=None, **kw):
        asked.append(gate._always_offer(tool_call, scope, person_only, must_ask))
        return ApprovalResult(approved=True, always=True)

    gate._web_approval = fake_web

    async def ask(device, user, command):
        params = {'device': device, 'user': user, 'command': command}
        risk = DestructiveOpDetector().analyze('Run On Tailnet Device', params)
        gate.session_cache.clear()
        return await gate.check_approval(ToolCall('Run On Tailnet Device', params), risk, interface='web',
                                         scope='a1', person_only=True)

    token = web_turn_ctx.set({'emit': None, 'session_id': 's1', 'user_key': 'u1'})
    try:
        assert (await ask('nas', 'ada', 'systemctl status nginx')).approved
        assert asked == [{'kind': 'command', 'label': 'systemctl (ada@nas)', 'computer': 'tailnet:nas'}]
        again = await ask('nas.tail1234.ts.net', 'ada', 'systemctl status sshd')   # same machine, another name
        assert again.approved and again.cached and len(asked) == 1
        await ask('nas', 'deploy', 'systemctl status nginx')                       # another login
        await ask('attic', 'ada', 'systemctl status nginx')                        # another machine
        await ask('nas', 'ada', 'systemctl status nginx; reboot')                  # chained
        await ask('nas', 'ada', 'rm -rf /srv/old')                                 # never saved
        assert len(asked) == 5 and asked[3] is None and asked[4] is None
    finally:
        web_turn_ctx.reset(token)
    rules = {(r.computer, r.prefix) for r in await command_rules.rules_of('u1')}
    assert rules == {('tailnet:nNAS:ada', 'systemctl'), ('tailnet:nNAS:deploy', 'systemctl'),
                     ('tailnet:nOLD:ada', 'systemctl')}


# ---------------------------------------------------------------------------
# A task worker without Tailscale of its own
# ---------------------------------------------------------------------------

async def test_a_worker_without_tailscale_asks_the_web_process(db, tailnet, ssh, monkeypatch):  # noqa: F811
    from cognitrix.computer.paired import relay

    ada = await _user('Ada', 'ada@example.com')
    await devices.allow('nNAS', ['ada'], 'tailscale')
    sent = []

    async def to_web(op, payload, *, timeout=30.0):
        sent.append((op, payload))
        return await devices.serve(op, {'op': op, **payload})     # what the relay route does

    monkeypatch.setattr(relay, 'send', to_web)
    monkeypatch.setattr(devices, 'usable_here', lambda: False)
    token = _as(ada)
    try:
        listed = await _call('List Tailnet Devices')
        assert listed.status == 'success' and listed.text.startswith('- nas (linux, online): you may run commands as ada')
        ran = await _call('Run On Tailnet Device', device='nas', user='ada', command='uptime', approved=True)
        assert ran.status == 'success' and ran.text.startswith('ada@nas exit code 0')
        assert sent[1] == ('tailnet_run', {'device': 'nas', 'user': 'ada', 'command': 'uptime', 'timeout': 60})

        async def nobody(op, payload, *, timeout=30.0):
            return None

        monkeypatch.setattr(relay, 'send', nobody)
        lost = await _call('Run On Tailnet Device', device='nas', user='ada', command='uptime', approved=True)
        assert lost.error.code == 'no_tailscale'
    finally:
        from cognitrix.tools.utils import reset_execution_context
        reset_execution_context(token)


# ---------------------------------------------------------------------------
# Settings > Tailscale
# ---------------------------------------------------------------------------

async def test_only_an_admin_sees_and_changes_the_devices(db, tailnet, ssh, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from cognitrix.api.routes import tailscale as routes
    from cognitrix.common.security import AuthContext
    from cognitrix.tailscale import api, computers

    async def is_admin(user):
        return user.id == 'boss'

    async def changed(app):
        return {'private': {}, 'public': {}}

    monkeypatch.setattr(routes, 'is_admin', is_admin)
    monkeypatch.setattr(service, 'settings_changed', changed)
    monkeypatch.setattr(service, 'snapshot', lambda: {'private': {}, 'public': {}})
    request = SimpleNamespace(app=None, scope={})
    boss, member = (AuthContext(user=SimpleNamespace(id=name, email=f'{name}@example.com'))
                    for name in ('boss', 'member'))

    for call in (routes.read_devices(request, member), routes.remove_device('nNAS', member),
                 routes.allow_device('nNAS', routes.DeviceBody(users=['ada']), member),
                 routes.check_computers(member), routes.save_tailscale(routes.TailscaleBody(devices='off'), request, member)):
        with pytest.raises(HTTPException) as refused:
            await call
        assert refused.value.status_code == 403
    seen = await routes.read_tailscale(request, member)
    assert 'computers' not in seen and 'devices' not in seen

    saved = await routes.save_tailscale(
        routes.TailscaleBody(devices='admins', sandbox='admins', sandbox_tag='boxes', oauth_id='kABC',
                             oauth_secret='tskey-client-kABC-SECRET'), request, boss)
    assert saved['devices'] == 'admins'
    assert saved['computers']['scope'] == 'admins' and saved['computers']['tag'] == 'tag:boxes'
    assert saved['computers']['oauth_id'] == 'kABC' and saved['computers']['oauth_saved'] is True
    assert 'SECRET' not in json.dumps(saved)                    # the secret is never sent back
    assert '"tag:boxes": ["autogroup:admin"]' in saved['computers']['policy']
    for bad in (routes.TailscaleBody(devices='anyone'), routes.TailscaleBody(sandbox='some')):
        with pytest.raises(HTTPException) as refused:
            await routes.save_tailscale(bad, request, boss)
        assert refused.value.status_code == 400

    tailnet.peers['k4']['Tags'] = ['tag:boxes']                 # the agents' computer carries the new tag
    page = await routes.read_devices(request, boss)
    assert page['scope'] == 'admins' and page['ssh'] is True and page['key'].startswith('ssh-ed25519 ')
    assert [d['name'] for d in page['devices']] == ['nas', 'Office-PC', 'attic']
    allowed = await routes.allow_device('nNAS', routes.DeviceBody(users=['ada'], path='key'), boss)
    assert allowed['allowed'] and allowed['users'] == ['ada'] and allowed['path'] == 'key'
    with pytest.raises(HTTPException) as bad_name:
        await routes.allow_device('nNAS', routes.DeviceBody(users=['-oProxyCommand=x']), boss)
    assert bad_name.value.status_code == 400
    with pytest.raises(HTTPException) as unknown:
        await routes.allow_device('nNOPE', routes.DeviceBody(users=['ada']), boss)
    assert unknown.value.status_code == 404
    assert (await routes.remove_device('nNAS', boss)) == {'removed': True}
    with pytest.raises(HTTPException) as gone:
        await routes.remove_device('nNAS', boss)
    assert gone.value.status_code == 404

    # Check: the saved OAuth client is tried against Tailscale.
    monkeypatch.setattr(api, 'access_token', lambda: 'token')
    monkeypatch.setattr(api, 'join_key', lambda tag, description: 'tskey-auth-once')
    monkeypatch.setattr(computers, '_status_cache', None)
    checked = await routes.check_computers(boss)
    assert checked['ok'] is True and [s['name'] for s in checked['steps']] == ['oauth', 'join_key', 'engine']
    assert 'tskey' not in json.dumps(checked)
