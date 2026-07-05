"""Per-API-key rate limiting. JWT/browser traffic never passes through here.

ponytail: per-process sliding window — swap for a Redis window if cognitrix
ever runs multi-worker/multi-instance (limits currently multiply per process).
"""

import os
import time
from collections import deque

from fastapi import HTTPException

WINDOW_SECONDS = 60.0

_windows: dict[str, deque] = {}


def _default_limit() -> int:
    try:
        return int(os.getenv('COGNITRIX_API_RATE_LIMIT', '60'))
    except ValueError:
        return 60


def check_rate_limit(key) -> None:
    """Record one request for this key; raise 429 when over its per-minute cap."""
    limit = key.rate_limit or _default_limit()
    if limit <= 0:
        return
    now = time.monotonic()
    window = _windows.setdefault(key.id, deque())
    while window and now - window[0] > WINDOW_SECONDS:
        window.popleft()
    if len(window) >= limit:
        retry_after = max(1, int(WINDOW_SECONDS - (now - window[0])) + 1)
        raise HTTPException(
            status_code=429,
            detail="API key rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    window.append(now)
