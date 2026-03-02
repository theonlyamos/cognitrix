import inspect
import logging
import uuid
from typing import Any, TypeAlias

from odbms import Model
from openai import OpenAI, OpenAIError
from pydantic import Field

from cognitrix.utils import image_to_base64
from cognitrix.utils.llm_response import LLMResponse

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

class LLM(Model):
    """
    A class for representing a large language model.

    Args:
        model (str): The name of the OpenAI model to use
        temperature (float): The temperature to use when generating text
        api_key (str): Your OpenAI API key
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier for the LLM provider"""

    model: str = Field(default='')
    """model endpoint to use"""

    temperature: float = Field(default=0.4)
    """What sampling temperature to use."""

    api_key: str = Field(default='')
    """API key"""

    base_url: str = ''
    """Base url of local llm server"""

    max_tokens: int = Field(default=512)
    """The maximum number of tokens to generate in the completion."""

    supports_system_prompt: bool = Field(default=False)
    """Whether the model supports system prompts."""

    is_multimodal: bool = Field(default=False)
    """Whether the model is multimodal."""

    provider: str = Field(default="openai")
    """This is set to the name of the class"""

    supports_tool_use: bool = True
    """Whether the provider supports tool use"""

    client: Any = None
    """The client object for the llm provider"""

    def __init__(self, **data: Any):
        super().__init__(**data)
        if 'provider' not in data:
            self.provider = self.__class__.__name__

    def format_query(self, messages: list[dict[str, Any]]) -> list:
        """Delegate to LLMManager"""
        return LLMManager.format_query(self, messages)

    def format_tools(self, tools: list[dict[str, Any]]):
        """Delegate to LLMManager"""
        return LLMManager.format_tools(tools)

    @staticmethod
    def list_llms():
        """Delegate to LLMManager"""
        return LLMManager.list_llms()

    @staticmethod
    def load_llm(provider: str):
        """Delegate to LLMManager"""
        return LLMManager.load_llm(provider)

    def get_supported_models(self):
        """Delegate to LLMManager"""
        return LLMManager.get_supported_models(self)

    async def __call__(self, prompt: list[dict[str, Any]], stream: bool = False, tools: Any = None, **kwds: Any):
        """Delegate to LLMManager"""
        if tools is None:
            tools = []
        return await LLMManager.generate_response(self, prompt, stream, tools, **kwds)

LLMList: TypeAlias = list[LLM]

