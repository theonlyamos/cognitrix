import json
from odbms import Model
from pydantic import Field
from typing import Any, List, Dict, TypeAlias, Union
from openai import OpenAI
from rich import print
import os
import logging
import inspect
import asyncio
from openai import OpenAIError

from cognitrix.utils import image_to_base64
from cognitrix.utils.llm_response import LLMResponse

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

LLMList: TypeAlias = List['LLM']

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

    model: str = Field(default=None)
    """model endpoint to use""" 
  
    temperature: float = Field(default=0.4)
    """What sampling temperature to use.""" 

    api_key: str = Field(default=None)
    """API key""" 
    
    base_url: str = ''
    """Base url of local llm server"""
    
    max_tokens: int = Field(default=512)
    """The maximum number of tokens to generate in the completion.""" 
    
    supports_system_prompt: bool = Field(default=False)
    """Whether the model supports system prompts."""
    
    is_multimodal: bool = Field(default=False)
    """Whether the model is multimodal."""
    
    provider: str = Field(default="")
    """This is set to the name of the class"""
    
    # tools: List[Dict] = []
    # """Functions calling tools formatted for this specific llm provider"""
    
    supports_tool_use: bool = True
    """Whether the provider supports tool use"""
    
    client: Any = None
    """The client object for the llm provider"""
    
    def __init__(self, *args, **data):
        super().__init__(**data)
        if not 'provider' in data.keys():
            self.provider = self.__class__.__name__
    
    def format_query(self, message: dict[str, str], chat_history: List[Dict[str, Any]] = []) -> list:
        """Formats a message for the Claude API.

        Args:
            message (dict[str, str]): The message to be formatted for the Claude API.

        Returns:
            list: A list of formatted messages for the Claude API.
        """
        
        formatted_message = [*chat_history, message]
        
        messages = []
        
        for fm in formatted_message:
            if fm['type'] == 'text':
                new_message: Union[str, Dict[str, Any]] = fm['content']
                text_message = new_message['llm_response'] if isinstance(new_message, dict) else new_message
                text_message = text_message if fm['role'].lower() in ['user', 'assistant'] else "User: " + text_message
                message_role = fm['role'].lower() if fm['role'].lower() in ['user', 'assistant'] else 'user'
                full_message = {
                    "role": message_role,
                    "content": text_message
                }
                messages.append(full_message)
            elif fm['type'] == 'image':
                base64_image = image_to_base64(fm['content']) # type: ignore
                messages.append({
                    "role": fm['role'].lower(),
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
            else:
                print(fm)
        
        return messages

    
    def format_tools(self, tools: list[dict[str, Any]]):
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
    def load_llm(provider: str):
        """Dynamically load LLMs based on name"""
        try:
            provider = provider.lower()
            module = __import__(str(__package__), fromlist=[provider])
            llm: type[LLM] | None = next((f[1] for f in inspect.getmembers(module, inspect.isclass) if (len(f) and f[0].lower() == provider)), None)
            if not llm:
                raise Exception(f"LLM {provider} not found")
            return llm()
        except Exception as e:
            logging.exception(e)
            return None
    
    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], stream: bool = False, tools: Any = [], **kwds: Any):
        """Generates a response to a query using the OpenAI API.

        Args:
            query (dict): The query to generate a response to.
            system_prompt (str): System prompt for the agent
            chat_history (list): Chat history

        Returns:
            A string containing the generated response.
        """
        try:
            if not self.client:
                self.client = OpenAI(api_key=self.api_key)
                if 'helicone' in self.base_url:
                    self.client = OpenAI(
                        api_key=self.api_key, 
                        default_headers={'Helicone-Auth': f'Bearer {os.getenv("HELICONE_API_KEY")}'}
                    )
                if self.base_url:
                    self.client.base_url = self.base_url
            
            formatted_messages = self.format_query(query, chat_history)
            
            if self.model in ['o1-mini', 'o1-preview']:
                stream = False
                
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "user", "content": system_prompt},
                        *formatted_messages
                    ],
                    max_tokens=65536 if self.model == 'o1-mini' else 32768,
                )

            else:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        *formatted_messages
                    ],
                    tools=tools,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    stream=stream
                )
            response = LLMResponse()
            
            if not stream:
                if hasattr(completion.choices[0].message, 'tool_calls') and completion.choices[0].message.tool_calls:
                    for tool_call in completion.choices[0].message.tool_calls:
                        response.tool_call.append({'name': tool_call.function.name, 'arguments': json.loads(tool_call.function.arguments)})
                if completion.choices[0].message.content:
                    response.add_chunk(completion.choices[0].message.content)
                yield response
            elif stream and hasattr(completion, 'choices'):
                for chunk in completion:
                    if (hasattr(chunk, 'choices') and 
                        chunk.choices and 
                        hasattr(chunk.choices[0].delta, 'tool_calls') and 
                        chunk.choices[0].delta.tool_calls):
                        for tool_call in chunk.choices[0].delta.tool_calls:
                            response.tool_call.append({'name': tool_call.function.name, 'arguments': json.loads(tool_call.function.arguments)})
                        yield response
                    if chunk.choices and len(chunk.choices) and chunk.choices[0].delta.content is not None:
                        response.add_chunk(chunk.choices[0].delta.content)
                        yield response
            
        except OpenAIError as e:
            logger.error(f"OpenAI API error: {str(e)}")
            yield LLMResponse(llm_response=f"Error: OpenAI API encountered an issue - {str(e)}")
        except asyncio.TimeoutError:
            logger.error("Request to OpenAI API timed out")
            yield LLMResponse(llm_response="Error: Request timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in LLM __call__ method: {str(e)}")
            yield LLMResponse(llm_response=f"An unexpected error occurred: {str(e)}")
            