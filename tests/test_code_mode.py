"""Code mode (Run Code) and tools loaded on demand (Find Tools)."""

import asyncio

import pytest

import cognitrix.agents  # noqa: F401  (attaches call_tools to Agent)
from cognitrix.models.agent import Agent
from cognitrix.providers.base import LLM
from cognitrix.sessions.base import Session
from cognitrix.tools import code_mode
from cognitrix.tools.base import ToolManager, clear_tool_cache
from cognitrix.tools.tool import tool
from cognitrix.utils.llm_response import LLMResponse

pytest.importorskip('py_mini_racer')

CALLS: list[tuple[str, dict]] = []
IN_FLIGHT = {'now': 0, 'max': 0}


@tool(category='test')
async def add_numbers(a: int, b: int) -> str:
    """Add two numbers."""
    CALLS.append(('add', {'a': a, 'b': b}))
    IN_FLIGHT['now'] += 1
    IN_FLIGHT['max'] = max(IN_FLIGHT['max'], IN_FLIGHT['now'])
    try:
        await asyncio.sleep(0.05)
    finally:
        IN_FLIGHT['now'] -= 1
    return str(a + b)


@tool(category='test')
async def list_files(folder: str) -> str:
    """List the files in a folder as JSON."""
    CALLS.append(('list', {'folder': folder}))
    return '[{"name": "a.py", "size": 10}, {"name": "b.md", "size": 900}]'


@tool(category='test')
async def always_fails(reason: str = '') -> str:
    """A tool that raises."""
    raise RuntimeError('broken on purpose')


@tool(category='test')
async def delete_everything(confirm: bool = False) -> str:
    """Never allowed in these tests."""
    CALLS.append(('delete', {}))
    return 'deleted'


def _agent(tools, **extra) -> Agent:
    return Agent(
        name='Coder',
        llm=LLM(provider='openai', base_url='http://example.invalid', api_key='test', model='test'),
        system_prompt='Test',
        tools=tools,
        **extra,
    )


@pytest.fixture(autouse=True)
def _reset():
    CALLS.clear()
    IN_FLIGHT.update(now=0, max=0)
    clear_tool_cache()
    yield
    clear_tool_cache()


def _run_code():
    found = ToolManager.get_by_name('run_code')
    assert found is not None
    return found


async def _run(agent, code):
    run_code = _run_code()
    result = await run_code.run(code=code, parent=agent, interface='cli')
    return result.outcome


async def test_one_script_calls_many_tools_in_parallel_and_returns_only_the_result():
    agent = _agent([add_numbers, list_files, _run_code()])
    outcome = await _run(agent, """
        const sums = await Promise.all([1, 2, 3, 4, 5].map(n => tools.add_numbers({a: n, b: 10})));
        const files = await tools.list_files({folder: "."});
        console.log("biggest", files.sort((x, y) => y.size - x.size)[0].name);
        return {total: sums.map(Number).reduce((x, y) => x + y, 0)};
    """)

    assert outcome.status == 'success', outcome.text
    assert 'Ran 6 tool calls (6 succeeded)' in outcome.text
    assert 'biggest b.md' in outcome.text
    assert '"total": 65' in outcome.text
    assert len([c for c in CALLS if c[0] == 'add']) == 5
    # The five calls ran together, not one after another. Counting overlapping
    # calls rather than timing them keeps this steady on a busy CI machine.
    assert IN_FLIGHT['max'] > 1


async def test_a_failing_tool_throws_inside_the_script():
    agent = _agent([always_fails, _run_code()])
    outcome = await _run(agent, """
        try { await tools.always_fails({}); return "no error"; }
        catch (e) { return "caught: " + e.tool; }
    """)
    assert outcome.status == 'success', outcome.text
    assert 'caught: always_fails' in outcome.text
    assert '1 failed' in outcome.text


async def test_permissions_apply_to_calls_made_from_the_script():
    agent = _agent([delete_everything, _run_code()], tool_permissions={'Delete Everything': 'deny'})
    outcome = await _run(agent, 'return await tools.delete_everything({confirm: true});')
    assert outcome.status == 'error'
    assert 'blocked' in outcome.text.lower() or 'not' in outcome.text.lower()
    assert CALLS == []


async def test_only_assigned_tools_can_be_called():
    agent = _agent([_run_code()])
    outcome = await _run(agent, 'return await tools.bash({command: "echo hi"});')
    assert outcome.status == 'error'
    assert 'not assigned' in outcome.text


