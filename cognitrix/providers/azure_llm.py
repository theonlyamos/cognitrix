import os
import logging
import inspect
import asyncio
from odbms import Model
from pydantic import Field
from typing import Any, List, Dict, TypeAlias
from openai import OpenAI
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import (
    AssistantMessage, 
    SystemMessage, 
    UserMessage,
    TextContentItem,
    ImageContentItem,
    ImageUrl,
    ImageDetailLevel
)
from azure.core.credentials import AzureKeyCredential

from cognitrix.providers.base import LLM
from cognitrix.utils import image_to_base64
from cognitrix.utils.llm_response import LLMResponse

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')


class Azure(LLM):

    model: str = 'gpt-4o'
    """model endpoint to use""" 
  
    temperature: float = Field(default=0.4)
    """What sampling temperature to use.""" 

    api_key: str = os.getenv('GITHUB_TOKEN', '')
    """API key""" 
    
    base_url: str = 'https://models.inference.ai.azure.com'
    """Base url of local llm server"""
    
    max_tokens: int = Field(default=1000)
    """The maximum number of tokens to generate in the completion.""" 
    
    supports_system_prompt: bool = Field(default=True)
    """Whether the model supports system prompts."""
    
    is_multimodal: bool = Field(default=True)
    """Whether the model is multimodal."""
    
    provider: str = Field(default="Azure")
    """This is set to the name of the class"""
    
    supports_tool_use: bool = True
    """Whether the provider supports tool use"""
    
    client: Any = None
    """The client object for the llm provider"""
    
    def format_query(self, message: dict[str, str], chat_history: List[Dict[str, str]] = []) -> list:
        """Formats a message for the Azure inference API endpoint.

        Args:
            message (dict[str, str]): The message to be formatted for the Azure inference API endpoint.
            chat_history (list[dict[str, str]]): The chat history to be formatted for the Azure inference API endpoint.

        Returns:
            list: A list of formatted messages for the Azure inference API endpoint.
        """
        
        formatted_message = [*chat_history, message]
        
        messages = []
        
        for fm in formatted_message:
            if fm['type'] == 'text':
                if fm['role'].lower() == 'user':
                    messages.append(UserMessage(content=fm['content']))
                else:
                    messages.append(AssistantMessage(content=fm['content']))
            elif fm['type'] == 'image':
                base64_image = image_to_base64(fm['content']) # type: ignore
                messages.append(UserMessage(content=[
                    TextContentItem(text="This is the result of the latest screenshot"),
                    ImageContentItem(
                        url=ImageUrl(url=f"data:image/jpeg;base64,{base64_image}"),
                        detail=ImageDetailLevel.HIGH
                    ) # type: ignore
                ]))
            else:
                logger.warning(f"Unsupported message type: {fm}")
        
        return messages

    
    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], **kwds: Any):
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
                self.client = ChatCompletionsClient(
                    endpoint=self.base_url,
                    credential=AzureKeyCredential(self.api_key),
                )
            
            formatted_messages = self.format_query(query, chat_history)
            
            result = self.client.complete(
                stream=True,
                messages=[
                    SystemMessage(content=system_prompt),
                    *formatted_messages,
                ],
                model=self.model,
            )

            response = LLMResponse()
            for update in result:
                if update.choices:
                    if update.choices[0].delta.content:
                        response.add_chunk(update.choices[0].delta.content)
                        yield response

        except asyncio.TimeoutError:
            logger.error("Request to OpenAI API timed out")
            yield LLMResponse(llm_response="Error: Request timed out. Please try again later.")
        except Exception as e:
            logger.exception(e)
            yield LLMResponse(llm_response=f"An unexpected error occurred: {str(e)}")