"""The app's own CLI in the sandboxed computer: installed in the image from
the same source, and given the person's model keys only as placeholders."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cognitrix.common import key_proxy as kp
from cognitrix.common import sandbox_work as sw
from cognitrix.common.key_proxy import HiddenValue

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    from cognitrix.config import settings
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    return tmp_path


@pytest.mark.parametrize('command', [
    'cognitrix -p hi', 'cd x && cognitrix --version', 'FOO=1 cognitrix skills --list',
    'echo $(cognitrix --version)', '/usr/local/bin/cognitrix -v', 'cognitrix-python x.py',
    'ls\ncognitrix --version', 'true; cognitrix', '(cognitrix -p hi)',
])
def test_commands_that_run_the_cli(command):
    assert sw.runs_cognitrix(command)


@pytest.mark.parametrize('command', [
    'cd cognitrix && pytest', 'pip install cognitrix', 'ls cognitrix/', 'git clone x/cognitrix',
    'python -m cognitrix', 'cat cognitrix.log', 'echo cognitrix', '',
])
def test_other_commands_do_not(command):
    assert not sw.runs_cognitrix(command)


def test_cli_keys_are_hidden_per_provider_host(monkeypatch):
    import cognitrix.providers.saved_keys as keys
    real = {'anthropic': 'sk-ant-real', 'google': 'g-real'}
    monkeypatch.setattr(keys, 'env_key', lambda provider: real.get(provider))
    monkeypatch.setattr(keys, 'get_saved_key', lambda provider: None)
    env = sw.cognitrix_cli_env()
    assert set(env) == {'ANTHROPIC_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY'}
    assert all(isinstance(v, HiddenValue) for v in env.values())
    assert env['ANTHROPIC_API_KEY'].hosts == ('api.anthropic.com',)
    assert env['GOOGLE_API_KEY'].hosts == ('generativelanguage.googleapis.com',)


def test_running_the_cli_sends_placeholders_never_keys(workdir, monkeypatch):
    import cognitrix.providers.saved_keys as keys
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
    monkeypatch.setattr(keys, 'env_key', lambda provider: 'sk-ant-real-key' if provider == 'anthropic' else None)
    monkeypatch.setattr(keys, 'get_saved_key', lambda provider: None)
    work = sw.SandboxWork('alice', 'agent-1')
    work._box = Box()
    work.run('cognitrix --provider anthropic -p hi')
    env = sent[0]['env']
    assert 'sk-ant-real-key' not in json.dumps(sent)
    assert kp.MARK in env['ANTHROPIC_API_KEY'] and env['HTTPS_PROXY'].startswith('http://cognitrix:')
    # A person's own variable of the same name wins.
    sent.clear()
    work.run('cognitrix -p hi', env={'ANTHROPIC_API_KEY': 'mine'})
    assert sent[0]['env']['ANTHROPIC_API_KEY'] == 'mine'
    # Other commands get no model keys.
    sent.clear()
    work.run('cd cognitrix && pytest')
    assert 'ANTHROPIC_API_KEY' not in sent[0]['env']


def test_agent_is_told_about_the_cli():
    text = sw.sandbox_prompt([], 'agent-1', 'alice')
    assert '`cognitrix`' in text and 'cognitrix-python' in text


def test_image_installs_the_cli_from_this_source_without_keys():
    dockerfile = (ROOT / 'sandbox/desktop/Dockerfile').read_text()
    assert 'source=pyproject.toml,target=/tmp/cognitrix-src/pyproject.toml' in dockerfile
    assert 'source=cognitrix,target=/tmp/cognitrix-src/cognitrix' in dockerfile
    assert 'cognitrix-cli/install.sh package /tmp/cognitrix-src /opt/cognitrix-cli' in dockerfile
    assert 'API_KEY' not in dockerfile.split('cognitrix-cli/install.sh package')[1].split('USER agent')[0]
    install = (ROOT / 'sandbox/desktop/cognitrix-cli/install.sh').read_text()
    assert 'skip="chromadb"' in install
    for wrapper in ('cognitrix', 'cognitrix-python'):
        assert 'env -u PYTHONPATH /opt/cognitrix-cli/bin/' in (ROOT / 'sandbox/desktop/cognitrix-cli' / wrapper).read_text()
    workflow = (ROOT / '.github/workflows/sandbox-image.yml').read_text()
    assert "- 'pyproject.toml'" in workflow and 'COGNITRIX_VERSION=' in workflow


def test_version_bump_only_rebuilds_the_last_layers():
    """A version bump must not change the big layers' digests, or everyone
    downloads the whole ~3.6 GB image again (sandbox-image.yml keeps a cache)."""
    dockerfile = (ROOT / 'sandbox/desktop/Dockerfile').read_text()
    final = dockerfile.split('\nFROM base\n')[1]
    order = [
        'npm install -g',
        'install.sh deps',
        'install.sh package',
        'COPY cognitrix/computer/',
        'ARG COGNITRIX_VERSION',
    ]
    positions = [final.index(step) for step in order]
    assert positions == sorted(positions)
    # The deps layer sees only the dependency list, never pyproject.toml
    # (which holds the version) or the source.
    deps_step = final.split('COPY --from=cli-requirements')[1].split('install.sh deps')[0]
    assert 'pyproject.toml' not in deps_step and 'source=cognitrix' not in deps_step
    before_package = final.split('install.sh package')[0].rsplit('RUN', 1)[0]
    assert 'source=pyproject.toml' not in before_package and 'COPY cognitrix' not in before_package
    # A chmod over /usr/local/bin would copy uv and agy into another layer.
    assert 'chmod 755 /usr/local/bin/*' not in dockerfile
    workflow = (ROOT / '.github/workflows/sandbox-image.yml').read_text()
    assert 'cache-from: type=registry' in workflow and 'cache-to: type=registry' in workflow
    assert 'mode=max' in workflow
