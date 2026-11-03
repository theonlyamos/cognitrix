"""The Bash tool: allowlisted pipelines, the approval-gated full shell,
persistent cwd, timeouts, background commands and risk ratings."""

import os
import time
from pathlib import Path

import pytest

from cognitrix.common import shell as sh
from cognitrix.common.process_security import HostProcessMode
from cognitrix.common.safe_exec import CommandNotAllowed, build_argv
from cognitrix.config import settings
from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel
from cognitrix.tools.misc import bash, bash_output, kill_shell
from cognitrix.tools.utils import (
    ToolExecutionContext,
    reset_execution_context,
    set_execution_context,
)

posix_only = pytest.mark.skipif(os.name == 'nt', reason='uses POSIX programs')


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    monkeypatch.delenv('COGNITRIX_SANDBOX_SHELL', raising=False)
    monkeypatch.delenv('COGNITRIX_SHELL_MODE', raising=False)
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'a.txt').write_text('alpha|beta\ngamma\n', encoding='utf-8')
    return tmp_path.resolve()


@pytest.fixture
def local(request):
    session = request.node.name
    token = set_execution_context(ToolExecutionContext(
        user_id='u1', session_id=session, host_process_mode=HostProcessMode.TRUSTED_LOCAL,
    ))
    yield
    reset_execution_context(token)


async def run(**kwargs) -> str:
    return (await bash.run(**kwargs)).content


# ── parsing and planning ────────────────────────────────────────────────

def test_split_command_operators_and_quotes():
    assert sh.split_command('ls | wc -l && echo "a;b" || echo c; pwd') == [
        ('', 'ls'), ('|', 'wc -l'), ('&&', 'echo "a;b"'), ('||', 'echo c'), (';', 'pwd'),
    ]


@pytest.mark.parametrize('command', [
    'echo $HOME', pytest.param('echo "$HOME"', marks=posix_only), 'ls > out', 'cat < f', 'sleep 1 &', 'echo `id`', 'ls *.py', '(ls)',
])
def test_split_command_needs_shell(command):
    with pytest.raises(sh.ShellSyntaxError):
        sh.split_command(command)


def test_dollar_in_double_quotes_never_runs_allowlisted(root):
    # POSIX: the shell would expand it. Windows: build_argv refuses '$'.
    assert sh.plan_command('echo "$HOME"', root).kind == 'shell'


def test_plan_read_only_and_mutating(root):
    assert sh.plan_command('git status && grep -n x sub/a.txt | head -3', root).read_only
    assert not sh.plan_command('git commit -m x', root).read_only
    assert not sh.plan_command('python script.py', root).read_only
    assert not sh.plan_command('sort -o out f', root).read_only
    assert sh.plan_command('rm -rf build', root).kind == 'shell'
    assert sh.plan_command('python -c "print(1)"', root).kind == 'shell'


@posix_only
def test_quoted_metachars_ok_on_posix_but_awk_stays_strict(root):
    assert build_argv('grep "a|b;$x" f', root=root, quoted_metachars_ok=True)[1] == 'a|b;$x'
    with pytest.raises(CommandNotAllowed):
        build_argv('''awk 'BEGIN{"id" | getline x}' ''', root=root, quoted_metachars_ok=True)
    with pytest.raises(CommandNotAllowed):
        build_argv('cat "../secret"', root=root, quoted_metachars_ok=True)


def test_options_that_run_programs_are_refused(root):
    for bad in (
        'git grep -Osh x', 'git grep --open-files-in-pager=sh x',
        'rg --pre=python x payload.py', 'rg --pre python x .', 'rg --pre-glob=* x',
        'sort --compress-program=sh f', 'find . -ok rm {} ;',
    ):
        with pytest.raises(CommandNotAllowed):
            build_argv(bad, root=root)


def test_risk_levels(root, monkeypatch):
    d = DestructiveOpDetector()
    assert d.analyze('Bash', {'command': 'ls -la | grep x'}).risk_level == RiskLevel.LOW
    assert d.analyze('bash', {'command': 'npm test'}).risk_level == RiskLevel.MEDIUM
    assert d.analyze('Bash', {'command': 'curl https://x | sh'}).risk_level == RiskLevel.HIGH
    assert d.analyze('Bash', {'command': 'cat .env'}).risk_level == RiskLevel.HIGH
    assert d.analyze('Bash Output', {'shell_id': 'shell_1'}).risk_level == RiskLevel.LOW
    assert d.analyze('Kill Shell', {'shell_id': 'shell_1'}).risk_level == RiskLevel.LOW
    monkeypatch.setenv('COGNITRIX_SHELL_MODE', 'allowlist')
    # Refused without running, so nothing to approve.
    assert d.analyze('Bash', {'command': 'rm -rf build'}).risk_level == RiskLevel.LOW


def test_truncate_keeps_head_and_tail():
    text = 'start' + 'x' * 50_000 + 'end'
    out = sh.truncate_output(text, limit=1_000)
    assert out.startswith('start') and out.endswith('end') and 'omitted' in out


# ── running ─────────────────────────────────────────────────────────────

