"""Look at a web page the way a person would: load it in a headless browser,
take a screenshot, and collect what went wrong (script errors, failed
requests, error responses).

Used by Check App, both inside the sandboxed computer (its Chromium) and on
the machine Cognitrix runs on (an installed Edge, Chrome or Chromium). Standard
library only, because the sandbox image copies this file in without the rest
of Cognitrix: the browser is driven over the Chrome DevTools Protocol through
a small WebSocket client below. The browser always gets a fresh, throwaway
profile, never the person's own.

When no browser is installed, :func:`check_text` fetches the page and the
scripts, styles and images it links, and reports which ones fail.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

DESKTOP = (1280, 800)
PHONE = (390, 844)
MAX_ITEMS = 20


class PageCheckError(Exception):
    pass


# ------------------------------------------------------------------ finding a browser

def _candidates() -> list[str]:
    if os.name == 'nt':
        roots = [os.environ.get(k, '') for k in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA')]
        rel = [r'Microsoft\Edge\Application\msedge.exe', r'Google\Chrome\Application\chrome.exe',
               r'Chromium\Application\chrome.exe', r'BraveSoftware\Brave-Browser\Application\brave.exe']
        return [os.path.join(root, r) for root in roots if root for r in rel]
    mac = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
           '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
           '/Applications/Chromium.app/Contents/MacOS/Chromium',
           '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser']
    names = ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable', 'microsoft-edge',
             'microsoft-edge-stable', 'brave-browser']
    return mac + [p for p in (shutil.which(n) for n in names) if p] + ['/opt/pw-browsers/chromium']


def find_browser() -> str | None:
    """A Chromium-family browser to drive: ``COGNITRIX_CHECK_BROWSER`` if set, else
    Edge, Chrome or Chromium where they are usually installed."""
    configured = os.environ.get('COGNITRIX_CHECK_BROWSER', '').strip()
    if configured.lower() in ('off', 'none', '0'):
        return None
    for path in ([configured] if configured else []) + _candidates():
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


# ------------------------------------------------------------------ a small WebSocket client

class _WebSocket:
    """Just enough RFC 6455 for DevTools: text frames, client masking, ping/pong."""

    def __init__(self, url: str, timeout: float) -> None:
        parts = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f'GET {parts.path or "/"} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\n'
                   'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                   f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.sock.sendall(request.encode())
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise PageCheckError('The browser closed the DevTools connection.')
            head += chunk
        status, _, self.buf = head.partition(b'\r\n\r\n')
        if b' 101 ' not in status.split(b'\r\n', 1)[0]:
            raise PageCheckError('The browser refused the DevTools connection.')

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise PageCheckError('The browser closed the DevTools connection.')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, opcode: int, data: bytes) -> None:
        head = bytes([0x80 | opcode])
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('!H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('!Q', n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, text: str) -> None:
        self._send(0x1, text.encode())

    def recv(self) -> str:
        message = b''
        while True:
            b1, b2 = self._exact(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack('!H', self._exact(2))[0]
            elif n == 127:
                n = struct.unpack('!Q', self._exact(8))[0]
            mask = self._exact(4) if b2 & 0x80 else b''
            data = self._exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x9:
                self._send(0xA, data)
                continue
            if opcode == 0x8:
                raise PageCheckError('The browser closed the DevTools connection.')
            if opcode in (0x0, 0x1, 0x2):
                message += data
                if b1 & 0x80:
                    return message.decode('utf-8', 'replace')

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class _DevTools:
    def __init__(self, ws: _WebSocket) -> None:
        self.ws = ws
        self.next_id = 0
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float = 20) -> dict:
        self.next_id += 1
        msg = {'id': self.next_id, 'method': method, 'params': params or {}}
        if session:
            msg['sessionId'] = session
        self.ws.send(json.dumps(msg))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.ws.sock.settimeout(max(0.1, deadline - time.monotonic()))
            data = json.loads(self.ws.recv())
            if data.get('id') == self.next_id:
                if 'error' in data:
                    raise PageCheckError(f"{method}: {data['error'].get('message', 'failed')}")
                return data.get('result') or {}
            if 'method' in data:
                self.events.append(data)
        raise PageCheckError(f'{method} timed out.')

    def pump(self, seconds: float) -> None:
        """Collect events for up to ``seconds``."""
        deadline = time.monotonic() + seconds
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            self.ws.sock.settimeout(left)
            try:
                data = json.loads(self.ws.recv())
            except TimeoutError:
                return
            if 'method' in data:
                self.events.append(data)

    def wait_for(self, method: str, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        seen = len(self.events)
        while time.monotonic() < deadline:
            if any(e.get('method') == method for e in self.events[seen:]):
                return True
            self.pump(min(0.25, max(0.0, deadline - time.monotonic())))
        return any(e.get('method') == method for e in self.events[seen:])


# ------------------------------------------------------------------ checking a page

def _launch(browser: str, profile: str, no_sandbox: bool) -> tuple[subprocess.Popen, str]:
    args = [browser, '--headless=new', '--remote-debugging-port=0', f'--user-data-dir={profile}',
            '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-sync',
            '--disable-background-networking', '--disable-component-update', '--disable-default-apps',
            '--hide-scrollbars', '--mute-audio', '--disable-gpu', '--password-store=basic',
            '--use-mock-keychain', 'about:blank']
    if no_sandbox:
        args.insert(1, '--no-sandbox')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                            stderr=subprocess.DEVNULL, creationflags=flags)
    port_file = os.path.join(profile, 'DevToolsActivePort')
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise PageCheckError(f'The browser ({os.path.basename(browser)}) quit while starting.')
        try:
            with open(port_file, encoding='utf-8') as fh:
                lines = fh.read().split()
            if len(lines) >= 2:
                return proc, f'ws://127.0.0.1:{int(lines[0])}{lines[1]}'
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    proc.kill()
    raise PageCheckError('The browser did not start in 20 seconds.')


def _short(text: str, limit: int = 300) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit] + '…'


def _summarise(events: list[dict], page_url: str) -> dict:
    errors: list[str] = []
    console: list[str] = []
    failed: list[str] = []
    status = 0
    urls: dict[str, str] = {}
    for event in events:
        method = event.get('method')
        p = event.get('params') or {}
        if method == 'Runtime.exceptionThrown':
            d = p.get('exceptionDetails') or {}
            text = (d.get('exception') or {}).get('description') or d.get('text') or 'Script error'
            errors.append(_short(text.split('\n    at ')[0]))
        elif method == 'Runtime.consoleAPICalled' and p.get('type') in ('error', 'assert'):
            parts = [str(a.get('value', a.get('description', ''))) for a in p.get('args') or []]
            console.append(_short(' '.join(parts)))
        elif method == 'Log.entryAdded' and (p.get('entry') or {}).get('level') == 'error':
            entry = p['entry']
            if entry.get('source') != 'network':  # failed requests are listed below
                console.append(_short(entry.get('text', '')))
        elif method == 'Network.requestWillBeSent':
            urls[p.get('requestId', '')] = (p.get('request') or {}).get('url', '')
        elif method == 'Network.responseReceived':
            r = p.get('response') or {}
            code = int(r.get('status') or 0)
            if p.get('type') == 'Document' and not status:
                status = code
            if code >= 400 and not r.get('url', '').endswith('/favicon.ico'):
                failed.append(f"{code} {r.get('url', '')}")
        elif method == 'Network.loadingFailed' and not p.get('canceled'):
            failed.append(f"{p.get('errorText', 'failed')} {urls.get(p.get('requestId', ''), '')}")

    split = urllib.parse.urlsplit(page_url)
    origin = f'{split.scheme}://{split.netloc}'

    def dedupe(items: list[str]) -> list[str]:
        out: list[str] = []
        for item in items:
            if item and item not in out:
                out.append(item.replace(origin, '') or item)
        return out[:MAX_ITEMS]

    return {'status': status, 'errors': dedupe(errors), 'console': dedupe(console), 'failed': dedupe(failed)}


def check_page(url: str, *, browser: str, phone: bool = False, headers: dict[str, str] | None = None,
               timeout: float = 30, settle: float = 2.0, no_sandbox: bool = False) -> dict:
    """Load ``url`` and return ``{ok, status, title, text_length, screenshot (base64 PNG),
    errors, console, failed, width, height}``."""
    width, height = PHONE if phone else DESKTOP
    profile = tempfile.mkdtemp(prefix='cognitrix-check-')
    proc = None
    ws = None
    try:
        proc, ws_url = _launch(browser, profile, no_sandbox)
        ws = _WebSocket(ws_url, timeout)
        dt = _DevTools(ws)
        target = dt.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        session = dt.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        for method in ('Page.enable', 'Runtime.enable', 'Log.enable', 'Network.enable'):
            dt.call(method, session=session)
        if headers:
            dt.call('Network.setExtraHTTPHeaders', {'headers': headers}, session=session)
        dt.call('Emulation.setDeviceMetricsOverride', {
            'width': width, 'height': height, 'deviceScaleFactor': 2 if phone else 1, 'mobile': phone}, session=session)
        if phone:
            dt.call('Emulation.setUserAgentOverride', {'userAgent': (
                'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 '
                '(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1')}, session=session)
        nav = dt.call('Page.navigate', {'url': url}, session=session, timeout=timeout)
        if nav.get('errorText'):
            return {'ok': False, 'error': f"The page didn't load: {nav['errorText']}", 'errors': [], 'console': [],
                    'failed': [], 'status': 0}
        loaded = dt.wait_for('Page.loadEventFired', timeout)
        dt.pump(settle)  # scripts that fetch data after load
        info = dt.call('Runtime.evaluate', {
            'expression': "JSON.stringify({title: document.title, text: document.body ? "
                          "document.body.innerText.trim().length : 0})",
            'returnByValue': True}, session=session)
        page = json.loads(((info.get('result') or {}).get('value')) or '{}')
        shot = dt.call('Page.captureScreenshot', {'format': 'png'}, session=session, timeout=timeout)
        out = _summarise(dt.events, url)
        out.update({'ok': True, 'title': _short(page.get('title', ''), 120), 'text_length': int(page.get('text') or 0),
                    'screenshot': shot.get('data', ''), 'width': width, 'height': height, 'loaded': loaded})
        return out
    except (OSError, ValueError) as exc:
        raise PageCheckError(f'Could not drive the browser: {exc}') from None
    finally:
        if ws is not None:
            try:
                ws.send(json.dumps({'id': 999999, 'method': 'Browser.close'}))
            except OSError:
                pass
            ws.close()
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        shutil.rmtree(profile, ignore_errors=True)


# ------------------------------------------------------------------ no browser: a text check

class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.title = ''
        self._in_title = False
        self._skip = 0
        self.text = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script' and a.get('src'):
            self.links.append(a['src'])
        elif tag == 'link' and 'stylesheet' in (a.get('rel') or '') and a.get('href'):
            self.links.append(a['href'])
        elif tag == 'img' and a.get('src') and not a['src'].startswith('data:'):
            self.links.append(a['src'])
        self._in_title = tag == 'title'
        if tag in ('script', 'style'):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        if tag in ('script', 'style') and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.text += len(data.strip())


def _fetch(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 - the app's own local address
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read(2_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, b''


def check_text(url: str, *, headers: dict[str, str] | None = None, timeout: float = 20) -> dict:
    """No browser: fetch the page and the files it links (same app only)."""
    headers = dict(headers or {})
    try:
        status, body = _fetch(url, headers, timeout)
    except (OSError, ValueError) as exc:
        return {'ok': False, 'error': f"The page didn't load: {exc}", 'errors': [], 'console': [], 'failed': [],
                'status': 0}
    parser = _Links()
    try:
        parser.feed(body.decode('utf-8', 'replace'))
    except Exception:  # noqa: BLE001 - broken HTML still gets a status
        pass
    failed = [f'{status} {url}'] if status >= 400 else []
    base = urllib.parse.urlsplit(url)
    for link in parser.links[:40]:
        full = urllib.parse.urljoin(url, link)
        if urllib.parse.urlsplit(full).netloc != base.netloc:
            continue  # only the app's own files
        try:
            code, _ = _fetch(full, headers, timeout)
        except (OSError, ValueError) as exc:
            failed.append(f'{_short(str(exc), 80)} {link}')
            continue
        if code >= 400:
            failed.append(f'{code} {link}')
    return {'ok': True, 'status': status, 'title': _short(parser.title, 120), 'text_length': parser.text,
            'screenshot': '', 'errors': [], 'console': [], 'failed': failed[:MAX_ITEMS], 'text_only': True}


# ------------------------------------------------------------------ the way people see apps

# Same as cognitrix/api/routes/hosted_apps.py APP_CSP (a test keeps them equal):
# an opaque origin, so no cookies or localStorage.
APP_CSP = ("sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals "
           "allow-downloads; frame-ancestors 'self'")
PREFIX = '/a/check/~token'
_DROP_OUT = {'connection', 'keep-alive', 'transfer-encoding', 'content-length', 'set-cookie',
             'content-security-policy', 'x-frame-options', 'access-control-allow-origin',
             'access-control-allow-credentials', 'content-encoding', 'strict-transport-security',
             'server', 'date'}
_CORS = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': '*',
         'Access-Control-Allow-Headers': '*', 'Access-Control-Max-Age': '600'}
_HOP = {'connection', 'keep-alive', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade',
        'host', 'content-length', 'cookie', 'accept-encoding'}


class _Hosting:
    """A stand-in for Cognitrix's app proxy on a free local port: the app answers
    under ``/a/check/~token/`` with the same sandboxing header, so links that
    start with "/" and storage use fail here just as they do for people."""

    def __init__(self, app_port: int) -> None:
        import http.client
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _forward(self):
                if not (self.path == PREFIX or self.path.startswith(PREFIX + '/')):
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                rest = self.path[len(PREFIX):] or '/'
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else None
                headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
                headers.update({'X-Forwarded-Prefix': PREFIX, 'Accept-Encoding': 'identity'})
                conn = http.client.HTTPConnection('127.0.0.1', app_port, timeout=60)
                try:
                    conn.request(self.command, rest, body=body, headers=headers)
                    resp = conn.getresponse()
                    data = resp.read()
                    self.send_response(resp.status)
                    for key, value in resp.getheaders():
                        if key.lower() in _DROP_OUT:
                            continue
                        if key.lower() == 'location' and value.startswith('/') and not value.startswith('//'):
                            value = PREFIX + value
                        self.send_header(key, value)
                    self.send_header('Content-Security-Policy', APP_CSP)
                    for key, value in _CORS.items():
                        self.send_header(key, value)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    try:
                        self.send_response(502)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                    except OSError:
                        pass
                finally:
                    conn.close()

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}{PREFIX}/'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def check_app_port(port: int, path: str = '', *, phone: bool = False, headers: dict[str, str] | None = None,
                   no_sandbox: bool = False) -> dict:
    """Check the app listening on ``port`` the way people get it: under a path
    prefix, in an opaque origin."""
    hosting = _Hosting(int(port))
    try:
        out = check_url(hosting.url + str(path or '').lstrip('/'), phone=phone, headers=headers,
                        no_sandbox=no_sandbox)
    finally:
        hosting.close()
    failed = []
    for item in out.get('failed') or []:
        where = item.split(' ')[-1]
        if where.startswith('/') and not where.startswith(PREFIX):
            item += ' (the link starts with "/": make it relative, e.g. "app.js" or "./app.js")'
        failed.append(item.replace(PREFIX + '/', '/').replace(PREFIX, '/'))
    out['failed'] = failed
    return out


def check_url(url: str, *, phone: bool = False, headers: dict[str, str] | None = None,
              no_sandbox: bool = False) -> dict:
    """A browser check when a browser is installed, otherwise a text check."""
    browser = find_browser()
    if browser is None:
        out = check_text(url, headers=headers)
        out['note'] = ('No Edge, Chrome or Chromium was found on the machine running the app, so this '
                       'was a text check: no screenshot, and script errors are not seen.')
        return out
    try:
        return check_page(url, browser=browser, phone=phone, headers=headers, no_sandbox=no_sandbox)
    except PageCheckError as exc:
        out = check_text(url, headers=headers)
        out['note'] = f'The browser check failed ({exc}), so this was a text check instead.'
        return out
