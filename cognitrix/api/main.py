import asyncio
import os
from contextlib import asynccontextmanager

import aiofiles
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
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
    app_sleeper = None
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
        from ..hosted_apps.store import sleeper_loop
        app_sleeper = asyncio.create_task(sleeper_loop())
        try:
            # Apps published from other folders before the apps folder existed.
            from ..hosted_apps.store import move_into_apps_folder
            await move_into_apps_folder()
        except Exception:
            import logging
            logging.getLogger('cognitrix.log').exception('Could not move apps into the apps folder')
        try:
            from ..meetings.notes import fail_interrupted
            await fail_interrupted()
        except Exception:
            import logging
            logging.getLogger('cognitrix.log').exception('Could not tidy up meeting notes')
        try:
            # Agents list the shared skills in their prompt from this cache;
            # only the CLI used to fill it, so server chats saw no skills.
            from ..skills.manager import get_skill_manager
            await get_skill_manager().discover_all()
        except Exception:
            import logging
            logging.getLogger('cognitrix.log').exception('Could not load skills at startup')
        try:
            from ..agents.templates import upgrade_default_assistants
            await upgrade_default_assistants()
        except Exception:
            import logging
            logging.getLogger('cognitrix.log').exception('Could not update default assistant prompts')
        try:
            from ..channels.service import start_channels
            await start_channels()
        except Exception:
            import logging
            logging.getLogger('cognitrix.log').exception('Chat app bots did not start')
        yield
    finally:
        cleanup_errors = []
        try:
            from ..channels.service import stop_channels
            await stop_channels()
        except BaseException as exc:
            cleanup_errors.append(exc)
        tasks = tuple(task for task in (scheduler, recovery, app_sleeper) if task is not None)
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


# What a page served by this engine may load and run. Everything the app needs
# ships with it, so scripts come only from the engine itself; images may come
# from anywhere because agents' replies link to them. COGNITRIX_CSP=off drops
# the header, any other value replaces the policy. The desktop app is left
# alone: its window talks to the shell in ways a strict policy can block.
_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https: http:; "
    "media-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def _content_security_policy() -> str:
    configured = os.environ.get('COGNITRIX_CSP', '').strip()
    if configured.lower() in ('off', '0', 'false', 'none'):
        return ''
    if configured:
        return configured
    return '' if os.environ.get('COGNITRIX_DESKTOP') == '1' else _DEFAULT_CSP


class SecurityHeaders:
    """Plain ASGI middleware (not BaseHTTPMiddleware, which gets in the way of
    the streaming endpoints)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        # The interactive API docs load their own scripts from a CDN.
        docs_page = scope.get('path', '') in ('/docs', '/redoc', '/docs/oauth2-redirect')

        async def send_with_headers(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers') or [])
                present = {name.lower() for name, _ in headers}
                extra = [(b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'same-origin')]
                content_type = next((value for name, value in headers if name.lower() == b'content-type'), b'')
                policy = _content_security_policy()
                if policy and not docs_page and content_type.startswith(b'text/html'):
                    extra.append((b'content-security-policy', policy.encode('latin-1', 'ignore')))
                headers.extend(item for item in extra if item[0] not in present)
                message = {**message, 'headers': headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)


class ClientTimezone:
    """Remember the browser's timezone (X-Timezone) for this request, so the
    date agents are told is the person's date, not the server's."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from ..common.clock import clean_timezone, client_timezone

        raw = next((value for name, value in scope.get('headers') or [] if name == b'x-timezone'), b'')
        name = clean_timezone(raw.decode('latin-1', 'ignore')) if raw else ''
        if not name:
            return await self.app(scope, receive, send)
        token = client_timezone.set(name)
        try:
            await self.app(scope, receive, send)
        finally:
            client_timezone.reset(token)


app.add_middleware(ClientTimezone)

app.include_router(api_router)
# Apps agents built, served at /a/<slug>/~<token>/ (before the SPA catch-all).
from .routes.hosted_apps import app_proxy  # noqa: E402

app.include_router(app_proxy)
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


def _top_level_build_files() -> dict[str, str]:
    """Request path -> file, for files directly in the build folder and its
    icons folder. Pages (.html) are left to the SPA fallback."""
    files: dict[str, str] = {}
    for prefix, folder in (('', FRONTEND_BUILD_DIR), ('icons/', FRONTEND_BUILD_DIR / 'icons')):
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if entry.is_file() and entry.suffix != '.html':
                files[prefix + entry.name] = str(entry)
    return files


# SPA fallback — MUST be registered last so real routes (api, /health, the
# static mounts) aren't shadowed by this catch-all.
@app.get("/{path:path}")
async def index(request: Request, path: str):
    # Unknown API paths are real 404s, not the SPA's index.html with a 200.
    if path == 'api' or path.startswith(('api/', 'v1/')) or path == 'v1':
        raise HTTPException(status_code=404, detail='Not Found')
    # Files at the top of the build (icons, the web app manifest, theme.js).
    # Looked up among the files that are really there, so the requested path
    # never becomes part of a file path.
    static_file = _top_level_build_files().get(path)
    if static_file is not None:
        return FileResponse(static_file)
    index_file = FRONTEND_BUILD_DIR / 'index.html'
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail='Frontend is not built (run `npm run build` in frontend/)')
    async with aiofiles.open(index_file, encoding='utf-8') as file:
        content = await file.read()

    return HTMLResponse(content)