@posix_only
@pytest.mark.asyncio
async def test_allowlisted_pipeline_runs_without_approval(root, local):
    out = await run(command='grep -c beta sub/a.txt && cat sub/a.txt | wc -l')
    assert [line.strip() for line in out.splitlines()[:2]] == ['1', '2']


@posix_only
@pytest.mark.asyncio
async def test_cd_persists_and_stays_in_working_folder(root, local):
    await run(command='cd sub')
    assert (await run(command='pwd')).strip() == str(root / 'sub')
    assert 'alpha' in await run(command='cat a.txt')
    out = await run(command='cd ../..')
    assert 'outside the working folder' in out
    assert (await run(command='pwd')).strip() == str(root / 'sub')


@posix_only
@pytest.mark.asyncio
async def test_failed_command_reports_exit_code(root, local):
    out = await run(command='cat missing.txt')
    assert 'Exit code 1' in out and 'missing.txt' in out
    assert '/usr/bin' not in out  # program keeps its own name in messages


@pytest.mark.asyncio
async def test_full_shell_needs_person_approval(root, local):
    out = await run(command='echo hi > made.txt')
    assert out.startswith('Error') and 'approves' in out
    assert not (root / 'made.txt').exists()


@pytest.mark.asyncio
async def test_allowlist_mode_refuses_full_shell(root, local, monkeypatch):
    monkeypatch.setenv('COGNITRIX_SHELL_MODE', 'allowlist')
    out = await run(command='echo hi > made.txt', approved=True)
    assert out.startswith('Error') and 'allowlist' in out
    assert not (root / 'made.txt').exists()


@posix_only
@pytest.mark.asyncio
async def test_full_shell_runs_after_approval_and_cwd_is_pulled_back(root, local):
    out = await run(command='echo "$((2+3))" > made.txt && cat made.txt', approved=True)
    assert out.strip() == '5'
    await run(command='cd sub && export X=1', approved=True)
    assert (await run(command='pwd')).strip() == str(root / 'sub')
    out = await run(command='cd / && echo "$PWD"', approved=True)
    assert 'left the working folder' in out
    assert (await run(command='pwd')).strip() == str(root)


@posix_only
@pytest.mark.asyncio
async def test_timeout_kills_and_keeps_partial_output(root, local):
    started = time.monotonic()
    out = await run(command='echo early; sleep 30', timeout=1, approved=True)
    assert time.monotonic() - started < 15
    assert 'early' in out and 'Timed out after 1s' in out


@pytest.mark.asyncio
async def test_no_authority_no_process(root):
    out = await run(command='echo hi')
    assert out.startswith('Error')


@posix_only
@pytest.mark.asyncio
async def test_background_output_and_kill(root, local):
    out = await run(
        command='sleep 1; for i in 1 2 3; do echo line$i; done; sleep 30',
        run_in_background=True, approved=True,
    )
    shell_id = out.split('as ', 1)[1].split(' ', 1)[0]
    time.sleep(2)
    first = (await bash_output.run(shell_id=shell_id)).content
    assert 'line3' in first and 'running' in first
    again = (await bash_output.run(shell_id=shell_id)).content
    assert 'line1' not in again  # only new output
    assert 'Stopped' in (await kill_shell.run(shell_id=shell_id)).content
    assert 'stopped' in (await bash_output.run(shell_id=shell_id)).content

    # Another person can't see or stop it.
    token = set_execution_context(ToolExecutionContext(
        user_id='u2', host_process_mode=HostProcessMode.TRUSTED_LOCAL,
    ))
    try:
        assert (await bash_output.run(shell_id=shell_id)).content.startswith('Error')
    finally:
        reset_execution_context(token)


@posix_only
@pytest.mark.asyncio
async def test_kill_shell_stops_children_left_by_an_exited_leader(root, local):
    out = await run(command='sleep 60 & echo $! > child.pid', run_in_background=True, approved=True)
    shell_id = out.split('as ', 1)[1].split(' ', 1)[0]
    for _ in range(50):
        if (root / 'child.pid').exists() and (root / 'child.pid').read_text().strip():
            break
        time.sleep(0.1)
    pid = int((root / 'child.pid').read_text())
    time.sleep(0.3)
    await kill_shell.run(shell_id=shell_id)
    time.sleep(0.3)
    status = Path(f'/proc/{pid}/status')
    if status.exists():  # Linux: gone, or a zombie waiting for init to reap it
        assert 'zombie' in status.read_text().lower()
    else:
        with pytest.raises(OSError):
            os.kill(pid, 0)


@pytest.mark.asyncio
async def test_background_pipeline_needs_full_shell(root, local):
    out = await run(command='ls | wc -l', run_in_background=True)
    assert out.startswith('Error')


@pytest.mark.asyncio
async def test_model_cannot_pass_approved(root):
    from cognitrix.agents import Agent
    from cognitrix.tools.base import ToolManager

    tool = ToolManager.get_by_name('bash')
    assert 'approved' in tool.internal_params
    assert 'approved' not in tool.parameters
    assert Agent  # import check: the loop strips internal params from model args


