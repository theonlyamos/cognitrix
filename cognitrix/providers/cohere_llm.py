import cohere
from cohere.errors.client_closed_request_error import ClientClosedRequestError
from cognitrix.providers.base import LLM, LLMResponse
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import logging
import sys
import os
import ast
import asyncio

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.INFO
)
logger = logging.getLogger('cognitrix.log')
load_dotenv()

class Cohere(LLM):
    """A class for interacting with the Cohere API.

    Args:
        model (str): The name of the OpenAI model to use
        temperature (float): The temperature to use when generating text
        api_key (str): Your OpenAI API key
        chat_history (list): Chat history
        max_tokens (int): The maximum number of tokens to generate in the completion
        supports_system_prompt (bool): Flag to indicate if system prompt should be supported
        system_prompt (str): System prompt to prepend to queries
    """
    model: str = 'command-r-plus'
    """model endpoint to use""" 
    
    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    api_key: str = os.getenv('CO_API_KEY', '')
    """Cohere API key""" 
    
    supports_tool_use: bool = False
    
    def format_query(self, chat_history: List[Dict[str, str]] = []) -> list:
        """Formats messages for the Gemini API"""
        formatted_messages = []
        
        for fm in chat_history:
            msg = fm.copy()
            if fm['role'].lower() != 'user':
                msg['role'] = 'Chatbot'
            formatted_messages.append(msg)

        return formatted_messages
    
    def format_tools(self, tools: list[dict[str, Any]]):
        """Format tools for the groq sdk"""
        for tool in tools:
            f_tool = {
                "name": tool['name'],
                "description": tool['description'],
                "parameter_definitions": {}
            }
            for key, value in tool['parameters'].items():
                f_tool['parameter_definitions'][key] = {
                    'type': value,
                    'required': (key in tool['required'])
                }
            
            # self.tools.append(f_tool)

    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], **kwds: Any):
        try:
            if not self.client:
                self.client = cohere.Client(api_key=self.api_key)
            
            response = LLMResponse()
            chat_history = self.format_query(chat_history)
            
            stream = self.client.chat_stream( 
                model=self.model,
                message=query['content'],
                temperature=self.temperature,
                preamble=system_prompt, # type: ignore
                chat_history=chat_history,
                prompt_truncation='auto',
                citation_quality='accurate',
                connectors=[{"id": "web-search"}],
                # tools=[
                #     {"name":"calculator"},
                #     {"name":"python_interpreter"},
                #     {"name":"internet_search"}
                # ]
            )
            
            for event in stream:
                if event.event_type == 'text-generation' or event.event_type == 'tool-calls-chunk':
                    if hasattr(event, 'text'):
                        response.add_chunk(event.text)
                        yield response
                    elif event.event_type == 'stream-end':
                        response.add_chunk(event.response.result)
                        yield response
            
            # yield response

        except ClientClosedRequestError as e:
            logger.error(f"Cohere API error: {str(e)}")
            yield LLMResponse(llm_response=f"Error: Cohere API encountered an issue - {str(e)}")
        except asyncio.TimeoutError:
            logger.error("Request to Cohere API timed out")
            yield LLMResponse(llm_response="Error: Request to Cohere timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in CohereLLM __call__ method: {str(e)}")
            yield LLMResponse(llm_response=f"An unexpected error occurred in CohereLLM: {str(e)}")
