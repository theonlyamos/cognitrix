"""The sandbox container's main process: one desktop per agent.

Each desktop is its own X display (``:1``, ``:2``, ...) with a window manager,
a live-screen server (x11vnc, password protected, view-only until a person
takes over) and a driver worker. Desktops share the container's files and
accessibility bus. Every request except /health and /test-call needs the
container's token (``Authorization: Bearer <COGNITRIX_SANDBOX_TOKEN>``):

    GET    /health
    GET    /test-call                     the practice call page the agent's own browser opens (no data)
    POST   /agents/<name>/files           {give_back?}: let 'agent' read an agent's files (before a snapshot
                                          or copy), or hand them back to its own user (after); '_all' = every agent
    POST   /exec                          run a short shell script as this computer's user: {script, input?}
                                          -> {code, stdout, stderr} (the engine's snapshots and key proxy
                                          use these where Docker can't reach in, e.g. on Fly)
    POST   /exec/out                      run {script}; the answer body is its output (X-Exit-Code header)
    POST   /exec/in                       run the script in X-Exec-Script (base64) with the body as input
    GET    /activity                      seconds since the last request, and whether work is running
                                          (the engine stops an idle computer; this call doesn't count)
    GET    /desktops                      list desktops
    POST   /desktops/<name>               start one (or return the running one)
    DELETE /desktops/<name>               stop one
    POST   /desktops/<name>/call          a driver call (see worker.py)
    POST   /desktops/<name>/hold          a person takes over: agent paused, screen accepts input
    POST   /desktops/<name>/release       hand back: screen view-only again
    POST   /desktops/<name>/audio/start   listen to what the desktop plays (a call), see audio.py
    POST   /desktops/<name>/audio/stop
    GET    /desktops/<name>/audio?after=N&ack=M pieces of speech heard since piece N (kept until
                                          acknowledged up to M, so an engine restart can read them again)
    POST   /desktops/<name>/audio/say     play audio into the desktop's microphone
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
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cognitrix.computer.sandbox import SERVICE_PORT, check_name, token
from cognitrix.computer.sandbox.apps import AppHost, AppHostError, unb64
from cognitrix.computer.sandbox.audio import DesktopAudio
from cognitrix.computer.sandbox.users import AgentUser, Users, keep_caps_to_self
from cognitrix.computer.sandbox.workspace import MIN_FREE_MB, Workspace, WorkspaceError, free_memory_mb

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
    user: AgentUser | None = None  # the agent's own Linux user (None: everything runs as 'agent')
    xauth: str = ''  # this display's X cookie file: only its own agent's apps can connect
    audio: DesktopAudio | None = None  # its own speaker and microphone (calls)
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
    def __init__(self, users: Users | None = None) -> None:
        self.desktops: dict[str, Desktop] = {}
        self._guard = threading.Lock()
        self.users = users or Users(enabled=False)
        os.makedirs(TOKENS, exist_ok=True)
        if self.users.enabled:
            os.chmod(STATE, 0o711)  # agents' users read their own display's X cookie in here
            os.chmod(TOKENS, 0o700)

    def user_for(self, name: str) -> AgentUser | None:
        """The agent's own user, with its folders ready (None when agents
        share the 'agent' user)."""
        if not self.users.enabled:
            return None
        running = {n for n, d in self.desktops.items() if all(p.poll() is None for p in d.procs.values())}
        user = self.users.get(name, running)
        if user is not None:
            self.users.prepare(name, user)
        return user

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
                raise RuntimeError(self._busy(f'it already runs {MAX_DESKTOPS} agent desktops, the most it can'))
            free = free_memory_mb()
            if free is not None and free < 2 * MIN_FREE_MB:
                raise RuntimeError(self._busy(f'it is almost out of memory ({free} MB free)'))
            # VNC passwords are 8 characters at most.
            desk = Desktop(name, self._free_number(), secrets.token_hex(4), secrets.token_hex(4))
            desk.user = self.user_for(name)
            self._start(desk)
            self.desktops[name] = desk
            return desk

    def _busy(self, why: str) -> str:
        now = time.time()
        idle = sorted(self.desktops.values(), key=lambda d: d.last_used)
        names = ', '.join(f'{d.name} (idle {int((now - d.last_used) // 60)} min)' for d in idle[:6])
        return (f'The sandboxed computer is busy: {why}. Desktops running: {names}. Tell the person: they can stop '
                "an agent's desktop they don't need in the Computer panel, or give Docker more memory.")

    def _start(self, desk: Desktop) -> None:
        env = dict(os.environ, DISPLAY=desk.display, DESKTOP_NAME=desk.name)
        env.pop('COGNITRIX_SANDBOX_TOKEN', None)  # apps on the desktop never see the container token
        # Its own speaker and microphone, so a call on this desktop is heard
        # (and the agent heard) without mixing with another desktop's.
        desk.audio = DesktopAudio(desk.name)
        env.update(desk.audio.setup())
        quiet = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'start_new_session': True}
        width, height = SCREEN.split('x')[:2]
        auth: list[str] = []
        if desk.user is not None:
            # Each display takes only clients with its cookie, so one agent's
            # apps can't watch or type into another agent's screen.
            desk.xauth = self._make_xauth(desk)
            env['XAUTHORITY'] = desk.xauth
            auth = ['-auth', desk.xauth]
        desk.procs['xvfb'] = subprocess.Popen(  # noqa: S603
            ['Xvfb', desk.display, '-screen', '0', SCREEN, '-nolisten', 'tcp', *auth], env=env, **quiet)
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
            ['x11vnc', '-display', desk.display, *auth, '-rfbport', str(desk.vnc_port), '-localhost', '-passwdfile', pwfile,
             '-shared', '-forever', '-noxdamage', '-quiet', '-geometry', f'{width}x{height}'],
            env=env, **quiet)
        with open(os.path.join(TOKENS, desk.name), 'w') as fh:
            fh.write(f'{desk.name}: localhost:{desk.vnc_port}\n')
        command = [os.environ.get('COGNITRIX_SANDBOX_PYTHON', 'python3'), '-m', 'cognitrix.computer.sandbox.worker',
                   '--port', str(desk.worker_port)]
        as_user: dict = {}
        if desk.user is not None:
            # The driver, and every app it opens, runs as the agent's own user
            # with its own session and accessibility bus.
            env = desk.user.env(desk.name, env)
            command = ['dbus-run-session', '--', *command, '--own-a11y']
            as_user = desk.user.popen_kwargs()
        desk.procs['worker'] = subprocess.Popen(  # noqa: S603
            command, env=env, stdout=None, stderr=None, start_new_session=True, cwd=env.get('HOME'), **as_user)
        if not _wait_for(lambda: self._worker_up(desk), 20):
            logger.warning('desktop %s: worker did not start', desk.name)

    def _make_xauth(self, desk: Desktop) -> str:
        path = os.path.join(STATE, f'{desk.name}.xauth')
        try:
            os.remove(path)
        except OSError:
            pass
        subprocess.run(['xauth', '-q', '-f', path, 'add', desk.display, '.', secrets.token_hex(16)],  # noqa: S603,S607
                       check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chown(path, -1, desk.user.gid)
        os.chmod(path, 0o640)
        return path

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
        if desk.xauth:
            env['XAUTHORITY'] = desk.xauth
        env.pop('COGNITRIX_SANDBOX_TOKEN', None)
        desk.terminal = subprocess.Popen(  # noqa: S603
            ['xterm', '-T', 'Terminal', '-class', 'CognitrixTerminal', '-fa', 'DejaVu Sans Mono', '-fs', '11',
             '-bg', '#1e1f22', '-fg', '#d8dade', '-cr', '#1e1f22', '-sl', '5000', '-b', '8',
             '-e', 'tail', '-n', '400', '-F', log],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def _stop(self, desk: Desktop) -> None:
        if desk.audio is not None:
            desk.audio.teardown()
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
        for path in (os.path.join(TOKENS, desk.name), os.path.join(STATE, f'{desk.name}.vncpass'),
                     os.path.join(STATE, f'{desk.name}.xauth')):
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

    def audio(self, name: str) -> DesktopAudio | None:
        desk = self.desktops.get(check_name(name))
        if desk is None:
            return None
        desk.last_used = time.time()
        return desk.audio

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


class _Activity:
    """When the computer was last used, for idle shutdown on hosted servers."""

    def __init__(self) -> None:
        self.last = time.time()
        self.in_flight = 0
        self._guard = threading.Lock()

    def begin(self) -> None:
        with self._guard:
            self.in_flight += 1
            self.last = time.time()

    def end(self) -> None:
        with self._guard:
            self.in_flight = max(0, self.in_flight - 1)
            self.last = time.time()


def busy_reasons(sup: Supervisor, work: Workspace | None, activity: _Activity) -> list[str]:
    """Why this computer shouldn't be stopped now, even when nobody called lately."""
    reasons = []
    if activity.in_flight:
        reasons.append('a request is running')
    if any(d.held_by for d in sup.desktops.values()):
        reasons.append('a person has control')
    if work is not None and any(job.running for job in list(work.jobs.values())):
        reasons.append('a command is running')
    recorders = (getattr(getattr(d, 'audio', None), 'recorder', None) for d in list(sup.desktops.values()))
    if any(rec is not None and rec.alive for rec in recorders):
        reasons.append('an agent is in a call')
    return reasons


