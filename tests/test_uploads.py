"""P1 upload handling: attachment save/cap/sanitize + image-content resolution."""

import base64
import io
import os

import pytest
from fastapi import HTTPException
from PIL import Image

from cognitrix.api.routes.agents import (
    MAX_UPLOAD_FILE_BYTES,
    _decode_data_url,
    _save_attachments,
)
from cognitrix.providers.base import LLMManager


def _png_data_url() -> str:
    buf = io.BytesIO()
    Image.new('RGB', (2, 2), (255, 0, 0)).save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _text_data_url(text: str = 'hello') -> str:
    return 'data:text/plain;base64,' + base64.b64encode(text.encode()).decode()


def test_decode_data_url():
    assert _decode_data_url(_text_data_url('hi')) == b'hi'
    assert _decode_data_url('not-a-data-url') is None
    assert _decode_data_url('') is None


def test_save_attachments_splits_and_confines(tmp_path, monkeypatch):
    monkeypatch.setenv('COGNITRIX_TOOLS_ROOT', str(tmp_path))
    images, files = _save_attachments([
        {'kind': 'image', 'name': 'shot.png', 'dataUrl': _png_data_url()},
        # crafted traversal name must be stripped to a basename under uploads/
        {'kind': 'file', 'name': '../../evil.txt', 'dataUrl': _text_data_url()},
    ])
    assert len(images) == 1 and len(files) == 1
    for entry in (*images, *files):
        assert os.path.isfile(entry['path'])
        # every saved file stays under <tools_root>/uploads
        assert str((tmp_path / 'uploads').resolve()) in entry['path']
    assert os.path.basename(files[0]['path']).endswith('evil.txt')
    assert '..' not in os.path.relpath(files[0]['path'], tmp_path)


def test_non_image_declared_as_image_falls_back_to_file(tmp_path, monkeypatch):
    monkeypatch.setenv('COGNITRIX_TOOLS_ROOT', str(tmp_path))
    images, files = _save_attachments([
        {'kind': 'image', 'name': 'fake.png', 'dataUrl': _text_data_url('not really a png')},
    ])
    assert images == [] and len(files) == 1


def test_save_attachments_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv('COGNITRIX_TOOLS_ROOT', str(tmp_path))
    payload = base64.b64encode(b'x' * (MAX_UPLOAD_FILE_BYTES + 1)).decode()
    with pytest.raises(HTTPException) as exc:
        _save_attachments([
            {'kind': 'file', 'name': 'big.bin',
             'dataUrl': 'data:application/octet-stream;base64,' + payload},
        ])
    assert exc.value.status_code == 413


def test_image_url_from_content(tmp_path):
    data_uri = _png_data_url()
    assert LLMManager._image_url_from_content(data_uri) == data_uri  # passthrough

    path = tmp_path / 'x.png'
    Image.new('RGB', (2, 2)).save(path, format='PNG')
    out = LLMManager._image_url_from_content(str(path))
    assert out and out.startswith('data:image/png;base64,')

    assert LLMManager._image_url_from_content(str(tmp_path / 'missing.png')) is None
