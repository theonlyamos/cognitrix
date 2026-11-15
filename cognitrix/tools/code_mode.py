"""Code mode and tools loaded on demand.

Two per-agent switches (``Agent.tool_mode``) that cut the tokens a long job
spends on tools:

* ``on_demand``: the model is sent only a few core tool schemas plus Find
  Tools. Find Tools searches the agent's other tools and loads the ones it
  picks, so their full schemas are sent from the next step on (and for the
  rest of the chat, see ``Session.loaded_tools``).
* ``code``: Run Code lets the model write one JavaScript snippet that calls
  many tools (in a loop, in parallel with ``Promise.all``) and returns only
  what it needs, instead of one model round trip per call with every result
  pasted into the chat.

Run Code runs the snippet in a V8 isolate (mini-racer) with no file system,
network or modules. The only way out is ``tools.<name>(args)``, which
goes through the agent's normal tool path (``Agent.call_tools``): assigned
tools only, permission levels, risk checks and approvals all apply to every
call made from the script, exactly as if the model had made it directly.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import uuid
from typing import Any

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ArtifactRef, ToolOutcome

logger = logging.getLogger('cognitrix.log')

CODE_MODE_CATEGORY = 'code_mode'
FIND_TOOLS = 'Find Tools'
RUN_CODE = 'Run Code'

# Always sent in on-demand mode: the meta tools and the ones that steer the
# chat itself (asking the person, folders, skills, cards).
CORE_TOOL_NAMES = frozenset({
    'find tools', 'run code', 'ask user', 'request folder', 'load skill', 'show card',
})

# Below this many tools, loading on demand saves too little to be worth a step.
ON_DEMAND_MIN_TOOLS = int(os.getenv('COGNITRIX_ON_DEMAND_MIN_TOOLS', '12') or 12)

CODE_TIMEOUT_SECONDS = float(os.getenv('COGNITRIX_CODE_MODE_TIMEOUT', '600') or 600)
CODE_MEMORY_BYTES = 256 * 1024 * 1024
MAX_CALLS_PER_SCRIPT = int(os.getenv('COGNITRIX_CODE_MODE_MAX_CALLS', '200') or 200)
MAX_OUTPUT_CHARS = 20_000

# Tools a script can't call: no scripts inside scripts, no loading tools
# (the script can already call any assigned tool), and nothing that waits on
# the person or hands them the screen from the middle of a script.
_NOT_FROM_CODE = frozenset({'run code', 'find tools', 'ask user', 'ask to take over'})

# The tool names Find Tools loaded during this turn; the chat loop sends
# their schemas from the next step on. None when on-demand loading is off.
loaded_this_turn: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar(
    'code_mode_loaded_tools', default=None)


def _key(name: str) -> str:
    return str(name or '').strip().casefold().replace('_', ' ')


def js_name(name: str) -> str:
    """The name a tool has inside Run Code (and in tool calls): snake_case."""
    return re.sub(r'\W+', '_', str(name or '').strip()).strip('_').lower()


def tool_mode(agent: Any) -> dict[str, bool]:
    raw = getattr(agent, 'tool_mode', None) or {}
    if not isinstance(raw, dict):
        return {'code': False, 'on_demand': False}
    return {'code': bool(raw.get('code')), 'on_demand': bool(raw.get('on_demand'))}


def normalize_tool_mode(value: Any) -> dict[str, bool]:
    """What the agent page may store: two switches, nothing else."""
    if value in (None, ''):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError('tool_mode must be an object') from None
    if not isinstance(value, dict):
        raise ValueError('tool_mode must be an object')
    unknown = set(value) - {'code', 'on_demand'}
    if unknown:
        raise ValueError(f"Unknown tool_mode setting: {', '.join(sorted(unknown))}")
    return {k: bool(v) for k, v in value.items() if v}


def code_mode_available() -> bool:
    try:
        import py_mini_racer  # noqa: F401
    except Exception:
        return False
    return True


def turn_tools(agent: Any) -> list:
    """The meta tools this agent's settings add to a turn."""
    from cognitrix.tools.base import ToolManager

    mode = tool_mode(agent)
    names = []
    if mode['on_demand'] and len(getattr(agent, 'tools', None) or []) >= ON_DEMAND_MIN_TOOLS:
        names.append(FIND_TOOLS)
    if mode['code'] and code_mode_available():
        names.append(RUN_CODE)
    have = {_key(getattr(t, 'name', '')) for t in agent.tools}
    out = []
    for name in names:
        if _key(name) in have:
            continue
        found = ToolManager.get_by_name(name)
        if found is not None:
            out.append(found)
    return out