class LLMManager:
    """Manager class for LLM-related business logic"""

    @staticmethod
    def format_query(llm: LLM, messages: list[dict[str, Any]]) -> list:
        """Formats a message list for an LLM API.

        Args:
            llm (LLM): The LLM instance
            messages (List[Dict[str, Any]]): The list of messages to be formatted.

        Returns:
            list: A list of formatted messages.
        """

        formatted_messages = []

        for fm in messages:
            role = fm.get('role', 'user').lower()
            # Ensure role is one of the accepted values for most APIs
            if role not in ['system', 'user', 'assistant']:
                role = 'user'

            content = fm.get('content', '')
            msg_type = fm.get('type', 'text')

            if msg_type == 'text':
                formatted_messages.append({
                    "role": role,
                    "content": content
                })
            elif msg_type == 'image' and llm.is_multimodal:
                base64_image = image_to_base64(content) # type: ignore
                formatted_messages.append({
                    "role": role,
                    "content": [
                        {
                            "type": "text",
                            "text": "This is the result of the latest screenshot"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                })

        return formatted_messages

    @staticmethod
    def format_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Format tools for the provider sdk"""

        formatted_tools = []

        for tool in tools:
            f_tool = {
                "type": "function",
                "function": {
                    "name": tool['name'].replace(' ', '_'),
                    "description": tool['description'][:1024],
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": tool['required'],
                    },
                },
            }
            for key, value in tool['parameters'].items():
                f_tool['function']['parameters']['properties'][key] = {'type': value}

            formatted_tools.append(f_tool)

        return formatted_tools

    @staticmethod
    def list_llms():
        """List all supported LLMs"""
        try:
            module = __import__(str(__package__), fromlist=['__init__'])
            return  [f[1] for f in inspect.getmembers(module, inspect.isclass) if f[0] != 'LLM' and f[0] != 'LLMResponse']
        except Exception as e:
            logging.exception(e)
            return []

    @staticmethod
    def load_llm(provider: str) -> LLM | None:
        """Dynamically load LLMs based on name"""
        try:
            provider = provider.lower()
            module = __import__(str(__package__), fromlist=[provider])
            llm_class: type[LLM] | None = next((f[1] for f in inspect.getmembers(module, inspect.isclass) if (len(f) and f[0].lower() == provider)), None)
            if not llm_class:
                raise Exception(f"LLM {provider} not found")
            return llm_class()
        except Exception as e:
            logging.exception(e)
            return None

    @staticmethod
    def get_supported_models(llm: LLM):
        """Get supported models for an LLM"""
        client = OpenAI(api_key=llm.api_key, base_url=llm.base_url)
        return client.models.list()

    @staticmethod
    async def generate_response(llm: LLM, prompt: list[dict[str, Any]], stream: bool = False, tools: Any = None, **kwds: Any):
        """Generates a response to a query using the LLM.

        Args:
            llm (LLM): The LLM instance
            prompt (List[Dict[str, Any]]): A list of messages comprising the full context,
                                           including a 'system' role message.
            stream (bool): Whether to stream the response.
            tools (Any): A list of tools available for the LLM to use.

        Returns:
            An LLMResponse object containing the generated response.
        """
        if tools is None:
            tools = []
        try:
            client_kwargs = {"api_key": llm.api_key}
            
            if llm.base_url:
                client_kwargs["base_url"] = llm.base_url
            
            if 'helicone' in llm.base_url:
                from cognitrix.config import settings
                client_kwargs["default_headers"] = {'Helicone-Auth': f'Bearer {settings.get_api_key("helicone")}'}
            
            client = OpenAI(**client_kwargs)

            formatted_messages = LLMManager.format_query(llm, prompt)
            formatted_tools = LLMManager.format_tools(tools) if tools else None

            completion_params = {
                "model": llm.model,
                "messages": formatted_messages,
                "max_tokens": llm.max_tokens,
                "temperature": llm.temperature,
                "stream": stream,
            }

            if formatted_tools:
                completion_params["tools"] = formatted_tools

            if stream:
                return LLMManager._handle_streaming_response(client, completion_params)
            else:
                return await LLMManager._handle_non_streaming_response(client, completion_params)

        except OpenAIError as e:
            logger.error(f"OpenAI API error: {str(e)}")
            return LLMResponse(llm_response=f"Error: OpenAI API encountered an issue - {str(e)}")
        except TimeoutError:
            logger.error("Request to OpenAI API timed out")
            return LLMResponse(llm_response="Error: Request timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in LLM generate_response: {str(e)}")
            return LLMResponse(llm_response=f"An unexpected error occurred: {str(e)}")

    @staticmethod
    async def _handle_streaming_response(client: OpenAI, params: dict[str, Any]):
        """Handle streaming response from LLM"""
        try:
            stream = client.chat.completions.create(**params)
            response = LLMResponse()

            for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    response.add_chunk(chunk.choices[0].delta.content)
                    yield response
        except Exception as e:
            logger.exception(f"Error in streaming response: {str(e)}")
            yield LLMResponse(llm_response=f"Streaming error: {str(e)}")

    @staticmethod
    async def _handle_non_streaming_response(client: OpenAI, params: dict[str, Any]):
        """Handle non-streaming response from LLM"""
        try:
            response = client.chat.completions.create(**params)
            content = response.choices[0].message.content or ""
            return LLMResponse(llm_response=content)
        except Exception as e:
            logger.exception(f"Error in non-streaming response: {str(e)}")
            return LLMResponse(llm_response=f"Response error: {str(e)}")
