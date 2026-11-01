"""The Coding Agent tool: handing work to Claude Code / Codex / Gemini CLI in
the background, the approval rule, argv per CLI and mode, and the risk card."""

import os
import stat
import sys
import time
from pathlib import Path

import pytest

from cognitrix.common import coding_agents as ca
from cognitrix.common import shell as sh
from cognitrix.common.process_security import HostProcessMode
from cognitrix.config import settings
from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel
from cognitrix.tools.misc import bash_output, coding_agent, kill_shell
from cognitrix.tools.utils import (
    ToolExecutionContext,
    reset_execution_context,
    set_execution_context,
)

posix_only = pytest.mark.skipif(os.name == 'nt', reason='uses a POSIX script as a fake CLI')

# A stand-in for a coding agent CLI: prints its arguments and the task it got
# on stdin, then writes a file, like an agent making an edit.
FAKE_CLI = f"""#!{sys.executable}
import sys, time
args = sys.argv[1:]
task = sys.stdin.read()
print('ARGS', ' '.join(args))
print('TASK', task)
sys.stdout.flush()
if 'slow' in task:
    time.sleep(30)
open('made_by_agent.txt', 'w').write('hi')
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    (work / 'sub').mkdir(parents=True)
    monkeypatch.setattr(settings, 'tools_root', work.resolve())
    monkeypatch.delenv('COGNITRIX_SANDBOX_SHELL', raising=False)
    for key in ca.CLIS:
        monkeypatch.delenv(f'COGNITRIX_{key.upper()}_PATH', raising=False)
    return work.resolve()


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """A PATH holding only the fake CLIs we create."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    monkeypatch.setenv('PATH', str(bin_dir))

    def add(name: str) -> Path:
        path = bin_dir / name
        path.write_text(FAKE_CLI, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path
    return add


@pytest.fixture
def local(request):
    token = set_execution_context(ToolExecutionContext(
        user_id='u1', session_id=request.node.name, host_process_mode=HostProcessMode.TRUSTED_LOCAL,
    ))
    yield
    reset_execution_context(token)


async def run(tool, **kwargs) -> str:
    return (await tool.run(**kwargs)).content


# ── argv, names and risk ────────────────────────────────────────────────

def test_argv_per_cli_and_mode():
    assert ca.build_argv('claude', 'claude', 'edit', None) == [
        'claude', '-p', '--output-format', 'text', '--permission-mode', 'acceptEdits',
    ]
    assert ca.build_argv('claude', 'claude', 'read_only', 'sonnet')[-2:] == ['--model', 'sonnet']
    assert 'plan' in ca.build_argv('claude', 'claude', 'read_only', None)
    assert 'bypassPermissions' in ca.build_argv('claude', 'claude', 'full', None)
    codex = ca.build_argv('codex', 'codex', 'edit', 'gpt-5.5')
    assert codex[:2] == ['codex', 'exec'] and codex[-1] == '-'
    assert codex[codex.index('--sandbox') + 1] == 'workspace-write'
    assert 'read-only' in ca.build_argv('codex', 'codex', 'read_only', None)
    assert 'yolo' in ca.build_argv('gemini', 'gemini', 'full', None)
    assert 'auto_edit' in ca.build_argv('gemini', 'gemini', 'edit', None)


def test_names_modes_and_models():
    assert ca.normalise_agent('Claude Code') == 'claude'
    assert ca.normalise_agent(None) == 'auto'
    assert ca.normalise_agent('codex-cli') == 'codex'
    with pytest.raises(ca.CodingAgentError):
        ca.normalise_agent('cursor')
    assert ca.normalise_mode('read-only') == 'read_only'
    assert ca.normalise_mode(None) == 'edit'
    with pytest.raises(ca.CodingAgentError):
        ca.normalise_mode('yolo')
    assert ca.validate_model('claude-sonnet-5-5[1m]') == 'claude-sonnet-5-5[1m]'
    for bad in ('a b', '--dangerously-skip-permissions', 'x;rm -rf /', '"q"'):
        with pytest.raises(ca.CodingAgentError):
            ca.validate_model(bad)


def test_risk_always_asks_except_listing():
    detector = DestructiveOpDetector()
    edit = detector.analyze('Coding Agent', {'task': 'fix the tests', 'agent': 'codex'})
    assert edit.risk_level == RiskLevel.MEDIUM
    assert 'Codex' in edit.details and 'fix the tests' in edit.details
    full = detector.analyze('Coding Agent', {'task': 'x', 'mode': 'full'})
    assert full.risk_level == RiskLevel.HIGH
    listing = detector.analyze('Coding Agent', {'task': '', 'agent': 'list'})
    assert listing.risk_level == RiskLevel.LOW


# ── the tool ────────────────────────────────────────────────────────────

@posix_only
async def test_list_shows_installed(root, fake_bin, local):
    fake_bin('codex')
    out = await run(coding_agent, task='', agent='list')
    assert 'codex (Codex): installed' in out
    assert 'claude (Claude Code): not installed' in out


async def test_refused_without_person_approval(root, local):
    out = await run(coding_agent, task='do it', agent='claude')
    assert 'only runs after the user approves' in out


@posix_only
async def test_nothing_installed(root, fake_bin, local):
    out = await run(coding_agent, task='do it', approved=True)
    assert 'No coding agent CLI is installed' in out


@posix_only
async def test_runs_with_task_on_stdin_and_reports_changes(root, fake_bin, local):
    fake_bin('claude')
    task = 'Add a file; then say "done" & exit $HOME'
    out = await run(coding_agent, task=task, working_dir='sub', wait_seconds=20, approved=True)
    assert 'Claude Code is working in' in out
    assert 'ARGS -p --output-format text --permission-mode acceptEdits' in out
    # The task arrives verbatim on stdin, not through a shell.
    assert f'TASK {task}' in out
    assert 'Claude Code finished after' in out
    assert (root / 'sub' / 'made_by_agent.txt').exists()


@posix_only
async def test_auto_prefers_claude_then_codex(root, fake_bin, local):
    fake_bin('codex')
    fake_bin('gemini')
    out = await run(coding_agent, task='hello', mode='read_only', wait_seconds=20, approved=True)
    assert 'Codex is working' in out
    assert '--sandbox read-only' in out


@posix_only
async def test_background_read_and_kill(root, fake_bin, local):
    fake_bin('claude')
    out = await run(coding_agent, task='slow job', approved=True)
    shell_id = out.split(' as ', 1)[1].split(' ', 1)[0]
    assert shell_id.startswith('shell_')
    deadline = time.monotonic() + 10
    seen = out
    while 'TASK slow job' not in seen and time.monotonic() < deadline:
        seen += await run(bash_output, shell_id=shell_id)
        time.sleep(0.2)
    assert 'TASK slow job' in seen
    assert (await run(bash_output, shell_id=shell_id)).startswith(f'[{shell_id}: running]')
    stopped = await run(kill_shell, shell_id=shell_id)
    assert 'Stopped' in stopped


@posix_only
async def test_time_limit_stops_the_job(root, fake_bin, local, monkeypatch):
    fake_bin('claude')
    monkeypatch.setattr(ca, 'time_limit', lambda: 1)
    out = await run(coding_agent, task='slow job', wait_seconds=15, approved=True)
    assert 'stopped after reaching its time limit' in out


async def test_working_dir_must_stay_inside(root, local, monkeypatch):
    monkeypatch.setenv('COGNITRIX_SANDBOX_SHELL', '1')
    out = await run(coding_agent, task='x', working_dir='/', approved=True)
    assert 'outside the working folder' in out


async def test_shared_server_refuses(root):
    token = set_execution_context(ToolExecutionContext(
        user_id='u1', session_id='s', host_process_mode=HostProcessMode.DENY,
    ))
    try:
        out = await run(coding_agent, task='x', approved=True)
    finally:
        reset_execution_context(token)
    assert out.startswith('Error')


def test_changed_files_in_git_repo(tmp_path):
    import subprocess
    if not sh.shutil.which('git'):
        pytest.skip('git not installed')
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    assert 'clean' in ca.changed_files(tmp_path)
    (tmp_path / 'new.py').write_text('x', encoding='utf-8')
    assert '?? new.py' in ca.changed_files(tmp_path)
    assert ca.changed_files(tmp_path / 'nope') == ''


@posix_only
def test_time_limit_stops_children_left_running(tmp_path):
    # The leader exits at once but leaves a child running in its group.
    shell = sh.start_background(
        ['/bin/sh', '-c', 'sleep 30 & echo $!'], owner='u-orphans', command='orphan',
        cwd=tmp_path, max_seconds=1,
    )
    assert sh.wait_background(shell, 5)
    time.sleep(0.3)
    child = int(sh.read_background(shell).splitlines()[-1])
    os.kill(child, 0)  # still alive before the limit
    deadline = time.monotonic() + 5
    alive = True
    while alive and time.monotonic() < deadline:
        time.sleep(0.2)
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            alive = False
    assert not alive


@posix_only
def test_watchdog_retired_when_job_finishes(tmp_path):
    shell = sh.start_background(
        ['/bin/sh', '-c', 'echo hi'], owner='u-retire', command='quick',
        cwd=tmp_path, max_seconds=3600,
    )
    assert sh.wait_background(shell, 5)
    shell.reader.join(timeout=10)
    shell.watchdog.join(timeout=5)
    assert not shell.watchdog.is_alive()
