"""Skills-security regression tests.

The dynamic-context (!`cmd`) and pip-install steps must run only AFTER an
approval gate, and a skill that carries either is treated as at least MEDIUM
risk even if it declares LOW. Also: the shared command whitelist rejects
inline-exec flags.
"""

import pytest

from cognitrix.common.safe_exec import CommandNotAllowed, build_argv
from cognitrix.skills.executor import SkillExecutor
from cognitrix.skills.models import RiskLevel, SkillEventType, SkillManifest, SkillSafety

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


async def _run(manifest, monkeypatch, approve):
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

    events = []
    async for ev in _executor().execute(manifest, arguments=""):
        events.append(ev)
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
async def test_plain_skill_no_gate(monkeypatch):
    # No !`cmd`, no deps, LOW risk -> no approval needed, no command run.
    manifest = SkillManifest(
        name="plain", description="d", body="just text",
        safety=SkillSafety(risk_level=RiskLevel.LOW),
    )
    events, captured = await _run(manifest, monkeypatch, approve=False)
    assert captured["gate_called"] is False  # LOW + no cmd/deps -> no approval
    assert captured["ran_cmd"] is False
