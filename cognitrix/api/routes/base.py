from fastapi import APIRouter
from ...config import API_VERSION

api_router = APIRouter(
    prefix=f"/api/{API_VERSION}"
)
