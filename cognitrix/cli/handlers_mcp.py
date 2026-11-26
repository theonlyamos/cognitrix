"""`cognitrix mcp`: MCP servers from the command line.

Servers added here go in ``~/.cognitrix/mcp.json``: shared servers that any
agent on this install can be given (cognitrix/mcp/registry.py). Servers people
added in the app (Settings, Agent access / Connected apps) are listed too.

    cognitrix mcp list [--json]
    cognitrix mcp add fs -- npx -y @modelcontextprotocol/server-filesystem ~/work
    cognitrix mcp add docs --url https://example.com/mcp --header "Authorization=Bearer ..."
    cognitrix mcp add fs --agent Coder -- npx ...     # and give it to an agent
    cognitrix mcp show fs | test fs | tools fs
    cognitrix mcp enable fs | disable fs | remove fs
    cognitrix mcp attach fs --agent Coder | detach fs --agent Coder
    cognitrix mcp import .mcp.json                    # Claude Code / Cursor / Cognitrix files

For one run only (nothing saved): ``cognitrix --mcp-config servers.json -p ...``.
"""
from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

from .utils import print_json, print_table


def _pairs(items: list[str] | None, what: str) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for item in items or []:
        sep = '=' if '=' in item else (':' if ':' in item else '')
        if not sep:
            raise ValueError(f'Write each {what} as NAME=VALUE (got "{item}").')
        key, value = item.split(sep, 1)
        out[key.strip()] = value.strip()
    return out or None


def config_from_entry(name: str, entry: dict[str, Any]) -> Any:
    """A server from an ``mcpServers`` entry (Claude Code, Cursor, VS Code)
    or a Cognitrix ``servers`` entry."""
    from cognitrix.mcp.server_manager import MCPServerConfig, MCPTransportType

    if not isinstance(entry, dict):
        raise ValueError(f'Server {name} is not an object.')
    kind = str(entry.get('transport') or entry.get('type') or '').lower()
    if kind in ('streamable-http', 'streamable_http', 'http'):
        transport = MCPTransportType.HTTP
    elif kind == 'sse':
        transport = MCPTransportType.SSE
    elif kind == 'stdio' or entry.get('command'):
        transport = MCPTransportType.STDIO
    else:
        transport = MCPTransportType.HTTP
    args = entry.get('args') or []
    config = MCPServerConfig(
        name=str(entry.get('name') or name),
        transport=transport,
        description=str(entry.get('description') or ''),
        url=entry.get('url') or None,
        command=entry.get('command') or None,
        args=[str(a) for a in args] if isinstance(args, list) else None,
        env={str(k): str(v) for k, v in (entry.get('env') or {}).items()} or None,
        working_directory=entry.get('cwd') or entry.get('working_directory') or None,
        headers={str(k): str(v) for k, v in (entry.get('headers') or {}).items()} or None,
        timeout=int(entry.get('timeout') or 30),
        enabled=bool(entry.get('enabled', not entry.get('disabled', False))),
    )
    errors = config.validate()
    if errors:
        raise ValueError(f'{name}: {"; ".join(errors)}')
    return config