async def test_scripts_cannot_reach_files_network_or_nested_scripts():
    agent = _agent([_run_code()])
    outcome = await _run(agent, """
        return [typeof require, typeof fetch, typeof process, typeof XMLHttpRequest].join(",");
    """)
    assert 'undefined,undefined,undefined,undefined' in outcome.text
    outcome = await _run(agent, 'return await tools.run_code({code: "return 1"});')
    assert outcome.status == 'error'
    assert 'cannot be called from Run Code' in outcome.text


async def test_syntax_errors_and_runaway_scripts_are_reported(monkeypatch):
    agent = _agent([_run_code()])
    outcome = await _run(agent, 'return (;')
    assert outcome.status == 'error'
    assert 'SyntaxError' in outcome.text

    monkeypatch.setattr(code_mode, 'CODE_TIMEOUT_SECONDS', 0.5)
    outcome = await asyncio.wait_for(_run(agent, 'while (true) {}'), 10)
    assert outcome.status == 'error'
    assert 'longer than' in outcome.text


def test_signature_and_search():
    schema = add_numbers.to_dict_format()
    assert code_mode.signature(schema) == 'tools.add_numbers({a: number, b: number})'
    schemas = [t.to_dict_format() for t in (add_numbers, list_files, always_fails)]
    hits = code_mode._search(schemas, 'list folder files', [], limit=8)
    assert code_mode._schema_name(hits[0]) == 'List_Files'
    exact = code_mode._search(schemas, '', ['add_numbers'], limit=8)
    assert [code_mode._schema_name(s) for s in exact] == ['Add_Numbers']


async def test_find_tools_loads_matches_for_the_turn():
    agent = _agent([add_numbers, list_files])
    find = ToolManager.get_by_name('find_tools')
    loaded: set[str] = set()
    token = code_mode.loaded_this_turn.set(loaded)
    try:
        result = await find.run(query='add numbers', parent=agent)
    finally:
        code_mode.loaded_this_turn.reset(token)
    assert 'tools.add_numbers({a: number, b: number})' in result.outcome.text
    assert 'Add_Numbers' in loaded


def test_turn_tools_follow_the_agent_switches(monkeypatch):
    monkeypatch.setattr(code_mode, 'ON_DEMAND_MIN_TOOLS', 2)
    off = _agent([add_numbers, list_files])
    assert code_mode.turn_tools(off) == []
    on = _agent([add_numbers, list_files], tool_mode={'code': True, 'on_demand': True})
    assert sorted(t.name for t in code_mode.turn_tools(on)) == ['Find Tools', 'Run Code']
    # Too few tools to bother loading on demand.
    small = _agent([add_numbers], tool_mode={'on_demand': True})
    assert code_mode.turn_tools(small) == []


def test_tool_mode_is_validated():
    assert code_mode.normalize_tool_mode({'code': True, 'on_demand': False}) == {'code': True}
    assert code_mode.normalize_tool_mode(None) == {}
    with pytest.raises(ValueError):
        code_mode.normalize_tool_mode({'turbo': True})
    agent = Agent.model_validate({
        'name': 'A', 'system_prompt': 'x', 'tool_mode': '{"code": true}',
        'llm': {'provider': 'openai', 'model': 'm', 'base_url': 'http://x', 'api_key': 'k'},
    })
    assert agent.tool_mode == {'code': True}


async def test_on_demand_turn_sends_core_tools_then_the_loaded_ones(monkeypatch):
    monkeypatch.setattr(code_mode, 'ON_DEMAND_MIN_TOOLS', 2)
    agent = _agent([add_numbers, list_files, always_fails], tool_mode={'on_demand': True})
    session = Session(agent_id=str(agent.id))
    sent: list[list[str]] = []
    prompts: list = []

    async def fake_generate(llm, prompt, stream=False, tools=None, **kwargs):
        sent.append(sorted(item['function']['name'] for item in (tools or [])))
        prompts.append(prompt)
        response = LLMResponse()
        if len(sent) == 1:
            response.tool_calls = [{'name': 'find_tools', 'arguments': {'names': ['add_numbers']},
                                    'tool_call_id': 'c1'}]
        elif len(sent) == 2:
            response.tool_calls = [{'name': 'add_numbers', 'arguments': {'a': 1, 'b': 2},
                                    'tool_call_id': 'c2'}]
        else:
            response.add_chunk('3')
        return response

    async def fake_save(self):
        return None

    monkeypatch.setattr('cognitrix.providers.base.LLMManager.generate_response', staticmethod(fake_generate))
    monkeypatch.setattr(Session, 'save', fake_save)

    await session('add 1 and 2', agent, 'cli', False, lambda *a, **k: None, None, True)

    assert sent[0] == ['Find_Tools', 'Load_Skill']
    assert 'Add_Numbers' in sent[1] and 'List_Files' not in sent[1]
    assert session.loaded_tools == ['Add_Numbers']
    assert ('add', {'a': 1, 'b': 2}) in CALLS
    system = prompts[0][0]['content']
    assert 'Tools load on demand' in system and 'list_files' in system
    # Still there after the prompt is rebuilt following a tool call.
    assert 'Tools load on demand' in prompts[2][0]['content']

    # The next turn starts with the tool it loaded before.
    sent.clear()
    await session('again', agent, 'cli', False, lambda *a, **k: None, None, True)
    assert sent[0] == ['Add_Numbers', 'Find_Tools', 'Load_Skill']


