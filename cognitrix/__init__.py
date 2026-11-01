from .common.encoding import ensure_utf8_stdio as _ensure_utf8_stdio

# Every entry point (CLI, desktop engine, uvicorn, Celery worker) imports this
# package. On Windows a redirected stdout defaults to cp1252, so the first
# emoji or non-latin character a tool or status line prints would raise
# "'charmap' codec can't encode ..." and fail the tool call.
_ensure_utf8_stdio()

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
