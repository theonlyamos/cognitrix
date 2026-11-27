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
