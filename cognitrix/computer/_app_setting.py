"""``app_setting`` for code under cognitrix/computer.

The sandbox image ships only this package (sandbox/desktop/Dockerfile), not
cognitrix/app_settings.py, so the supervisor must still import cleanly there.
Inside the image these settings are never consulted, so the default is right.
"""
from __future__ import annotations


def app_setting(name: str, default: str = '') -> str:
    try:
        from cognitrix.app_settings import get
    except ImportError:  # the sandbox image: only cognitrix/computer is there
        return default
    return get(name, default)
