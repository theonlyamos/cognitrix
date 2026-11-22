"""A chat turn on a paired PC: which PC, what the agent is told, and sending
the shell and file tools there.

A chat picks a PC with the computer chip (``Session.computer``). For that
turn the execution context carries the device id; ``run_remote`` sends the
PC tools (``PC_TOOLS``) to it instead of running them on the server.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from types import SimpleNamespace
from typing import Any

from cognitrix.computer.paired import (
    APPS,
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

# How long a task run waits for its PC to come online.
OFFLINE_WAIT_S = 30 * 60.0


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
    from cognitrix.computer.paired.hub import hub, status
    if not hub.serving and not out['online']:
        live = await status(out['id'])  # the task worker: ask the web process
        if live and live.get('online'):
            out.update({k: live[k] for k in ('online', 'paused', 'powers', 'folders') if k in live})
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
    try:
        from cognitrix.projects.context import narrow_pc_folders
        folders = narrow_pc_folders(list(folders))
    except Exception:
        pass
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
    if APPS in powers and not device.get('read_only'):
        lines.append(f"The window tools (list_windows, open_app, look_at_window, click_element, type_in_window and "
                     f"the rest) work on {name}'s own screen, in app windows in the background, not on your "
                     "sandboxed computer. Opening an app asks the person first. Don't touch apps they didn't ask "
                     'about, and never enter passwords or payment details.')
    agents_md = device.get('agents_md') or ('', '')
    if folders and agents_md[1]:
        path, text = agents_md
        skip = ''
        try:
            from cognitrix.projects.context import get
            project = get()
            skip = project.instructions if project is not None else ''
        except Exception:
            pass
        if ' '.join(text.split()) != ' '.join((skip or '').split()):
            lines += ['', f'### Instructions from {path}',
                      'Follow these for work in that folder. Where they disagree with a deeper AGENTS.md, '
                      'the file closest to the files you change wins, and what the person asks in this chat '
                      'wins over all of them.', text, '']
    if folders:
        lines.append('Folders there may have their own AGENTS.md (or CLAUDE.md) in subfolders. Before you '
                     'change files under one, read the closest one and follow it.')
    if device.get('read_only'):
        lines.append('You are helping another agent: on this PC you can only look (Read, Glob, Grep). '
                     'Writing, editing and commands are refused.')
    if not device.get('online'):
        lines.append(f'{name} is offline right now. If the person asks for work there, tell them to start the '
                     'Cognitrix desktop app (or `cognitrix connect`) on it. Do not move to another computer on your own.')
    elif device.get('paused'):
        lines.append(f'Agents are paused on {name} right now. Tell the person if you need it.')
    return '\n'.join(lines)


def _project_folders() -> dict[str, Any]:
    """The PC folders the turn's project limits it to (sent with each call)."""
    try:
        from cognitrix.projects.context import get
        project = get()
    except Exception:
        return {}
    if project is None or not project.pc_folders or project.computer in ('', 'sandbox'):
        return {}
    return {'only_folders': list(project.pc_folders)}


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
    if code == f'no_{APPS}':
        return f"Error: The person hasn't allowed apps on {name}. Ask them to allow it there."
    if code == f'no_{FILES}':
        return f"Error: The person hasn't allowed file access on {name}. Ask them to allow it there."
    return f'Error: {code}'


