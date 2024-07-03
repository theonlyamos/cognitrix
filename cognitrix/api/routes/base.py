from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...agents.prompt_generator import PromptGenerator
from ...config import API_VERSION

api_router = APIRouter(
    prefix=f"/api/{API_VERSION}"
)

class PromptData(BaseModel):
    agentName: Optional[str] = ''
    prompt: str

@api_router.post('/generate')
def generate_agent_system_prompt(request: Request, data: PromptData):
    prompt = data.prompt
    name = data.agentName
    agent = PromptGenerator(llm=request.state.agent.llm)
    agent.llm.system_prompt = agent.prompt_template
    
    full_prompt = "## Agent Description"
    if name:
        full_prompt += f"""\n\n## Agent Name: {name}"""
    
    full_prompt += f"""\n\n{prompt}"""

    response = agent.generate(full_prompt)
    
    return JSONResponse({'status': True, 'data': response.text})
