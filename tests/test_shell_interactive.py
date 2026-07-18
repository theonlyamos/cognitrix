"""Interactive shell: no-console fallback + '!'-prefixed shell routing.

- The REPL must not crash where prompt_toolkit can't run (Git Bash / pipes /
  CI); it falls back to plain input().
- Only lines prefixed with '!' run in the terminal; everything else goes to the
  AI (so a natural-language query is never mistaken for a shell command).
"""

import asyncio

import pytest

from cognitrix.cli import shell
from cognitrix.common.process_security import HostProcessMode


@pytest.mark.asyncio
async def test_read_query_falls_back_to_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "typed line")
    assert await shell._read_query(None, "rich", "plain$ ") == "typed line"


@pytest.mark.asyncio
async def test_read_query_uses_prompt_session():
    class PS:
        async def prompt_async(self, p):
            return "from prompt_toolkit"

    assert await shell._read_query(PS(), "rich", "plain$ ") == "from prompt_toolkit"


@pytest.mark.asyncio
async def test_ai_turn_injects_trusted_local_host_process_context():
    captured = {}

    class FakeSession:
        async def __call__(self, *args, **kwargs):
            captured.update(kwargs)

    await shell._run_ai_turn(FakeSession(), 'question', object(), False)

    assert captured['tool_context'].host_process_mode is HostProcessMode.TRUSTED_LOCAL


@pytest.mark.asyncio
async def test_only_bang_prefixed_input_runs_shell(monkeypatch):
    inputs = iter(["!echo hi", "date of the meeting?", "q"])

    class FakePS:
        def __init__(self, *a, **k):
            pass

        async def prompt_async(self, *a, **k):
            try:
                return next(inputs)
            except StopIteration:
                return "q"

    monkeypatch.setattr(shell, "PromptSession", FakePS)

    ran = []
    monkeypatch.setattr(shell, "run_shell_command", lambda cmd: ran.append(cmd) or True)

    async def no_slash(q, a, s):
        return False

    monkeypatch.setattr(shell, "handle_slash_command", no_slash)
    monkeypatch.setattr(shell, "is_multi_step_task", lambda q: False)

    class StubMgr:
        _cache = {"x": 1}

        def list_skills_sync(self):
            return []

    monkeypatch.setattr(shell, "get_skill_manager", lambda: StubMgr())

    ai = []

    class FakeSession:
        async def __call__(self, query, agent, stream=False, output=print, **kwargs):
            ai.append(query)

    agent = type("A", (), {"name": "T"})()

    await shell.initialize_shell(FakeSession(), agent, stream=False)

    # Only the '!'-prefixed line ran in the terminal (with the '!' stripped)...
    assert ran == ["echo hi"]
    # ...and the command-word query went to the AI, not the shell.
    assert ai == ["date of the meeting?"]


@pytest.mark.asyncio
async def test_prompt_session_construction_failure_falls_back(monkeypatch):
    # Simulate prompt_toolkit failing to build (NoConsoleScreenBufferError etc.);
    # the shell should degrade to input() rather than crash.
    inputs = iter(["q"])

    class ExplodingPS:
        def __init__(self, *a, **k):
            raise RuntimeError("NoConsoleScreenBufferError")

    monkeypatch.setattr(shell, "PromptSession", ExplodingPS)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    class StubMgr:
        _cache = {"x": 1}

        def list_skills_sync(self):
            return []

    monkeypatch.setattr(shell, "get_skill_manager", lambda: StubMgr())
    agent = type("A", (), {"name": "T"})()

    # Should run and exit cleanly (via the 'q' from the input() fallback).
    await shell.initialize_shell(object(), agent, stream=False)


# --- Loop robustness / hangs / correctness regressions ---------------------

def _stub_skill_manager(monkeypatch):
    class StubMgr:
        _cache = {"x": 1}

        def list_skills_sync(self):
            return []

    monkeypatch.setattr(shell, "get_skill_manager", lambda: StubMgr())


def _scripted_prompt_session(monkeypatch, lines):
    it = iter(lines)

    class FakePS:
        def __init__(self, *a, **k):
            pass

        async def prompt_async(self, *a, **k):
            try:
                return next(it)
            except StopIteration:
                return "q"

    monkeypatch.setattr(shell, "PromptSession", FakePS)


@pytest.mark.asyncio
async def test_loop_survives_ai_turn_error(monkeypatch):
    # A failing AI turn must NOT tear down the session (was `except: break`).
    _scripted_prompt_session(monkeypatch, ["boom", "ok", "q"])
    _stub_skill_manager(monkeypatch)

    async def no_slash(q, a, s):
        return False

    monkeypatch.setattr(shell, "handle_slash_command", no_slash)
    monkeypatch.setattr(shell, "is_multi_step_task", lambda q: False)

    seen = []

    class FlakySession:
        async def __call__(self, query, agent, stream=False, output=print, **kwargs):
            seen.append(query)
            if query == "boom":
                raise RuntimeError("kaboom")

    agent = type("A", (), {"name": "T"})()
    await shell.initialize_shell(FlakySession(), agent, stream=False)

    # The error on "boom" didn't end the session — "ok" was still processed.
    assert seen == ["boom", "ok"]


def test_list_tasks_and_teams_are_async():
    # They call async Model.all(); if left sync they return un-awaited coroutines
    # and crash with "'coroutine' object is not iterable".
    from cognitrix.cli import handlers

    assert asyncio.iscoroutinefunction(handlers.list_tasks)
    assert asyncio.iscoroutinefunction(handlers.list_teams)


def test_run_shell_command_uses_devnull_and_timeout(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr(shell.subprocess, "run", fake_run)
    assert shell.run_shell_command("echo ok") is True
    # A '!' command can't block on stdin or run forever.
    assert captured["stdin"] is shell.subprocess.DEVNULL
    assert captured["timeout"] == shell.SHELL_COMMAND_TIMEOUT
    assert captured["cmd"] == "echo ok"


def test_run_shell_command_empty_is_noop(monkeypatch):
    called = []
    monkeypatch.setattr(shell.subprocess, "run", lambda *a, **k: called.append(1))
    assert shell.run_shell_command("   ") is False
    assert called == []  # nothing executed for an empty '!' line


def test_run_shell_command_timeout_is_caught(monkeypatch):
    def boom(cmd, **kwargs):
        raise shell.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(shell.subprocess, "run", boom)
    # A timed-out command reports failure instead of raising into the REPL.
    assert shell.run_shell_command("sleep 999") is False


def test_completer_only_path_completes_for_shell_input():
    calls = []

    class SpyPath:
        def get_completions(self, doc, event):
            calls.append(doc.text)
            return iter([])

    completer = shell.CognitrixCompleter(["cd", "q"], ["/help", "/history"])
    completer.path_completer = SpyPath()

    def texts(s):
        doc = shell.Document(s, cursor_position=len(s))
        return [c.text for c in completer.get_completions(doc, None)]

    # Slash command completes by prefix, no filesystem access.
    assert "/history" in texts("/hi")
    assert calls == []
    # Bare natural-language query: still no per-keystroke directory listing.
    texts("what time is the meeting")
    assert calls == []
    # 'cd ' and '!<cmd> ' are the only inputs that path-complete.
    texts("cd sub")
    texts("!ls sub")
    assert calls == ["sub", "sub"]
