import aiofiles
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..config import FRONTEND_BUILD_DIR, settings
from .routes import api_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(api_router)
app.mount('/css', StaticFiles(directory=FRONTEND_BUILD_DIR / 'css', html=True),  name='static')
app.mount('/assets', StaticFiles(directory=FRONTEND_BUILD_DIR / 'assets', html=True),  name='static')
app.mount('/webfonts', StaticFiles(directory=FRONTEND_BUILD_DIR / 'webfonts', html=True),  name='static')
app.mount('/fonts', StaticFiles(directory=FRONTEND_BUILD_DIR / 'fonts'),  name='fonts')


@app.get('/health')
async def healthcheck():
    return {'status': True}


# SPA fallback — MUST be registered last so real routes (api, /health, the
# static mounts) aren't shadowed by this catch-all.
@app.get("/{path:path}")
async def index(request: Request, path: str):
    index_file = FRONTEND_BUILD_DIR / 'index.html'
    async with aiofiles.open(index_file) as file:
        content = await file.read()

    return HTMLResponse(content)
