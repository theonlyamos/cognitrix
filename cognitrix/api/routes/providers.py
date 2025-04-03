from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ...providers import LLM

providers_api = APIRouter(
    prefix='/providers'
)

@providers_api.get('')
async def list_tools():
    providers = LLM.list_llms()
    response = [provider().dict() for provider in providers]
    
    return response

@providers_api.get('/{provider_name}')
async def load_provider(provider_name: str):
    provider = LLM.load_llm(provider_name)
    response = {}
    if provider:
        response = provider.dict()
    
    return JSONResponse(response)