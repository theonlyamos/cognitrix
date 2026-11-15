"""CI helpers for desktop code signing and the update manifest."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'desktop' / 'scripts'


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


signing = _load('code_signing')
manifest = _load('make_update_manifest')

AZURE = {
    'AZURE_TENANT_ID': 't', 'AZURE_CLIENT_ID': 'c', 'AZURE_CLIENT_SECRET': 's',
    'AZURE_SIGNING_ENDPOINT': 'https://eus.codesigning.azure.net',
    'AZURE_SIGNING_ACCOUNT': 'acct', 'AZURE_SIGNING_PROFILE': 'prof',
}


def test_no_secrets_writes_empty_config(tmp_path):
    out = tmp_path / 'signing.json'
    assert signing.windows(out, env={}, engine=tmp_path) == 0
    assert json.loads(out.read_text()) == {}


def test_partial_azure_secrets_are_ignored():
    env = dict(AZURE)
    env.pop('AZURE_SIGNING_PROFILE')
    assert signing.windows_sign_command(env) is None


def test_azure_preferred_over_certificate():
    method, cmd = signing.windows_sign_command({**AZURE, 'WINDOWS_CERTIFICATE_THUMBPRINT': 'AB'})
    assert method == 'Azure Artifact Signing'
    assert cmd['cmd'] == 'artifact-signing-cli'
    assert cmd['args'][-1] == '%1'
    assert 'prof' in cmd['args'] and 'acct' in cmd['args']


def test_certificate_signs_engine_and_writes_config(tmp_path, monkeypatch):
    (tmp_path / 'engine').mkdir()
    exe = tmp_path / 'engine' / 'cognitrix-engine.exe'
    exe.write_bytes(b'MZ')
    calls = []
    monkeypatch.setattr(signing.subprocess, 'run', lambda cmd, check: calls.append(cmd))
    out = tmp_path / 'signing.json'
    env = {'WINDOWS_CERTIFICATE_THUMBPRINT': 'ABC123', 'SIGNTOOL': r'C:\Kits\signtool.exe'}
    assert signing.windows(out, env=env, engine=tmp_path / 'engine') == 0
    assert calls == [[r'C:\Kits\signtool.exe', 'sign', '/sha1', 'ABC123', '/fd', 'sha256',
                      '/tr', signing.TIMESTAMP_URL, '/td', 'sha256', '/d', 'Cognitrix', str(exe)]]
    config = json.loads(out.read_text())
    assert config['bundle']['windows']['signCommand']['args'][-1] == '%1'


def test_mach_o_files_deepest_first_engine_last(tmp_path):
    macho = b'\xcf\xfa\xed\xfe' + b'\0' * 12
    (tmp_path / '_internal' / 'numpy').mkdir(parents=True)
    (tmp_path / 'cognitrix-engine').write_bytes(macho)
    (tmp_path / '_internal' / 'libpython.dylib').write_bytes(macho)
    (tmp_path / '_internal' / 'numpy' / 'core.so').write_bytes(macho)
    (tmp_path / '_internal' / 'data.json').write_text('{}')
    files = [p.relative_to(tmp_path).as_posix() for p in signing.mach_o_files(tmp_path)]
    assert files == ['_internal/numpy/core.so', '_internal/libpython.dylib', 'cognitrix-engine']


def test_macos_engine_without_identity_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(signing.subprocess, 'run', lambda *a, **k: pytest.fail('signed'))
    assert signing.macos_engine('', tmp_path) == 0


@pytest.mark.parametrize('names, expected', [
    (['Cognitrix_x64.app.tar.gz.sig', 'Cognitrix_aarch64.app.tar.gz.sig'],
     {'darwin-x86_64': 'Cognitrix_x64.app.tar.gz', 'darwin-aarch64': 'Cognitrix_aarch64.app.tar.gz'}),
    (['Cognitrix.app.tar.gz.sig'], {'darwin-aarch64': 'Cognitrix.app.tar.gz'}),
    (['Cognitrix_0.5.6_x64-setup.exe.sig'], {'windows-x86_64': 'Cognitrix_0.5.6_x64-setup.exe'}),
])
def test_update_manifest_platforms(tmp_path, names, expected):
    for name in names:
        (tmp_path / name).write_text('sig')
    built = manifest.build('v0.5.6', 'https://example.test/dl', tmp_path)
    assert {k: v['url'].rsplit('/', 1)[1] for k, v in built['platforms'].items()} == expected
