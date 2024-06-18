from ..llms import Together
from ..llms import Cohere
from ..agents import AIAssistant
from ..tools import (
    Calculator, YoutubePlayer,PythonREPL,
    InternetBrowser, FSBrowser
)

from .routes import api_router

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(api_router)

@app.get('/health')
async def healthcheck():
    return {'status': True}