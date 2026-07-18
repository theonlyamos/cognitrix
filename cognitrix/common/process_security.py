"""Explicit authority boundary for processes launched on the host.

Argument inspection cannot contain an interpreter, package manager, VCS, or
other general process. Shared web/task turns therefore fail closed. Only a
direct local CLI entry point may inject ``TRUSTED_LOCAL`` authority.
"""

from __future__ import annotations

from enum import Enum


class HostProcessAccessError(PermissionError):
    """Raised before a host process is launched without explicit authority."""


class HostProcessMode(str, Enum):
    """Runtime-only process authority; the safe default is categorical denial."""

    DENY = 'deny'
    TRUSTED_LOCAL = 'trusted_local'


def require_host_process_authority(mode: HostProcessMode) -> None:
    """Reject every mode except the explicit local-operator capability."""
    if mode is not HostProcessMode.TRUSTED_LOCAL:
        raise HostProcessAccessError(
            'Host processes are unavailable in shared agent sessions; '
            'use the Read, Grep, Glob, Write, or Edit tools instead'
        )


__all__ = [
    'HostProcessAccessError',
    'HostProcessMode',
    'require_host_process_authority',
]
