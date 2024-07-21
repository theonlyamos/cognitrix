from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...agents import PromptGenerator
from ...config import API_VERSION

api_router = APIRouter(
    prefix=f"/api/{API_VERSION}"
)

class PromptData(BaseModel):
    agentName: Optional[str] = ''
    prompt: str
