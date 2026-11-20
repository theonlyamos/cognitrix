"""A chat turn on a paired PC: which PC, what the agent is told, and sending
the shell and file tools there.

A chat picks a PC with the computer chip (``Session.computer``). For that
turn the execution context carries the device id; ``run_remote`` sends the
PC tools (``PC_TOOLS``) to it instead of running them on the server.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from cognitrix.computer.paired import (
    CALL_TIMEOUT_S,
    COMMANDS,
    DEFAULT_CALL_TIMEOUT_S,
    FILES,
    PC_TOOL_KEYS,
    tool_power,
)

logger = logging.getLogger('cognitrix.paired')

READ_ONLY_PC_TOOLS = frozenset({'Read', 'Glob', 'Grep'})

# device id -> name, so an offline PC can still be named in an error.
_names: dict[str, str] = {}


def _platform_label(platform: str) -> str:
    p = str(platform or '').lower()
    for key, label in (('win', 'Windows'), ('darwin', 'macOS'), ('mac', 'macOS'), ('linux', 'Linux')):
        if key in p:
            return label
    return ''


async def chat_device(session: Any, user_id: Any) -> dict[str, Any] | None:
    """The PC this chat works on (the store's view of it), or None. Only the
    chat owner's own PCs count; a PC that was unpaired is forgotten."""
    return await device_view(user_id, getattr(session, 'computer', None))


async def device_view(user_id: Any, device_id: Any, *, read_only: bool = False) -> dict[str, Any] | None:
    """The person's own PC by id (the store's view of it), or None. With
    read_only (a helper agent's turn) it only offers looking at files."""
    if not device_id or not user_id:
        return None
    from cognitrix.computer.paired.store import device_of, view
    try:
        device = await device_of(user_id, device_id)
    except Exception:
        logger.debug('Could not look up the paired PC', exc_info=True)
        return None
    if device is None:
        return None
    out = view(device)
    _names[out['id']] = out['name']
    if read_only:
        out['read_only'] = True
    return out


def tools_for(device: dict[str, Any]) -> tuple[str, ...]:
    """Registered names of the PC tools this PC allows (added to the turn)."""
    powers = set(device.get('powers') or [])
    names = tuple(k for k, p in PC_TOOL_KEYS.items() if p in powers)
    if device.get('read_only'):
        allowed = {n.lower().replace(' ', '_') for n in READ_ONLY_PC_TOOLS}
        names = tuple(n for n in names if n in allowed)
    return names


def device_prompt(device: dict[str, Any]) -> str:
    name = device.get('name') or 'the PC'
    kind = _platform_label(device.get('platform') or '')
    powers = set(device.get('powers') or [])
    lines = [
        f'## Your computer for code: {name}',
        f"This chat works on {name}, the person's own {kind + ' ' if kind else ''}computer, paired with "
        "Cognitrix. Bash, Bash Output, Kill Shell, Read, Write, Edit, Glob and Grep run there, on the person's "
        'real files, not on the server and not in a sandbox. Paths are paths on that computer.',
    ]
    folders = device.get('folders') or []
    if folders:
        lines.append('Folders you can use there (nothing outside them):')
        for f in folders:
            lines.append(f"- {f.get('path')} ({'read & write' if f.get('writable', True) else 'read only'})")
        lines.append('Relative paths and commands start in the first one.')
    else:
        lines.append('The person has not shared any folders on it yet, so file tools and commands will be refused. '
                     'Ask them to add a folder on that PC.')
    if COMMANDS in powers:
        lines.append('Each command asks the person first. Prefer one clear command over many small ones.')
    else:
        lines.append('Commands are switched off on that PC: Bash will be refused.')
    if FILES not in powers:
        lines.append('Files are switched off on that PC: Read, Write, Edit, Glob and Grep will be refused.')
    if FILES in powers:
        lines.append('Writes and edits change the real files right away, so be careful and tell the person what '
                     'you changed.')
    if device.get('read_only'):
        lines.append('You are helping another agent: on this PC you can only look (Read, Glob, Grep). '
                     'Writing, editing and commands are refused.')
    if not device.get('online'):
        lines.append(f'{name} is offline right now. If the person asks for work there, tell them to start the '
                     'Cognitrix desktop app (or `cognitrix connect`) on it. Do not move to another computer on your own.')
    elif device.get('paused'):
        lines.append(f'Agents are paused on {name} right now. Tell the person if you need it.')
    return '\n'.join(lines)


def _jsonable(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in (args or {}).items():
        if key in ('parent', 'interface'):
            continue
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        out[key] = value
    return out


def _unavailable(code: str, name: str) -> str:
    if code == 'offline':
        return (f"Error: {name} is offline, so this didn't run. Tell the person that the Cognitrix desktop app "
                "(or `cognitrix connect`) has to be running on that PC. Don't switch to another computer on your own.")
    if code == 'paused':
        return f"Error: The person paused agents on {name}, so this didn't run. Ask them to resume it."
    if code == f'no_{COMMANDS}':
        return f"Error: The person hasn't allowed commands on {name}. Use the file tools, or ask them to allow commands there."
    if code == f'no_{FILES}':
        return f"Error: The person hasn't allowed file access on {name}. Ask them to allow it there."
    return f'Error: {code}'


async def run_remote(tool: Any, params: dict[str, Any]) -> str | None:
    """Run ``tool`` on this turn's paired PC. None when the turn isn't on a PC
    or the tool doesn't run there (it then runs as usual)."""
    from cognitrix.tools.utils import current_execution_context

    ctx = current_execution_context()
    device_id = getattr(ctx, 'device', None)
    name = str(getattr(tool, 'name', '') or '')
    power = tool_power(name)
    if not device_id or power is None:
        return None
    if getattr(ctx, 'device_read_only', False) and name not in READ_ONLY_PC_TOOLS:
        return (f"Error: {name} isn't allowed here. A helper agent can only look at files on the person's PC "
                '(Read, Glob, Grep), not change them or run commands.')
    from cognitrix.computer.paired.hub import DeviceUnavailable, hub

    label = (hub.get(device_id).name if hub.get(device_id) else None) or _names.get(str(device_id)) or 'The PC'
    try:
        return await hub.call(
            device_id, name, _jsonable(params), power=power,
            ctx={'session_id': ctx.session_id, 'agent_id': ctx.agent_id, 'task_id': ctx.task_id},
            timeout=CALL_TIMEOUT_S.get(name, DEFAULT_CALL_TIMEOUT_S),
        )
    except DeviceUnavailable as exc:
        msg = str(exc)
        return _unavailable(msg, label) if msg in ('offline', 'paused', f'no_{FILES}', f'no_{COMMANDS}') \
            else f'Error: {msg}'


def device_turn() -> bool:
    from cognitrix.tools.utils import current_execution_context
    return bool(getattr(current_execution_context(), 'device', None))
