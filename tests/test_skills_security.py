"""Skills-security regression tests.

The dynamic-context (!`cmd`) and pip-install steps must run only AFTER an
approval gate, and a skill that carries either is treated as at least MEDIUM
risk even if it declares LOW. Also: the shared command whitelist rejects
inline-exec flags.
"""

import asyncio
from types import SimpleNamespace

import pytest

from cognitrix.common.process_security import HostProcessMode
from cognitrix.common.safe_exec import CommandNotAllowed, build_argv
from cognitrix.skills.executor import SkillExecutionError, SkillExecutor
from cognitrix.skills.models import RiskLevel, SkillEventType, SkillManifest, SkillSafety
from cognitrix.tools.utils import (
    ToolExecutionContext,
    current_execution_context,
    reset_execution_context,
    set_execution_context,
)

# --- whitelist flag gating ---

def test_legit_interpreter_use_allowed():
    assert build_argv("python script.py")[:2] == ["python", "script.py"]
    assert build_argv("git log --oneline")[0] == "git"


@pytest.mark.parametrize("bad", [
    "python -c import os",
    "python3 -c print(1)",
    "node -e process.exit()",
    "node --eval x",
    "find . -delete",
    "find . -exec rm {} +",
    "sed -i s/a/b/ f",
    "sed -i.bak s/a/b/ f",
    "awk 'BEGIN{system(\"id\")}'",
])
def test_inline_exec_flags_rejected(bad):
    with pytest.raises(CommandNotAllowed):
        build_argv(bad)


# --- executor: approval before side effects ---

def _executor():
    # execute() reaches the gate before touching agent_manager/llm, so stubs are fine.
    return SkillExecutor(agent_manager=object(), llm=object())


async def _run(manifest, monkeypatch, approve, arguments=''):
    captured = {"ran_cmd": False, "installed": False, "gate_called": False}

    async def fake_gate(self, tool_call, risk, interface='cli', **kw):
        from cognitrix.safety.approval_gate import ApprovalResult
        captured["gate_called"] = True
        return ApprovalResult(approved=approve)

    monkeypatch.setattr("cognitrix.safety.approval_gate.ApprovalGate.check_approval", fake_gate)

    async def fake_shell(self, cmd):
        captured["ran_cmd"] = True
        return "output"

    async def fake_deps(self, manifest):
        captured["installed"] = True

    monkeypatch.setattr(SkillExecutor, "_run_shell_command", fake_shell)
    monkeypatch.setattr(SkillExecutor, "_ensure_dependencies", fake_deps)

    token = set_execution_context(ToolExecutionContext(
        host_process_mode=HostProcessMode.TRUSTED_LOCAL,
    ))
    events = []
    try:
        async for ev in _executor().execute(manifest, arguments=arguments):
            events.append(ev)
    finally:
        reset_execution_context(token)
    return events, captured


@pytest.mark.asyncio
async def test_low_risk_skill_with_shell_requires_approval(monkeypatch):
    # LOW declared, but contains a !`cmd` block -> must be gated; denial blocks it.
    manifest = SkillManifest(
        name="danger", description="d",
        body="context: !`python -c evil`",
        safety=SkillSafety(risk_level=RiskLevel.LOW),
    )
    events, captured = await _run(manifest, monkeypatch, approve=False)
    assert captured["gate_called"] is True   # gated despite declaring LOW
    assert captured["ran_cmd"] is False       # command NOT executed on denial
    assert any(e.type == SkillEventType.SKILL_ERROR for e in events)


@pytest.mark.asyncio
async def test_approved_skill_runs_command(monkeypatch):
    manifest = SkillManifest(
        name="ok", description="d",
        body="context: !`git status`",
        safety=SkillSafety(risk_level=RiskLevel.LOW),
    )
    events, captured = await _run(manifest, monkeypatch, approve=True)
    assert captured["ran_cmd"] is True  # runs only after approval


@pytest.mark.asyncio
async def test_rendered_argument_cannot_inject_dynamic_command_before_gate(
    monkeypatch,
):
    manifest = SkillManifest(
        name='rendered-dynamic',
        description='d',
        body='context: $ARGUMENTS',
        safety=SkillSafety(risk_level=RiskLevel.LOW),
    )

    events, captured = await _run(
        manifest,
        monkeypatch,
        approve=False,
        arguments='!`echo injected`',
    )

    assert captured['gate_called'] is True
    assert captured['ran_cmd'] is False
    assert any(event.type == SkillEventType.SKILL_ERROR for event in events)


@pytest.mark.asyncio
async def test_plain_skill_no_gate(monkeypatch):
    # No !`cmd`, no deps, LOW risk -> no approval needed, no command run.
    manifest = SkillManifest(
        name="plain", description="d", body="just text",
        safety=SkillSafety(risk_level=RiskLevel.LOW),
    )
    events, captured = await _run(manifest, monkeypatch, approve=False)
    assert captured["gate_called"] is False  # LOW + no cmd/deps -> no approval
    assert captured["ran_cmd"] is False


@pytest.mark.asyncio
async def test_dynamic_skill_is_denied_without_host_process_authority(monkeypatch):
    spawned = []

    async def fake_run(*args, **kwargs):
        spawned.append((args, kwargs))
        return 'unexpected'

    monkeypatch.setattr(
        'cognitrix.skills.executor.run_whitelisted_async', fake_run
    )
    manifest = SkillManifest(
        name='dynamic-denied',
        description='d',
        body='context: !`echo secret`',
        safety=SkillSafety(risk_level=RiskLevel.LOW),
    )

    events = []
    async for event in _executor().execute(manifest):
        events.append(event)

    assert spawned == []
    assert any(event.type == SkillEventType.SKILL_ERROR for event in events)


