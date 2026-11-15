"""Keys the agent never sees.

Commands in the sandboxed computer used to get real API keys and tokens as
environment variables, so anything the agent ran could print or send them
anywhere. Now a key that has approved sites (a coding CLI's API key, a saved
sign-in with allowed hosts, a sandbox variable with "Only sent to" sites)
reaches the container as a placeholder such as ``sk-ant-cgxph3f9c…``. Commands
send their HTTPS traffic through this proxy, which runs in the engine (outside
the container), and the proxy swaps the placeholder for the real value only in
requests to that key's approved sites. Anywhere else the placeholder stays a
useless string.

How it works:

- ``HiddenValue`` marks an environment value that should be hidden, with the
  sites it may go to. ``SandboxWork.run`` swaps each one for ``placeholder()``
  and adds ``KeyProxy.env()`` (HTTPS_PROXY and CA bundle variables).
- CONNECT tunnels to hosts no key of this person's is approved for are passed
  through untouched (no TLS interception).
- For approved hosts the proxy terminates TLS with a certificate from its own
  CA (the container trusts it through the CA bundle variables; the CA's private
  key never leaves the engine), fills placeholders in the request line and
  headers (Basic auth included), sends ``Connection: close`` and pipes the
  rest. Request bodies are not rewritten.
- Each person's sandbox gets its own proxy password, so one person's
  placeholders only ever resolve for their own container.
- Tunnels to loopback, link-local and unspecified addresses are refused, so the
  proxy can't be used to reach the engine or other services on this machine.

A tool that ignores HTTPS_PROXY just sends the placeholder and gets an
authentication error; the real value still never enters the container.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import socket
import ssl
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger('cognitrix.key_proxy')

HIDE_ENV = 'COGNITRIX_SANDBOX_HIDE_KEYS'
BIND_ENV = 'COGNITRIX_SANDBOX_PROXY_BIND'
PORT_ENV = 'COGNITRIX_SANDBOX_PROXY_PORT'
UPSTREAM_CA_ENV = 'COGNITRIX_SANDBOX_PROXY_UPSTREAM_CA'
MARK = 'cgxph'
_CORE = re.compile(MARK + r'([0-9a-f]{32})')
_PREFIX = re.compile(r'^((?:[A-Za-z]{1,12}[-_]){1,2})')
MAX_HEAD = 64 * 1024
MAX_ENTRIES_PER_PERSON = 500
_HOP_HEADERS = {'connection', 'keep-alive', 'proxy-connection', 'proxy-authorization', 'proxy-authenticate', 'te',
                'trailer'}


def hiding_enabled() -> bool:
    """Off only when COGNITRIX_SANDBOX_HIDE_KEYS is 0/false/off."""
    return os.environ.get(HIDE_ENV, '1').strip().lower() not in ('0', 'false', 'no', 'off')


class HiddenValue(str):
    """A secret environment value with the sites it may be sent to."""

    hosts: tuple[str, ...]
    label: str

    def __new__(cls, value: str, hosts: Any, label: str = '') -> HiddenValue:
        obj = super().__new__(cls, value)
        obj.hosts = tuple(str(h).strip().lower() for h in (hosts or ()) if str(h).strip())
        obj.label = label
        return obj

    def __reduce__(self):  # keep pickling/copying working
        return (HiddenValue, (str(self), self.hosts, self.label))


def hidden_if_hosts(value: str, hosts: Any, label: str = '') -> str:
    """A HiddenValue when there are sites to limit it to, else the plain value."""
    hosts = [h for h in (hosts or []) if str(h).strip()]
    return HiddenValue(value, hosts, label) if hosts and value else value


@dataclass
class _Entry:
    placeholder: str
    value: str
    hosts: tuple[str, ...]
    label: str


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

def _state_dir() -> Path:
    from cognitrix.config import settings
    path = Path(settings.workdir) / 'sandbox-proxy'
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _write_private(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _CA:
    """The proxy's own certificate authority, made once and kept in the
    engine's data folder. Only its certificate goes into containers."""

    def __init__(self, folder: Path):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        self._x509, self._hashes, self._ser, self._ec, self._oid = x509, hashes, serialization, ec, NameOID
        key_file, cert_file = folder / 'ca.key', folder / 'ca.pem'
        if key_file.exists() and cert_file.exists():
            self.key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
            self.cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Cognitrix sandbox key proxy'),
                              x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Cognitrix (this computer only)')])
            now = _dt.datetime.now(_dt.UTC)
            self.cert = (
                x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(self.key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - _dt.timedelta(days=1)).not_valid_after(now + _dt.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                             content_commitment=False, key_encipherment=False,
                                             data_encipherment=False, key_agreement=False, encipher_only=False,
                                             decipher_only=False), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(self.key.public_key()), critical=False)
                .sign(self.key, hashes.SHA256())
            )
            _write_private(key_file, self.key.private_bytes(serialization.Encoding.PEM,
                                                            serialization.PrivateFormat.PKCS8,
                                                            serialization.NoEncryption()))
            _write_private(cert_file, self.cert.public_bytes(serialization.Encoding.PEM))
        self.pem = self.cert.public_bytes(serialization.Encoding.PEM)
        self.fingerprint = hashlib.sha256(self.pem).hexdigest()[:16]
        self._leaf_key = ec.generate_private_key(ec.SECP256R1())
        self._leaf_dir = Path(tempfile.mkdtemp(prefix='cognitrix-keyproxy-'))
        self._contexts: dict[str, ssl.SSLContext] = {}
        self._lock = threading.Lock()

    def context_for(self, host: str) -> ssl.SSLContext:
        """A server-side TLS context with a certificate for ``host``."""
        with self._lock:
            ctx = self._contexts.get(host)
            if ctx is not None:
                return ctx
            x509, ser = self._x509, self._ser
            try:
                san: Any = x509.IPAddress(ipaddress.ip_address(host))
            except ValueError:
                san = x509.DNSName(host)
            now = _dt.datetime.now(_dt.UTC)
            cert = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(self._oid.COMMON_NAME, host[:64])]))
                .issuer_name(self.cert.subject).public_key(self._leaf_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - _dt.timedelta(days=1)).not_valid_after(now + _dt.timedelta(days=90))
                .add_extension(x509.SubjectAlternativeName([san]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()),
                               critical=False)
                .sign(self.key, self._hashes.SHA256())
            )
            safe = hashlib.sha256(host.encode()).hexdigest()[:24]
            cert_file, key_file = self._leaf_dir / f'{safe}.pem', self._leaf_dir / f'{safe}.key'
            _write_private(cert_file, cert.public_bytes(ser.Encoding.PEM) + self.pem)
            _write_private(key_file, self._leaf_key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8,
                                                                  ser.NoEncryption()))
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert_file, key_file)
            ctx.set_alpn_protocols(['http/1.1'])
            self._contexts[host] = ctx
            return ctx


