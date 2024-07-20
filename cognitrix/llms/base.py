import json
from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional, TypeAlias, TypedDict
from openai import OpenAI
import logging
import inspect

from cognitrix.utils import xml_to_dict, image_to_base64
from cognitrix.tools.base import Tool

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

LLMList: TypeAlias = List['LLM']

class LLMResponse:
    """Class to handle and separate LLM responses into text and tool calls."""
    
    def __init__(self, llm_response: Optional[str]=None):
        self.chunks = []
        self.llm_response = llm_response
        self.current_chunk: str = ''
        self.text: Optional[str] = None
        self.result: Optional[str] = None
        self.tool_calls: Optional[Dict[str, Any]] = None
        self.artifacts: Optional[List[Dict[str, Any]]] = None
        self.observation: Optional[str] = None
        self.thought: Optional[str] = None
        self.type: Optional[str] = None
        self.before: Optional[str] = None
        self.after: Optional[str] = None

        # self.parse_llm_response()
    
    def add_chunk(self, chunk):
        self.current_chunk = chunk
        self.chunks.append(chunk)
        self.parse_llm_response()

    def parse_llm_response(self):
        full_response = ''.join(self.chunks)
        response_data = xml_to_dict(full_response)

        try:
            if isinstance(response_data, dict):
                response = response_data['response']
                if isinstance(response, dict):
                    for key, value in response.items():
                        if key == 'result':
                            self.text = value
                        setattr(self, key, value)

                else:
                    self.text = response

        except Exception as e:
            logger.exception(e)
            self.text = str(response_data)
            

class LLM(BaseModel):
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
    
    # system_prompt: str = Field(default='')
    # """System prompt to use for context."""
    
    is_multimodal: bool = Field(default=False)
    """Whether the model is multimodal."""
    
    provider: str = Field(default="")
    """This is set to the name of the class"""
    
    # chat_history: List[Dict[str, str]] = []
    # """Chat history stored as a list of responses"""
    
    # tools: List[Dict] = []
    # """Functions calling tools formatted for this specific llm provider"""
    
    supports_tool_use: bool = True
    """Whether the provider supports tool use"""
    
    client: Any = None
    """The client object for the llm provider"""
    
    def __init__(self, **data):
        super().__init__(**data)
        self.provider = self.__class__.__name__
    
    def format_query(self, message: dict[str, str], chat_history: List[Dict[str, str]] = []) -> list:
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
                messages.append({
                    "role": fm['role'].lower(),
                    "content": fm['message']
                })
            elif fm['type'] == 'image':
                base64_image = image_to_base64(fm['image']) # type: ignore
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

    
    # def format_tools(self, tools: list[dict[str, Any]]):
    #     """Format tools for the provider sdk"""
    #     for tool in tools:
    #         f_tool = {
    #             "type": "function",
    #             "function": {
    #                 "name": tool['name'].replace(' ', '_'),
    #                 "description": tool['description'][:1024],
    #                 "parameters": {
    #                     "type": "object",
    #                     "properties": {},
    #                     "required": tool['required'],
    #                 },
    #             },
    #         }
    #         for key, value in tool['parameters'].items():
    #             f_tool['function']['parameters']['properties'][key] = {'type': value}
            
    #         self.tools.append(f_tool)
    
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
            llm = [f[1] for f in inspect.getmembers(module, inspect.isclass) if (len(f) and f[0].lower() == provider)]
            return llm[0] if len(llm) else None
        except Exception as e:
            logging.exception(e)
            return None
    
    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], **kwds: Any):
        """Generates a response to a query using the OpenAI API.

        Args:
            query (dict): The query to generate a response to.
            system_prompt (str): System prompt for the agent
            chat_history (list): Chat history

        Returns:
            A string containing the generated response.
        """

        if not self.client:
            self.client = OpenAI(api_key=self.api_key)
            if self.base_url:
                self.client.base_url = self.base_url
            
        formatted_messages = self.format_query(query, chat_history)
        
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "user", "content": system_prompt},
                *formatted_messages
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        response = LLMResponse()
        for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                response.add_chunk(chunk.choices[0].delta.content)
                yield response