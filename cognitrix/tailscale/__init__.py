"""Tailscale in Cognitrix.

Tailscale gives a machine a private, encrypted network (a "tailnet") shared
with the owner's other devices. This package is the only code that talks to
it. Everything is switched on in Settings > Tailscale (cognitrix/app_settings.py);
nothing is read from the environment.

- ``cli``      finds and runs the Tailscale client, with time limits.
- ``daemon``   runs a built-in Tailscale where the machine has none (a server
               in Docker or on Fly).
- ``doors``    two extra listeners on this machine only: the tailnet door
               (Tailscale Serve, private) and the public door (Tailscale
               Funnel, an allow list of paths).
- ``service``  what Settings shows and does: status, sign in, the private
               address, the public address for webhooks.
- ``identity`` who a visitor from the tailnet is, and which account their
               Tailscale login is linked to.
- ``api``      Tailscale's web API with the OAuth client saved in Settings:
               one-time join keys, the device list, removing a device.
- ``outbound`` how this engine dials a tailnet address (straight, or through
               the built-in Tailscale's local proxy).
- ``computers`` each person's Fly computer as a machine on the tailnet.
- ``devices``  the tailnet devices agents may run commands on.

Addresses other code needs (links, webhooks) come from
``cognitrix.common.addresses``, which reads what ``service`` last wrote, so
no request waits on the Tailscale client. See docs/tailscale.md.
"""
from __future__ import annotations

from pathlib import Path

from cognitrix.app_settings import get as app_setting
from cognitrix.app_settings import get_bool

# Funnel only listens on these (Tailscale's limit).
FUNNEL_PORTS = (443, 8443, 10000)
DEFAULT_SERVE_PORT = 443
DEFAULT_FUNNEL_PORT = 8443
DEFAULT_HOSTNAME = 'cognitrix'

# What the public door may let through (COGNITRIX_TAILSCALE_FUNNEL_ALLOW).
ALLOW_WEBHOOKS = 'webhooks'
ALLOW_LINKS = 'links'
ALLOW_APP = 'app'
ALLOW_CHOICES = (ALLOW_WEBHOOKS, ALLOW_LINKS, ALLOW_APP)


def folder() -> Path:
    """Where Tailscale state, the door ports and the last known addresses live.
    Same data folder as app_settings.json, shared by every Cognitrix process."""
    return Path.home() / '.cognitrix' / 'tailscale'


def enabled() -> bool:
    return get_bool('COGNITRIX_TAILSCALE', False)


def serve_wanted() -> bool:
    return enabled() and get_bool('COGNITRIX_TAILSCALE_SERVE', False)


def funnel_wanted() -> bool:
    return enabled() and get_bool('COGNITRIX_TAILSCALE_FUNNEL', False)


def _port(name: str, default: int) -> int:
    try:
        port = int(app_setting(name, '') or default)
    except ValueError:
        return default
    return port if 0 < port < 65536 else default


def serve_port() -> int:
    return _port('COGNITRIX_TAILSCALE_SERVE_PORT', DEFAULT_SERVE_PORT)


def funnel_port() -> int:
    port = _port('COGNITRIX_TAILSCALE_FUNNEL_PORT', DEFAULT_FUNNEL_PORT)
    return port if port in FUNNEL_PORTS else DEFAULT_FUNNEL_PORT


def funnel_allow() -> frozenset[str]:
    """Groups of paths the public door passes. Webhooks unless set otherwise."""
    raw = app_setting('COGNITRIX_TAILSCALE_FUNNEL_ALLOW', '') or ALLOW_WEBHOOKS
    picked = {p.strip().lower() for p in raw.replace('\n', ',').split(',') if p.strip()}
    return frozenset(p for p in picked if p in ALLOW_CHOICES) or frozenset({ALLOW_WEBHOOKS})


def hostname() -> str:
    raw = (app_setting('COGNITRIX_TAILSCALE_HOSTNAME', '') or DEFAULT_HOSTNAME).strip().lower()
    cleaned = ''.join(c if c.isascii() and c.isalnum() else '-' for c in raw).strip('-')
    return cleaned[:63] or DEFAULT_HOSTNAME


# Whose Fly computers join the tailnet (COGNITRIX_TAILSCALE_SANDBOX), and whose
# agents may run commands on tailnet devices (COGNITRIX_TAILSCALE_DEVICES).
NOBODY = 'nobody'
OFF = 'off'
ADMINS = 'admins'
EVERYONE = 'everyone'
DEFAULT_SANDBOX_TAG = 'tag:cognitrix-sandbox'


def sandbox_choice() -> str:
    """What was picked in Settings: nobody, admins or everyone."""
    raw = (app_setting('COGNITRIX_TAILSCALE_SANDBOX', '') or NOBODY).strip().lower()
    return raw if raw in (ADMINS, EVERYONE) else NOBODY


def sandbox_scope() -> str:
    """The choice in force: nobody while Tailscale itself is off."""
    return sandbox_choice() if enabled() else NOBODY


def sandbox_tag() -> str:
    raw = (app_setting('COGNITRIX_TAILSCALE_SANDBOX_TAG', '') or DEFAULT_SANDBOX_TAG).strip().lower()
    if not raw.startswith('tag:'):
        raw = 'tag:' + raw
    name = ''.join(c for c in raw[4:] if c.isascii() and (c.isalnum() or c == '-'))
    return f'tag:{name}' if name and name[0].isalpha() else DEFAULT_SANDBOX_TAG


def devices_choice() -> str:
    """What was picked in Settings: off, admins or everyone."""
    raw = (app_setting('COGNITRIX_TAILSCALE_DEVICES', '') or OFF).strip().lower()
    return raw if raw in (ADMINS, EVERYONE) else OFF


def devices_scope() -> str:
    """The choice in force: off while Tailscale itself is off."""
    return devices_choice() if enabled() else OFF