def read_config_file(path: str) -> list[Any]:
    """Servers in a JSON file: ``{"mcpServers": {name: {...}}}`` (Claude
    Code, Cursor), ``{"servers": {...}}`` (VS Code) or ``{"servers": [...]}``
    (Cognitrix's own mcp.json). A JSON string instead of a path works too."""
    text = path
    if not path.lstrip().startswith('{'):
        try:
            text = Path(path).expanduser().read_text(encoding='utf-8')
        except OSError as exc:
            raise ValueError(f"Couldn't read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f'{path} is not valid JSON: {exc}') from exc
    servers = data.get('mcpServers') if isinstance(data, dict) else None
    if servers is None and isinstance(data, dict):
        servers = data.get('servers')
    if isinstance(servers, list):
        return [config_from_entry(str(s.get('name') or ''), s) for s in servers if isinstance(s, dict)]
    if isinstance(servers, dict):
        return [config_from_entry(name, entry) for name, entry in servers.items()]
    raise ValueError(f'{path} has no "mcpServers" or "servers".')


async def use_for_run(agent: Any, path: str) -> list[str]:
    """``--mcp-config``: give ``agent`` these servers for this run only. They
    join the shared servers in memory (not saved to mcp.json) and the agent's
    server list (the agent isn't saved afterwards)."""
    from cognitrix.mcp.server_manager import mcp_server_manager
    from cognitrix.tools.base import ToolManager

    names = []
    for config in read_config_file(path):
        mcp_server_manager.servers[config.name] = config
        names.append(config.name)
    agent.mcp_servers = [*dict.fromkeys([*(agent.mcp_servers or []), *names])]
    # The agent reaches its servers through these two tools.
    have = {str(getattr(t, 'name', '')).lower() for t in agent.tools or []}
    for tool_name in ('list_mcp_tools', 'run_mcp_tool'):
        tool = ToolManager.get_by_name(tool_name)
        if tool is not None and tool.name.lower() not in have:
            agent.tools.append(tool)
    return names


def _row(config: Any, source: str) -> dict[str, Any]:
    target = config.url or ' '.join([config.command or '', *(config.args or [])]).strip()
    return {
        'name': config.name,
        'transport': getattr(config.transport, 'value', config.transport),
        'target': target,
        'enabled': bool(config.enabled),
        'source': source,
        'id': config.id,
        'owner_id': config.owner_id,
        'description': config.description or '',
    }


async def _all_rows() -> list[dict[str, Any]]:
    from cognitrix.mcp import registry
    from cognitrix.mcp.server_manager import mcp_server_manager

    rows = [_row(c, 'mcp.json') for c in mcp_server_manager.list_servers()]
    for config in await registry._database_servers():
        rows.append(_row(config, 'app (shared)' if not config.owner_id else 'app (personal)'))
    return rows


def _redacted(config: Any) -> dict[str, Any]:
    data = config.to_dict()
    data['transport'] = getattr(config.transport, 'value', config.transport)
    for key in ('env', 'headers'):
        if data.get(key):
            data[key] = {k: '(set)' for k in data[key]}
    return data


async def _agent(name: str) -> Any:
    from .handlers import find_agent

    agent = await find_agent(name)
    if agent is None:
        raise ValueError(f'No agent called {name}.')
    return agent


async def _attach(server: str, agent_name: str, attach: bool = True) -> None:
    agent = await _agent(agent_name)
    refs = [str(r) for r in agent.mcp_servers or []]
    if attach and server not in refs:
        refs.append(server)
    elif not attach:
        refs = [r for r in refs if r != server]
    agent.mcp_servers = refs
    await agent.save()
    print(f"{'Gave' if attach else 'Took'} {server} {'to' if attach else 'from'} {agent.name}.")


async def _test(config: Any, show_tools: bool, as_json: bool) -> bool:
    from cognitrix.mcp.client import DynamicMCPClient

    client = DynamicMCPClient()
    try:
        if not await client.connect_to_server(config):
            print(f"Couldn't connect to {config.name}.", file=sys.stderr)
            return False
        tools = await client.list_tools(config.name) or []
        if as_json:
            print_json({'name': config.name, 'connected': True,
                        'tools': [{'name': t.get('name'), 'description': t.get('description')} for t in tools]})
        elif show_tools:
            print_table([[t.get('name'), ' '.join(str(t.get('description') or '').split())[:80]] for t in tools],
                        ['Tool', 'Description'])
        else:
            print(f"{config.name}: connected, {len(tools)} tools.")
        return True
    except Exception as exc:
        print(f"Couldn't connect to {config.name}: {exc}", file=sys.stderr)
        return False
    finally:
        try:
            await client.disconnect_all()
        except Exception:
            pass


async def manage_mcp(args: Namespace) -> None:
    from cognitrix.mcp.server_manager import MCPServerConfig, MCPTransportType, mcp_server_manager

    from .core import run_configuration

    await run_configuration()
    action = getattr(args, 'mcp_action', None) or 'list'
    as_json = getattr(args, 'json', False)
    try:
        if action == 'list':
            rows = await _all_rows()
            if as_json:
                print_json(rows)
            elif not rows:
                print('No MCP servers yet. Add one: cognitrix mcp add NAME -- COMMAND ARGS  or  --url URL')
            else:
                print_table([[r['name'], r['transport'], 'on' if r['enabled'] else 'off', r['source'],
                              r['target'][:60]] for r in rows], ['Name', 'Transport', 'Enabled', 'Where', 'Command / URL'])
            return

        if action == 'add':
            command = split_add_args(args)
            if args.url and command:
                raise ValueError('Give either --url or a command after --, not both.')
            if not args.url and not command:
                raise ValueError('Give a command after -- (stdio server) or --url (HTTP/SSE server).')
            transport = (MCPTransportType.STDIO if command else
                         MCPTransportType.SSE if args.transport == 'sse' else MCPTransportType.HTTP)
            config = MCPServerConfig(
                name=args.name, transport=transport, description=args.description or '',
                url=args.url or None, command=command[0] if command else None,
                args=command[1:] if command else None, env=_pairs(args.env, 'variable'),
                headers=_pairs(args.header, 'header'), timeout=args.timeout,
                working_directory=args.cwd or None,
            )
            errors = config.validate()
            if errors:
                raise ValueError('; '.join(errors))
            existed = mcp_server_manager.get_server(args.name) is not None
            mcp_server_manager.add_server(config)
            print(f"{'Updated' if existed else 'Added'} MCP server {args.name} ({transport.value}).")
            if args.agent:
                await _attach(args.name, args.agent)
            return

        if action == 'import':
            configs = read_config_file(args.file)
            for config in configs:
                mcp_server_manager.add_server(config)
                print(f'Added MCP server {config.name} ({config.transport.value}).')
                if args.agent:
                    await _attach(config.name, args.agent)
            return

        if action in ('attach', 'detach'):
            await _attach(args.name, args.agent, attach=action == 'attach')
            return

        # The rest name a server: an mcp.json one, or one of the app's.
        config = mcp_server_manager.get_server(args.name)
        if config is None and action in ('show', 'test', 'tools'):
            from cognitrix.mcp import registry

            config = next((c for c in await registry._database_servers()
                           if c.name == args.name or c.id == args.name), None)
            if config is not None:
                config = await registry._with_saved_secrets(config)
        if config is None:
            raise LookupError(f"No MCP server called {args.name}. `cognitrix mcp list` shows them.")

        if action == 'show':
            data = _redacted(config)
            if as_json:
                print_json(data)
            else:
                for key, value in data.items():
                    if value not in (None, '', [], {}):
                        print(f'{key}: {value}')
        elif action in ('test', 'tools'):
            if not await _test(config, action == 'tools', as_json):
                sys.exit(1)
        elif action == 'remove':
            mcp_server_manager.remove_server(args.name)
            print(f'Removed MCP server {args.name}.')
        elif action in ('enable', 'disable'):
            (mcp_server_manager.enable_server if action == 'enable' else mcp_server_manager.disable_server)(args.name)
            print(f"{'Enabled' if action == 'enable' else 'Disabled'} MCP server {args.name}.")
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)