def sent_schemas(formatted: list[dict[str, Any]], loaded: set[str] | None) -> list[dict[str, Any]]:
    """The tool schemas to send this step.

    With on-demand loading (``loaded`` is a set) only the core tools and the
    ones already loaded go out; otherwise every schema does.
    """
    if loaded is None:
        return formatted
    keep = CORE_TOOL_NAMES | {_key(n) for n in loaded}
    return [f for f in formatted if _key(_schema_name(f)) in keep]


def _schema_name(schema: dict[str, Any]) -> str:
    fn = schema.get('function') if isinstance(schema, dict) else None
    return str((fn or {}).get('name') or schema.get('name') or '')


def _schema_params(schema: dict[str, Any]) -> dict[str, Any]:
    fn = schema.get('function') if isinstance(schema, dict) else None
    params = (fn or {}).get('parameters') or schema.get('parameters') or {}
    return params if isinstance(params, dict) else {}


def _schema_description(schema: dict[str, Any]) -> str:
    fn = schema.get('function') if isinstance(schema, dict) else None
    return str((fn or {}).get('description') or schema.get('description') or '')


_JS_TYPES = {'string': 'string', 'integer': 'number', 'number': 'number', 'boolean': 'boolean',
             'array': 'any[]', 'object': 'object', 'null': 'null'}


def signature(schema: dict[str, Any]) -> str:
    """``tools.web_fetch({url: string, prompt?: string})`` for a tool schema."""
    params = _schema_params(schema)
    props = params.get('properties') or {}
    required = set(params.get('required') or [])
    parts = []
    for name, prop in props.items():
        ptype = prop.get('type') if isinstance(prop, dict) else None
        if isinstance(ptype, list):
            ptype = '|'.join(_JS_TYPES.get(str(t), 'any') for t in ptype)
        else:
            ptype = _JS_TYPES.get(str(ptype), 'any')
        parts.append(f"{name}{'' if name in required else '?'}: {ptype}")
    return f"tools.{js_name(_schema_name(schema))}({{{', '.join(parts)}}})"


def _words(text: str) -> set[str]:
    return {w for w in re.split(r'[^a-z0-9]+', text.casefold()) if len(w) > 1}


def _search(schemas: list[dict[str, Any]], query: str, names: list[str], limit: int) -> list[dict[str, Any]]:
    by_key = {_key(_schema_name(s)): s for s in schemas}
    by_key.update({_key(js_name(_schema_name(s))): s for s in schemas})
    picked: list[dict[str, Any]] = []
    for name in names:
        hit = by_key.get(_key(name)) or by_key.get(_key(js_name(name)))
        if hit is not None and hit not in picked:
            picked.append(hit)
    words = _words(query)
    if words:
        scored = []
        for s in schemas:
            name_words = _words(_schema_name(s).replace('_', ' '))
            desc_words = _words(_schema_description(s)[:400])
            score = 3 * len(words & name_words) + len(words & desc_words)
            if score:
                scored.append((score, _schema_name(s), s))
        scored.sort(key=lambda row: (-row[0], row[1]))
        for _score, _name, s in scored:
            if s not in picked:
                picked.append(s)
            if len(picked) >= limit:
                break
    return picked[:max(limit, len(names))]


def _agent_schemas(agent: Any) -> list[dict[str, Any]]:
    from cognitrix.safety.permissions import visible_tools

    out = []
    for t in visible_tools(agent, list(getattr(agent, 'tools', None) or [])):
        try:
            out.append(t.to_dict_format())
        except Exception:
            logger.debug('Could not describe tool %s', getattr(t, 'name', '?'), exc_info=True)
    return out


def find_tools_note(agent: Any) -> str:
    """A system-prompt line naming the tools that aren't loaded yet."""
    others = [
        js_name(_schema_name(s)) for s in _agent_schemas(agent)
        if _key(_schema_name(s)) not in CORE_TOOL_NAMES
    ]
    if not others:
        return ''
    return (
        "Tools load on demand: only a few tools are described to you up front. Your other tools are: "
        + ', '.join(sorted(others))
        + ". Call find_tools with keywords or exact names to load the ones you need before using them"
        + (" (or call them from run_code)." if tool_mode(agent)['code'] else ".")
    )


