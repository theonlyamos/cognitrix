from .agents import Agent as Agent
from .providers import LLM as LLM
from .providers import LLMResponse as LLMResponse
from .tools import Tool as Tool

# Backward compatibility aliases
def _llm_openai(): return LLM.load_llm("openai")
def _llm_openrouter(): return LLM.load_llm("openrouter")
def _llm_ollama(): return LLM.load_llm("ollama")
OpenAI = _llm_openai
OpenRouter = _llm_openrouter
Ollama = _llm_ollama

__all__ = [
    "Tool",
    "Agent",
    "LLM",
    "LLMResponse",
    "OpenAI",
    "OpenRouter",
    "Ollama",
]