# The practice call (Meetings > Test call): a page the agent's own browser
# opens here to check joining, the call chat, speaking and hearing without a
# real meeting. It plays the microphone back out of the speaker, so what the
# agent says comes back to it as call sound. Served without the token, like
# /health, because the browser opening it has none; it holds no data.
TEST_CALL_PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Practice call</title>
<style>body{font:16px sans-serif;margin:40px;max-width:640px}button,input{font:inherit;padding:6px 10px}
#room{display:none}#log p{margin:4px 0}</style></head>
<body><h1>Practice call</h1>
<div id="lobby"><label>Your name <input id="name" aria-label="Your name"></label>
<button id="join">Join now</button></div>
<div id="room"><p id="status">You are in the practice call.</p>
<button id="leave">Leave call</button>
<div id="log" role="log" aria-label="Call chat"></div>
<input id="msg" aria-label="Send a message" placeholder="Send a message">
</div>
<script>
const $=id=>document.getElementById(id);
$('join').onclick=async()=>{
  $('lobby').remove();$('room').style.display='block';
  try{const s=await navigator.mediaDevices.getUserMedia({audio:true});
    const a=new Audio();a.srcObject=s;a.play();}catch(e){$('status').textContent='No microphone: '+e.message;}
};
$('msg').addEventListener('keydown',e=>{if(e.key==='Enter'&&$('msg').value.trim()){
  const p=document.createElement('p');p.textContent=$('msg').value;$('log').appendChild(p);$('msg').value='';}});