@tool(category=CODE_MODE_CATEGORY, retryable=False, max_attempts=1)
async def find_tools(query: str = '', names: list[str] | None = None, *, parent=None) -> ToolOutcome:
    """Find and load more of your tools. Only a few of your tools are described to you up front; this
    searches the rest by keywords (query) or exact names (names), shows how to call each match, and loads
    them so you can call them directly from your next step on.

    Args:
        query (str): Keywords for what you want to do, like "read web page" or "github issues".
        names (list[str]): Exact tool names to load, like ["web_fetch", "grep"].
    """
    if parent is None:
        return ToolOutcome.failure('no_agent', 'Find Tools needs an agent to look in.')
    if isinstance(names, str):
        names = [n.strip() for n in names.split(',') if n.strip()]
    names = [str(n) for n in (names or []) if str(n).strip()]
    if not query.strip() and not names:
        return ToolOutcome.failure('missing_query', 'Give keywords (query) or exact tool names (names).')
    schemas = [s for s in _agent_schemas(parent) if _key(_schema_name(s)) not in CORE_TOOL_NAMES]
    matches = _search(schemas, query, names, limit=8)
    if not matches:
        return ToolOutcome.success(
            'No matching tools. Your tools are: '
            + ', '.join(sorted(js_name(_schema_name(s)) for s in schemas)))
    loaded = loaded_this_turn.get()
    if loaded is not None:
        loaded.update(_schema_name(s) for s in matches)
    lines = ['Loaded these tools; call them directly from your next step (or from run_code):']
    for s in matches:
        lines.append(f"- {signature(s)}: {_schema_description(s)[:300]}")
    return ToolOutcome.success('\n'.join(lines))


_PRELUDE = r"""
(() => {
  const fmt = (v) => {
    if (typeof v === 'string') return v;
    try { return JSON.stringify(v); } catch (e) { return String(v); }
  };
  const logs = [];
  globalThis.__logs = logs;
  const log = (...a) => { logs.push(a.map(fmt).join(' ')); };
  globalThis.console = { log, info: log, warn: log, error: log, debug: log };
  globalThis.print = log;
  const call = globalThis.__call;
  delete globalThis.__call;
  globalThis.tools = new Proxy({}, {
    get(_t, name) {
      if (typeof name !== 'string' || name === 'then') return undefined;
      return async (args) => {
        const raw = await call(name, JSON.stringify(args === undefined ? {} : args));
        const r = JSON.parse(raw);
        if (!r.ok) { const e = new Error(r.error); e.tool = name; throw e; }
        return r.value;
      };
    },
  });
})();
"""


def _wrap(code: str) -> str:
    return (
        "(async () => {\n"
        "  const __result = await (async () => {\n" + code + "\n  })();\n"
        "  let out;\n"
        "  try { out = __result === undefined ? null : JSON.parse(JSON.stringify(__result)); }\n"
        "  catch (e) { out = String(__result); }\n"
        "  return JSON.stringify({result: out, logs: globalThis.__logs});\n"
        "})()"
    )


def _parse_value(text: str) -> Any:
    stripped = text.strip()
    if stripped[:1] in ('{', '[') or stripped in ('true', 'false', 'null'):
        try:
            return json.loads(stripped)
        except ValueError:
            pass
    return text


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[cut: {len(text) - limit} more characters. Return less from the script.]"


