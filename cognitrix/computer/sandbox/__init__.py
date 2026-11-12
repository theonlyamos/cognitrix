"""A sandboxed computer for agents: a Linux desktop in a container that agents
drive with the same driver as the person's own computer, and that the person
can watch and take over.

Layout:

- ``supervisor.py`` runs inside the container. It starts one desktop (an X
  display with its own window manager, live-screen stream and driver worker)
  per agent, and forwards driver calls to that desktop's worker.
- ``worker.py`` runs inside the container, one per desktop: it hosts
  ``Driver(LinuxBackend(), sandbox=True)`` on that desktop's display.
- ``client.py`` runs in the engine: ``RemoteDriver`` (the driver the computer
  tools use when the sandbox is on) and ``SandboxManager`` (starts and finds
  each user's container through Docker).

This module holds the wire format both sides share. It must import nothing
outside the standard library and ``cognitrix.computer``, because the image
ships only that package.
"""
from __future__ import annotations

import base64
import io
import os
import re
from dataclasses import asdict
from typing import Any

from cognitrix.computer import ActionEvent, ActionResult, Element, Snapshot, WindowInfo

SERVICE_PORT = 8790  # supervisor API inside the container
VIEWER_PORT = 6080  # noVNC / websockify inside the container
TOKEN_ENV = 'COGNITRIX_SANDBOX_TOKEN'

# Desktop names are used in file names, process names and URLs.
_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,47}$')


def desktop_name(raw: Any) -> str:
    """A safe desktop name from an agent id or name ('main' when none)."""
    text = re.sub(r'[^a-z0-9_-]+', '-', str(raw or 'main').lower()).strip('-_')[:48] or 'main'
    if not _NAME.match(text):
        text = 'd-' + text[:46]
    return text


def check_name(name: str) -> str:
    if not _NAME.match(name or ''):
        raise ValueError('bad desktop name')
    return name


def window_to_dict(w: WindowInfo | None) -> dict | None:
    return None if w is None else asdict(w)


def window_from_dict(d: dict | None) -> WindowInfo | None:
    if not d:
        return None
    d = dict(d)
    d['rect'] = tuple(d.get('rect') or (0, 0, 0, 0))
    return WindowInfo(**d)


def element_to_dict(e: Element) -> dict:
    return {'role': e.role, 'name': e.name, 'value': e.value, 'enabled': e.enabled, 'actions': list(e.actions),
            'rect': list(e.rect), 'state': dict(e.state), 'accelerator': e.accelerator}


def element_from_dict(d: dict) -> Element:
    return Element(role=d.get('role', ''), name=d.get('name', ''), value=d.get('value'),
                   enabled=bool(d.get('enabled', True)), actions=list(d.get('actions') or []),
                   rect=tuple(d.get('rect') or (0, 0, 0, 0)), state=dict(d.get('state') or {}),
                   accelerator=d.get('accelerator', ''))


def snapshot_to_dict(s: Snapshot) -> dict:
    return {'id': s.id, 'window_id': s.window_id, 'elements': [element_to_dict(e) for e in s.elements],
            'origin': list(s.origin), 'size': list(s.size), 'scale': s.scale}


def snapshot_from_dict(d: dict) -> Snapshot:
    return Snapshot(d['id'], int(d['window_id']), [element_from_dict(e) for e in d.get('elements', [])],
                    tuple(d['origin']), tuple(d['size']), float(d['scale']))


def result_to_dict(r: ActionResult) -> dict:
    return {'status': r.status, 'message': r.message, 'focus_changed': r.focus_changed}


def result_from_dict(d: dict) -> ActionResult:
    return ActionResult(d['status'], d['message'], bool(d.get('focus_changed')))


def event_to_dict(e: ActionEvent) -> dict:
    return asdict(e)


def event_from_dict(d: dict) -> ActionEvent:
    return ActionEvent(**d)


def image_to_b64(image: Any) -> str | None:
    if image is None:
        return None
    buf = io.BytesIO()
    image.convert('RGB').save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


def image_from_b64(data: str | None) -> Any:
    if not data:
        return None
    from PIL import Image

    return Image.open(io.BytesIO(base64.b64decode(data)))


def token() -> str:
    return os.environ.get(TOKEN_ENV, '')
