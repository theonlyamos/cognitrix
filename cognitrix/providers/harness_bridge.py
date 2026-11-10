"""A small MCP server that lends a coding agent CLI this agent's Cognitrix tools.

While a CLI turn runs (providers/harness.py), the CLI connects here over
loopback HTTP. Its tool calls come back into Cognitrix and run through the same
permission and approval checks as any other agent's. One server per turn: it
listens on a random 127.0.0.1 port, the address carries a one-time token, and
it is gone when the turn ends.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hmac
import json
import logging
import re
import secrets
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger('cognitrix.log')

SERVER_NAME = 'cognitrix'
APPROVE_TOOL = 'approve'
"""Claude Code sends its permission prompts to this tool (--permission-prompt-tool)."""

# The CLI has its own, better-fitting versions of these, working in its folder.
OWN_TOOLS = frozenset({
    'edit', 'glob', 'grep', 'read', 'write', 'bash', 'bash_output', 'kill_shell',
    'open_file', 'coding_agent', 'request_folder',
})

ToolCaller = Callable[[str, dict[str, Any]], Awaitable[tuple]]
"""(tool name, arguments) -> (text for the model, worked?[, images as data: URIs])."""
Approver = Callable[[str, dict[str, Any]], Awaitable[tuple[bool, str]]]
"""(the CLI's tool name, its input) -> (allowed?, why not)."""


def mcp_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(name or '').strip())[:64]


def lendable(tool_dicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tools in the model-call format, as MCP tool definitions, minus OWN_TOOLS."""
    out, seen = [], set()
    for item in tool_dicts or []:
        fn = item.get('function') if isinstance(item, dict) else None
        if not isinstance(fn, dict) or not fn.get('name'):
            continue
        name = mcp_name(fn['name'])
        if name.lower() in OWN_TOOLS or name == APPROVE_TOOL or name in seen:
            continue
        seen.add(name)
        schema = fn.get('parameters')
        if not isinstance(schema, dict) or schema.get('type') != 'object':
            schema = {'type': 'object', 'properties': {}}
        out.append({'name': name, 'source_name': fn['name'],
                    'description': str(fn.get('description') or '')[:4000], 'input_schema': schema})
    return out


@dataclass
class Bridge:
    """The running server for one turn."""
    tools: list[dict[str, Any]]
    call_tool: ToolCaller
    approve: Approver | None = None
    context: contextvars.Context | None = None
    """The chat turn's context, so tool calls see its user, folders and approval channel."""
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    port: int = 0
    _server: Any = None
    _task: asyncio.Task | None = None
    _stack: contextlib.AsyncExitStack | None = None
    _running: set[asyncio.Task] = field(default_factory=set)

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.port}/mcp/{self.token}'

    def tool_names(self) -> list[str]:
        return [t['name'] for t in self.tools]

    async def _in_turn(self, coro) -> Any:
        """Run in the turn's context; stop with the turn."""
        task = asyncio.get_running_loop().create_task(coro, context=self.context)
        self._running.add(task)
        try:
            return await task
        finally:
            self._running.discard(task)

    async def start(self) -> Bridge:
        import uvicorn
        from mcp import types
        from mcp.server.lowlevel import Server
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings

        bridge = self

        async def list_tools(_ctx, _params) -> types.ListToolsResult:
            tools = [types.Tool(name=t['name'], description=t['description'], input_schema=t['input_schema'])
                     for t in bridge.tools]
            if bridge.approve is not None:
                tools.append(types.Tool(
                    name=APPROVE_TOOL,
                    description='Used by the app to ask the person before a risky action. Do not call it yourself.',
                    input_schema={'type': 'object', 'properties': {
                        'tool_name': {'type': 'string'}, 'input': {'type': 'object'},
                        'tool_use_id': {'type': 'string'}}, 'required': ['tool_name', 'input']},
                ))
            return types.ListToolsResult(tools=tools)

        async def call_tool(_ctx, params) -> types.CallToolResult:
            name, args = params.name, dict(params.arguments or {})
            if name == APPROVE_TOOL and bridge.approve is not None:
                asked_input = args.get('input') if isinstance(args.get('input'), dict) else {}
                try:
                    allowed, why = await bridge._in_turn(bridge.approve(str(args.get('tool_name') or ''), asked_input))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a failed ask is a no
                    logger.exception('Could not ask about a coding agent action: %s', exc)
                    allowed, why = False, 'Cognitrix could not ask the person.'
                answer = ({'behavior': 'allow', 'updatedInput': asked_input} if allowed
                          else {'behavior': 'deny', 'message': why or 'The person said no.'})
                return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(answer))])
            known = next((t for t in bridge.tools if t['name'] == name), None)
            if known is None:
                return types.CallToolResult(
                    content=[types.TextContent(type='text', text=f"There is no tool named '{name}'.")], is_error=True)
            images: list[str] = []
            try:
                text, ok, *rest = await bridge._in_turn(bridge.call_tool(known['source_name'], args))
                images = list(rest[0]) if rest and rest[0] else []
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reported to the model
                logger.exception('Tool %s failed for a coding agent: %s', name, exc)
                text, ok = f'{name} failed: {exc}', False
            content: list[Any] = [types.TextContent(type='text', text=text or '(no output)')]
            for uri in images:
                # Screenshots and other pictures a tool wants the model to see.
                match = re.match(r'^data:(image/[\w.+-]+);base64,(.+)$', str(uri), re.DOTALL)
                if match:
                    content.append(types.ImageContent(type='image', mime_type=match.group(1), data=match.group(2)))
            return types.CallToolResult(content=content, is_error=not ok)

        server = Server(SERVER_NAME, version='1', on_list_tools=list_tools, on_call_tool=call_tool)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', 0))
        self.port = sock.getsockname()[1]
        hosts = [f'127.0.0.1:{self.port}', f'localhost:{self.port}']
        manager = StreamableHTTPSessionManager(
            server, json_response=True, stateless=True,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                allowed_origins=[f'http://{h}' for h in hosts]),
        )
        expected = f'/mcp/{self.token}'

        async def app(scope, receive, send) -> None:
            if scope['type'] != 'http':
                return
            path = scope.get('path') or ''
            if not hmac.compare_digest(path.rstrip('/').encode(), expected.encode()):
                await send({'type': 'http.response.start', 'status': 404, 'headers': []})
                await send({'type': 'http.response.body', 'body': b''})
                return
            await manager.handle_request(scope, receive, send)

        class _Server(uvicorn.Server):
            # The engine owns Ctrl+C and friends; this helper must not take them.
            @contextlib.contextmanager
            def capture_signals(self):
                yield

        self._stack = contextlib.AsyncExitStack()
        try:
            await self._stack.enter_async_context(manager.run())
            self._server = _Server(uvicorn.Config(app, log_level='critical', access_log=False, lifespan='off'))
            self._task = asyncio.get_running_loop().create_task(self._server.serve(sockets=[sock]))
            for _ in range(500):
                if self._server.started or self._task.done():
                    break
                await asyncio.sleep(0.01)
            if not self._server.started:
                raise RuntimeError('the tools server did not start')
        except BaseException:
            sock.close()
            await self.stop()
            raise
        return self

    async def stop(self) -> None:
        for task in list(self._running):
            task.cancel()
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                self._task.cancel()
            self._task = None
        if self._stack is not None:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
            self._stack = None