def _js_error(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    # mini-racer prefixes the location; keep the message short and useful.
    return text.splitlines()[0][:2000] if len(text) > 2000 else text


@tool(category=CODE_MODE_CATEGORY, retryable=False, max_attempts=1, occupies_execution_slot=False)
async def run_code(code: str, *, parent=None, interface=None) -> ToolOutcome:
    """Run JavaScript that calls your tools, to do many tool calls in one step. Call any of your tools as
    `await tools.<tool_name>({...})` with the same arguments you would pass directly; it returns the
    tool's result (parsed when it is JSON, else text) and throws if the tool fails or is refused. Use loops,
    `Promise.all` for parallel calls, and plain JavaScript to filter results, then `return` only what you
    need (or `console.log` it). The code runs as the body of an async function, without files, network
    or modules: everything goes through `tools`. Approvals and permissions apply to each call.

    Args:
        code (str): The JavaScript function body, e.g. `const r = await tools.grep({pattern: "TODO"}); return r;`
    """
    if parent is None:
        return ToolOutcome.failure('no_agent', 'Run Code needs an agent to run tools for.')
    if not code or not code.strip():
        return ToolOutcome.failure('missing_code', 'Give the JavaScript to run in code.')
    try:
        from py_mini_racer import MiniRacer
    except Exception:
        return ToolOutcome.failure(
            'code_mode_unavailable',
            'Run Code is not available on this install (the mini-racer package is missing). Call tools directly.')

    calls: list[dict[str, Any]] = []
    artifacts: list[ArtifactRef] = []
    seen_artifacts: set[str] = set()
    turn_interface = interface or 'cli'

    # Script names are snake_case aliases (js_name); MCP tool names can keep
    # hyphens, so map each alias back to the assigned tool's real name.
    aliases = {js_name(t.name): t.name for t in (getattr(parent, 'tools', None) or [])
               if getattr(t, 'name', None)}

    # mini-racer runs the Python callback outside this task's context, so
    # without this every tool the script calls would lose the caller's
    # authority (signed-in user, API key scopes, session, folders) and fall
    # back to an anonymous default. Each call gets its own copy, as a direct
    # tool call in the chat loop would.
    caller_context = contextvars.copy_context()

    async def call(name: Any, args_json: Any) -> str:
        return await asyncio.get_running_loop().create_task(
            _call(name, args_json), context=caller_context.copy())

    async def _call(name: Any, args_json: Any) -> str:
        name = aliases.get(js_name(str(name)), str(name))
        if len(calls) >= MAX_CALLS_PER_SCRIPT:
            return json.dumps({'ok': False, 'error': f'Too many tool calls in one script (limit {MAX_CALLS_PER_SCRIPT}).'})
        try:
            args = json.loads(str(args_json)) if args_json else {}
        except ValueError:
            return json.dumps({'ok': False, 'error': 'Tool arguments must be plain JSON values.'})
        if not isinstance(args, dict):
            return json.dumps({'ok': False, 'error': f'Pass {name} an object of arguments.'})
        if _key(name) in _NOT_FROM_CODE:
            return json.dumps({'ok': False, 'error': f'{name} cannot be called from Run Code.'})
        entry: dict[str, Any] = {'tool': name, 'ok': False}
        calls.append(entry)
        try:
            result = await parent.call_tools(
                [{'name': name, 'arguments': args, 'tool_call_id': f'code-{uuid.uuid4().hex[:12]}'}],
                interface=turn_interface,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug('Run Code tool call failed', exc_info=True)
            entry['error'] = str(exc)
            return json.dumps({'ok': False, 'error': f'{name} failed: {exc}'})
        item = (result.get('result') or [{}])[0] if isinstance(result, dict) else {}
        outcome = item.get('outcome') or {}
        data = str(item.get('data') or '')
        for ref in outcome.get('artifacts') or []:
            try:
                art = ArtifactRef.model_validate(ref)
            except Exception:
                continue
            if art.id not in seen_artifacts:
                seen_artifacts.add(art.id)
                artifacts.append(art)
        if item.get('success'):
            entry['ok'] = True
            return json.dumps({'ok': True, 'value': _parse_value(str(outcome.get('text', data)))})
        message = str(((outcome.get('error') or {}).get('message')) or data or f'{name} failed')
        entry['error'] = message[:300]
        return json.dumps({'ok': False, 'error': message})

    ctx = MiniRacer()
    try:
        ctx.set_hard_memory_limit(CODE_MEMORY_BYTES)
        async with ctx.wrap_py_function(call) as js_call:
            ctx.eval('this')['__call'] = js_call
            ctx.eval(_PRELUDE)
            try:
                promise = await asyncio.wait_for(ctx.eval_cancelable(_wrap(code)), CODE_TIMEOUT_SECONDS)
                raw = await asyncio.wait_for(promise, CODE_TIMEOUT_SECONDS) if hasattr(promise, '__await__') else promise
            except TimeoutError:
                return _finish(calls, artifacts, None, [], error=f'The script ran longer than {int(CODE_TIMEOUT_SECONDS)} seconds and was stopped.')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logs = _logs(ctx)
                if ctx.was_hard_memory_limit_reached():
                    return _finish(calls, artifacts, None, logs, error='The script used too much memory and was stopped.')
                return _finish(calls, artifacts, None, logs, error=_js_error(exc))
    finally:
        try:
            ctx.close()
        except Exception:
            pass
    try:
        payload = json.loads(str(raw))
    except ValueError:
        payload = {'result': str(raw), 'logs': []}
    return _finish(calls, artifacts, payload.get('result'), list(payload.get('logs') or []))


def _logs(ctx: Any) -> list[str]:
    try:
        raw = ctx.eval('JSON.stringify(globalThis.__logs || [])')
        return list(json.loads(str(raw)))
    except Exception:
        return []


def _finish(calls: list[dict[str, Any]], artifacts: list[ArtifactRef], result: Any,
            logs: list[str], *, error: str | None = None) -> ToolOutcome:
    parts = []
    if calls:
        ok = sum(1 for c in calls if c.get('ok'))
        failed = [c for c in calls if not c.get('ok')]
        line = f"Ran {len(calls)} tool call{'s' if len(calls) != 1 else ''} ({ok} succeeded"
        line += f", {len(failed)} failed)" if failed else ")"
        parts.append(line)
    if logs:
        parts.append('Output:\n' + '\n'.join(str(x) for x in logs))
    if result is not None:
        shown = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1)
        parts.append('Result:\n' + shown)
    if error:
        parts.append('Error: ' + error)
        text = _clip('\n\n'.join(parts))
        outcome = ToolOutcome.failure('script_error', text)
        outcome.artifacts = artifacts
        return outcome
    if not parts or (len(parts) == 1 and calls):
        parts.append('The script returned nothing. Return or console.log what you need.')
    return ToolOutcome.success(_clip('\n\n'.join(parts)), artifacts=artifacts)
