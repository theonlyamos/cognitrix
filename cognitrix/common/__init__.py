"""Common helpers.

Re-exports are lazy (PEP 562): importing a submodule like
``cognitrix.common.safe_exec`` runs this package init, and eagerly importing
``security`` here dragged fastapi (~1s) into every startup. Attribute access
(``from cognitrix.common import hash_password``) still works and loads on demand.
"""

import importlib

_LAZY = {
    'Token': 'security',
    'authenticate': 'security',
    'create_access_token': 'security',
    'get_current_user': 'security',
    'get_user': 'security',
    'hash_password': 'security',
    'identity': 'security',
    'verify_password': 'security',
    'Utils': 'utils',
}


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f'.{module}', __name__), name)


__all__ = [
    "Utils",
    "identity",
    "authenticate",
    "get_user",
    "get_current_user",
    "create_access_token",
    "hash_password",
    "verify_password",
    "Token",
]
