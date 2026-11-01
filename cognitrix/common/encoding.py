"""Text-encoding helpers that keep Cognitrix working on Windows.

Windows still defaults to a legacy code page (usually cp1252) for redirected
stdout/stderr, for ``open()`` without an ``encoding`` and for subprocess pipes.
Model output, tool parameters and status messages routinely contain characters
outside that code page (emoji, curly quotes, non-latin text), and writing them
raises ``UnicodeEncodeError: 'charmap' codec can't encode ...``. When that
happens inside the tool loop the whole tool call fails.

Keep this module dependency-free: it is imported from ``cognitrix/__init__``.
"""
from __future__ import annotations

import locale
import logging
import os
import sys
from collections.abc import Mapping

logger = logging.getLogger('cognitrix.log')


def _is_utf8(encoding: str | None) -> bool:
    return (encoding or '').lower().replace('-', '').replace('_', '') in ('utf8', 'utf8sig')


def ensure_utf8_stdio() -> None:
    """Switch stdout/stderr to UTF-8 (replacing anything unencodable).

    No-op for streams that are already UTF-8, missing (``pythonw``/windowed
    PyInstaller builds set them to ``None``) or not reconfigurable.
    """
    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name, None)
        if stream is None or _is_utf8(getattr(stream, 'encoding', None)):
            continue
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except Exception:  # pragma: no cover - exotic stream objects
            pass


def decode_output(data: bytes | None) -> str:
    """Decode subprocess output: UTF-8 first, then the OS code page.

    Most tools (git, python, node) print UTF-8, while native Windows commands
    print in the console code page. Never raises.
    """
    if not data:
        return ''
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass
    fallback = locale.getpreferredencoding(False) or 'utf-8'
    try:
        return data.decode(fallback, errors='replace')
    except LookupError:
        return data.decode('utf-8', errors='replace')


def utf8_child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for child processes that makes Python children write UTF-8
    to their pipes instead of the Windows code page."""
    merged = dict(os.environ if env is None else env)
    merged['PYTHONIOENCODING'] = 'utf-8'
    merged.setdefault('PYTHONUTF8', '1')
    return merged


def status_print(*objects, markup: bool = False, **kwargs) -> None:
    """Print a progress/status line to the terminal without ever raising.

    Status output is cosmetic: a console that can't show a character, or a
    tool parameter that happens to look like Rich markup (``[/red]``), must
    not fail the tool call that triggered it. Markup is off by default for
    that reason; pass ``markup=True`` only for fixed strings.
    """
    try:
        from rich import get_console

        get_console().print(*objects, markup=markup, highlight=False, **kwargs)
    except Exception as exc:
        logger.debug("status output dropped: %s", exc)
