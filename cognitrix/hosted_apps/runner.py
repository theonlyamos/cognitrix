"""Where apps run.

- ``SandboxRunner``: in the person's sandbox computer (a Docker container,
  see ``cognitrix/computer/sandbox``). The usual case, and the only one where
  an app's code is kept away from this machine.
- ``LocalRunner``: on the machine Cognitrix runs on, when the sandboxed
  computer is off. Publishing then asks the person first, like the shell.

Both have the same blocking methods (call them from a thread) and the same
``AppHost`` underneath; ``target()`` says where the proxy sends requests.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from cognitrix.computer.sandbox.apps import INSTALL_TIMEOUT, START_TIMEOUT, AppHost, AppHostError, b64

DEPLOY_TIMEOUT = INSTALL_TIMEOUT + START_TIMEOUT + 120


class RunnerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def apps_enabled() -> bool:
    return os.getenv('COGNITRIX_APPS', '').strip().lower() not in ('off', '0', 'false', 'no')


def local_allowed() -> bool:
    """Apps may run on this machine when the sandbox is off, unless turned off
    (servers people share usually want ``COGNITRIX_APPS_LOCAL=off``)."""
    return os.getenv('COGNITRIX_APPS_LOCAL', '').strip().lower() not in ('off', '0', 'false', 'no')


def default_runtime() -> str | None:
    """'sandbox', 'local', or None when apps can't run here."""
    if not apps_enabled():
        return None
    from cognitrix.computer.sandbox.client import sandbox_mode

    if sandbox_mode():
        return 'sandbox'
    return 'local' if local_allowed() else None


_local_host: AppHost | None = None
_local_guard = threading.Lock()


def local_host() -> AppHost:
    global _local_host
    with _local_guard:
        if _local_host is None:
            from cognitrix.config import settings

            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('COGNITRIX_', 'JWT_')) and not k.endswith(('_API_KEY', '_TOKEN', '_SECRET'))}
            _local_host = AppHost(str(Path(settings.workdir) / 'apps-host'), base_env=env)
        return _local_host


class LocalRunner:
    runtime = 'local'

    def __init__(self) -> None:
        self.host = local_host()

    def _do(self, fn, *args):
        try:
            return fn(*args)
        except AppHostError as exc:
            raise RunnerError(exc.code, exc.message) from None

    def deploy(self, slug: str, version: int, archive: bytes, command: str, install: str, env: dict,
               port: int) -> dict:
        return self._do(self.host.deploy, slug, version, archive, command, install, env, port)

    def start(self, slug: str) -> dict:
        return self._do(self.host.start, slug)

    def stop(self, slug: str) -> dict:
        return self._do(self.host.stop, slug)

    def promote(self, slug: str, version: int) -> dict:
        return self._do(self.host.promote, slug, version)

    def remove(self, slug: str) -> dict:
        return self._do(self.host.remove, slug)

    def status(self, slug: str) -> dict:
        return self._do(self.host.status, slug)

    def logs(self, slug: str, lines: int = 200, version: int = 0) -> str:
        return self._do(self.host.logs, slug, lines, version)

    def target(self, slug: str) -> tuple[str, dict[str, str]]:
        port = self._do(self.host.port_for, slug, True)
        if not port:
            raise RunnerError('not_running', 'The app did not start.')
        return f'http://127.0.0.1:{port}', {}


class SandboxRunner:
    runtime = 'sandbox'

    def __init__(self, owner_id: Any):
        from cognitrix.computer import DriverError, Unsupported
        from cognitrix.computer.sandbox.client import get_manager

        try:
            self.box = get_manager().sandbox(owner_id)
        except (DriverError, Unsupported) as exc:
            raise RunnerError('no_sandbox', str(exc)) from None

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        from cognitrix.computer import DriverError

        try:
            out = self.box.request(method, path, body, timeout=timeout)
        except DriverError as exc:
            msg = str(exc)
            if 'not_found' in msg or 'Apps are off' in msg:
                msg = ("The agents' computer is running an older image without Apps. Restart it from "
                       'Settings > Computer to update it.')
            raise RunnerError('sandbox_error', msg) from None
        if not out.get('ok') and out.get('error') and out.get('status') != 'failed':
            raise RunnerError(str(out.get('error')), str(out.get('message') or out.get('error')))
        return out

    def deploy(self, slug: str, version: int, archive: bytes, command: str, install: str, env: dict,
               port: int) -> dict:
        return self._req('POST', f'/apps/{slug}/deploy', {
            'version': version, 'archive': b64(archive), 'command': command, 'install': install,
            'env': env, 'port': port}, timeout=DEPLOY_TIMEOUT)

    def start(self, slug: str) -> dict:
        return self._req('POST', f'/apps/{slug}/start', {}, timeout=START_TIMEOUT + 30)

    def stop(self, slug: str) -> dict:
        return self._req('POST', f'/apps/{slug}/stop', {})

    def promote(self, slug: str, version: int) -> dict:
        return self._req('POST', f'/apps/{slug}/promote', {'version': version}, timeout=START_TIMEOUT + 30)

    def remove(self, slug: str) -> dict:
        return self._req('DELETE', f'/apps/{slug}')

    def status(self, slug: str) -> dict:
        return self._req('GET', f'/apps/{slug}')

    def logs(self, slug: str, lines: int = 200, version: int = 0) -> str:
        return str(self._req('GET', f'/apps/{slug}/logs?lines={int(lines)}&version={int(version)}').get('log') or '')

    def target(self, slug: str) -> tuple[str, dict[str, str]]:
        return f'{self.box.api}/apps/{slug}/http', {'X-Cognitrix-Sandbox-Token': self.box.token}


def runner_for(runtime: str, owner_id: Any):
    if runtime == 'local':
        if not local_allowed():
            raise RunnerError('local_off', 'Apps on this machine are turned off (COGNITRIX_APPS_LOCAL=off).')
        return LocalRunner()
    return SandboxRunner(owner_id)
