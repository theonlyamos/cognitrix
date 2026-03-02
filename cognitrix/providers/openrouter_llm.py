import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv

from cognitrix.providers.base import LLM, LLMManager, LLMResponse

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')
load_dotenv()


class OpenRouter(LLM):
    """A class for interacting with OpenRouter API.
    
    OpenRouter provides access to 200+ LLMs through a unified OpenAI-compatible API.
    See https://openrouter.ai/docs for more information.

    Features:
        - Access to models from OpenAI, Anthropic, Google, Meta, Mistral, and more
        - Model variants: :free, :extended, :exacto, :thinking, :online, :nitro
        - Prompt caching support
        - Structured outputs (JSON mode)
        - Automatic provider fallback

    Args:
        model: The name of the model to use (default: z-ai/glm-4.5-air:free)
        temperature: The temperature to use when generating text.
        api_key: Your OpenRouter API key.
        referer: Your website URL (required for OpenRouter tracking)
        title: Your application name (optional, for OpenRouter tracking)
    """
    model: str = 'z-ai/glm-4.5-air:free'
    """model endpoint to use"""

    temperature: float = 0.7
    """What sampling temperature to use."""

    chat_history: list[str] = []
    """Chat history"""

    api_key: str = os.getenv('OPENROUTER_API_KEY', '')
    """OpenRouter API key"""

    base_url: str = 'https://openrouter.ai/api/v1'
    """Base URL for the OpenRouter API"""

    supports_system_prompt: bool = True
    """Flag to indicate if system prompt should be supported"""

    system_prompt: str = ""
    """System prompt to prepend to queries"""

    max_tokens: int = 4096
    """Maximum output tokens"""

    provider: str = "OpenRouter"
    
    referer: str = 'https://github.com/cognitrix'
    """HTTP-Referer header (required by OpenRouter)"""
    
    title: str = 'Cognitrix'
    """X-Title header (optional, for OpenRouter tracking)"""
    
    response_format: Optional[dict] = None
    """Response format for structured outputs. Use {"type": "json_object"} for JSON mode."""
    
    enable_prompt_cache: bool = False
    """Enable prompt caching for supported models."""
    
    transforms: Optional[list[str]] = None
    """Message transforms. Options: 'middle-out' for context compression."""

    def __init__(self, **data: Any):
        super().__init__(**data)
        self.client = None

    async def __call__(self, prompt: list[dict[str, Any]], stream: bool = False, tools: Any = None, **kwds: Any):
        """Generate response with OpenRouter-specific headers and features."""
        if tools is None:
            tools = []
        
        from openai import OpenAI
        
        try:
            client_kwargs = {
                "api_key": self.api_key,
                "base_url": self.base_url,
                "default_headers": {
                    "HTTP-Referer": self.referer,
                    "X-Title": self.title,
                }
            }
            
            client = OpenAI(**client_kwargs)
            
            formatted_messages = LLMManager.format_query(self, prompt)
            formatted_tools = LLMManager.format_tools(tools) if tools else None
            
            completion_params = {
                "model": self.model,
                "messages": formatted_messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "stream": stream,
            }
            
            # OpenRouter-specific parameters
            if self.response_format:
                completion_params["response_format"] = self.response_format
            
            if self.enable_prompt_cache:
                completion_params["extra_body"] = {"prompt_cache": "auto"}
            
            if self.transforms:
                completion_params["extra_body"] = completion_params.get("extra_body", {})
                completion_params["extra_body"]["transforms"] = self.transforms
            
            if formatted_tools:
                completion_params["tools"] = formatted_tools
            
            if stream:
                return self._handle_streaming_response(client, completion_params)
            else:
                return await self._handle_non_streaming_response(client, completion_params)
            
        except Exception as e:
            logger.exception(f"OpenRouter error: {str(e)}")
            return LLMResponse(llm_response=f"Error: {str(e)}")

    async def _handle_streaming_response(self, client: 'OpenAI', params: dict[str, Any]):
        """Handle streaming response from OpenRouter."""
        try:
            stream = client.chat.completions.create(**params)
            response = LLMResponse()
            
            for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    response.add_chunk(chunk.choices[0].delta.content)
                    yield response
        except Exception as e:
            logger.exception(f"Error in OpenRouter streaming: {str(e)}")
            yield LLMResponse(llm_response=f"Streaming error: {str(e)}")

    async def _handle_non_streaming_response(self, client: 'OpenAI', params: dict[str, Any]):
        """Handle non-streaming response from OpenRouter."""
        try:
            response = client.chat.completions.create(**params)
            content = response.choices[0].message.content or ""
            
            # Extract reasoning tokens if present (for thinking models)
            reasoning = None
            if hasattr(response.choices[0].message, 'reasoning_content'):
                reasoning = response.choices[0].message.reasoning_content
            
            llm_response = LLMResponse(llm_response=content)
            if reasoning:
                llm_response.reasoning = reasoning
            return llm_response
        except Exception as e:
            logger.exception(f"Error in OpenRouter response: {str(e)}")
            return LLMResponse(llm_response=f"Response error: {str(e)}")

    def get_supported_models(self):
        import requests
        
        url = "https://openrouter.ai/api/v1/models"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            return [entry['id'] for entry in response.json().get('data', [])]
        return []
    
    @staticmethod
    def get_free_models():
        """Get list of free models (with :free variant)."""
        return [
            "openai/gpt-4o-mini:free",
            "anthropic/claude-3.5-sonnet:free",
            "meta-llama/llama-3.1-8b-instruct:free",
            "google/gemma-2-9b-it:free",
            "mistralai/mistral-7b-instruct:free",
        ]
    
    @staticmethod
    def get_fastest_models():
        """Get list of fastest models (with :nitro variant)."""
        return [
            "openai/gpt-4o: nitro",
            "anthropic/claude-3.5-sonnet: nitro",
            "google/gemini-pro-1.5: nitro",
        ]