def _add_options(parser) -> None:
    parser.add_argument('--url', default='', help='Address of an HTTP or SSE server')
    parser.add_argument('--transport', choices=('http', 'sse'), default='http', help='For --url (default http)')
    parser.add_argument('--header', '-H', action='append', default=[], metavar='NAME=VALUE',
                        help='HTTP header (repeat)')
    parser.add_argument('--env', '-e', action='append', default=[], metavar='NAME=VALUE',
                        help='Environment variable for a command server (repeat)')
    parser.add_argument('--cwd', default='', help='Folder a command server starts in')
    parser.add_argument('--description', default='')
    parser.add_argument('--timeout', type=int, default=30, help='Seconds to wait for the server (default 30)')
    parser.add_argument('--agent', default='', help='Also give the server to this agent')


def split_add_args(args: Namespace) -> list[str]:
    """`mcp add NAME [options] -- COMMAND ARGS`: argparse hands everything
    after NAME over as one list; options before `--` are read here, the rest is
    the server's command line (its own flags stay its own)."""
    import argparse

    tokens = list(args.command_line or [])
    if '--' in tokens:
        cut = tokens.index('--')
        options, command = tokens[:cut], tokens[cut + 1:]
    else:
        options, command = tokens, []
    if options:
        parser = argparse.ArgumentParser(prog='cognitrix mcp add', add_help=False)
        _add_options(parser)
        defaults = {a.dest: a.default for a in parser._actions}
        parsed, rest = parser.parse_known_args(options)
        if rest and not command:
            command = rest  # `mcp add NAME npx ...` without the --
        elif rest:
            raise ValueError(f'Unknown options before --: {" ".join(rest)}')
        for key, value in vars(parsed).items():
            if value != defaults.get(key):
                current = getattr(args, key, None)
                setattr(args, key, (current or []) + value if isinstance(value, list) else value)
    return command


def add_parser(subparsers) -> None:
    import argparse

    parser = subparsers.add_parser(
        'mcp', help='List, add, test or remove MCP servers, and give them to agents',
        description='Servers added here are saved in ~/.cognitrix/mcp.json and can be given to any agent.')
    parser.add_argument('--json', action='store_true', help='Print JSON')
    inner = parser.add_subparsers(dest='mcp_action')
    json_flag = {'action': 'store_true', 'default': argparse.SUPPRESS, 'help': 'Print JSON'}

    listing = inner.add_parser('list', help='List MCP servers (mcp.json and the app\'s)')
    listing.add_argument('--json', **json_flag)

    add = inner.add_parser('add', help='Add or update a server: NAME -- COMMAND ARGS, or NAME --url URL',
                           usage='cognitrix mcp add NAME [--url URL | -- COMMAND [ARGS ...]] [options]')
    add.add_argument('name')
    _add_options(add)
    add.add_argument('command_line', nargs=argparse.REMAINDER, help='-- then the command and its arguments')

    importing = inner.add_parser('import', help='Add every server in a .mcp.json / Cursor / VS Code / Cognitrix file')
    importing.add_argument('file')
    importing.add_argument('--agent', default='', help='Also give the servers to this agent')

    for name, text in (('show', 'Show a server (secrets hidden)'), ('test', 'Connect to a server and count its tools'),
                       ('tools', 'List the tools a server offers'), ('remove', 'Remove a server from mcp.json'),
                       ('enable', 'Turn a server on'), ('disable', 'Turn a server off')):
        sub = inner.add_parser(name, help=text)
        sub.add_argument('name')
        if name in ('show', 'test', 'tools'):
            sub.add_argument('--json', **json_flag)
    for name, text in (('attach', 'Give a server to an agent'), ('detach', 'Take a server from an agent')):
        sub = inner.add_parser(name, help=text)
        sub.add_argument('name', help='Server name (or an app server\'s id)')
        sub.add_argument('--agent', required=True)
    parser.set_defaults(func=manage_mcp)