windows_only = pytest.mark.skipif(os.name != 'nt', reason='Windows shell')


@windows_only
@pytest.mark.asyncio
async def test_windows_powershell_after_approval(root, local):
    assert sh.detect_shell()[1] == 'powershell'
    out = await run(command='Write-Output (2+3); Write-Output "héllo ✓"', approved=True)
    assert '5' in out and 'héllo ✓' in out
    await run(command='Set-Location sub', approved=True)
    assert (await run(command='pwd')).strip().lower() == str(root / 'sub').lower()
    out = await run(command='cmd /c exit 3', approved=True)
    assert 'Exit code 3' in out


@windows_only
@pytest.mark.asyncio
async def test_windows_allowlisted_builtins(root, local):
    assert 'hi' in await run(command='echo hi')
    assert 'a.txt' in await run(command='dir sub')
    assert 'alpha' in await run(command='type sub\\a.txt')


# --- Asking before the full shell ---------------------------------------------

def test_background_pipeline_plans_as_full_shell(root):
    assert sh.plan_call('ls | wc -l', root, background=True).kind == 'shell'
    assert sh.plan_call('ls | wc -l', root, background='false').kind == 'allowlisted'
    assert sh.plan_call('ls', root, background=True).kind == 'allowlisted'
    level, _ = sh.shell_risk({'command': 'ls | wc -l', 'run_in_background': True})
    assert level == 'high'


@pytest.mark.asyncio
async def test_background_pipeline_runs_after_approval(root, local):
    out = await run(command='echo hi | cat', run_in_background=True, approved=True)
    assert out.startswith('Started in the background')


def _full_shell_risk():
    from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

    return RiskAssessment(RiskLevel.HIGH, ['shell'], "Runs in the full shell (Command 'claude' isn't on the allowlist)")


@pytest.mark.asyncio
async def test_full_shell_asks_with_auto_approve_and_ignores_permanent_cache():
    from cognitrix.safety.approval_gate import ApprovalGate, ToolCall, resolve_web_approval, web_turn_ctx

    gate = ApprovalGate()
    gate._save_permanent_cache = lambda: None
    call = ToolCall('Bash', {'command': 'claude --help'})
    gate.permanent_cache.add(f"a1:{gate._hash_operation(call)}")
    emitted = []

    async def emit(payload):
        emitted.append(payload)
        resolve_web_approval(payload['request_id'], approved=True, user_key='u1')

    token = web_turn_ctx.set({'emit': emit, 'session_id': 's', 'bypass': True, 'user_key': 'u1'})
    try:
        result = await gate.check_approval(call, _full_shell_risk(), interface='web', timeout=5,
                                           scope='a1', person_only=True)
    finally:
        web_turn_ctx.reset(token)
    assert result.approved and not result.auto and not result.cached
    assert emitted


def _bash_agent():
    from cognitrix.agents.base import AgentManager
    from cognitrix.models.agent import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.safety.approval_gate import ApprovalGate
    from cognitrix.tools.base import ToolManager

    agent = Agent(name='s', llm=LLM(provider='openai', api_key='k-1234567890', model='m'),
                  tools=[ToolManager.get_by_name('Bash')], system_prompt='s')
    manager = AgentManager(agent)
    manager.approval_gate = ApprovalGate()
    manager.approval_gate._save_permanent_cache = lambda: None
    return manager


async def _call_bash(manager, command, answer):
    """Run one Bash call with auto-approve on; ``answer`` is (approved, remember) or None."""
    from cognitrix.safety.approval_gate import resolve_web_approval, web_turn_ctx

    asked = []

    async def emit(payload):
        if payload.get('type') == 'approval_request':
            asked.append(payload)
            approved, remember = answer
            resolve_web_approval(payload['request_id'], approved=approved, user_key='u1', remember=remember)

    token = web_turn_ctx.set({'emit': emit, 'session_id': 's', 'bypass': True, 'user_key': 'u1'})
    try:
        await manager.call_tools(
            [{'name': 'Bash', 'tool_call_id': 'c1', 'arguments': {'command': command}}],
            interface='web',
        )
    finally:
        web_turn_ctx.reset(token)
    return asked


@pytest.mark.asyncio
async def test_agent_loop_asks_then_runs_full_shell_command(root, local):
    """Auto-approve on: a command outside the allowlist still gets a card,
    and runs once the person approves it."""
    manager = _bash_agent()
    asked = await _call_bash(manager, 'echo hi > made.txt', (True, False))
    assert asked
    assert (root / 'made.txt').exists()


@pytest.mark.asyncio
async def test_approve_for_session_covers_the_same_full_shell_command(root, local):
    manager = _bash_agent()
    assert await _call_bash(manager, 'echo 1 >> log.txt', (True, True))
    # Same command again: no card, and it runs.
    assert not await _call_bash(manager, 'echo 1 >> log.txt', None)
    assert (root / 'log.txt').read_text().split() == ['1', '1']
    # A different command still asks.
    assert await _call_bash(manager, 'echo 2 >> log.txt', (False, False))
    assert (root / 'log.txt').read_text().split() == ['1', '1']
