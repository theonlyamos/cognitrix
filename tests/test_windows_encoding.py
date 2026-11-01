"""Windows code-page regressions.

On Windows a redirected stdout (desktop engine, Celery worker log, uvicorn under
a service) encodes as cp1252. Printing the "⚠️ Risk detected" status line for a
risky tool call raised "'charmap' codec can't encode characters", and the tool
call (e.g. create_agent) failed with that as its result.
"""

import io
import subprocess
import sys

import pytest

from cognitrix.common import encoding, safe_exec
from cognitrix.common.process_security import HostProcessMode
from cognitrix.models import Agent
from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel
from cognitrix.tools.resilient_tool_wrapper import ToolResult


class _LockedCp1252(io.TextIOWrapper):
    """A strict cp1252 stream that can't be switched to UTF-8: the worst case."""

    reconfigure = None


def _cp1252_stream():
    return _LockedCp1252(io.BytesIO(), encoding='cp1252', errors='strict')


@pytest.fixture
def cp1252_stdio(monkeypatch):
    monkeypatch.setattr(sys, 'stdout', _cp1252_stream())
    monkeypatch.setattr(sys, 'stderr', _cp1252_stream())


def test_status_print_never_raises_on_cp1252(cp1252_stdio):
    encoding.status_print('⚠️  Risk detected: high')
    encoding.status_print('日本語 “quotes”')


def test_status_print_does_not_parse_markup_from_tool_params(cp1252_stdio):
    # Tool parameters can contain text that looks like Rich markup.
    encoding.status_print("Running tool with parameters: {'content': '[/red] [bold'}")


def test_ensure_utf8_stdio_switches_a_cp1252_stream(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
    monkeypatch.setattr(sys, 'stdout', stream)
    encoding.ensure_utf8_stdio()
    assert stream.encoding == 'utf-8'
    stream.write('⚠️ ✅')  # would raise UnicodeEncodeError under cp1252


def test_ensure_utf8_stdio_tolerates_missing_streams(monkeypatch):
    # pythonw / windowed PyInstaller builds have no stdout at all.
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    encoding.ensure_utf8_stdio()


def test_decode_output_prefers_utf8_then_code_page(monkeypatch):
    assert encoding.decode_output('✅ café'.encode()) == '✅ café'
    assert encoding.decode_output(None) == ''
    monkeypatch.setattr(encoding.locale, 'getpreferredencoding', lambda _=False: 'cp1252')
    assert encoding.decode_output('café'.encode('cp1252')) == 'café'


def test_utf8_child_env_forces_python_children_to_utf8():
    env = encoding.utf8_child_env({'PATH': '/bin'})
    assert env['PYTHONIOENCODING'] == 'utf-8'
    assert env['PATH'] == '/bin'


def test_run_whitelisted_decodes_bytes_output(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout='✅ café'.encode(), stderr=b'')

    monkeypatch.setattr(safe_exec.subprocess, 'run', fake_run)
    out = safe_exec.run_whitelisted('ls', host_process_mode=HostProcessMode.TRUSTED_LOCAL)
    assert '✅ café' in out
    assert 'text' not in captured  # bytes in, decoded by decode_output
    assert captured['env']['PYTHONIOENCODING'] == 'utf-8'


@pytest.mark.asyncio
async def test_risky_tool_call_succeeds_with_cp1252_stdout(monkeypatch, cp1252_stdio):
    monkeypatch.setenv('COGNITRIX_AUTO_APPROVE', '1')
    llm = LLM(provider='openai', base_url='http://x', api_key='k', model='m')
    agent = Agent(name='A', llm=llm, system_prompt='sys', tools=[
        Tool(name='Create Agent', description='d', parameters={}),
    ])
    monkeypatch.setattr(
        'cognitrix.agents.base.ToolManager.get_by_name',
        staticmethod(lambda name: Tool(name=name, description='d', parameters={})),
    )
    # Same assessment the real detector gives the reported create_agent call.
    monkeypatch.setattr(
        'cognitrix.safety.destructive_ops.DestructiveOpDetector.analyze',
        lambda self, name, args: RiskAssessment(
            risk_level=RiskLevel.HIGH, categories=['file_deletion'],
            details='File or directory deletion', confidence=1.0,
        ),
    )

    async def fake_run_tool(self, tool, params, **kw):
        return ToolResult(success=True, data='Agent "Coder" created successfully')

    monkeypatch.setattr(
        'cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool', fake_run_tool
    )

    result = await agent.call_tools([{
        'name': 'Create Agent',
        'tool_call_id': 'c1',
        'arguments': {'name': 'Coder', 'description': 'Don’t delete files ✅ [/red]'},
    }])
    entry = result['result'][0]
    assert entry['success'] is True, entry
    assert 'created successfully' in entry['data']
