"""How this engine dials an address on the tailnet.

- **The machine's own Tailscale** (a PC, a server with Tailscale installed):
  tailnet addresses are ordinary addresses. Dial them straight.
- **The built-in Tailscale** (a server in Docker or on Fly, see ``daemon.py``)
  runs without a network device, so nothing on this machine routes a tailnet
  address. It offers a SOCKS5 proxy on this machine instead. ``reach`` opens
  a small listener on 127.0.0.1 that passes each connection through that
  proxy, so every caller keeps using a plain ``http://127.0.0.1:<port>`` or
  ``ws://`` address with whatever client it already has.

The listener is on this machine only. What it leads to still asks for its own
token (the agents' computer does), like the proxy it stands in front of.
"""
from __future__ import annotations

import ipaddress
import logging
import select
import socket
import socketserver
import struct
import threading

logger = logging.getLogger('cognitrix.tailscale')

_CONNECT_TIMEOUT_S = 15.0
_lock = threading.Lock()
_forwards: dict[tuple[str, int], _Forward] = {}


def proxy_port() -> int:
    """The built-in Tailscale's local proxy port; 0 when tailnet addresses are
    dialled straight (the machine's own Tailscale, or none)."""
    from cognitrix.tailscale import daemon, service

    if service.mode() != service.BUILTIN or not daemon.running():
        return 0
    return daemon.proxy_port()


def _socks_connect(proxy: int, host: str, port: int) -> socket.socket:
    """A connection to ``host:port`` on the tailnet through the SOCKS5 proxy."""
    sock = socket.create_connection(('127.0.0.1', proxy), timeout=_CONNECT_TIMEOUT_S)
    try:
        sock.sendall(b'\x05\x01\x00')
        if _exactly(sock, 2) != b'\x05\x00':
            raise OSError('the Tailscale proxy refused the connection')
        try:
            ip = ipaddress.ip_address(host)
            where = (b'\x01' if ip.version == 4 else b'\x04') + ip.packed
        except ValueError:
            name = host.encode('idna')
            where = b'\x03' + bytes([len(name)]) + name
        sock.sendall(b'\x05\x01\x00' + where + struct.pack('>H', port))
        head = _exactly(sock, 4)
        if head[1] != 0:
            raise OSError(f"the tailnet address didn't answer (code {head[1]})")
        if head[3] == 1:
            _exactly(sock, 4 + 2)
        elif head[3] == 4:
            _exactly(sock, 16 + 2)
        else:
            _exactly(sock, _exactly(sock, 1)[0] + 2)
        sock.settimeout(None)
        return sock
    except BaseException:
        sock.close()
        raise


def _exactly(sock: socket.socket, count: int) -> bytes:
    data = b''
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise OSError('the Tailscale proxy closed the connection')
        data += chunk
    return data


def _pump(a: socket.socket, b: socket.socket) -> None:
    """Pass bytes both ways until either side closes."""
    try:
        while True:
            ready, _, _ = select.select([a, b], [], [], 600)
            if not ready:
                return  # nothing either way for ten minutes
            for src in ready:
                data = src.recv(65536)
                if not data:
                    return
                (b if src is a else a).sendall(data)
    except OSError:
        return
    finally:
        for sock in (a, b):
            try:
                sock.close()
            except OSError:
                pass


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Forward:
    """A listener on 127.0.0.1 for one tailnet ``host:port``."""

    def __init__(self, host: str, port: int):
        target = (host, port)

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                proxy = proxy_port()
                if not proxy:
                    return
                try:
                    upstream = _socks_connect(proxy, *target)
                except OSError as exc:
                    logger.debug("Couldn't reach %s:%s on the tailnet: %s", target[0], target[1], exc)
                    return
                _pump(self.request, upstream)

        self.server = _Server(('127.0.0.1', 0), Handler)
        self.port = int(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, name=f'tailnet-forward-{host}:{port}', daemon=True).start()

    def close(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except OSError:
            pass


def reach(host: str, port: int) -> tuple[str, int]:
    """Where to dial for ``host:port`` on the tailnet: the address itself, or
    a listener on this machine that leads there."""
    if not proxy_port():
        return host, int(port)
    key = (host, int(port))
    with _lock:
        forward = _forwards.get(key)
        if forward is None:
            forward = _forwards[key] = _Forward(*key)
        return '127.0.0.1', forward.port


def close_all() -> None:
    with _lock:
        forwards = list(_forwards.values())
        _forwards.clear()
    for forward in forwards:
        forward.close()
