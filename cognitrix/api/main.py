import aiofiles
from fastapi.responses import HTMLResponse
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .routes import api_router
from ..config import FRONTEND_BUILD_DIR

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(api_router)
app.mount('/css', StaticFiles(directory=FRONTEND_BUILD_DIR / 'css', html=True),  name='static')
app.mount('/assets', StaticFiles(directory=FRONTEND_BUILD_DIR / 'assets', html=True),  name='static')
app.mount('/webfonts', StaticFiles(directory=FRONTEND_BUILD_DIR / 'webfonts', html=True),  name='static')

@app.get("/{path:path}")
async def index(request: Request, path: str):
    index_file = FRONTEND_BUILD_DIR / 'index.html'
    content = ''
    async with aiofiles.open(index_file, 'r') as file:
        content = await file.read()
    
    return HTMLResponse(content)

@app.get('/health')
async def healthcheck():
    return {'status': True}