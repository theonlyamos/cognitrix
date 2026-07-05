from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from cognitrix.common.security import crud_scope, redact_secrets

from ...providers import LLM

providers_api = APIRouter(
    prefix='/providers',
    dependencies=[Depends(crud_scope)],
)

def _llm_to_dict(llm) -> dict:
    """Serialize LLM to dict (Pydantic v1/v2 compatible)."""
    return getattr(llm, 'model_dump', getattr(llm, 'dict', lambda: {}))()


@providers_api.get('/{provider_name}')
async def load_provider(provider_name: str):
    provider = LLM.load_llm(provider_name)
    response: dict = _llm_to_dict(provider) if provider else {}
    # Never return the provider api_key to the client.
    return JSONResponse(redact_secrets(response))
