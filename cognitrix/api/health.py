"""Lightweight readiness checks that do not import the SPA application."""

import asyncio
from collections.abc import Callable

from fastapi import HTTPException

from ..celery_worker import broker_available


async def task_runtime_health(
    probe: Callable[[], bool] = broker_available,
) -> dict[str, bool]:
    if not await asyncio.to_thread(probe):
        raise HTTPException(status_code=503, detail='Task runtime unavailable')
    return {'status': True}