async def test_hyphenated_tool_names_are_reachable_by_their_script_alias():
    from cognitrix.models.tool import Tool
    from cognitrix.tools.utils import ToolCallResult

    class Hyphen(Tool):
        async def run(self, **kwargs):
            CALLS.append(('hyphen', kwargs))
            return ToolCallResult(content='read ok', tool_name=self.name)

    mcp_like = Hyphen(name='my-server_read_file', description='Read a file', parameters={'path': 'str'})
    agent = _agent([mcp_like, _run_code()])
    outcome = await _run(agent, 'return await tools.my_server_read_file({path: "a.txt"});')
    assert outcome.status == 'success', outcome.text
    assert 'read ok' in outcome.text
    hits = code_mode._search([mcp_like.to_dict_format()], '', ['my_server_read_file'], limit=8)
    assert len(hits) == 1


@tool(category='test')
async def who_am_i() -> str:
    """Report the caller's user and API key scopes."""
    from cognitrix.tools.utils import current_execution_context
    ctx = current_execution_context()
    return f"{ctx.user_id}|{','.join(sorted(ctx.scopes or [])) or 'all'}"


async def test_tools_called_from_a_script_keep_the_callers_context():
    """A tool called from Run Code sees the same signed-in user and API key
    limits as a direct call. mini-racer runs the Python callback outside the
    caller's context, which used to drop them (create_task then saw nobody)."""
    from cognitrix.tools.utils import (
        ToolExecutionContext,
        reset_execution_context,
        set_execution_context,
    )

    agent = _agent([who_am_i, _run_code()])
    token = set_execution_context(ToolExecutionContext(user_id='user-1', scopes=frozenset({'read'})))
    try:
        outcome = await _run(agent, """
            const both = await Promise.all([tools.who_am_i({}), tools.who_am_i({})]);
            return both;
        """)
    finally:
        reset_execution_context(token)
    assert outcome.status == 'success', outcome.text
    assert outcome.text.count('user-1|read') == 2


async def test_a_check_in_can_use_code_mode_but_still_cannot_change_anything(monkeypatch):
    """A standing assistant's check-in (the morning brief) is read-only. Find
    Tools and Run Code change nothing themselves, so they run; every call a
    script makes is still checked, so a change from a script is refused."""
    from cognitrix.safety import permissions
    from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel
    from cognitrix.safety.permissions import decide_tool_call, read_only_block

    # Stand-in for a reading tool like Read or Grep.
    monkeypatch.setattr(permissions, 'READ_ONLY_TOOLS', permissions.READ_ONLY_TOOLS | {'list_files'})
    low = RiskAssessment(RiskLevel.LOW, [], '', 1.0)
    find_tools = ToolManager.get_by_name('find_tools')
    agent = _agent([list_files, delete_everything, _run_code()], permission_level='allow')
    token = permissions.read_only_run.set('check-ins can only look')
    try:
        for meta in (find_tools, _run_code()):
            decision = decide_tool_call(agent, meta, {}, low)
            assert read_only_block(decision, meta, {}).action == 'run', meta.name

        outcome = await _run(agent, 'return await tools.list_files({folder: "."});')
        assert outcome.status == 'success', outcome.text
        assert 'a.py' in outcome.text

        CALLS.clear()
        outcome = await _run(agent, 'return await tools.delete_everything({confirm: true});')
        assert outcome.status == 'error'
        assert 'check-ins can only look' in outcome.text
        assert CALLS == []
    finally:
        permissions.read_only_run.reset(token)
