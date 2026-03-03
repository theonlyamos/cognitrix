from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...providers import LLM

providers_api = APIRouter(
    prefix='/providers'
)

def _llm_to_dict(llm) -> dict:
    """Serialize LLM to dict (Pydantic v1/v2 compatible)."""
    return getattr(llm, 'model_dump', getattr(llm, 'dict', lambda: {}))()


@providers_api.get('/{provider_name}')
async def load_provider(provider_name: str):
    provider = LLM.load_llm(provider_name)
    response: dict = _llm_to_dict(provider) if provider else {}
    return JSONResponse(response)