@pytest.mark.asyncio
async def test_pip_skill_is_denied_before_dependency_probe_or_spawn(monkeypatch):
    imported = []
    spawned = []
    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == 'host_reader':
            imported.append(name)
            raise AssertionError('dependency import must not run')
        return original_import(name, *args, **kwargs)

    async def fake_spawn(*args, **kwargs):
        spawned.append((args, kwargs))
        raise AssertionError('pip must not spawn')

    monkeypatch.setattr('builtins.__import__', fake_import)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_spawn)
    manifest = SkillManifest(
        name='deps-denied',
        description='d',
        body='plain prompt',
        dependencies={'pip': ['host-reader']},
        safety=SkillSafety(risk_level=RiskLevel.LOW),
    )

    events = []
    async for event in _executor().execute(manifest):
        events.append(event)

    assert imported == []
    assert spawned == []
    assert any(event.type == SkillEventType.SKILL_ERROR for event in events)


class _FakeTool:
    def __init__(self, name):
        self.name = name

    def to_dict_format(self):
        return {'type': 'function', 'function': {'name': self.name}}


class _AwaitableAgent:
    def __init__(self, *, agent_id, tools):
        self.id = agent_id
        self.system_prompt = 'selected prompt'
        self.tools = tools
        self.awaited = False

    def __await__(self):
        self.awaited = True

        async def resolve():
            return self

        return resolve().__await__()


class _Response:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.current_chunk = ''


def _fork_harness(monkeypatch, selected, tool_calls):
    from cognitrix import agents, models

    captured = {}
    parent_tools = [_FakeTool('Read'), _FakeTool('Bash')]
    parent_manager = SimpleNamespace(
        agent=SimpleNamespace(tools=parent_tools),
    )
    responses = iter([_Response(tool_calls), _Response([])])

    async def llm(_messages, **_kwargs):
        response = next(responses)

        async def stream():
            yield response

        return stream()

    class FakeAgent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.id = kwargs.get('id', 'ephemeral')

        @classmethod
        def find_one(cls, _query):
            return selected

    class FakeAgentManager:
        def __init__(self, agent):
            self.agent = agent
            captured['agent'] = agent

        def formatted_system_prompt(self):
            return self.agent.system_prompt

        async def call_tools(self, calls):
            captured['context'] = current_execution_context()
            captured['calls'] = calls
            return {'type': 'tool_calls_result', 'result': []}

    monkeypatch.setattr(models, 'Agent', FakeAgent)
    monkeypatch.setattr(agents.base, 'AgentManager', FakeAgentManager)
    return SkillExecutor(parent_manager, llm), captured


@pytest.mark.asyncio
async def test_forked_selected_agent_cannot_bypass_parent_or_manifest_tools(
    monkeypatch,
):
    selected = _AwaitableAgent(
        agent_id='selected-agent',
        tools=[_FakeTool('Read'), _FakeTool('Bash'), _FakeTool('Write')],
    )
    executor, captured = _fork_harness(monkeypatch, selected, [])
    manifest = SkillManifest(
        name='fork-filter',
        description='d',
        body='prompt',
        context='fork',
        agent='selected',
        allowed_tools=['Read'],
    )
    token = set_execution_context(ToolExecutionContext(
        allowed_agents=frozenset({'selected-agent'}),
    ))
    try:
        async for _ in executor._execute_forked('prompt', manifest, None):
            pass
    finally:
        reset_execution_context(token)

    assert selected.awaited is True
    assert [tool.name for tool in captured['agent'].tools] == ['Read']


@pytest.mark.asyncio
async def test_forked_selected_agent_must_be_allowed_by_parent_context(monkeypatch):
    selected = _AwaitableAgent(
        agent_id='blocked-agent',
        tools=[_FakeTool('Read')],
    )
    executor, _captured = _fork_harness(monkeypatch, selected, [])
    manifest = SkillManifest(
        name='fork-agent-denied',
        description='d',
        body='prompt',
        context='fork',
        agent='blocked',
    )
    token = set_execution_context(ToolExecutionContext(
        allowed_agents=frozenset({'other-agent'}),
    ))
    try:
        with pytest.raises(SkillExecutionError, match='not authorized'):
            async for _ in executor._execute_forked('prompt', manifest, None):
                pass
    finally:
        reset_execution_context(token)

    assert selected.awaited is True


@pytest.mark.asyncio
async def test_forked_tool_calls_bind_non_transitive_child_context(monkeypatch):
    selected = _AwaitableAgent(
        agent_id='selected-agent',
        tools=[_FakeTool('Read')],
    )
    executor, captured = _fork_harness(
        monkeypatch,
        selected,
        [{'name': 'Read', 'arguments': {'file_path': 'x'}}],
    )
    manifest = SkillManifest(
        name='fork-context',
        description='d',
        body='prompt',
        context='fork',
        agent='selected',
        allowed_tools=['Read'],
    )
    token = set_execution_context(ToolExecutionContext(
        user_id='user-1',
        session_id='parent-session',
        agent_id='parent-agent',
        allowed_agents=frozenset({'selected-agent'}),
        host_process_mode=HostProcessMode.TRUSTED_LOCAL,
    ))
    try:
        async for _ in executor._execute_forked('prompt', manifest, None):
            pass
    finally:
        reset_execution_context(token)

    child = captured['context']
    assert child.user_id == 'user-1'
    assert child.allowed_agents == frozenset({'selected-agent'})
    assert child.session_id is None
    assert child.agent_id is None
    assert child.host_process_mode is HostProcessMode.DENY
