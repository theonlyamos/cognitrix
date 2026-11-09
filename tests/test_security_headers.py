"""Security headers on pages and the top-level files of the built web app."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from cognitrix.api import main

    build = tmp_path / 'dist'
    (build / 'icons').mkdir(parents=True)
    (build / 'index.html').write_text('<!doctype html><title>Cognitrix</title>', encoding='utf-8')
    (build / 'manifest.webmanifest').write_text('{"name": "Cognitrix"}', encoding='utf-8')
    (build / 'icons' / 'icon-192.png').write_bytes(b'png')
    (tmp_path / 'secret.txt').write_text('keep out', encoding='utf-8')
    monkeypatch.setattr(main, 'FRONTEND_BUILD_DIR', build)
    monkeypatch.delenv('COGNITRIX_CSP', raising=False)
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    return TestClient(main.app)


def test_pages_get_a_policy_and_files_are_served(client, monkeypatch):
    page = client.get('/home')
    assert page.status_code == 200 and 'Cognitrix' in page.text
    policy = page.headers['content-security-policy']
    assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert page.headers['x-content-type-options'] == 'nosniff'

    manifest = client.get('/manifest.webmanifest')
    assert manifest.json() == {'name': 'Cognitrix'}
    assert 'content-security-policy' not in manifest.headers
    assert client.get('/icons/icon-192.png').content == b'png'
    # Outside the build folder: the page, never the file.
    assert 'keep out' not in client.get('/..%2Fsecret.txt').text

    assert client.get('/api/v1/nope').status_code == 404


def test_policy_can_be_replaced_or_switched_off(client, monkeypatch):
    monkeypatch.setenv('COGNITRIX_CSP', "default-src 'self'")
    assert client.get('/home').headers['content-security-policy'] == "default-src 'self'"
    monkeypatch.setenv('COGNITRIX_CSP', 'off')
    assert 'content-security-policy' not in client.get('/home').headers
    monkeypatch.delenv('COGNITRIX_CSP')
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    assert 'content-security-policy' not in client.get('/home').headers