async def look_on_pc(window_id: int) -> tuple[str, bytes | None] | str:
    """Look at a window on this turn's paired PC: (its text, PNG bytes of
    the screenshot or None). A string is a reason it couldn't."""
    import base64

    from cognitrix.computer.paired import APPS
    from cognitrix.computer.paired.hub import DeviceUnavailable, hub
    from cognitrix.tools.utils import current_execution_context

    ctx = current_execution_context()
    device_id = getattr(ctx, 'device', None)
    label = (hub.get(device_id).name if hub.get(device_id) else None) or _names.get(str(device_id)) or 'The PC'
    try:
        text, images = await hub.call_full(
            device_id, 'Look At Window', {'window_id': int(window_id)}, power=APPS,
            ctx={'session_id': ctx.session_id, 'agent_id': ctx.agent_id, 'task_id': ctx.task_id,
                 'coords_normalized': _coords_normalized(), **_project_folders()},
            timeout=CALL_TIMEOUT_S.get('Look At Window', DEFAULT_CALL_TIMEOUT_S))
    except DeviceUnavailable as exc:
        msg = str(exc)
        return _unavailable(msg, label) if msg in ('offline', 'paused', f'no_{APPS}') else f'Error: {msg}'
    for image in images:
        head, _, data = image.partition(',')
        if head.startswith('data:image/') and data:
            try:
                return text, base64.b64decode(data)
            except ValueError:
                continue
    return text, None


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

    async def go() -> str:
        return await hub.call(
            device_id, name, _jsonable(params), power=power,
            ctx={'session_id': ctx.session_id, 'agent_id': ctx.agent_id, 'task_id': ctx.task_id,
                 'coords_normalized': _coords_normalized(), **_project_folders()},
            timeout=CALL_TIMEOUT_S.get(name, DEFAULT_CALL_TIMEOUT_S),
        )

    try:
        try:
            return await go()
        except DeviceUnavailable as exc:
            if str(exc) != 'offline' or not ctx.task_id:
                raise
            # A task waits for the PC to come back, then tells the person.
            if not await _wait_for(device_id, ctx, label, longest=MID_STEP_WAIT_S):
                raise
            return await go()
    except DeviceUnavailable as exc:
        msg = str(exc)
        return _unavailable(msg, label) if msg in ('offline', 'paused', f'no_{FILES}', f'no_{COMMANDS}', f'no_{APPS}') \
            else f'Error: {msg}'


# (task id, device id) -> when a task gave up waiting for that PC, so its
# later calls fail at once instead of waiting again.
_gave_up: dict[tuple[str, str], float] = {}


def offline_wait_s() -> float:
    try:
        return float(os.environ.get('COGNITRIX_PAIRED_TASK_WAIT_S', '') or OFFLINE_WAIT_S)
    except ValueError:
        return OFFLINE_WAIT_S


# A PC that drops in the middle of a task step is waited for this long at
# most, so the wait ends well inside the step's own time limit. The long wait
# (offline_wait_s) happens before a step starts: see wait_before_step.
MID_STEP_WAIT_S = 300.0


async def wait_before_step(device_id: Any, *, task_id: Any, user_id: Any) -> str | None:
    """Before a task step on a PC: wait for the PC if it's offline, outside the
    step's time limit. None when it's online; otherwise why the step can't run."""
    from cognitrix.computer.paired.hub import status

    live = await status(device_id)
    if live and live.get('online'):
        return None
    label = _names.get(str(device_id)) or 'The PC'
    try:
        from cognitrix.computer.paired.store import device_of
        device = await device_of(str(user_id), str(device_id)) if user_id else None
        if device is not None:
            label = device.name or label
    except Exception:
        logger.debug('Could not look up the PC', exc_info=True)
    ctx = SimpleNamespace(task_id=task_id, user_id=user_id)
    if await _wait_for(device_id, ctx, label):
        return None
    return _unavailable('offline', label)


async def _wait_for(device_id: Any, ctx: Any, label: str, *, longest: float | None = None) -> bool:
    """A task run's PC is offline: wait up to 30 minutes (or ``longest``) for it to
    come back. True when it did; otherwise tell the person and return False."""
    from cognitrix.computer.paired.hub import status

    key = (str(ctx.task_id), str(device_id))
    if key in _gave_up:
        return False
    wait = offline_wait_s() if longest is None else min(offline_wait_s(), longest)
    step = max(0.2, min(15.0, wait / 20))
    deadline = time.monotonic() + wait
    logger.info('Task %s is waiting for %s to come online', ctx.task_id, label)
    while time.monotonic() < deadline:
        await asyncio.sleep(step)
        live = await status(device_id)
        if live and live.get('online'):
            return True
    _gave_up[key] = time.time()
    try:
        from cognitrix.tasks.automation import create_notification
        if ctx.user_id:
            await create_notification(
                str(ctx.user_id), f'{label} is offline',
                f'A task waited {int(wait // 60) or 1} minutes for {label} to come online, then stopped using it. '
                'Start the Cognitrix desktop app (or `cognitrix connect`) on that PC and run the task again.',
                task_id=str(ctx.task_id), urgent=True)
    except Exception:
        logger.debug('Could not tell the person the PC stayed offline', exc_info=True)
    return False


def _coords_normalized() -> bool:
    try:
        from cognitrix.tools.misc import coords_normalized
        return bool(coords_normalized.get())
    except Exception:
        return False


def device_turn() -> bool:
    from cognitrix.tools.utils import current_execution_context
    return bool(getattr(current_execution_context(), 'device', None))
