"""Engine side of the sandboxed computer.

When ``COGNITRIX_COMPUTER_SANDBOX=docker``, the computer tools drive a
desktop in a Docker container instead of the machine the engine runs on:
one container per user, and one desktop per agent inside it (like virtual
desktops: separate screens, mice and keyboards, shared files).

``SandboxManager`` starts or finds each user's container with the Docker CLI;
``RemoteDriver`` has the same methods the tools use on the local ``Driver``
and forwards them to the agent's desktop.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from cognitrix.computer import (
    Actor,
    Blocked,
    Busy,
    DriverError,
    HumanControl,
    Snapshot,
    Stale,
    Unsupported,
    WindowInfo,
    _global_listeners,
    current_actor,
)
from cognitrix.computer.sandbox import (
    SERVICE_PORT,
    TOKEN_ENV,
    VIEWER_PORT,
    desktop_name,
    event_from_dict,
    image_from_b64,
    result_from_dict,
    snapshot_from_dict,
    window_from_dict,
)

logger = logging.getLogger('cognitrix.sandbox')

MODE_ENV = 'COGNITRIX_COMPUTER_SANDBOX'
# Published by .github/workflows/sandbox-image.yml; Docker pulls it on first use.
IMAGE = os.environ.get('COGNITRIX_SANDBOX_IMAGE', 'ghcr.io/theonlyamos/cognitrix-desktop:latest')
SETTINGS_FILE = Path.home() / '.cognitrix' / 'computer-sandbox.json'
LABEL = 'cognitrix.sandbox'
# How often the engine pulls the image to see whether a newer one was
# published (only the changed layers download). 0 turns the check off.
UPDATE_CHECK_HOURS_ENV = 'COGNITRIX_SANDBOX_UPDATE_CHECK_HOURS'
_IMAGE_ID_TTL_S = 60.0

_ERRORS = {'human_control': HumanControl, 'stale': Stale, 'busy': Busy, 'blocked': Blocked,
           'unsupported': Unsupported}


def sandbox_mode() -> str | None:
    """'docker' when agents use the sandboxed computer, else None. The
    environment variable wins; otherwise the setting saved from Settings."""
    value = os.environ.get(MODE_ENV, '').strip().lower()
    if value:
        return 'docker' if value in ('docker', '1', 'true', 'yes', 'on') else None
    return 'docker' if _saved().get('enabled') else None


def mode_from_env() -> bool:
    """True when the environment decides, so Settings can't change it."""
    return bool(os.environ.get(MODE_ENV, '').strip())


_saved_cache: tuple[float, dict] = (-1.0, {})


def _saved() -> dict:
    global _saved_cache
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except OSError:
        return {}
    if mtime != _saved_cache[0]:
        try:
            _saved_cache = (mtime, json.loads(SETTINGS_FILE.read_text()))
        except (OSError, ValueError):
            _saved_cache = (mtime, {})
    return _saved_cache[1]


def save_enabled(enabled: bool) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = dict(_saved())
    data['enabled'] = bool(enabled)
    SETTINGS_FILE.write_text(json.dumps(data))


