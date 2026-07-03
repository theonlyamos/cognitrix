"""Security regression tests for the bash and file tools.

These pin the hardening: the bash tool must reject shell injection and
non-whitelisted commands, and the file tools must not read outside the
configured tools root.
"""

import pytest

from cognitrix.config import settings
from cognitrix.tools.misc import Read, Write, bash


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    "git status; rm -rf .",   # separator
    "git $(id)",              # substitution
    "echo hi | sh",           # pipe
    "rm -rf /",               # non-whitelisted
    "curl http://evil",       # non-whitelisted
])
async def test_bash_rejects_dangerous(payload):
    res = await bash.run(command=payload)
    assert res.content.startswith("Error"), f"expected rejection for {payload!r}, got {res.content!r}"


@pytest.mark.asyncio
async def test_bash_allows_whitelisted():
    res = await bash.run(command="echo hello")
    assert "hello" in res.content


@pytest.mark.asyncio
async def test_read_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tools_root", tmp_path.resolve())
    res = await Read.run(file_path="../../../../etc/passwd")
    assert res.content.startswith("Error")


@pytest.mark.asyncio
async def test_read_allows_inside_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tools_root", tmp_path.resolve())
    (tmp_path / "hello.txt").write_text("hi there")
    res = await Read.run(file_path="hello.txt")
    assert "hi there" in res.content


@pytest.mark.asyncio
async def test_write_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tools_root", tmp_path.resolve())
    res = await Write.run(file_path="../escape.txt", content="nope")
    assert res.content.startswith("Error")
    assert not (tmp_path.parent / "escape.txt").exists()
