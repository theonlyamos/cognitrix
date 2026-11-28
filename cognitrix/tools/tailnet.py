"""Agent tools for the machines on the owner's tailnet (cognitrix/tailscale/devices.py):

- **List Tailnet Devices**: what is on the tailnet and which machines agents
  may use. It only reads.
- **Run On Tailnet Device**: one command on one machine, as one of the login
  names an admin allowed there. A person approves every command on a card
  first: the tool refuses a call no person approved, whatever the agent's
  permission level or an automatic approval says (see ``needs_person`` in
  cognitrix/safety/permissions.py).

Neither is given to an agent by default: add them to the agents that should
have them. Both refuse until an admin turns the feature on in Settings >
Tailscale.
"""
from __future__ import annotations

import asyncio
from typing import Any

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome

TAILNET_TOOL_CATEGORY = 'tailnet'
RUN_TOOL = 'run_on_tailnet_device'
RISK_CATEGORY = 'tailnet_device'

_OFF = ("Agents can't use tailnet devices on this server. Tell the person: an admin can turn it on in Settings, "
        'Tailscale, under Devices agents may use.')
_NOT_YOU = ("Only admins' agents may use tailnet devices on this server. Tell the person: an admin can change "
            'that in Settings, Tailscale.')


def _person(parent: Any) -> str | None:
    from cognitrix.tools.access import _person as person_of

    return person_of(parent)


async def _refusal(parent: Any) -> ToolOutcome | None:
    from cognitrix import tailscale
    from cognitrix.tailscale import devices

    if tailscale.devices_scope() == tailscale.OFF:
        return ToolOutcome.failure('tailnet_off', _OFF, denied=True)
    if not await devices.allowed_for(_person(parent)):
        return ToolOutcome.failure('tailnet_not_allowed', _NOT_YOU, denied=True)
    return None


@tool(category=TAILNET_TOOL_CATEGORY)
async def list_tailnet_devices(*, parent: Any | None = None):
    """List the machines on the person's Tailscale network (their tailnet) and which of them you may run
    commands on with Run On Tailnet Device.

    A machine marked "not allowed" can't be used: tell the person which one you need, an admin allows it in
    Settings, Tailscale.

    Returns:
        str: One line per machine: name, system, online or offline, and the login names you may use on it.
    """
    from cognitrix.tailscale import devices

    refused = await _refusal(parent)
    if refused is not None:
        return refused
    if await asyncio.to_thread(devices.usable_here):
        return ToolOutcome.success(devices.describe(await devices.listing()))
    return _outcome(await devices.via_web('tailnet_list', {}))


def _outcome(out: dict) -> ToolOutcome:
    if out.get('ok'):
        return ToolOutcome.success(str(out.get('text') or ''))
    return ToolOutcome.failure(str(out.get('code') or 'failed'), str(out.get('text') or ''),
                               denied=bool(out.get('denied')))


def run_details(params: dict) -> str:
    """The approval card's text for Run On Tailnet Device."""
    device = str(params.get('device') or '?')[:80]
    user = str(params.get('user') or '').strip()[:64]
    command = str(params.get('command') or '').strip()
    who = f' as {user}' if user else ''
    return (f'Runs on {device}{who}, a real machine on your tailnet (not the agent\'s sandbox): '
            f'{command[:600]}')


@tool(category=TAILNET_TOOL_CATEGORY, retryable=False, max_attempts=1)
async def run_on_tailnet_device(device: str, user: str, command: str, timeout: int = 60, *,
                                approved: bool = False, parent: Any | None = None):
    """Run one shell command on a machine on the person's Tailscale network and return what it printed.

    The person approves every command first, seeing the machine, the login name and the full command. It is a
    real machine of theirs, so prefer commands that only look (status, logs, disk space) and say why you need
    anything that changes it. Only machines and login names from List Tailnet Devices work.

    Args:
        device (str): The machine's name, as List Tailnet Devices shows it.
        user (str): The login name to run as: one of those List Tailnet Devices shows for that machine.
        command (str): The command line, run by that login's shell on the machine.
        timeout (int, optional): Seconds to wait for it. Defaults to 60, at most 900.

    Returns:
        str: The command's output and exit code, or why it didn't run.
    """
    from cognitrix.tailscale import devices

    refused = await _refusal(parent)
    if refused is not None:
        return refused
    if not approved:
        return ToolOutcome.failure(
            'approval_required', 'A command on a tailnet device only runs after the person approves this exact '
            "call (remembered and automatic approvals don't count). Ask them.", denied=True)
    timeout = max(1, min(int(timeout or devices.DEFAULT_TIMEOUT_S), devices.MAX_TIMEOUT_S))
    if await asyncio.to_thread(devices.usable_here):
        return _outcome(await devices.perform(device, user, command, timeout))
    # A task worker without Tailscale of its own: the web process runs it.
    return _outcome(await devices.via_web('tailnet_run', {'device': device, 'user': user, 'command': command,
                                                          'timeout': timeout}, timeout=timeout + 45))
