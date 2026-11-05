"""Tests for the shared command/path safety boundaries."""

import pytest

from cognitrix.common.safe_exec import (
    CommandNotAllowed,
    PathEscapesRoot,
    build_argv,
    resolve_within_root,
    run_whitelisted,
    run_whitelisted_async,
)
from cognitrix.common.process_security import (
    HostProcessAccessError,
    HostProcessMode,
)


class TestBuildArgv:
    def test_allows_whitelisted_command(self):
        assert build_argv("git status") == ["git", "status"]

    @pytest.mark.parametrize("bad", [
        "git status; rm -rf .",   # command separator
        "git $(id)",              # command substitution
        "git `id`",               # backtick substitution
        "cat a | sh",             # pipe
        "echo x > file",          # redirect
        "cat ../secret",          # path traversal in args
        "echo a && rm b",         # logical and
    ])
    def test_rejects_shell_metacharacters(self, bad):
        with pytest.raises(CommandNotAllowed):
            build_argv(bad)

    @pytest.mark.parametrize("bad", ["rm -rf /", "curl http://evil", "bash -c x", ""])
    def test_rejects_non_whitelisted_or_empty(self, bad):
        with pytest.raises(CommandNotAllowed):
            build_argv(bad)


class TestResolveWithinRoot:
    def test_allows_paths_inside_root(self, tmp_path):
        assert resolve_within_root("sub/file.txt", tmp_path) == (tmp_path / "sub" / "file.txt").resolve()

    def test_allows_root_itself(self, tmp_path):
        assert resolve_within_root(".", tmp_path) == tmp_path.resolve()

    @pytest.mark.parametrize("bad", ["../../etc/passwd", "..", "sub/../../outside"])
    def test_rejects_relative_escapes(self, tmp_path, bad):
        with pytest.raises(PathEscapesRoot):
            resolve_within_root(bad, tmp_path)

    def test_rejects_absolute_outside_root(self, tmp_path):
        with pytest.raises(PathEscapesRoot):
            resolve_within_root("/etc/passwd", tmp_path)


class TestRunners:
    def test_run_whitelisted_executes(self):
        out = run_whitelisted(
            "echo hello", host_process_mode=HostProcessMode.TRUSTED_LOCAL
        )
        assert "hello" in out

    def test_run_whitelisted_blocks_injection(self):
        with pytest.raises(CommandNotAllowed):
            run_whitelisted(
                "echo hi; rm -rf .",
                host_process_mode=HostProcessMode.TRUSTED_LOCAL,
            )

    def test_run_whitelisted_defaults_to_a_required_explicit_policy(self):
        with pytest.raises(TypeError):
            run_whitelisted("echo denied")

    def test_run_whitelisted_denies_untrusted_policy(self):
        with pytest.raises(HostProcessAccessError):
            run_whitelisted(
                "echo denied", host_process_mode=HostProcessMode.DENY
            )

    @pytest.mark.asyncio
    async def test_run_whitelisted_async_executes(self):
        out = await run_whitelisted_async(
            "echo hello", host_process_mode=HostProcessMode.TRUSTED_LOCAL
        )
        assert "hello" in out

    @pytest.mark.asyncio
    async def test_run_whitelisted_async_denies_untrusted_policy(self):
        with pytest.raises(HostProcessAccessError):
            await run_whitelisted_async(
                "echo denied", host_process_mode=HostProcessMode.DENY
            )
