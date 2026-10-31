import asyncio
from contextlib import asynccontextmanager

import aiofiles
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..config import FRONTEND_BUILD_DIR, initialize_database, settings
from ..media.staging import (
    start_attachment_maintenance,
    stop_attachment_maintenance,
)
from ..tasks.recovery import recovery_loop, run_recovery_pass
from ..tasks.scheduler import scheduler_loop
from .health import task_runtime_health
from .routes import api_router
from .routes.openai_compat import openai_api


def _patch_odbms_json_schema() -> None:
    """odbms' PyObjectId has a pydantic-v1 style __get_pydantic_json_schema__
    (one argument), which made /openapi.json return 500 and /docs unusable.
    ponytail: remove once odbms ships the two-argument signature."""
    try:
        from odbms import model as odbms_model
    except Exception:
        return
    py_object_id = getattr(odbms_model, 'PyObjectId', None)
    if py_object_id is None:
        return

    def _json_schema(cls, _core_schema=None, _handler=None):
        return {'type': 'string'}

    # Models built at import time already hold a reference to the original
    # function, so swap its code in place rather than rebinding the name.
    original = py_object_id.__dict__.get('__get_pydantic_json_schema__')
    func = getattr(original, '__func__', None)
    if func is not None and func.__code__.co_argcount == 2:
        func.__code__ = _json_schema.__code__
        func.__defaults__ = _json_schema.__defaults__


_patch_odbms_json_schema()


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
# Static mounts are optional so the API also runs without a built frontend
# (API-only deploys, tests, a fresh clone before `npm run build`).
for _route, _subdir, _html in (
    ('/css', 'css', True),
    ('/assets', 'assets', True),
    ('/webfonts', 'webfonts', True),
    ('/fonts', 'fonts', False),
):
    if (FRONTEND_BUILD_DIR / _subdir).is_dir():
        app.mount(_route, StaticFiles(directory=FRONTEND_BUILD_DIR / _subdir, html=_html), name=_subdir)


@app.get('/health')
async def healthcheck():
    return await task_runtime_health()


# SPA fallback — MUST be registered last so real routes (api, /health, the
# static mounts) aren't shadowed by this catch-all.
@app.get("/{path:path}")
async def index(request: Request, path: str):
    # Unknown API paths are real 404s, not the SPA's index.html with a 200.
    if path == 'api' or path.startswith(('api/', 'v1/')) or path == 'v1':
        raise HTTPException(status_code=404, detail='Not Found')
    index_file = FRONTEND_BUILD_DIR / 'index.html'
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail='Frontend is not built (run `npm run build` in frontend/)')
    async with aiofiles.open(index_file) as file:
        content = await file.read()

    return HTMLResponse(content)
