from cognitrix.providers.base import LLM, LLMResponse
from typing import Any, Dict, List, Union
import google.genai as genai
from google.genai.types import GenerateContentConfig, Content, Tool, GoogleSearch, Part, FunctionDeclaration
from dotenv import load_dotenv
from PIL import Image
import logging
import sys
import os
import io
import asyncio
from google.api_core import exceptions as google_exceptions

from cognitrix.utils import image_to_base64

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')
load_dotenv()


class Google(LLM):
    """A class for interacting with the Gemini API.

    Args:
        model (str): The name of the OpenAI model to use
        temperature (float): The temperature to use when generating text
        api_key (str): Your OpenAI API key
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """
    model: str = 'gemini-2.5-flash-preview-05-20'
    """model endpoint to use""" 
    
    temperature: float = 0.2
    """What sampling temperature to use.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    api_key: str = os.getenv('GOOGLE_API_KEY', '')
    """GOOGLE API key""" 
    
    max_tokens: int = 8192
    """Maximum output tokens"""
    
    supports_system_prompt: bool = True
    """Flag to indicate if system prompt should be supported"""
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""
    
    supports_tool_use: bool = False
    """Whether the model supports tool use."""
    
    def format_tools(self, tools: list[dict[str, Any]]) -> List[FunctionDeclaration]:
        """Formats tools into the specified JSON schema structure."""
        formatted_tools = []
        for tool_spec in tools:
            # Assuming the input tool_spec largely matches the desired format
            formatted_tool = {
                "name": tool_spec['function'].get("name", "").replace(' ', '_'), # Sanitize name
                "description": tool_spec['function'].get("description", ""),
                "parameters": tool_spec['function'].get("parameters", {"type": "object", "properties": {}, "required": []})
            }

            # Basic validation/ensure structure - assuming input `parameters` is already correct
            if "type" not in formatted_tool["parameters"]:
                 formatted_tool["parameters"]["type"] = "object"
            if "properties" not in formatted_tool["parameters"]:
                 formatted_tool["parameters"]["properties"] = {}
            # No need to truncate description based on the example format

            formatted_tools.append(formatted_tool)

        return formatted_tools
    
    def format_query(self, message: dict[str, Any], chat_history: List[Dict[str, str]]) -> list:
        """Formats messages for the Gemini API"""
        formatted_message = [*chat_history, message]
        
        messages = []
        
        for fm in formatted_message:
            if fm['type'] == 'text': 
                new_message: Union[str, Dict[str, Any]] = fm['content']
                text_message = new_message['llm_response'] if isinstance(new_message, dict) else new_message
                role = "user" if fm['role'].lower() == "user" else "model"
                messages.append(Content(role=role, parts=[Part.from_text(text=text_message)]))
                
            elif fm['type'] == 'image':
                screenshot_bytes = io.BytesIO()
                
                fm['content'].save(screenshot_bytes, format='JPEG') # type: ignore
                # upload_image = Image.open(screenshot_bytes)
                role = "user" if fm['role'].lower() == "user" else "model"
                messages.append(Content(role=role, parts=[
                    Part.from_bytes(screenshot_bytes.getvalue(), mime_type='image/jpeg'), # type: ignore
                    Part.from_text(text='Above is the screenshot')
                ]))
                

        return messages

    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], stream: bool = False, tools: Any = [], **kwds: Any):
        try:
            client = genai.Client(
                api_key=self.api_key
            )
            
            tools = [
                # Tool(google_search=GoogleSearch()),
                Tool(function_declarations=self.format_tools(tools))
            ]
            
            generate_content_config = GenerateContentConfig(
                tools=tools,
                response_mime_type="text/plain",
                system_instruction=[
                    Part.from_text(text=system_prompt)
                ]
            )
            
            # genai.configure(
            #     api_key=self.api_key,
            #     client_options={
            #         'api_endpoint': 'gateway.helicone.ai',
            #     },
            #     default_metadata=[
            #         ('helicone-auth', f'Bearer {os.environ.get("HELICONE_API_KEY")}'),
            #         ('helicone-target-url', 'https://generativelanguage.googleapis.com')
            #     ],
            #     transport="rest"
            # )
            
            contents = self.format_query(query, chat_history)

            # if not self.client:
            #     self.client = genai.GenerativeModel(model_name=self.model, generation_config=generation_config)


            completion = client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=generate_content_config,
            )
            
            response = LLMResponse()
    
            for chunk in completion:
                if chunk.text:
                    response.add_chunk(chunk.text)
            yield response

        except google_exceptions.GoogleAPIError as e:
            logger.error(f"Google API error: {str(e)}")
            yield LLMResponse(llm_response=f"Error: Google API encountered an issue - {str(e)}")
        except asyncio.TimeoutError:
            logger.error("Request to Google API timed out")
            yield LLMResponse(llm_response="Error: Request to Google timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in GoogleLLM __call__ method: {str(e)}")
            yield LLMResponse(llm_response=f"An unexpected error occurred in GoogleLLM: {str(e)}")

