"""Engine side of the sandboxed computer.

When ``COGNITRIX_COMPUTER_SANDBOX=docker``, the computer tools drive a
desktop in a Docker container instead of the machine the engine runs on:
one container per user, and one desktop per agent inside it (like virtual
desktops: separate screens, mice and keyboards, shared files). With
``COGNITRIX_COMPUTER_SANDBOX=fly`` each user's computer is a Fly Machine
instead (see ``fly.py``), stopped when idle and started on the next request.

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
from cognitrix.computer._app_setting import app_setting
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
AGENT_USER_CAPS = ('CHOWN', 'FOWNER', 'SETGID', 'SETUID')
AGENT_USERS_LABEL = 'cognitrix.agent-users'
# Published by .github/workflows/sandbox-image.yml; Docker pulls it on first use.
IMAGE = app_setting('COGNITRIX_SANDBOX_IMAGE', 'ghcr.io/theonlyamos/cognitrix-desktop:latest')
SETTINGS_FILE = Path.home() / '.cognitrix' / 'computer-sandbox.json'
LABEL = 'cognitrix.sandbox'
# How often the engine pulls the image to see whether a newer one was
# published (only the changed layers download). 0 turns the check off.
UPDATE_CHECK_HOURS_ENV = 'COGNITRIX_SANDBOX_UPDATE_CHECK_HOURS'
_IMAGE_ID_TTL_S = 60.0
# A check that failed (offline, Docker busy, a slow download cut off) is
# tried again after this long instead of waiting the full interval.
_PULL_RETRY_S = 1200.0
# The image is a few GB; a first download on a slow line takes a while.
_PULL_TIMEOUT_S = 3 * 3600.0
# A running computer made from an older image moves onto the newer one once
# it has been idle this long (0 turns that off; the panel's Update button
# still works). Checked every AUTO_UPDATE_EVERY_S.
AUTO_UPDATE_IDLE_S = float(app_setting('COGNITRIX_SANDBOX_AUTO_UPDATE_IDLE_S', '600') or 0)
AUTO_UPDATE_EVERY_S = 300.0

_NO_OWNER = object()

_ERRORS = {'human_control': HumanControl, 'stale': Stale, 'busy': Busy, 'blocked': Blocked,
           'unsupported': Unsupported}


def sandbox_mode() -> str | None:
    """'docker' or 'fly' when agents use the sandboxed computer, else None.
    "Where agents get a computer" in Settings wins; then the on/off switch
    (which only switches the local Docker one on); with neither, it is on
    when Docker is installed."""
    value = app_setting(MODE_ENV, '').strip().lower()
    if value:
        if value == 'fly':
            return 'fly'
        return 'docker' if value in ('docker', '1', 'true', 'yes', 'on') else None
    saved = _saved()
    if 'enabled' in saved:
        return 'docker' if saved.get('enabled') else None
    # Never switched in Settings: on wherever Docker is installed, so agents
    # do computer work on their own computer rather than the person's.
    return 'docker' if _docker_installed() else None


_docker_seen: tuple[float, bool] = (-1.0, False)


def _docker_installed() -> bool:
    global _docker_seen
    now = time.monotonic()
    if _docker_seen[0] < 0 or now - _docker_seen[0] > 300:
        _docker_seen = (now, bool(shutil.which('docker')))
    return _docker_seen[1]


def mode_from_env() -> bool:
    """True when "Where agents get a computer" decides, so the switch can't change it."""
    return bool(app_setting(MODE_ENV, '').strip())


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

    def __init__(self, name: str, token: str, api_port: int, viewer_port: int, host: str = '127.0.0.1'):
        self.name = name
        self.token = token
        address = f'[{host}]' if ':' in host else host  # an IPv6 address on Fly's private network
        self.api = f'http://{address}:{api_port}'
        self.viewer_port = viewer_port
        self.viewer_base = f'ws://{address}:{viewer_port}'
        self.machine_id = ''  # Fly only
        self.image_id = ''  # the image the container was made from (sha256:...)
        self.container_id = ''
        # The user it belongs to, for restarting it onto a newer image (None is
        # the signed-out local user, so "not known" is _NO_OWNER).
        self.owner: Any = _NO_OWNER
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

    def call(self, method: str, path: str, body: dict | None = None, *, content: bytes | None = None,
             params: dict | None = None, timeout: float | None = None) -> dict:
        """Like ``request`` but returns the answer even when it is an error
        ({'ok': False, 'error': code, 'message': ...}), for callers that
        tell errors apart. Only a computer that doesn't answer raises."""
        try:
            resp = self._client.request(
                method, self.api + path, json=body if content is None else None, content=content, params=params,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                headers={'Content-Type': 'application/gzip'} if content is not None else None)
        except httpx.HTTPError as exc:
            raise DriverError(f"The agent's computer is not responding ({type(exc).__name__}). "
                              'It may still be starting; try again in a moment.') from None
        try:
            out = resp.json()
        except ValueError:
            return {'ok': False, 'error': 'bad_answer', 'message': f"The agent's computer answered {resp.status_code}."}
        if resp.status_code == 404 and out.get('error') == 'not_found' and not out.get('message'):
            out['message'] = ("The agent's computer doesn't know this yet: update it (Settings, Computer) "
                              'to get coding in the sandbox.')
        return out

    def desktop(self, name: str) -> dict:
        out = self.request('POST', f'/desktops/{name}', timeout=60)
        self.passwords[name] = out.get('vnc_password', '')
        return out['desktop']

    def desktops(self) -> list[dict]:
        return self.request('GET', '/desktops').get('desktops', [])

    def viewer_url(self, desktop: str) -> str:
        """The live screen of one desktop (websockify inside the computer)."""
        return f'{self.viewer_base}/websockify?token={desktop}'


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
        self._last_pull_ok = True
        self._image_id_cache: tuple[float, str] = (0.0, '')
        # Key proxy: where it listens and where containers reach it, and the
        # containers that already trust its CA.
        self._proxy_where: tuple[str, str] | None = None
        self._ca_installed: dict[str, str] = {}
        # Open live screens per user: a hosted computer isn't stopped while
        # someone watches it.
        self._viewers: dict[str, int] = {}
        self._auto_update: threading.Thread | None = None

    kind = 'docker'

    def viewer_opened(self, user_id: Any) -> None:
        key = user_key(user_id)
        with self._guard:
            self._viewers[key] = self._viewers.get(key, 0) + 1

    def viewer_closed(self, user_id: Any) -> None:
        key = user_key(user_id)
        with self._guard:
            left = self._viewers.get(key, 0) - 1
            if left > 0:
                self._viewers[key] = left
            else:
                self._viewers.pop(key, None)

    def watched(self, user_id: Any) -> bool:
        return self._viewers.get(user_key(user_id), 0) > 0

    def asleep(self, user_id: Any) -> bool:
        """True when the user's computer exists but is stopped to save money
        (hosted only; a local one is just started when needed)."""
        return False

    # -- docker -------------------------------------------------------------
    def _docker(self, *args: str, check: bool = True, timeout: float = 120,
                input: str | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.docker, *args], capture_output=True, text=True, check=check,  # noqa: S603
                                  timeout=timeout, input=input)
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
            '--hostname', 'cognitrix-sandbox',
            # The key proxy runs on this machine (see key_proxy_env).
            '--add-host', 'host.docker.internal:host-gateway',
            '--cap-drop', 'ALL', *self._agent_user_args(),
            '--security-opt', 'no-new-privileges',
            '--pids-limit', '2048', '--memory', app_setting('COGNITRIX_SANDBOX_MEMORY', '4g'),
            '--shm-size', '1g',
            self.image, timeout=1200)  # the first start downloads the image

    def _agent_user_args(self) -> list[str]:
        """An image that gives each agent its own Linux user starts as root
        only to drop to 'agent' with the four capabilities that takes (see
        sandbox/desktop/bin/cognitrix-sandbox-root and sandbox/users.py).
        An older image starts as 'agent' with none, as before."""
        inspect = ('image', 'inspect', '--format', '{{index .Config.Labels "' + AGENT_USERS_LABEL + '"}}', self.image)
        got = self._docker(*inspect, check=False, timeout=30)
        if got.returncode != 0:  # not downloaded yet: download it now to know
            self._docker('pull', '--quiet', self.image, check=False, timeout=1200)
            got = self._docker(*inspect, check=False, timeout=30)
        if got.returncode != 0 or got.stdout.strip() != '1':
            return []
        return ['--user', '0:0', *[f'--cap-add={c}' for c in AGENT_USER_CAPS]]

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
                box.owner = user_id
                self._boxes[name] = box
            box.image_id = str(info.get('Image') or '')
            box.container_id = str(info.get('Id') or '')
        self._wait_healthy(box)
        self.check_for_update()
        self._start_auto_update()
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
            # Still look for a newer image, so the computer starts on it
            # next time instead of on whatever was downloaded first.
            if info is not None:
                self.check_for_update()
                self._start_auto_update()
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
        """Made from an older image than the newest downloaded one, or started
        without what the image needs to give each agent its own Linux user
        (an older engine started it)."""
        current = str(info.get('Image') or '')
        latest = self._local_image_id()
        if current and latest and current != latest:
            return True
        return self._lacks_agent_users(info)

    def _lacks_agent_users(self, info: dict) -> bool:
        labels = (info.get('Config') or {}).get('Labels') or {}
        if labels.get(AGENT_USERS_LABEL) != '1':
            return False  # its image doesn't have them
        added = {str(c).upper().removeprefix('CAP_') for c in ((info.get('HostConfig') or {}).get('CapAdd') or [])}
        return not set(AGENT_USER_CAPS) <= added

    # -- moving idle computers onto a newer image ---------------------------------
    def _start_auto_update(self) -> None:
        if self._auto_update is not None or AUTO_UPDATE_IDLE_S <= 0:
            return
        self._auto_update = threading.Thread(target=self._auto_update_loop, name='sandbox-auto-update', daemon=True)
        self._auto_update.start()

    def _auto_update_loop(self) -> None:
        while True:
            time.sleep(AUTO_UPDATE_EVERY_S)
            try:
                # The check also runs here, not only when something asks for
                # the computer, so a long-running app keeps looking.
                self.check_for_update()
                self.update_idle()
            except Exception:
                logger.info("Couldn't check the agents' computers for updates", exc_info=True)

    def update_idle(self) -> list[str]:
        """Restart running computers that are outdated (see ``_outdated``) onto
        the newest image, once nobody has used one for a while: no request,
        command, take-over or open live screen. Files and logins stay; open
        windows close, which is why it waits. Returns the restarted names."""
        done = []
        for name, box in list(self._boxes.items()):
            if box.owner is _NO_OWNER:
                continue
            info = self._inspect(name)
            if info is None or not (info.get('State') or {}).get('Running') or not self._outdated(info):
                continue
            if self.watched(box.owner):
                continue
            try:
                activity = box.request('GET', '/activity', timeout=10)
            except DriverError:
                continue
            if activity.get('busy') or float(activity.get('idle_s') or 0) < AUTO_UPDATE_IDLE_S:
                continue
            logger.info('Moving idle sandbox %s onto the newer image', name)
            self.update(box.owner)
            done.append(name)
        return done

    def update_available(self, box: Sandbox) -> bool:
        """True when a newer image has been downloaded than the one this
        running computer was made from."""
        latest = self._local_image_id()
        return bool(box.image_id and latest and box.image_id != latest)

    def check_for_update(self, force: bool = False) -> bool:
        """Pull the image in the background when the last pull is old enough.
        Returns True when a pull was started."""
        try:
            hours = float(app_setting(UPDATE_CHECK_HOURS_ENV, '6') or 0)
        except ValueError:
            hours = 6.0
        if hours <= 0 and not force:
            return False
        with self._pull_guard:
            now = time.monotonic()
            wait = hours * 3600 if self._last_pull_ok else min(hours * 3600, _PULL_RETRY_S)
            if self._pulling or (not force and self._last_pull and now - self._last_pull < wait):
                return False
            self._pulling = True
            self._last_pull = now
        threading.Thread(target=self._pull, name='sandbox-image-pull', daemon=True).start()
        return True

    def _pull(self) -> None:
        ok = False
        started = time.monotonic()
        try:
            got = self._docker('pull', '--quiet', self.image, check=False, timeout=_PULL_TIMEOUT_S)
            if got.returncode != 0:
                logger.warning("Couldn't check for a newer sandbox image: %s",
                               (got.stderr or got.stdout or '').strip()[-300:])
            else:
                ok = True
            before = self._image_id_cache[1]
            after = self._local_image_id(fresh=True)
            if ok and before and after and before != after:
                logger.warning('Downloaded a newer sandbox image (%s) in %.0fs', after[:19],
                               time.monotonic() - started)
        except subprocess.TimeoutExpired:
            logger.warning('Downloading the newer sandbox image took over %.0f minutes; trying again later',
                           _PULL_TIMEOUT_S / 60)
        except Exception:  # Docker missing or stopped; try again soon
            logger.warning("Couldn't check for a newer sandbox image", exc_info=True)
        finally:
            with self._pull_guard:
                self._pulling = False
                self._last_pull_ok = ok
                # Count from the end, so a long download doesn't make the next
                # check due straight away.
                self._last_pull = time.monotonic()

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

    # -- running scripts in the computer (snapshots, the key proxy) --------------
    # As the computer's own unprivileged user. Docker: `docker exec`; Fly
    # (fly.py) goes through the supervisor's /exec routes instead.
    def exec_script(self, box: Sandbox, script: str, input: bytes | None = None,
                    timeout: float = 120) -> tuple[int, str, str]:
        """Run a short script; returns (exit code, output, errors)."""
        try:
            got = subprocess.run([self.docker, 'exec', *(['-i'] if input is not None else []), '-u', 'agent',  # noqa: S603
                                  box.name, 'sh', '-c', script], input=input, capture_output=True, timeout=timeout)
        except FileNotFoundError:
            raise Unsupported("The agents' computer needs Docker, and Docker isn't installed here.") from None
        except subprocess.TimeoutExpired:
            raise TimeoutError('It took too long.') from None
        return got.returncode, got.stdout.decode('utf-8', 'replace'), got.stderr.decode('utf-8', 'replace')

    def exec_out(self, box: Sandbox, script: str, out: Any, timeout: float = 3600) -> tuple[int, str]:
        """Run a script whose output (maybe large) goes into the file ``out``.
        Returns (exit code, the end of its errors)."""
        try:
            got = subprocess.run([self.docker, 'exec', '-u', 'agent', box.name, 'sh', '-c', script],  # noqa: S603
                                 stdout=out, stderr=subprocess.PIPE, timeout=timeout)
        except FileNotFoundError:
            raise Unsupported("The agents' computer needs Docker, and Docker isn't installed here.") from None
        except subprocess.TimeoutExpired:
            raise TimeoutError('It took too long.') from None
        return got.returncode, (got.stderr or b'')[-4000:].decode('utf-8', 'replace')

    def exec_in(self, box: Sandbox, script: str, source: Any, timeout: float = 3600) -> tuple[int, str]:
        """Run a script that reads the file ``source`` (maybe large) as input."""
        return self._run_with_input([self.docker, 'exec', '-i', '-u', 'agent', box.name, 'sh', '-c', script],
                                    source, timeout)

    @staticmethod
    def _run_with_input(args: list[str], source: Any, timeout: float) -> tuple[int, str]:
        try:
            got = subprocess.run(args, stdin=source, capture_output=True, timeout=timeout)  # noqa: S603
        except FileNotFoundError:
            raise Unsupported("The agents' computer needs Docker, and Docker isn't installed here.") from None
        except subprocess.TimeoutExpired:
            raise TimeoutError('It took too long.') from None
        return got.returncode, (got.stderr or b'')[-4000:].decode('utf-8', 'replace')

    def replace_home(self, user_id: Any, source: Any) -> None:
        """Empty the computer's home folder and unpack the archive file
        ``source`` into it, with the computer stopped. The caller starts it again."""
        name = self.container_name(user_id)
        with self._guard:
            self._docker('rm', '-f', name, check=False)
            self._boxes.pop(name, None)
        # A throwaway container on the same home volume, without network,
        # empties it and unpacks the snapshot as the agent user.
        from cognitrix.computer.sandbox.snapshots import EMPTY_HOME
        code, err = self._run_with_input(
            [self.docker, 'run', '--rm', '-i', '--network', 'none', '--cap-drop', 'ALL',
             '--security-opt', 'no-new-privileges', '-u', 'agent',
             '-v', f'cognitrix-sandbox-home-{user_key(user_id)}:/home/agent', '--entrypoint', 'sh', self.image,
             '-c', f'cd /home/agent && {EMPTY_HOME} && tar -xzf - --no-same-owner'], source, 3600)
        if code != 0:
            raise DriverError(f"Couldn't restore the snapshot: {err.strip()[-300:]}")

    # -- keys the agent never sees ---------------------------------------------
    def _proxy_addresses(self) -> tuple[str, str]:
        """(address the key proxy listens on, host containers reach it at).

        Docker Desktop (Windows, macOS) forwards host.docker.internal to this
        machine's loopback, so the proxy stays on 127.0.0.1. Docker on Linux
        reaches the host through the bridge network's gateway, so the proxy
        listens there (not on every interface). Both can be set with
        COGNITRIX_SANDBOX_PROXY_BIND / COGNITRIX_SANDBOX_PROXY_HOST."""
        if self._proxy_where is None:
            import sys
            bind = os.environ.get('COGNITRIX_SANDBOX_PROXY_BIND', '').strip()
            host = os.environ.get('COGNITRIX_SANDBOX_PROXY_HOST', '').strip()
            if not (bind and host):
                os_name = self._docker('info', '--format', '{{.OperatingSystem}}', check=False, timeout=20).stdout
                if sys.platform != 'linux' or 'Docker Desktop' in (os_name or ''):
                    bind, host = bind or '127.0.0.1', host or 'host.docker.internal'
                else:
                    got = self._docker('network', 'inspect', 'bridge', '--format',
                                       '{{range .IPAM.Config}}{{.Gateway}} {{end}}', check=False, timeout=20)
                    gateway = next((g for g in (got.stdout or '').split() if g.count('.') == 3), '')
                    bind, host = bind or gateway or '0.0.0.0', host or gateway or 'host.docker.internal'  # noqa: S104
            self._proxy_where = (bind, host)
        return self._proxy_where

    def _proxy_ports(self) -> tuple[int, ...]:
        """The ports the key proxy picks from; empty for any free one."""
        return ()

    def _install_ca(self, box: Sandbox, pem: bytes, fingerprint: str) -> None:
        """Put the key proxy's CA certificate (only the certificate) in the
        container, alone and appended to the system bundle."""
        if self._ca_installed.get(box.name) == f'{box.container_id}:{fingerprint}':
            return
        script = ('umask 022; d="$HOME/.cognitrix-sandbox/proxy"; mkdir -p "$d" && cat > "$d/ca.pem.tmp" '
                  '&& mv -f "$d/ca.pem.tmp" "$d/ca.pem" && cat /etc/ssl/certs/ca-certificates.crt "$d/ca.pem" '
                  '> "$d/bundle.pem.tmp" && mv -f "$d/bundle.pem.tmp" "$d/bundle.pem"')
        code, _, err = self.exec_script(box, script, input=pem, timeout=60)
        if code != 0:
            raise DriverError(f"Couldn't give the agents' computer the key proxy's certificate: {err.strip()[-300:]}")
        self._ca_installed[box.name] = f'{box.container_id}:{fingerprint}'

    def key_proxy_env(self, user_id: Any) -> dict[str, str]:
        """Variables that send a command's HTTPS through the key proxy, which
        fills in this person's hidden keys for their approved sites only."""
        from cognitrix.common.key_proxy import get_proxy, proxy_env

        box = self.sandbox(user_id)
        proxy = get_proxy()
        bind, host = self._proxy_addresses()
        ports = self._proxy_ports()
        try:
            _, port = proxy.ensure_started(bind, ports) if ports else proxy.ensure_started(bind)
        except OSError:
            if bind in ('0.0.0.0', '127.0.0.1', '::') or os.environ.get('COGNITRIX_SANDBOX_PROXY_BIND'):  # noqa: S104
                raise
            # The gateway isn't an address here (the engine runs in a
            # container itself): listen everywhere; the password still guards it.
            logger.info("Couldn't listen on %s for the key proxy; listening on all addresses", bind)
            self._proxy_where = ('0.0.0.0', host)  # noqa: S104
            _, port = (proxy.ensure_started('0.0.0.0', ports) if ports  # noqa: S104
                       else proxy.ensure_started('0.0.0.0'))  # noqa: S104
        self._install_ca(box, proxy.ca.pem, proxy.ca.fingerprint)
        token = proxy.token_for(user_key(user_id))
        folder = '/home/agent/.cognitrix-sandbox/proxy'
        return proxy_env(f'http://cognitrix:{token}@{host}:{port}', f'{folder}/bundle.pem', f'{folder}/ca.pem')

    def driver(self, user_id: Any, agent_id: Any = None) -> RemoteDriver:
        from cognitrix.projects.context import sandbox_identity
        box = self.sandbox(user_id)
        name = desktop_name(sandbox_identity(agent_id))
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
        kind = 'fly' if sandbox_mode() == 'fly' else 'docker'
        if _manager is None or _manager.kind != kind:
            if kind == 'fly':
                from cognitrix.computer.sandbox.fly import FlyManager
                _manager = FlyManager()
            else:
                _manager = SandboxManager()
        return _manager


def forget_fly_manager() -> None:
    """Drop a Fly manager after its settings change in Settings, so the next
    use builds one with the new token, app and region. Its machines keep
    running; the new manager finds them by their metadata."""
    global _manager
    with _manager_guard:
        if _manager is not None and _manager.kind == 'fly':
            stop = getattr(_manager, 'close', None)
            if callable(stop):
                stop()
            _manager = None


def sandbox_driver(actor: Actor | None = None) -> RemoteDriver:
    actor = actor or current_actor.get() or Actor()
    return get_manager().driver(actor.user_id, actor.agent_id)