$('leave').onclick=()=>{document.body.innerHTML='<h1>You left the call</h1>';};
</script></body></html>"""


class _DualStackServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        # Take IPv4 too (Fly's private network is IPv6; local Docker maps IPv4).
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (OSError, AttributeError):
            pass
        super().server_bind()


def make_server(sup: Supervisor, port: int = SERVICE_PORT, host: str | None = None,
                apps: AppHost | None = None, work: Workspace | None = None) -> ThreadingHTTPServer:
    secret = token()
    if not secret:
        raise SystemExit('COGNITRIX_SANDBOX_TOKEN is not set')
    # '::' on Fly, where the engine reaches the computer over the private IPv6 network.
    host = host or os.environ.get('COGNITRIX_SANDBOX_LISTEN') or '0.0.0.0'  # noqa: S104
    activity = _Activity()

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
            if method == 'GET' and self.path.split('?')[0] == '/test-call':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(TEST_CALL_PAGE)))
                self.end_headers()
                self.wfile.write(TEST_CALL_PAGE)
                return
            if not self._authed():
                return
            if method == 'GET' and self.path == '/activity':
                self._reply(200, {'ok': True, 'idle_s': round(time.time() - activity.last, 1),
                                  'busy': busy_reasons(sup, work, activity)})
                return
            activity.begin()
            try:
                self._dispatch(method)
            finally:
                activity.end()

        def _dispatch(self, method: str) -> None:
            parts = [p for p in self.path.split('?')[0].split('/') if p]
            if parts[:1] == ['apps']:
                self._apps(method, parts)
                return
            if parts[:1] == ['work']:
                self._work(method, parts)
                return
            if parts[:1] == ['agents'] and len(parts) == 3 and parts[2] == 'files' and method == 'POST':
                # Before a snapshot or copy, let 'agent' read the agent's files;
                # after a restore or copy, give them back to the agent's user.
                give_back = bool(self._body().get('give_back'))
                names = sup.users.known() if parts[1] == '_all' else [check_name(parts[1])]
                for name in names if sup.users.enabled else []:
                    if give_back:
                        user = sup.users.get(name)
                        if user is not None:
                            sup.users.prepare(name, user, force=True)
                    else:
                        sup.users.share(name)
                self._reply(200, {'ok': True, 'own_users': sup.users.enabled})
                return
            if parts[:1] == ['exec'] and method == 'POST':
                self._exec(parts[1:2])
                return
            if parts == ['pagecheck'] and method == 'POST':
                # Check Artifact: look at one self-contained page (sent whole) at
                # several screen sizes and colour schemes in a headless Chromium.
                self._pagecheck()
                return
            try:
                if parts == ['desktops'] and method == 'GET':
                    self._reply(200, {'ok': True, 'desktops': [d.info() for d in sup.desktops.values()]})
                elif len(parts) == 2 and parts[0] == 'desktops' and method == 'POST':
                    desk = sup.ensure(parts[1])
                    self._reply(200, {'ok': True, 'desktop': desk.info(), 'vnc_password': desk.password})
                elif len(parts) == 2 and parts[0] == 'desktops' and method == 'DELETE':
                    self._reply(200, {'ok': sup.stop(parts[1])})
                elif len(parts) >= 3 and parts[0] == 'desktops' and parts[2] == 'audio':
                    self._audio(method, parts)
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

        def _exec(self, kind: list[str]) -> None:
            import base64
            import tempfile

            from cognitrix.computer.sandbox.workspace import child_env

            env = child_env()
            if not kind:  # short: everything in one JSON answer
                body = self._body()
                script = str(body.get('script') or '')
                data = base64.b64decode(body['input']) if body.get('input') else b''
                try:
                    got = subprocess.run(['sh', '-c', script], input=data, capture_output=True,  # noqa: S603, S607
                                         env=env, cwd=env['HOME'], timeout=float(body.get('timeout') or 300))
                except subprocess.TimeoutExpired:
                    self._reply(200, {'ok': True, 'code': 124, 'stdout': '', 'stderr': 'timed out'})
                    return
                self._reply(200, {'ok': True, 'code': got.returncode,
                                  'stdout': got.stdout[-1_000_000:].decode('utf-8', 'replace'),
                                  'stderr': got.stderr[-4000:].decode('utf-8', 'replace')})
                return
            if kind == ['out']:  # the output can be big (a snapshot): via a file, then streamed
                script = str(self._body().get('script') or '')
                with tempfile.TemporaryFile(dir=STATE) as out, tempfile.TemporaryFile(dir=STATE) as err:
                    code = subprocess.run(['sh', '-c', script], stdout=out, stderr=err,  # noqa: S603, S607
                                          env=env, cwd=env['HOME'], timeout=3600).returncode
                    size = out.tell()
                    err.seek(max(0, err.tell() - 2000))
                    tail = base64.b64encode(err.read()).decode()
                    out.seek(0)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(size))
                    self.send_header('X-Exit-Code', str(code))
                    self.send_header('X-Stderr', tail)
                    self.end_headers()
                    shutil.copyfileobj(out, self.wfile, 1 << 20)
                return
            if kind == ['in']:  # the input can be big: streamed into the script
                self.close_connection = True  # the body may not all be read
                script = base64.b64decode(self.headers.get('X-Exec-Script', '')).decode()
                length = int(self.headers.get('Content-Length') or 0)
                proc = subprocess.Popen(['sh', '-c', script], stdin=subprocess.PIPE,  # noqa: S603, S607
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, cwd=env['HOME'])
                err: list[bytes] = []
                reader = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
                reader.start()
                try:
                    left = length
                    while left > 0:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        left -= len(chunk)
                        proc.stdin.write(chunk)
                except BrokenPipeError:
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                code = proc.wait(timeout=3600)
                reader.join(timeout=10)
                self._reply(200, {'ok': True, 'code': code,
                                  'stderr': (err[0] if err else b'')[-4000:].decode('utf-8', 'replace')})
                return
            self._reply(404, {'ok': False, 'error': 'not_found'})

        def _audio(self, method: str, parts: list[str]) -> None:
            from urllib.parse import parse_qs, urlsplit

            audio = sup.audio(parts[1])
            if audio is None:
                self._reply(200, {'ok': False, 'error': 'no_desktop', 'message': f'no desktop called {parts[1]!r}'})
                return
            action = parts[3] if len(parts) > 3 else ''
            if method == 'GET' and not action:
                q = parse_qs(urlsplit(self.path).query)
                ack = (q.get('ack') or [''])[0]
                self._reply(200, audio.read(int((q.get('after') or ['0'])[0] or 0),
                                            int(ack) if ack.lstrip('-').isdigit() else None))
            elif method == 'POST' and action == 'start':
                self._reply(200, audio.start())
            elif method == 'POST' and action == 'stop':
                after = self._body().get('after')
                self._reply(200, audio.stop(int(after) if after is not None else None))
            elif method == 'POST' and action == 'say':
                body = self._body()
                try:
                    data = unb64(body.get('audio', ''))
                except AppHostError:
                    self._reply(400, {'ok': False, 'error': 'bad_request', 'message': 'The audio was not base64.'})
                    return
                self._reply(200, audio.say(data, str(body.get('suffix') or '.wav')))
            else:
                self._reply(404, {'ok': False, 'error': 'not_found'})

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

        def _pagecheck(self) -> None:
            from cognitrix.computer.sandbox import pagecheck

            b = self._body()
            browser = pagecheck.find_browser()
            if browser is None:
                self._reply(200, {'ok': False, 'error': 'no_browser', 'message': 'No Chromium on this computer.'})
                return
            views = [str(v) for v in (b.get('views') or pagecheck.VIEWS)]
            for view in views:
                pagecheck.parse_view(view)
            try:
                results = pagecheck.check_html(str(b.get('html') or ''), str(b.get('csp') or ''), views,
                                               browser=browser, full_page=bool(b.get('full_page', True)),
                                               scroll_y=int(b.get('scroll_y') or 0), no_sandbox=True)
            except pagecheck.PageCheckError as exc:
                self._reply(200, {'ok': False, 'error': 'browser_failed', 'message': str(exc)})
                return
            self._reply(200, {'ok': True, 'views': results})

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
                elif method == 'POST' and action == 'check':
                    # Check App: wake it and look at it in a headless Chromium
                    # (no window on the desktops people watch).
                    from cognitrix.computer.sandbox.pagecheck import check_app_port

                    b = self._body()
                    port_ = apps.port_for(slug, wake=True)
                    if not port_:
                        self._reply(200, {'ok': False, 'error': 'The app is not running and did not start.'})
                        return
                    path = str(b.get('path') or '').lstrip('/')
                    headers = {str(k): str(v) for k, v in (b.get('headers') or {}).items()}
                    self._reply(200, check_app_port(port_, path, phone=bool(b.get('phone')), headers=headers,
                                                    no_sandbox=True))
                elif method == 'POST' and action == 'test':
                    # Test App: click through a throwaway copy of the app.
                    from cognitrix.computer.sandbox.apptest import test_app_copy

                    b = self._body()
                    headers = {str(k): str(v) for k, v in (b.get('headers') or {}).items()}
                    self._reply(200, test_app_copy(apps, slug, b.get('steps') or [], str(b.get('path') or ''),
                                                   data=str(b.get('data') or 'fresh'), phone=bool(b.get('phone')),
                                                   headers=headers, no_sandbox=True))
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

    server = (_DualStackServer if ':' in host else ThreadingHTTPServer)((host, port), Handler)
    server.daemon_threads = True
    server.activity = activity  # type: ignore[attr-defined]
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    keep_caps_to_self()  # nothing this starts gets the capabilities to switch users
    os.umask(0o007)
    users = Users()
    logger.info('agents get their own Linux users: %s', 'yes' if users.enabled else 'no')
    sup = Supervisor(users)
    apps = AppHost(APPS_ROOT)

    def show_terminal(agent: str, log: str) -> None:
        try:
            sup.open_terminal(agent, log)
        except Exception:  # the desktop limit, or no X: commands still run
            logger.info('no terminal for %s', agent, exc_info=True)

    work = Workspace(terminal_opener=show_terminal, user_for=sup.user_for)
    server = make_server(sup, apps=apps, work=work)
    signal.signal(signal.SIGTERM, lambda *_: (sup.shutdown(), apps.shutdown(), work.shutdown(), os._exit(0)))
    logger.info('sandbox supervisor on :%d (x11vnc %s)', SERVICE_PORT, shutil.which('x11vnc') or 'missing')
    server.serve_forever()


if __name__ == '__main__':
    main()
