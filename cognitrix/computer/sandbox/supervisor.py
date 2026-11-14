"""The sandbox container's main process: one desktop per agent.

Each desktop is its own X display (``:1``, ``:2``, ...) with a window manager,
a live-screen server (x11vnc, password protected, view-only until a person
takes over) and a driver worker. Desktops share the container's files and
accessibility bus. Every request except /health needs the container's token
(``Authorization: Bearer <COGNITRIX_SANDBOX_TOKEN>``):

    GET    /health
    GET    /desktops                      list desktops
    POST   /desktops/<name>               start one (or return the running one)
    DELETE /desktops/<name>               stop one
    POST   /desktops/<name>/call          a driver call (see worker.py)
    POST   /desktops/<name>/hold          a person takes over: agent paused, screen accepts input
    POST   /desktops/<name>/release       hand back: screen view-only again
    GET    /apps                          apps and whether each is running (see apps.py)
    GET    /apps/<slug>                   one app's status
    POST   /apps/<slug>/deploy            unpack, install and start a version
    POST   /apps/<slug>/start|stop        wake / sleep
    POST   /apps/<slug>/promote           make a kept version live again
    GET    /apps/<slug>/logs              the end of the live version's output
    POST   /work/<agent>/run              run a shell command in the agent's folder (see workspace.py)
    GET    /work/<agent>/jobs             the agent's commands
    POST   /work/<agent>/jobs/<id>/read|wait|stop|full
    POST   /work/<agent>/files/<op>       read, write, edit, glob, grep, find
    GET    /work/<agent>/projects         folders copied in from the person's computer
    PUT    /work/<agent>/projects/<name>  copy a folder in (tar.gz body; ?replace=1)
    POST   /work/<agent>/projects/<name>/changes|export|synced
    DELETE /work/<agent>/projects/<name>
    DELETE /apps/<slug>                   remove an app, its versions and data
    *      /apps/<slug>/http/<path>       the app itself (woken if asleep); the
                                          token comes in X-Cognitrix-Sandbox-Token
                                          so the app's own Authorization passes through

The live screen is served by websockify on VIEWER_PORT; a desktop's stream is
``/websockify?token=<name>``. Each desktop has two VNC passwords: the
view-only one (returned by POST /desktops/<name>) and the full one (returned
only by .../hold), so the screen takes input only while a person has control.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cognitrix.computer.sandbox import SERVICE_PORT, check_name, token
from cognitrix.computer.sandbox.apps import AppHost, AppHostError, unb64
from cognitrix.computer.sandbox.workspace import Workspace, WorkspaceError

logger = logging.getLogger('cognitrix.sandbox')

SCREEN = os.environ.get('COGNITRIX_SANDBOX_SCREEN', '1280x800x24')
MAX_DESKTOPS = int(os.environ.get('COGNITRIX_SANDBOX_MAX_DESKTOPS', '8'))
STATE = os.environ.get('COGNITRIX_SANDBOX_STATE', '/tmp/cognitrix-sandbox')
TOKENS = os.path.join(STATE, 'tokens')


@dataclass
class Desktop:
    name: str
    number: int
    password: str  # view only
    full_password: str  # mouse and keyboard, handed out on hold
    procs: dict[str, subprocess.Popen] = field(default_factory=dict)
    held_by: str | None = None
    terminal: subprocess.Popen | None = None  # xterm following the agent's commands
    started: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    @property
    def display(self) -> str:
        return f':{self.number}'

    @property
    def vnc_port(self) -> int:
        return 5900 + self.number

    @property
    def worker_port(self) -> int:
        return 9100 + self.number

    def info(self) -> dict:
        return {'name': self.name, 'display': self.display, 'held_by': self.held_by,
                'started': self.started, 'last_used': self.last_used,
                'running': all(p.poll() is None for p in self.procs.values())}


class Supervisor:
    def __init__(self) -> None:
        self.desktops: dict[str, Desktop] = {}
        self._guard = threading.Lock()
        os.makedirs(TOKENS, exist_ok=True)

    def _free_number(self) -> int:
        used = {d.number for d in self.desktops.values()}
        return next(n for n in range(1, 1000) if n not in used)

    def ensure(self, name: str) -> Desktop:
        check_name(name)
        with self._guard:
            desk = self.desktops.get(name)
            if desk is not None and all(p.poll() is None for p in desk.procs.values()):
                desk.last_used = time.time()
                return desk
            if desk is not None:
                self._stop(desk)
            if len(self.desktops) >= MAX_DESKTOPS:
                raise RuntimeError(f'this computer already runs {MAX_DESKTOPS} desktops; stop one first')
            # VNC passwords are 8 characters at most.
            desk = Desktop(name, self._free_number(), secrets.token_hex(4), secrets.token_hex(4))
            self._start(desk)
            self.desktops[name] = desk
            return desk

    def _start(self, desk: Desktop) -> None:
        env = dict(os.environ, DISPLAY=desk.display, DESKTOP_NAME=desk.name)
        env.pop('COGNITRIX_SANDBOX_TOKEN', None)  # apps on the desktop never see the container token
        quiet = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'start_new_session': True}
        width, height = SCREEN.split('x')[:2]
        desk.procs['xvfb'] = subprocess.Popen(  # noqa: S603
            ['Xvfb', desk.display, '-screen', '0', SCREEN, '-nolisten', 'tcp'], env=env, **quiet)
        _wait_for(lambda: os.path.exists(f'/tmp/.X11-unix/X{desk.number}'), 10)
        desk.procs['wm'] = subprocess.Popen(['openbox'], env=env, **quiet)  # noqa: S607
        if shutil.which('xsetroot'):  # a calm grey instead of a black desktop
            subprocess.run(['xsetroot', '-solid', '#3a3d42'], env=env, timeout=5,  # noqa: S607
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pwfile = os.path.join(STATE, f'{desk.name}.vncpass')
        fd = os.open(pwfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as fh:  # full password, then the view-only one
            fh.write(f'{desk.full_password}\n__BEGIN_VIEWONLY__\n{desk.password}\n')
        desk.procs['vnc'] = subprocess.Popen(  # noqa: S603
            ['x11vnc', '-display', desk.display, '-rfbport', str(desk.vnc_port), '-localhost', '-passwdfile', pwfile,
             '-shared', '-forever', '-noxdamage', '-quiet', '-geometry', f'{width}x{height}'],
            env=env, **quiet)
        with open(os.path.join(TOKENS, desk.name), 'w') as fh:
            fh.write(f'{desk.name}: localhost:{desk.vnc_port}\n')
        desk.procs['worker'] = subprocess.Popen(  # noqa: S603
            [os.environ.get('COGNITRIX_SANDBOX_PYTHON', 'python3'), '-m', 'cognitrix.computer.sandbox.worker',
             '--port', str(desk.worker_port)], env=env, stdout=None, stderr=None, start_new_session=True)
        if not _wait_for(lambda: self._worker_up(desk), 20):
            logger.warning('desktop %s: worker did not start', desk.name)

    def _worker_up(self, desk: Desktop) -> bool:
        try:
            self._call_worker(desk, {'method': 'status'}, timeout=2)
            return True
        except OSError:
            return False

    def open_terminal(self, name: str, log: str) -> None:
        """Show the agent's commands in a terminal on its desktop (started if
        needed), so a person watching sees what runs."""
        if not shutil.which('xterm'):
            return
        desk = self.ensure(name)
        if desk.terminal is not None and desk.terminal.poll() is None:
            return
        if not os.path.exists(log):
            open(log, 'a').close()  # noqa: SIM115
        env = dict(os.environ, DISPLAY=desk.display)
        env.pop('COGNITRIX_SANDBOX_TOKEN', None)
        desk.terminal = subprocess.Popen(  # noqa: S603
            ['xterm', '-T', 'Terminal', '-class', 'CognitrixTerminal', '-fa', 'DejaVu Sans Mono', '-fs', '11',
             '-bg', '#1e1f22', '-fg', '#d8dade', '-cr', '#1e1f22', '-sl', '5000', '-b', '8',
             '-e', 'tail', '-n', '400', '-F', log],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def _stop(self, desk: Desktop) -> None:
        if desk.terminal is not None and desk.terminal.poll() is None:
            try:
                os.killpg(desk.terminal.pid, signal.SIGTERM)
            except OSError:
                pass
        for proc in reversed(list(desk.procs.values())):
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
        for path in (os.path.join(TOKENS, desk.name), os.path.join(STATE, f'{desk.name}.vncpass')):
            try:
                os.remove(path)
            except OSError:
                pass

    def stop(self, name: str) -> bool:
        with self._guard:
            desk = self.desktops.pop(check_name(name), None)
        if desk is None:
            return False
        self._stop(desk)
        return True

    def _call_worker(self, desk: Desktop, body: dict, timeout: float = 120) -> dict:
        req = urllib.request.Request(f'http://127.0.0.1:{desk.worker_port}/call', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed local URL)
            return json.loads(resp.read())

    def call(self, name: str, body: dict) -> dict:
        desk = self.desktops.get(check_name(name))
        if desk is None:
            return {'ok': False, 'error': 'no_desktop', 'message': f'no desktop called {name!r}'}
        desk.last_used = time.time()
        return self._call_worker(desk, body)

    def hold(self, name: str, by: str) -> dict:
        desk = self.desktops.get(check_name(name))
        if desk is None:
            return {'ok': False, 'error': 'no_desktop', 'message': f'no desktop called {name!r}'}
        out = self._call_worker(desk, {'method': 'hold', 'args': {'by': by}})
        if out.get('ok'):
            desk.held_by = by
            out['vnc_password'] = desk.full_password
        return out

    def release(self, name: str) -> dict:
        desk = self.desktops.get(check_name(name))
        if desk is None:
            return {'ok': False, 'error': 'no_desktop', 'message': f'no desktop called {name!r}'}
        desk.held_by = None
        return self._call_worker(desk, {'method': 'release'})

    def shutdown(self) -> None:
        for name in list(self.desktops):
            self.stop(name)


def _wait_for(check, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


APPS_ROOT = os.environ.get('COGNITRIX_APPS_ROOT', os.path.join(os.path.expanduser('~'), '.cognitrix-apps'))
_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers',
        'transfer-encoding', 'upgrade', 'host', 'x-cognitrix-sandbox-token', 'content-length'}


def proxy_to_app(handler: BaseHTTPRequestHandler, port: int, path: str, method: str) -> None:
    """Forward one request to an app on 127.0.0.1:port and stream the answer back."""
    import http.client

    length = int(handler.headers.get('Content-Length') or 0)
    body = handler.rfile.read(length) if length else None
    headers = {k: v for k, v in handler.headers.items() if k.lower() not in _HOP}
    headers['Host'] = f'127.0.0.1:{port}'
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=300)
    try:
        conn.request(method, path or '/', body=body, headers=headers)
        resp = conn.getresponse()
        handler.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in _HOP:
                handler.send_header(k, v)
        handler.send_header('Connection', 'close')
        handler.end_headers()
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            handler.wfile.write(chunk)
    finally:
        conn.close()
    handler.close_connection = True


def make_server(sup: Supervisor, port: int = SERVICE_PORT, host: str = '0.0.0.0',  # noqa: S104
                apps: AppHost | None = None, work: Workspace | None = None) -> ThreadingHTTPServer:
    secret = token()
    if not secret:
        raise SystemExit('COGNITRIX_SANDBOX_TOKEN is not set')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, status: int, payload) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authed(self) -> bool:
            got = self.headers.get('Authorization', '')
            if got.startswith('Bearer ') and hmac.compare_digest(got[7:].encode(), secret.encode()):
                return True
            alt = self.headers.get('X-Cognitrix-Sandbox-Token', '')
            if alt and hmac.compare_digest(alt.encode(), secret.encode()):
                return True
            self._reply(401, {'ok': False, 'error': 'unauthorized'})
            return False

        def _body(self) -> dict:
            length = int(self.headers.get('Content-Length') or 0)
            return json.loads(self.rfile.read(length) or b'{}') if length else {}

        def _route(self, method: str) -> None:
            if method == 'GET' and self.path == '/health':
                self._reply(200, {'ok': True})
                return
            if not self._authed():
                return
            parts = [p for p in self.path.split('?')[0].split('/') if p]
            if parts[:1] == ['apps']:
                self._apps(method, parts)
                return
            if parts[:1] == ['work']:
                self._work(method, parts)
                return
            try:
                if parts == ['desktops'] and method == 'GET':
                    self._reply(200, {'ok': True, 'desktops': [d.info() for d in sup.desktops.values()]})
                elif len(parts) == 2 and parts[0] == 'desktops' and method == 'POST':
                    desk = sup.ensure(parts[1])
                    self._reply(200, {'ok': True, 'desktop': desk.info(), 'vnc_password': desk.password})
                elif len(parts) == 2 and parts[0] == 'desktops' and method == 'DELETE':
                    self._reply(200, {'ok': sup.stop(parts[1])})
                elif len(parts) == 3 and parts[0] == 'desktops' and method == 'POST':
                    name, action = parts[1], parts[2]
                    if action == 'call':
                        self._reply(200, sup.call(name, self._body()))
                    elif action == 'hold':
                        self._reply(200, sup.hold(name, str(self._body().get('by') or 'the person')[:80]))
                    elif action == 'release':
                        self._reply(200, sup.release(name))
                    else:
                        self._reply(404, {'ok': False, 'error': 'not_found'})
                else:
                    self._reply(404, {'ok': False, 'error': 'not_found'})
            except ValueError as exc:
                self._reply(400, {'ok': False, 'error': 'bad_request', 'message': str(exc)})
            except RuntimeError as exc:
                self._reply(409, {'ok': False, 'error': 'limit', 'message': str(exc)})
            except OSError as exc:
                self._reply(502, {'ok': False, 'error': 'worker_unreachable', 'message': str(exc)})

        def _work(self, method: str, parts: list[str]) -> None:
            from urllib.parse import parse_qs, unquote, urlsplit

            if work is None:
                self._reply(404, {'ok': False, 'error': 'no_workspace', 'message': 'Coding is off on this computer.'})
                return
            try:
                parts = [unquote(p) for p in parts]
                agent = check_name(parts[1] if len(parts) > 1 else '')
                what = parts[2] if len(parts) > 2 else ''
                if what == 'run' and method == 'POST':
                    b = self._body()
                    self._reply(200, {'ok': True, **work.run(
                        agent, b.get('command', ''), b.get('cwd'), b.get('timeout') or 120, b.get('env') or {},
                        bool(b.get('background')), b.get('stdin'), b.get('label'))})
                elif what == 'jobs' and len(parts) == 3 and method == 'GET':
                    self._reply(200, {'ok': True, 'jobs': work.list_jobs(agent)})
                elif what == 'jobs' and len(parts) == 5 and method == 'POST':
                    job_id, action = parts[3], parts[4]
                    b = self._body()
                    if action == 'read':
                        self._reply(200, {'ok': True, **work.read(job_id, agent, b.get('filter'))})
                    elif action == 'wait':
                        self._reply(200, {'ok': True, **work.wait(job_id, agent, b.get('seconds') or 0)})
                    elif action == 'stop':
                        self._reply(200, {'ok': True, **work.stop(job_id, agent)})
                    elif action == 'full':
                        self._reply(200, {'ok': True, 'output': work.full_output(job_id, agent)})
                    else:
                        self._reply(404, {'ok': False, 'error': 'not_found'})
                elif what == 'files' and len(parts) == 4 and method == 'POST':
                    b = self._body()
                    op = parts[3]
                    if op == 'read':
                        out = work.read_file(agent, b.get('path', ''), b.get('start') or 1, b.get('end'),
                                             b.get('numbers', True), int(b.get('limit') or 30_000))
                    elif op == 'write':
                        out = work.write_file(agent, b.get('path', ''), b.get('content', ''), bool(b.get('append')))
                    elif op == 'edit':
                        out = work.edit_file(agent, b.get('path', ''), b.get('old', ''), b.get('new', ''),
                                             bool(b.get('replace_all')), bool(b.get('create')))
                    elif op == 'glob':
                        out = work.glob(agent, b.get('pattern', '*'), b.get('path') or '.',
                                        bool(b.get('include_dirs')), b.get('max_results') or 100)
                    elif op == 'grep':
                        out = work.grep(agent, b.get('pattern', ''), b.get('path') or '.', b.get('include'),
                                        b.get('exclude'), b.get('context') or 0, b.get('ignore_case', True),
                                        b.get('max_results') or 100)
                    elif op == 'find':
                        out = {'files': work.find_files(agent, b.get('path') or '.', float(b.get('since') or 0),
                                                        tuple(b.get('suffixes') or ('.png',)),
                                                        int(b.get('limit') or 4))}
                    elif op == 'where':
                        out = {'root': work.root(agent), 'cwd': work.cwd(agent), 'projects': work.projects(agent)}
                    else:
                        self._reply(404, {'ok': False, 'error': 'not_found'})
                        return
                    self._reply(200, {'ok': True, **out})
                elif what == 'projects' and len(parts) == 3 and method == 'GET':
                    self._reply(200, {'ok': True, 'projects': work.projects(agent)})
                elif what == 'projects' and len(parts) >= 4:
                    name = parts[3]
                    action = parts[4] if len(parts) > 4 else ''
                    if method == 'PUT' and not action:
                        length = int(self.headers.get('Content-Length') or 0)
                        data = self.rfile.read(length) if length else b''
                        q = parse_qs(urlsplit(self.path).query)
                        replace = (q.get('replace') or ['0'])[0] in ('1', 'true')
                        source = (q.get('source') or [''])[0]
                        self._reply(200, {'ok': True, **work.import_project(agent, name, data, replace, source)})
                    elif method == 'DELETE' and not action:
                        self._reply(200, work.remove_project(agent, name))
                    elif method == 'POST' and action == 'changes':
                        self._reply(200, {'ok': True, **work.changes(agent, name, bool(self._body().get('patch', True)))})
                    elif method == 'POST' and action == 'export':
                        self._reply(200, {'ok': True, **work.export(agent, name, self._body().get('paths') or None)})
                    elif method == 'POST' and action == 'synced':
                        self._reply(200, work.synced(agent, name, list(self._body().get('paths') or [])))
                    else:
                        self._reply(404, {'ok': False, 'error': 'not_found'})
                else:
                    self._reply(404, {'ok': False, 'error': 'not_found'})
            except WorkspaceError as exc:
                self._reply(400, {'ok': False, 'error': exc.code, 'message': exc.message})
            except (ValueError, KeyError, TypeError) as exc:
                self._reply(400, {'ok': False, 'error': 'bad_request', 'message': str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except OSError as exc:
                try:
                    self._reply(500, {'ok': False, 'error': 'os_error', 'message': str(exc)})
                except OSError:
                    pass

        def _apps(self, method: str, parts: list[str]) -> None:
            from urllib.parse import parse_qs, urlsplit

            if apps is None:
                self._reply(404, {'ok': False, 'error': 'no_apps', 'message': 'Apps are off on this computer.'})
                return
            try:
                if parts == ['apps'] and method == 'GET':
                    self._reply(200, {'ok': True, 'apps': apps.list()})
                    return
                slug = parts[1] if len(parts) > 1 else ''
                action = parts[2] if len(parts) > 2 else ''
                if action == 'http':
                    port_ = apps.port_for(slug, wake=True)
                    if not port_:
                        self._reply(503, {'ok': False, 'error': 'not_running', 'message': 'The app is not running.'})
                        return
                    raw = self.path.split('/', 4)
                    rest = '/' + (raw[4] if len(raw) > 4 else '')
                    proxy_to_app(self, port_, rest, method)
                    return
                if len(parts) == 2 and method == 'GET':
                    self._reply(200, apps.status(slug))
                elif len(parts) == 2 and method == 'DELETE':
                    self._reply(200, apps.remove(slug))
                elif action == 'logs' and method == 'GET':
                    q = parse_qs(urlsplit(self.path).query)
                    self._reply(200, {'ok': True, 'log': apps.logs(slug, int((q.get('lines') or ['200'])[0]),
                                                                    int((q.get('version') or ['0'])[0]))})
                elif method == 'POST' and action == 'deploy':
                    b = self._body()
                    self._reply(200, apps.deploy(slug, int(b.get('version') or 1), unb64(b.get('archive', '')),
                                                 b.get('command', ''), b.get('install', ''), b.get('env') or {},
                                                 int(b.get('port') or 0)))
                elif method == 'POST' and action == 'start':
                    self._reply(200, apps.start(slug))
                elif method == 'POST' and action == 'stop':
                    self._reply(200, apps.stop(slug))
                elif method == 'POST' and action == 'promote':
                    self._reply(200, apps.promote(slug, int(self._body().get('version') or 0)))
                else:
                    self._reply(404, {'ok': False, 'error': 'not_found'})
            except AppHostError as exc:
                self._reply(400, {'ok': False, 'error': exc.code, 'message': exc.message})
            except (ValueError, KeyError) as exc:
                self._reply(400, {'ok': False, 'error': 'bad_request', 'message': str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except OSError as exc:
                try:
                    self._reply(502, {'ok': False, 'error': 'app_unreachable', 'message': str(exc)})
                except OSError:
                    pass

        def do_GET(self):  # noqa: N802
            self._route('GET')

        def do_PUT(self):  # noqa: N802
            self._route('PUT')

        def do_PATCH(self):  # noqa: N802
            self._route('PATCH')

        def do_HEAD(self):  # noqa: N802
            self._route('HEAD')

        def do_OPTIONS(self):  # noqa: N802
            self._route('OPTIONS')

        def do_POST(self):  # noqa: N802
            self._route('POST')

        def do_DELETE(self):  # noqa: N802
            self._route('DELETE')

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sup = Supervisor()
    apps = AppHost(APPS_ROOT)

    def show_terminal(agent: str, log: str) -> None:
        try:
            sup.open_terminal(agent, log)
        except Exception:  # the desktop limit, or no X: commands still run
            logger.info('no terminal for %s', agent, exc_info=True)

    work = Workspace(terminal_opener=show_terminal)
    server = make_server(sup, apps=apps, work=work)
    signal.signal(signal.SIGTERM, lambda *_: (sup.shutdown(), apps.shutdown(), work.shutdown(), os._exit(0)))
    logger.info('sandbox supervisor on :%d (x11vnc %s)', SERVICE_PORT, shutil.which('x11vnc') or 'missing')
    server.serve_forever()


if __name__ == '__main__':
    main()