# ---------------------------------------------------------------------------
# Rewriting one request head
# ---------------------------------------------------------------------------

def _host_allowed(host: str, patterns: tuple[str, ...]) -> bool:
    from cognitrix.credentials import host_allowed
    return host_allowed(host, list(patterns))


def fill(text: str, entries: dict[str, _Entry], host: str, used: set[str]) -> str:
    """Swap the placeholders in ``text`` that may go to ``host``."""
    if MARK not in text:
        return text
    for core in set(_CORE.findall(text)):
        entry = entries.get(core)
        if entry is None or entry.placeholder not in text:
            continue
        if not _host_allowed(host, entry.hosts):
            logger.info('Key proxy kept %s hidden: %s is not one of its sites', entry.label or 'a key', host)
            continue
        text = text.replace(entry.placeholder, entry.value)
        used.add(entry.label or core[:6])
    return text


def rewrite_head(head: bytes, entries: dict[str, _Entry], host: str) -> tuple[bytes, set[str]]:
    """Fill placeholders in a request line and headers, and ask the server to
    close the connection after this request (so the next one is read here
    again) unless it's an upgrade (websocket)."""
    used: set[str] = set()
    text = head.decode('latin-1')
    lines = text.split('\r\n')
    request_line = fill(lines[0], entries, host, used)
    headers: list[tuple[str, str]] = []
    upgrade = False
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(':')
        if not sep:
            continue
        lname = name.strip().lower()
        value = value.strip()
        if lname == 'upgrade':
            upgrade = True
        if lname == 'authorization' and value[:6].lower() == 'basic ':
            try:
                decoded = base64.b64decode(value[6:].strip(), validate=True).decode('latin-1')
            except (ValueError, UnicodeDecodeError):
                decoded = ''
            if MARK in decoded:
                filled = fill(decoded, entries, host, used)
                if filled != decoded:
                    value = 'Basic ' + base64.b64encode(filled.encode('latin-1')).decode()
        headers.append((name.strip(), fill(value, entries, host, used)))
    out = [request_line]
    connection_values = []
    for name, value in headers:
        lname = name.lower()
        if lname in _HOP_HEADERS:
            if lname == 'connection':
                connection_values.append(value)
            continue
        if '\r' in value or '\n' in value:
            raise ValueError('header value with a line break')
        out.append(f'{name}: {value}')
    if upgrade:
        out.append('Connection: ' + (', '.join(connection_values) or 'Upgrade'))
    else:
        out.append('Connection: close')
    return ('\r\n'.join(out) + '\r\n\r\n').encode('latin-1'), used


