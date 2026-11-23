"""The agents' sandboxed computer hosted on Fly.io.

With ``COGNITRIX_COMPUTER_SANDBOX=fly``, each user's computer is a Fly Machine
running the same image as local Docker (``sandbox/desktop/Dockerfile``), in
its own Fly app, with a Fly volume as its home folder (files, downloads,
browser logins). Inside it, each agent still gets its own desktop.

- **Wakes on the next request.** Anything that needs the computer (a computer
  tool, Run in sandbox, an App, the live screen's Wake button) calls
  ``sandbox()``, which starts a stopped or suspended machine and waits for it.
- **Sleeps when idle.** A background thread asks each running machine how
  long since it was last used (``GET /activity`` on the supervisor) and stops
  (or suspends) it after ``COGNITRIX_SANDBOX_IDLE_MINUTES`` (default 15) with
  nothing running and nobody watching its screen.
- **Private.** Machines get no public address. The engine reaches them over
  Fly's private network (6PN), so the engine must run on Fly in the same
  organisation. Every call still needs the machine's own token.

Settings (Settings > Computers; names as in cognitrix/app_settings.py):

    COGNITRIX_COMPUTER_SANDBOX=fly
    COGNITRIX_FLY_API_TOKEN           a token that can manage the sandbox app
    COGNITRIX_SANDBOX_FLY_APP         the Fly app the machines live in
    COGNITRIX_SANDBOX_FLY_REGION      default: the engine's own region (FLY_REGION)
    COGNITRIX_SANDBOX_FLY_CPUS        default 2 (shared CPUs)
    COGNITRIX_SANDBOX_FLY_CPU_KIND    default shared
    COGNITRIX_SANDBOX_FLY_MEMORY_MB   default 4096
    COGNITRIX_SANDBOX_FLY_VOLUME_GB   default 10 (the home folder)
    COGNITRIX_SANDBOX_IDLE_MINUTES    default 15; 0 never stops them
    COGNITRIX_SANDBOX_FLY_IDLE        stop (default) or suspend (keeps open
                                      windows; Fly limits which machines can)
    COGNITRIX_SANDBOX_IMAGE           the image, as for Docker

See docs/sandbox-on-fly.md for the one-time setup.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
import threading
import time
from typing import Any

import httpx

from cognitrix.app_settings import get as app_setting
from cognitrix.computer import DriverError, Unsupported
from cognitrix.computer.sandbox import SERVICE_PORT, TOKEN_ENV, VIEWER_PORT
from cognitrix.computer.sandbox.client import IMAGE, Sandbox, SandboxManager, user_key

logger = logging.getLogger('cognitrix.sandbox.fly')

API_URL = os.environ.get('COGNITRIX_FLY_API_URL', 'https://api.machines.dev/v1')
META_USER = 'cognitrix_user'
META_ROLE = 'cognitrix_sandbox'
HOME = '/home/agent'
# Started as root so the volume (mounted owned by root) can be handed to the
# agent user; everything after that runs as 'agent' with no privileges.
ENTRYPOINT = ['/usr/bin/tini', '--', '/usr/local/bin/cognitrix-sandbox-root']


def _int_env(name: str, default: int) -> int:
    try:
        return int(app_setting(name, '') or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(app_setting(name, '') or default)
    except ValueError:
        return default


def fly_token() -> str:
    return (app_setting('COGNITRIX_FLY_API_TOKEN') or '').strip()


def fly_app() -> str:
    return (app_setting('COGNITRIX_SANDBOX_FLY_APP') or '').strip()


def fly_region() -> str:
    return (app_setting('COGNITRIX_SANDBOX_FLY_REGION') or os.environ.get('FLY_REGION') or '').strip()


def idle_minutes() -> float:
    return _float_env('COGNITRIX_SANDBOX_IDLE_MINUTES', 15)


def idle_action() -> str:
    return 'suspend' if app_setting('COGNITRIX_SANDBOX_FLY_IDLE', '').strip().lower() == 'suspend' else 'stop'


def fly_settings() -> dict[str, Any]:
    """What Settings shows about the hosted computer (never the token)."""
    missing = [name for name, value in (('Fly API token', fly_token()), ('Fly app name', fly_app()),
                                        ('Region', fly_region())) if not value]
    return {'app': fly_app(), 'region': fly_region(), 'configured': not missing, 'missing': missing,
            'idle_minutes': idle_minutes(), 'idle_action': idle_action(),
            'size': f"{_int_env('COGNITRIX_SANDBOX_FLY_CPUS', 2)} CPU, "
                    f"{_int_env('COGNITRIX_SANDBOX_FLY_MEMORY_MB', 4096)} MB"}


class FlyError(DriverError):
    pass


class FlyMachines:
    """The few Fly Machines API calls the sandbox needs."""

    def __init__(self, token: str, app: str, base: str = API_URL, client: httpx.Client | None = None):
        self.app = app
        self.base = base.rstrip('/')
        self._client = client or httpx.Client(timeout=90, headers={'Authorization': f'Bearer {token}'})

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None,
              ok_missing: bool = False, timeout: float | None = None) -> Any:
        url = f'{self.base}/apps/{self.app}{path}'
        try:
            resp = self._client.request(method, url, json=body, params=params,
                                        timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
        except httpx.HTTPError as exc:
            raise FlyError(f"Couldn't reach Fly ({type(exc).__name__}). Try again in a moment.") from None
        if resp.status_code == 404 and ok_missing:
            return None
        if resp.status_code >= 400:
            try:
                detail = resp.json().get('error') or resp.text
            except ValueError:
                detail = resp.text
            if resp.status_code in (401, 403):
                raise Unsupported("Fly refused the server's token for the agents' computer. Check the Fly API token "
                                  'and app name in Settings, Computers.')
            raise FlyError(f"Fly couldn't do that: {str(detail)[:300]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def machines(self) -> list[dict]:
        return list(self._call('GET', '/machines') or [])

    def machine(self, machine_id: str) -> dict | None:
        return self._call('GET', f'/machines/{machine_id}', ok_missing=True)

    def create(self, body: dict) -> dict:
        return self._call('POST', '/machines', body, timeout=120)

    def update(self, machine_id: str, body: dict) -> dict:
        return self._call('POST', f'/machines/{machine_id}', body, timeout=120)

    def start(self, machine_id: str) -> None:
        self._call('POST', f'/machines/{machine_id}/start')

    def stop(self, machine_id: str) -> None:
        self._call('POST', f'/machines/{machine_id}/stop', {})

    def suspend(self, machine_id: str) -> None:
        self._call('POST', f'/machines/{machine_id}/suspend')

    def wait(self, machine_id: str, state: str = 'started', seconds: int = 60) -> None:
        self._call('GET', f'/machines/{machine_id}/wait', params={'state': state, 'timeout': min(int(seconds), 60)},
                   timeout=seconds + 15)

    def destroy(self, machine_id: str) -> None:
        self._call('DELETE', f'/machines/{machine_id}', params={'force': 'true'}, ok_missing=True)

    def volumes(self) -> list[dict]:
        return list(self._call('GET', '/volumes') or [])

    def create_volume(self, name: str, region: str, size_gb: int) -> dict:
        return self._call('POST', '/volumes', {'name': name, 'region': region, 'size_gb': size_gb,
                                               'encrypted': True})

    def delete_volume(self, volume_id: str) -> None:
        self._call('DELETE', f'/volumes/{volume_id}', ok_missing=True)


def _meta(machine: dict) -> dict:
    return ((machine.get('config') or {}).get('metadata') or {})


def _token_of(machine: dict) -> str:
    return str(((machine.get('config') or {}).get('env') or {}).get(TOKEN_ENV) or '')


class FlyManager(SandboxManager):
    """Starts, finds, stops and wakes users' computers as Fly Machines."""

    kind = 'fly'

    def __init__(self, image: str = IMAGE, api: FlyMachines | None = None, start_reaper: bool = True):
        super().__init__(image=image)
        if api is None:
            if not fly_token() or not fly_app():
                api = None
            else:
                api = FlyMachines(fly_token(), fly_app())
        self.api = api
        self._ids: dict[str, str] = {}  # user key -> machine id
        self._last_used: dict[str, float] = {}  # machine id -> time.time(), the engine's own view
        self._reaper: threading.Thread | None = None
        self._stop_reaper = threading.Event()
        if start_reaper and api is not None and idle_minutes() > 0:
            self._reaper = threading.Thread(target=self._reap_loop, name='sandbox-fly-idle', daemon=True)
            self._reaper.start()

    # -- helpers ------------------------------------------------------------
    def _need_api(self) -> FlyMachines:
        if self.api is None:
            raise Unsupported("The agents' computer is set to run on Fly, but the server has no Fly settings. "
                              'Add a Fly API token and app name in Settings, Computers (see docs/sandbox-on-fly.md).')
        return self.api

    def machine_name(self, user_id: Any) -> str:
        return f'sandbox-{user_key(user_id)}'

    @staticmethod
    def volume_name(user_id: Any) -> str:
        return f'home_{user_key(user_id)}'  # Fly volume names: letters, digits, underscores

    def _find(self, user_id: Any) -> dict | None:
        api = self._need_api()
        key = user_key(user_id)
        known = self._ids.get(key)
        if known:
            got = api.machine(known)
            if got and got.get('state') != 'destroyed':
                return got
            self._ids.pop(key, None)
        for machine in api.machines():
            if machine.get('state') in ('destroyed', 'destroying'):
                continue
            if _meta(machine).get(META_USER) == key or machine.get('name') == self.machine_name(user_id):
                self._ids[key] = machine['id']
                return machine
        return None

    def _volume(self, user_id: Any, region: str) -> dict:
        api = self._need_api()
        name = self.volume_name(user_id)
        for vol in api.volumes():
            if vol.get('name') == name and vol.get('state') not in ('destroyed', 'pending_destroy') \
                    and not vol.get('attached_machine_id'):
                return vol
        return api.create_volume(name, region, _int_env('COGNITRIX_SANDBOX_FLY_VOLUME_GB', 10))

    def machine_config(self, user_id: Any, token: str, volume_id: str) -> dict:
        return {
            'image': self.image,
            'env': {
                TOKEN_ENV: token,
                # Fly's private network is IPv6.
                'COGNITRIX_SANDBOX_LISTEN': '::',
                'COGNITRIX_SANDBOX_VIEWER_LISTEN': f'[::]:{VIEWER_PORT}',
            },
            'guest': {
                'cpu_kind': app_setting('COGNITRIX_SANDBOX_FLY_CPU_KIND', 'shared') or 'shared',
                'cpus': _int_env('COGNITRIX_SANDBOX_FLY_CPUS', 2),
                'memory_mb': _int_env('COGNITRIX_SANDBOX_FLY_MEMORY_MB', 4096),
            },
            'mounts': [{'volume': volume_id, 'path': HOME}],
            'processes': [{'name': 'sandbox', 'entrypoint': ENTRYPOINT, 'user': 'root'}],
            'restart': {'policy': 'on-failure', 'max_retries': 3},
            'metadata': {META_USER: user_key(user_id), META_ROLE: '1'},
            'auto_destroy': False,
            # No services: the machine has no public address at all.
        }

    def _create(self, user_id: Any) -> dict:
        api = self._need_api()
        region = fly_region()
        if not region:
            raise Unsupported('Set a region in Settings, Computers (or run the server on Fly) so the agents\' '
                              'computer knows where to start.')
        volume = self._volume(user_id, region)
        token = secrets.token_urlsafe(24)
        machine = api.create({'name': self.machine_name(user_id), 'region': volume.get('region') or region,
                              'config': self.machine_config(user_id, token, volume['id'])})
        self._ids[user_key(user_id)] = machine['id']
        return machine

    def _box(self, user_id: Any, machine: dict) -> Sandbox:
        name = self.machine_name(user_id)
        host = machine.get('private_ip') or f"{machine['id']}.vm.{self.api.app}.internal"
        token = _token_of(machine)
        box = self._boxes.get(name)
        if box is None or box.token != token or box.machine_id != machine['id'] or host not in box.api:
            box = Sandbox(name, token, SERVICE_PORT, VIEWER_PORT, host=host)
            box.machine_id = machine['id']
            box.container_id = machine['id']
            self._boxes[name] = box
        box.image_id = str((machine.get('config') or {}).get('image') or '')
        return box

    # -- the manager's calls ----------------------------------------------------
    def sandbox(self, user_id: Any) -> Sandbox:
        """The user's computer, created or woken if needed."""
        api = self._need_api()
        with self._guard:
            machine = self._find(user_id)
            if machine is None:
                machine = self._create(user_id)
            machine = self._settle(machine)
            if machine.get('state') in ('stopped', 'suspended'):
                if self._outdated_machine(machine):
                    api.update(machine['id'], {'config': {**machine['config'], 'image': self.image}})
                    machine = api.machine(machine['id']) or machine
                if machine.get('state') in ('stopped', 'suspended'):
                    api.start(machine['id'])
            if machine.get('state') != 'started':
                api.wait(machine['id'], 'started', 60)
                machine = api.machine(machine['id']) or machine
            box = self._box(user_id, machine)
        self._last_used[box.machine_id] = time.time()
        self._wait_healthy(box, 90)
        return box

    def _settle(self, machine: dict, seconds: float = 45) -> dict:
        """Wait out a stop or suspend that is under way, so it can be started."""
        deadline = time.monotonic() + seconds
        while machine.get('state') in ('stopping', 'suspending', 'replacing', 'updating') \
                and time.monotonic() < deadline:
            time.sleep(1)
            machine = self.api.machine(machine['id']) or machine
        return machine

    def _outdated_machine(self, machine: dict) -> bool:
        """The server now names another image (e.g. a new pinned version)."""
        return self._differs(str((machine.get('config') or {}).get('image') or ''))

    def _differs(self, current: str) -> bool:
        # A digest-pinned setting is compared whole; a tag is compared without
        # any digest Fly may have added when it resolved it.
        if not current:
            return False
        if '@' in self.image:
            return current != self.image
        return current.split('@')[0] != self.image

    def existing(self, user_id: Any) -> Sandbox | None:
        """The user's computer when it is running; never starts one."""
        machine = self._find(user_id)
        if machine is None or machine.get('state') != 'started':
            return None
        box = self._box(user_id, machine)
        return box

    def asleep(self, user_id: Any) -> bool:
        machine = self._find(user_id)
        return bool(machine and machine.get('state') in ('stopped', 'suspended', 'suspending', 'stopping'))

    def stop(self, user_id: Any) -> None:
        machine = self._find(user_id)
        if machine is not None and machine.get('state') == 'started':
            self._need_api().stop(machine['id'])
        self._boxes.pop(self.machine_name(user_id), None)

    def update(self, user_id: Any) -> Sandbox:
        """Restart the user's computer on the image the server names now
        (Fly looks the tag up again). Files and logins stay on the volume."""
        api = self._need_api()
        try:
            old = self.existing(user_id)
            names = [d['name'] for d in (old.desktops() if old else []) if d.get('name')]
        except Exception:
            names = []
        machine = self._find(user_id)
        if machine is None:
            return self.sandbox(user_id)
        with self._guard:
            api.update(machine['id'], {'config': {**machine['config'], 'image': self.image}})
            self._boxes.pop(self.machine_name(user_id), None)
        box = self.sandbox(user_id)
        for desktop in names:
            try:
                box.desktop(desktop)
            except Exception:
                logger.info("Couldn't reopen desktop %s after the update", desktop, exc_info=True)
        return box

    def reset(self, user_id: Any) -> None:
        """Destroy the machine and its home volume: a fresh computer next time."""
        api = self._need_api()
        machine = self._find(user_id)
        if machine is not None:
            api.destroy(machine['id'])
        self._ids.pop(user_key(user_id), None)
        self._boxes.pop(self.machine_name(user_id), None)
        name = self.volume_name(user_id)
        for _ in range(10):  # the volume detaches a moment after the machine goes
            vols = [v for v in api.volumes() if v.get('name') == name and v.get('state') not in ('destroyed',)]
            if not vols:
                return
            if all(not v.get('attached_machine_id') for v in vols):
                for vol in vols:
                    api.delete_volume(vol['id'])
                return
            time.sleep(1)
        logger.warning('Volume %s is still attached; it will be reused next time', name)

    # Newer images: Fly resolves the tag each time a machine is updated, so
    # there's no background pull here. Pin a version in COGNITRIX_SANDBOX_IMAGE
    # and stopped machines move to it when they next wake.
    def update_available(self, box: Sandbox) -> bool:
        return self._differs(box.image_id)

    def check_for_update(self, force: bool = False) -> bool:
        return False

    # -- running scripts in the computer (snapshots, the key proxy) ------------
    # There's no `docker exec` into a Fly Machine, so these go through the
    # supervisor's /exec routes (as the computer's own unprivileged user).
    def _docker(self, *args: str, check: bool = True, timeout: float = 120, input: str | None = None):
        raise Unsupported("That needs local Docker, and this server's agent computers run on Fly.")

    @staticmethod
    def _exec_answer(resp: httpx.Response) -> None:
        if resp.status_code == 404:
            raise Unsupported("The agents' computer is on an older image: update it (Computer panel, Update) "
                              'and try again.')
        if resp.status_code != 200:
            raise DriverError(f"The agent's computer answered {resp.status_code}.")

    def exec_script(self, box: Sandbox, script: str, input: bytes | None = None,
                    timeout: float = 120) -> tuple[int, str, str]:
        body: dict[str, Any] = {'script': script, 'timeout': timeout}
        if input is not None:
            body['input'] = base64.b64encode(input).decode()
        try:
            resp = box._client.post(box.api + '/exec', json=body, timeout=timeout + 30)
        except httpx.TimeoutException:
            raise TimeoutError('It took too long.') from None
        except httpx.HTTPError as exc:
            raise DriverError(f"The agent's computer is not responding ({type(exc).__name__}).") from None
        self._exec_answer(resp)
        out = resp.json()
        return int(out.get('code', 1)), str(out.get('stdout') or ''), str(out.get('stderr') or '')

    def exec_out(self, box: Sandbox, script: str, out: Any, timeout: float = 3600) -> tuple[int, str]:
        try:
            with box._client.stream('POST', box.api + '/exec/out', json={'script': script},
                                    timeout=httpx.Timeout(timeout, connect=30)) as resp:
                if resp.status_code != 200:
                    resp.read()
                self._exec_answer(resp)
                for chunk in resp.iter_bytes(1 << 20):
                    out.write(chunk)
                code = int(resp.headers.get('X-Exit-Code', '1'))
                err = base64.b64decode(resp.headers.get('X-Stderr', '')).decode('utf-8', 'replace')
        except httpx.TimeoutException:
            raise TimeoutError('It took too long.') from None
        except httpx.HTTPError as exc:
            raise DriverError(f"The agent's computer stopped answering ({type(exc).__name__}).") from None
        return code, err

    def exec_in(self, box: Sandbox, script: str, source: Any, timeout: float = 3600) -> tuple[int, str]:
        start = source.tell()
        size = source.seek(0, os.SEEK_END) - start
        source.seek(start)

        def chunks():
            while True:
                chunk = source.read(1 << 20)
                if not chunk:
                    return
                yield chunk

        headers = {'X-Exec-Script': base64.b64encode(script.encode()).decode(),
                   'Content-Length': str(size), 'Content-Type': 'application/octet-stream'}
        try:
            resp = box._client.post(box.api + '/exec/in', content=chunks(), headers=headers,
                                    timeout=httpx.Timeout(timeout, connect=30))
        except httpx.TimeoutException:
            raise TimeoutError('It took too long.') from None
        except httpx.HTTPError as exc:
            raise DriverError(f"The agent's computer stopped answering ({type(exc).__name__}).") from None
        self._exec_answer(resp)
        out = resp.json()
        return int(out.get('code', 1)), str(out.get('stderr') or '')

    def replace_home(self, user_id: Any, source: Any) -> None:
        """Close every desktop, then empty the home folder and unpack the
        snapshot in place; the machine is stopped after, so nothing that was
        running keeps old files open. The next request wakes it."""
        box = self.sandbox(user_id)
        for desktop in box.desktops():
            if desktop.get('name'):
                try:
                    box.request('DELETE', f"/desktops/{desktop['name']}", timeout=30)
                except DriverError:
                    logger.info("Couldn't close desktop %s before the restore", desktop['name'], exc_info=True)
        code, err = self.exec_in(box, 'cd /home/agent && find . -mindepth 1 -delete && '
                                      'tar -xzf - --no-same-owner', source)
        if code != 0:
            raise DriverError(f"Couldn't restore the snapshot: {err.strip()[-300:]}")
        machine = self._find(user_id)
        if machine is not None and machine.get('state') == 'started':
            api = self._need_api()
            api.stop(machine['id'])
            try:
                api.wait(machine['id'], 'stopped', 60)
            except Exception:
                logger.info('Agent computer %s took a while to stop after the restore', machine['id'], exc_info=True)
        self._boxes.pop(self.machine_name(user_id), None)

    # -- keys the agent never sees ---------------------------------------------
    def _proxy_addresses(self) -> tuple[str, str]:
        """The key proxy runs in the engine. Machines reach the engine over
        Fly's private network, so it listens on IPv6 (every proxied request
        still needs this person's own proxy password)."""
        if self._proxy_where is None:
            bind = os.environ.get('COGNITRIX_SANDBOX_PROXY_BIND', '').strip() or '::'
            host = os.environ.get('COGNITRIX_SANDBOX_PROXY_HOST', '').strip()
            if not host:
                private = os.environ.get('FLY_PRIVATE_IP', '').strip()
                app = os.environ.get('FLY_APP_NAME', '').strip()
                host = f'[{private}]' if private else (f'{app}.internal' if app else '')
            if not host:
                raise Unsupported("Hidden keys on Fly need the server to run on Fly (or set "
                                  'COGNITRIX_SANDBOX_PROXY_HOST to an address the computers can reach).')
            self._proxy_where = (bind, host)
        return self._proxy_where

    # -- idle shutdown ------------------------------------------------------------
    def _reap_loop(self) -> None:
        while not self._stop_reaper.wait(60):
            try:
                self.stop_idle()
            except Exception:
                logger.info('Idle check for agent computers failed', exc_info=True)

    def stop_idle(self, now: float | None = None) -> list[str]:
        """Stop (or suspend) running machines nobody used for a while.
        Returns the ids it stopped."""
        limit = idle_minutes() * 60
        if limit <= 0 or self.api is None:
            return []
        now = time.time() if now is None else now
        stopped = []
        for machine in self.api.machines():
            meta = _meta(machine)
            if machine.get('state') != 'started' or meta.get(META_ROLE) != '1':
                continue
            mid = machine['id']
            key = meta.get(META_USER, '')
            if self._viewers.get(key, 0) > 0:
                continue  # someone is watching its screen
            idle = self._idle_seconds(machine, now)
            if idle is None or idle < limit:
                continue
            try:
                if idle_action() == 'suspend':
                    try:
                        self.api.suspend(mid)
                    except FlyError:
                        self.api.stop(mid)  # this machine can't suspend
                else:
                    self.api.stop(mid)
                stopped.append(mid)
                logger.info('Agent computer %s was idle %.0f min; %s it', mid, idle / 60, idle_action())
            except Exception:
                logger.info("Couldn't stop idle agent computer %s", mid, exc_info=True)
        return stopped

    def _idle_seconds(self, machine: dict, now: float) -> float | None:
        """How long since it was last used, or None when it's busy."""
        mid = machine['id']
        engine_idle = now - self._last_used.setdefault(mid, now)  # first seen now: give it a full period
        out = self._activity(machine)
        if out is None:
            return engine_idle  # not answering, or an older image without /activity
        if out.get('busy'):
            self._last_used[mid] = now
            return None
        try:
            return min(engine_idle, float(out.get('idle_s', engine_idle)))
        except (TypeError, ValueError):
            return engine_idle

    def _activity(self, machine: dict) -> dict | None:
        host = machine.get('private_ip') or f"{machine['id']}.vm.{self.api.app}.internal"
        address = f'[{host}]' if ':' in host else host
        try:
            resp = httpx.get(f'http://{address}:{SERVICE_PORT}/activity', timeout=5,
                             headers={'Authorization': f'Bearer {_token_of(machine)}'})
            out = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        return out if isinstance(out, dict) and out.get('ok') else None

    def close(self) -> None:
        self._stop_reaper.set()