def docker_status() -> dict:
    """Can this machine run the sandbox? {'installed', 'running', 'image'}."""
    docker = shutil.which('docker')
    if not docker:
        return {'installed': False, 'running': False, 'image': False}
    try:
        info = subprocess.run([docker, 'info', '--format', '{{.ServerVersion}}'], capture_output=True, text=True,
                              timeout=15)  # noqa: S603
        running = info.returncode == 0 and bool(info.stdout.strip())
        image = running and subprocess.run([docker, 'image', 'inspect', IMAGE], capture_output=True,  # noqa: S603
                                           timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return {'installed': True, 'running': False, 'image': False}
    return {'installed': True, 'running': running, 'image': bool(image)}


def user_key(user_id: Any) -> str:
    return hashlib.sha256(str(user_id or 'local').encode()).hexdigest()[:12]


class Sandbox:
    """One user's running container."""

    def __init__(self, name: str, token: str, api_port: int, viewer_port: int):
        self.name = name
        self.token = token
        self.api = f'http://127.0.0.1:{api_port}'
        self.viewer_port = viewer_port
        self.image_id = ''  # the image the container was made from (sha256:...)
        self.passwords: dict[str, str] = {}  # desktop -> VNC password
        # One driver per desktop, kept so its snapshots last between tool calls
        # (normalized-coordinate clicks read the last look's size).
        self.drivers: dict[str, RemoteDriver] = {}
        self._client = httpx.Client(timeout=130, headers={'Authorization': f'Bearer {token}'})

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        try:
            resp = self._client.request(method, self.api + path, json=body,
                                        timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
        except httpx.HTTPError as exc:
            raise DriverError(f"The agent's computer is not responding ({type(exc).__name__}). "
                              'It may still be starting; try again in a moment.') from None
        try:
            out = resp.json()
        except ValueError:
            raise DriverError(f"The agent's computer answered {resp.status_code}.") from None
        if not out.get('ok') and resp.status_code >= 400:
            raise DriverError(out.get('message') or out.get('error') or f'sandbox error {resp.status_code}')
        return out

    def desktop(self, name: str) -> dict:
        out = self.request('POST', f'/desktops/{name}', timeout=60)
        self.passwords[name] = out.get('vnc_password', '')
        return out['desktop']

    def desktops(self) -> list[dict]:
        return self.request('GET', '/desktops').get('desktops', [])


class SandboxManager:
    """Starts, finds and stops users' sandbox containers through Docker."""

    def __init__(self, image: str = IMAGE, docker: str | None = None):
        self.image = image
        self.docker = docker or shutil.which('docker') or 'docker'
        self._boxes: dict[str, Sandbox] = {}
        self._guard = threading.Lock()
        # Newer-image check: a background `docker pull` every few hours.
        self._pull_guard = threading.Lock()
        self._pulling = False
        self._last_pull = 0.0  # time.monotonic(); 0 = not yet
        self._image_id_cache: tuple[float, str] = (0.0, '')

    # -- docker -------------------------------------------------------------
    def _docker(self, *args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.docker, *args], capture_output=True, text=True, check=check,  # noqa: S603
                                  timeout=timeout)
        except FileNotFoundError:
            raise Unsupported("The agents' computer needs Docker, and Docker isn't installed here. Install Docker "
                              'Desktop, or turn the sandboxed computer off.') from None
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or '').strip().splitlines()[-1:] or ['']
            if 'Cannot connect to the Docker daemon' in (exc.stderr or '') or 'docker API' in (exc.stderr or ''):
                raise Unsupported("Docker isn't running, so the agents' computer can't start. Start Docker "
                                  'Desktop and try again.') from None
            raise DriverError(f"Couldn't start the agents' computer: {detail[0][:300]}") from None

    def container_name(self, user_id: Any) -> str:
        return f'cognitrix-sandbox-{user_key(user_id)}'

    def _inspect(self, name: str) -> dict | None:
        got = self._docker('inspect', name, check=False)
        if got.returncode != 0:
            return None
        data = json.loads(got.stdout or '[]')
        return data[0] if data else None

    @staticmethod
    def _port(info: dict, port: int) -> int | None:
        bindings = ((info.get('NetworkSettings') or {}).get('Ports') or {}).get(f'{port}/tcp') or []
        for b in bindings:
            if b.get('HostPort'):
                return int(b['HostPort'])
        return None

    def _run(self, name: str, user_id: Any) -> None:
        token = secrets.token_urlsafe(24)
        key = user_key(user_id)
        self._docker(
            'run', '-d', '--name', name,
            '--label', f'{LABEL}=1', '--label', f'{LABEL}.user={key}', '--label', f'{LABEL}.token={token}',
            '-e', f'{TOKEN_ENV}={token}',
            '-p', f'127.0.0.1::{SERVICE_PORT}', '-p', f'127.0.0.1::{VIEWER_PORT}',
            '-v', f'cognitrix-sandbox-home-{key}:/home/agent',
            '--hostname', 'cognitrix-sandbox', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '2048', '--memory', os.environ.get('COGNITRIX_SANDBOX_MEMORY', '4g'),
            '--shm-size', '1g',
            self.image, timeout=1200)  # the first start downloads the image

    def sandbox(self, user_id: Any) -> Sandbox:
        """The user's running sandbox, started if needed."""
        name = self.container_name(user_id)
        with self._guard:
            box = self._boxes.get(name)
            info = self._inspect(name)
            if info is None:
                self._run(name, user_id)
                info = self._inspect(name)
            elif not (info.get('State') or {}).get('Running'):
                if self._outdated(info):
                    # Nothing is running on it, so move it onto the newer
                    # image now. Files and logins live in the home volume.
                    logger.info('Recreating stopped sandbox %s on the newer image', name)
                    self._docker('rm', '-f', name, check=False)
                    self._run(name, user_id)
                else:
                    self._docker('start', name)
                info = self._inspect(name)
            if info is None:
                raise DriverError("The agents' computer didn't start.")
            labels = (info.get('Config') or {}).get('Labels') or {}
            api_port, viewer_port = self._port(info, SERVICE_PORT), self._port(info, VIEWER_PORT)
            if not api_port or not viewer_port:
                raise DriverError("The agents' computer started without its ports; remove it and try again.")
            token = labels.get(f'{LABEL}.token', '')
            if box is None or box.token != token or not box.api.endswith(f':{api_port}'):
                box = Sandbox(name, token, api_port, viewer_port)
                self._boxes[name] = box
            box.image_id = str(info.get('Image') or '')
        self._wait_healthy(box)
        self.check_for_update()
        return box

    @staticmethod
    def _wait_healthy(box: Sandbox, seconds: float = 30) -> None:
        deadline = time.monotonic() + seconds
        while True:
            try:
                if httpx.get(box.api + '/health', timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise DriverError("The agents' computer started but isn't answering yet. Try again in a moment.")
            time.sleep(0.3)

    def existing(self, user_id: Any) -> Sandbox | None:
        """The user's sandbox when it's already running; never starts one."""
        name = self.container_name(user_id)
        info = self._inspect(name)
        if info is None or not (info.get('State') or {}).get('Running'):
            return None
        return self.sandbox(user_id)

    def stop(self, user_id: Any) -> None:
        name = self.container_name(user_id)
        self._docker('stop', name, check=False)
        self._boxes.pop(name, None)

    # -- newer image ----------------------------------------------------------
    def _local_image_id(self, fresh: bool = False) -> str:
        """Id of the image the next container would start from ('' if none)."""
        now = time.monotonic()
        at, cached = self._image_id_cache
        if not fresh and cached and now - at < _IMAGE_ID_TTL_S:
            return cached
        got = self._docker('image', 'inspect', '--format', '{{.Id}}', self.image, check=False, timeout=30)
        image_id = got.stdout.strip() if got.returncode == 0 else ''
        self._image_id_cache = (now, image_id)
        return image_id

    def _outdated(self, info: dict) -> bool:
        current = str(info.get('Image') or '')
        latest = self._local_image_id()
        return bool(current and latest and current != latest)

    def update_available(self, box: Sandbox) -> bool:
        """True when a newer image has been downloaded than the one this
        running computer was made from."""
        latest = self._local_image_id()
        return bool(box.image_id and latest and box.image_id != latest)

    def check_for_update(self, force: bool = False) -> bool:
        """Pull the image in the background when the last pull is old enough.
        Returns True when a pull was started."""
        try:
            hours = float(os.environ.get(UPDATE_CHECK_HOURS_ENV, '6') or 0)
        except ValueError:
            hours = 6.0
        if hours <= 0 and not force:
            return False
        with self._pull_guard:
            now = time.monotonic()
            if self._pulling or (not force and self._last_pull and now - self._last_pull < hours * 3600):
                return False
            self._pulling = True
            self._last_pull = now
        threading.Thread(target=self._pull, name='sandbox-image-pull', daemon=True).start()
        return True

    def _pull(self) -> None:
        try:
            got = self._docker('pull', '--quiet', self.image, check=False, timeout=1800)
            if got.returncode != 0:
                logger.info("Couldn't check for a newer sandbox image: %s",
                            (got.stderr or got.stdout or '').strip()[-300:])
            self._local_image_id(fresh=True)
        except Exception:  # Docker missing or stopped; try again next time
            logger.info("Couldn't check for a newer sandbox image", exc_info=True)
        finally:
            with self._pull_guard:
                self._pulling = False

    @property
    def checking_for_update(self) -> bool:
        return self._pulling

    def update(self, user_id: Any) -> Sandbox:
        """Move the user's computer onto the newest downloaded image. Open
        windows close; files and logins stay (they live in the home volume)."""
        name = self.container_name(user_id)
        try:  # reopen the same desktops afterwards, so the panel can reconnect
            old = self.existing(user_id)
            names = [d['name'] for d in (old.desktops() if old else []) if d.get('name')]
        except Exception:
            names = []
        with self._guard:
            self._docker('rm', '-f', name, check=False)
            self._boxes.pop(name, None)
        self._local_image_id(fresh=True)
        box = self.sandbox(user_id)
        for desktop in names:
            try:
                box.desktop(desktop)
            except Exception:
                logger.info("Couldn't reopen desktop %s after the update", desktop, exc_info=True)
        return box

    def reset(self, user_id: Any) -> None:
        """Remove the container and its home volume: a fresh computer next time."""
        name = self.container_name(user_id)
        self._docker('rm', '-f', name, check=False)
        self._docker('volume', 'rm', '-f', f'cognitrix-sandbox-home-{user_key(user_id)}', check=False)
        self._boxes.pop(name, None)

    def driver(self, user_id: Any, agent_id: Any = None) -> RemoteDriver:
        box = self.sandbox(user_id)
        name = desktop_name(agent_id)
        if name not in box.passwords:
            box.desktop(name)
        driver = box.drivers.get(name)
        if driver is None:
            driver = box.drivers[name] = RemoteDriver(box, name)
        return driver


class _RemoteBackend:
    """The bits of Backend the tools read directly."""

    def __init__(self, driver: RemoteDriver):
        self._driver = driver

    def window(self, window_id: int) -> WindowInfo | None:
        return window_from_dict(self._driver._call('window', window_id=int(window_id)))


class RemoteDriver:
    """Same calls as ``Driver``, run on one desktop of a user's sandbox."""

    sandbox = True

    def __init__(self, box: Sandbox, desktop: str):
        self.box = box
        self.desktop = desktop
        self.backend = _RemoteBackend(self)
        self._snapshots: dict[int, Snapshot] = {}

    def _call(self, method: str, **args: Any) -> Any:
        actor = current_actor.get()
        body = {'method': method, 'args': args, 'actor': actor.__dict__ if isinstance(actor, Actor) else None}
        out = self.box.request('POST', f'/desktops/{self.desktop}/call', body)
        if out.get('error') == 'no_desktop':  # the container restarted: start the desktop again
            self.box.desktop(self.desktop)
            out = self.box.request('POST', f'/desktops/{self.desktop}/call', body)
        for raw in out.get('events') or []:
            event = event_from_dict({**raw, 'desktop': self.desktop})
            for callback in list(_global_listeners):
                try:
                    callback(event)
                except Exception:
                    logger.exception('computer-use listener failed')
        if not out.get('ok'):
            raise _ERRORS.get(out.get('error'), DriverError)(out.get('message') or 'The action failed.')
        return out.get('result')

    def list_windows(self, query: str | None = None) -> list[WindowInfo]:
        return [window_from_dict(w) for w in self._call('list_windows', query=query) or []]

    def open(self, target: str, timeout_s: float = 12.0, args: str | None = None) -> WindowInfo | None:
        return window_from_dict(self._call('open', target=target, args=args, timeout_s=timeout_s))

    def look(self, window_id: int, query: str | None = None, limit: int = 60, viewer: Any = None):
        out = self._call('look', window_id=int(window_id), query=query, limit=int(limit), viewer=_viewer(viewer))
        snap = snapshot_from_dict(out['snapshot'])
        self._snapshots[snap.window_id] = snap
        shown = [(i, snap.elements[i]) for i in out.get('shown', []) if 0 <= i < len(snap.elements)]
        return image_from_b64(out.get('image')), snap, shown, out.get('note', '')

    def act_on_element(self, window_id: int, index: int, action: str = 'click', snapshot_id: str | None = None,
                       viewer: Any = None, use_screen: bool = False):
        return result_from_dict(self._call('act_on_element', window_id=int(window_id), index=int(index),
                                           action=action, snapshot_id=snapshot_id, viewer=_viewer(viewer),
                                           use_screen=use_screen))

    def set_text(self, window_id: int, text: str, index: int | None = None, snapshot_id: str | None = None,
                 viewer: Any = None):
        return result_from_dict(self._call('set_text', window_id=int(window_id), text=text, index=index,
                                           snapshot_id=snapshot_id, viewer=_viewer(viewer)))

    def click_point(self, window_id: int, x: float, y: float, button: str = 'left', double: bool = False,
                    viewer: Any = None, use_screen: bool = False):
        return result_from_dict(self._call('click_point', window_id=int(window_id), x=x, y=y, button=button,
                                           double=double, viewer=_viewer(viewer), use_screen=use_screen))

    def type_text(self, window_id: int, text: str, x: float | None = None, y: float | None = None,
                  viewer: Any = None, use_screen: bool = False):
        return result_from_dict(self._call('type_text', window_id=int(window_id), text=text, x=x, y=y,
                                           viewer=_viewer(viewer), use_screen=use_screen))

    def press_key(self, window_id: int, keys: str, use_screen: bool = False):
        return result_from_dict(self._call('press_key', window_id=int(window_id), keys=keys,
                                           use_screen=use_screen))

    def scroll(self, window_id: int, direction: str = 'down', amount: int = 3, index: int | None = None,
               viewer: Any = None):
        return result_from_dict(self._call('scroll', window_id=int(window_id), direction=direction,
                                           amount=int(amount), index=index, viewer=_viewer(viewer)))

    def stop_agent(self, agent_id: str) -> None:
        self._call('stop_agent', agent_id=str(agent_id))


def _viewer(viewer: Any) -> Any:
    # Viewers are compared for equality only; send something JSON can carry.
    return None if viewer is None else str(viewer)


_manager: SandboxManager | None = None
_manager_guard = threading.Lock()


def get_manager() -> SandboxManager:
    global _manager
    with _manager_guard:
        if _manager is None:
            _manager = SandboxManager()
        return _manager


def sandbox_driver(actor: Actor | None = None) -> RemoteDriver:
    actor = actor or current_actor.get() or Actor()
    return get_manager().driver(actor.user_id, actor.agent_id)