# ---------------------------------------------------------------------------
# The proxy
# ---------------------------------------------------------------------------

def _blocked_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split('%', 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast


async def _read_head(reader: asyncio.StreamReader) -> bytes:
    try:
        return await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=60)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        return b''


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.IncompleteReadError, ssl.SSLError):
        pass


def _close(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    try:
        writer.close()
    except Exception:  # noqa: S110 - already gone
        pass


class KeyProxy:
    """One HTTPS proxy for every sandbox this engine runs."""

    def __init__(self) -> None:
        self._entries: dict[str, OrderedDict[str, _Entry]] = {}
        self._tokens: dict[str, str] = {}  # proxy password -> person key
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.base_events.Server | None = None
        self._ca: _CA | None = None
        self._secret: bytes | None = None
        self.bind = ''
        self.port = 0

    # -- keys -----------------------------------------------------------------
    def _key(self) -> bytes:
        if self._secret is None:
            path = _state_dir() / 'placeholder.key'
            try:
                data = path.read_bytes()
            except OSError:
                data = b''
            if len(data) < 32:
                data = secrets.token_bytes(32)
                _write_private(path, data)
            self._secret = data
        return self._secret

    def _mac(self, *parts: str) -> str:
        return hmac.new(self._key(), '\0'.join(parts).encode(), hashlib.sha256).hexdigest()

    def token_for(self, person: str) -> str:
        token = self._mac('proxy', person)[:40]
        with self._lock:
            self._tokens[token] = person
        return token

    def placeholder(self, person: str, name: str, value: HiddenValue) -> str:
        """The stand-in the container gets for ``value``. The same person,
        name and value always give the same placeholder, so config files a
        tool saved keep working after the engine restarts."""
        core = self._mac('placeholder', person, name, str(value))[:32]
        match = _PREFIX.match(str(value))
        # Keep a short known prefix (sk-ant-, ghp_, xoxb-) so tools that check
        # a key's shape still accept it.
        prefix = match.group(1) if match and len(match.group(1)) <= 16 and len(str(value)) > 24 else ''
        placeholder = f'{prefix}{MARK}{core}'
        with self._lock:
            table = self._entries.setdefault(person, OrderedDict())
            table[core] = _Entry(placeholder, str(value), value.hosts, value.label or name)
            table.move_to_end(core)
            while len(table) > MAX_ENTRIES_PER_PERSON:
                table.popitem(last=False)
        return placeholder

    def entries(self, person: str) -> dict[str, _Entry]:
        with self._lock:
            return dict(self._entries.get(person) or {})

    def hosts_for(self, person: str) -> list[str]:
        out: set[str] = set()
        for entry in self.entries(person).values():
            out.update(entry.hosts)
        return sorted(out)

    # -- server ---------------------------------------------------------------
    @property
    def ca(self) -> _CA:
        if self._ca is None:
            self._ca = _CA(_state_dir())
        return self._ca

    def ensure_started(self, bind: str) -> tuple[str, int]:
        """Start listening on ``bind`` (once). Returns (bind, port)."""
        with self._lock:
            if self._server is not None:
                return self.bind, self.port
            _ = self.ca
            started = threading.Event()
            failure: list[BaseException] = []

            def run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    port = int(os.environ.get(PORT_ENV, '0') or 0)
                    self._server = loop.run_until_complete(
                        asyncio.start_server(self._handle, bind, port, limit=MAX_HEAD))
                    self._loop = loop
                    self.bind = bind
                    self.port = self._server.sockets[0].getsockname()[1]
                except BaseException as exc:  # noqa: BLE001 - reported to the caller
                    failure.append(exc)
                    started.set()
                    loop.close()
                    return
                started.set()
                loop.run_forever()

            threading.Thread(target=run, name='sandbox-key-proxy', daemon=True).start()
            started.wait(15)
            if failure:
                raise failure[0]
            if self._server is None:
                raise OSError("The sandbox key proxy didn't start.")
            logger.info('Sandbox key proxy listening on %s:%s', self.bind, self.port)
            return self.bind, self.port

    def stop(self) -> None:
        with self._lock:
            server, loop = self._server, self._loop
            self._server = self._loop = None
        if server is not None and loop is not None:
            loop.call_soon_threadsafe(server.close)
            loop.call_soon_threadsafe(loop.stop)

    # -- one connection -------------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        try:
            head = await _read_head(reader)
            if not head:
                return
            lines = head.decode('latin-1').split('\r\n')
            parts = lines[0].split()
            if len(parts) != 3 or parts[0].upper() != 'CONNECT':
                writer.write(b'HTTP/1.1 405 Method Not Allowed\r\nContent-Type: text/plain\r\nConnection: close\r\n'
                             b'\r\nOnly HTTPS goes through the sandbox key proxy.\n')
                await writer.drain()
                return
            person = self._person(lines[1:])
            if person is None:
                writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\n'
                             b'Proxy-Authenticate: Basic realm="cognitrix"\r\nConnection: close\r\n\r\n')
                await writer.drain()
                return
            host, _, port_s = parts[1].rpartition(':')
            host = host.strip('[]').lower().rstrip('.')
            try:
                port = int(port_s)
            except ValueError:
                port = 0
            if not host or not 0 < port < 65536:
                writer.write(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
                await writer.drain()
                return
            address = await self._resolve(host, port)
            if address is None:
                writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n'
                             b'The sandbox key proxy does not connect to this address.\n')
                await writer.drain()
                return
            entries = self.entries(person)
            hostport = host if port == 443 else f'{host}:{port}'
            intercept = any(_host_allowed(hostport, e.hosts) for e in entries.values())
            if not intercept:
                try:
                    up_reader, upstream = await asyncio.wait_for(asyncio.open_connection(address, port), 30)
                except (TimeoutError, OSError):
                    writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
                    await writer.drain()
                    return
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
                await self._tunnel(reader, writer, up_reader, upstream)
                return
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()
            await writer.start_tls(self.ca.context_for(host))
            request = await _read_head(reader)
            if not request:
                return
            try:
                request, used = rewrite_head(request, entries, hostport)
            except ValueError:
                writer.write(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
                await writer.drain()
                return
            client_ctx = ssl.create_default_context()
            extra_ca = os.environ.get(UPSTREAM_CA_ENV, '').strip()
            if extra_ca:  # a company proxy's or a test server's CA, on top of the usual ones
                client_ctx.load_verify_locations(extra_ca)
            client_ctx.set_alpn_protocols(['http/1.1'])
            try:
                up_reader, upstream = await asyncio.wait_for(
                    asyncio.open_connection(address, port, ssl=client_ctx, server_hostname=host), 30)
            except (TimeoutError, OSError, ssl.SSLError) as exc:
                body = f"The sandbox key proxy couldn't reach {host}: {exc}\n".encode()
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\nConnection: close\r\n'
                             + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
                await writer.drain()
                return
            if used:
                logger.info('Key proxy filled %s for %s', ', '.join(sorted(used)), host)
            upstream.write(request)
            await upstream.drain()
            await self._tunnel(reader, writer, up_reader, upstream)
        except Exception:  # one bad connection never stops the proxy
            logger.debug('Key proxy connection failed', exc_info=True)
        finally:
            _close(upstream)
            _close(writer)

    @staticmethod
    async def _tunnel(reader, writer, up_reader, upstream) -> None:
        up = asyncio.ensure_future(_pipe(reader, upstream))
        down = asyncio.ensure_future(_pipe(up_reader, writer))
        done, _ = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        if up in done and not down.done():
            # The client finished sending; let the answer finish arriving.
            try:
                await asyncio.wait_for(asyncio.shield(down), timeout=600)
            except TimeoutError:
                pass
        for task in (up, down):
            task.cancel()

    def _person(self, header_lines: list[str]) -> str | None:
        for line in header_lines:
            name, _, value = line.partition(':')
            if name.strip().lower() != 'proxy-authorization':
                continue
            value = value.strip()
            if value[:6].lower() != 'basic ':
                return None
            try:
                _, _, password = base64.b64decode(value[6:].strip()).decode('utf-8').partition(':')
            except (ValueError, UnicodeDecodeError):
                return None
            with self._lock:
                for token, person in self._tokens.items():
                    if hmac.compare_digest(token, password):
                        return person
            return None
        return None

    @staticmethod
    async def _resolve(host: str, port: int) -> str | None:
        """An address to connect to, or None when every address is one the
        proxy must not reach (this machine, link-local)."""
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return None
        addresses = [info[4][0] for info in infos]
        if not addresses or any(_blocked_address(a) for a in addresses):
            return None
        return addresses[0]


_proxy: KeyProxy | None = None
_proxy_guard = threading.Lock()


def get_proxy() -> KeyProxy:
    global _proxy
    with _proxy_guard:
        if _proxy is None:
            _proxy = KeyProxy()
        return _proxy


def proxy_env(url: str, bundle: str, ca_file: str) -> dict[str, str]:
    """Variables that send a command's HTTPS through the proxy and make it
    trust the proxy's CA (on top of the usual ones)."""
    no_proxy = 'localhost,127.0.0.1,::1,.localhost'
    return {
        'HTTPS_PROXY': url, 'https_proxy': url, 'NO_PROXY': no_proxy, 'no_proxy': no_proxy,
        # Node 22.21+/24 fetch only uses the proxy variables when asked to.
        'NODE_USE_ENV_PROXY': '1',
        'SSL_CERT_FILE': bundle, 'REQUESTS_CA_BUNDLE': bundle, 'CURL_CA_BUNDLE': bundle, 'GIT_SSL_CAINFO': bundle,
        'PIP_CERT': bundle, 'NODE_EXTRA_CA_CERTS': ca_file, 'DENO_CERT': ca_file,
    }
