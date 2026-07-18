import asyncio
from contextlib import asynccontextmanager

import aiofiles
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..celery_worker import broker_available
from ..config import FRONTEND_BUILD_DIR, initialize_database, settings
from ..media.staging import (
    start_attachment_maintenance,
    stop_attachment_maintenance,
)
from ..tasks.recovery import recovery_loop, run_recovery_pass
from ..tasks.scheduler import scheduler_loop
from .routes import api_router
from .routes.openai_compat import openai_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotent — the CLI already ran it, but a bare
    # `uvicorn cognitrix.api.main:app` must work too.
    await initialize_database()
    await run_recovery_pass()
    scheduler = None
    recovery = None
    maintenance_started = False
    try:
        scheduler = asyncio.create_task(scheduler_loop())
        recovery = asyncio.create_task(
            recovery_loop(
                interval_seconds=settings.task_recovery_interval_seconds,
            )
        )
        start_attachment_maintenance()
        maintenance_started = True
        yield
    finally:
        cleanup_errors = []
        tasks = tuple(task for task in (scheduler, recovery) if task is not None)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            if maintenance_started:
                await stop_attachment_maintenance()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            raise cleanup_errors[0]


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(api_router)
# OpenAI-compatible shim at the app root (/v1). MUST be registered before the
# SPA catch-all below, or GET /v1/models would be served index.html with 200.
app.include_router(openai_api)
app.mount('/css', StaticFiles(directory=FRONTEND_BUILD_DIR / 'css', html=True),  name='static')
app.mount('/assets', StaticFiles(directory=FRONTEND_BUILD_DIR / 'assets', html=True),  name='static')
app.mount('/webfonts', StaticFiles(directory=FRONTEND_BUILD_DIR / 'webfonts', html=True),  name='static')
app.mount('/fonts', StaticFiles(directory=FRONTEND_BUILD_DIR / 'fonts'),  name='fonts')


@app.get('/health')
async def healthcheck():
    if not await asyncio.to_thread(broker_available):
        raise HTTPException(status_code=503, detail='Task runtime unavailable')
    return {'status': True}


# SPA fallback — MUST be registered last so real routes (api, /health, the
# static mounts) aren't shadowed by this catch-all.
@app.get("/{path:path}")
async def index(request: Request, path: str):
    index_file = FRONTEND_BUILD_DIR / 'index.html'
    async with aiofiles.open(index_file) as file:
        content = await file.read()

    return HTMLResponse(content)
