import asyncio
import os
import sys
import logging
import together
from together.error import TogetherException
from cognitrix.llms.base import LLM, LLMResponse
from dotenv import load_dotenv
from pydantic import Extra, Field, root_validator
from typing import Any, Dict, List, Mapping, Optional 

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

B_INST, E_INST = "[INST]", "[/INST]"
B_SYS, E_SYS = "<<SYS>>\n", "\n<</SYS>>\n\n"


def cut_off_text(text, prompt):
    cutoff_phrase = prompt
    index = text.find(cutoff_phrase)
    if index != -1:
        return text[:index]
    else:
        return text

class Together(LLM):
    """Together large language models.""" 
    
    model: str = "mistralai/Mixtral-8x7B-Instruct-v0.1"
    """model endpoint to use""" 

    api_key: str = os.getenv("TOGETHER_API_KEY", "")
    """Together API key""" 

    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    max_tokens: int = 512
    """The maximum number of tokens to generate in the completion.""" 
    
    chat_history: list[str] = []
    """Chat history"""

    class Config:
        extra = 'allow'
    
    # @root_validator()
    # def validate_environment(cls, values: Dict) -> Dict:
    #     """Validate that the API key is set."""
    #     # api_key = get_from_dict_or_env(
    #     #     values, "api_key", "TOGETHER_API_KEY"
    #     # )
    #     values["api_key"] = cls.api_key
    #     return values
    
    @property
    def _llm_type(self) -> str:
        """Return type of LLM."""
        return "together"
    
    def format_query(self, message: dict[str, str], system_prompt: str, chat_history: List[Dict[str, str]] = []) -> str:
        """Formats a message for the Claude API.

        Args:
            message (dict[str, str]): The message to be formatted for the Claude API.

        Returns:
            list: A list of formatted messages for the Claude API.
        """
        
        
        
        formatted_message = [*chat_history, message]
        
        messages = B_INST + B_SYS + system_prompt + E_SYS
        
        for fm in formatted_message:
            if fm['type'] == 'text':
                messages += f"\n{fm['message']}"
        messages += E_INST
        
        return messages

    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], **kwds: Any):
        try:
            if not self.client:
                self.client = together
                
            self.client.api_key = self.api_key
            
            output = self.client.Complete.create(
                self.format_query(query, system_prompt, chat_history),
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            
            response = LLMResponse()
            response.add_chunk(output['output']['choices'][0]['text'] )
            yield response

        except TogetherException as e:
            logger.error(f"Together API error: {str(e)}")
            yield LLMResponse(llm_response=f"Error: Together API encountered an issue - {str(e)}")
        except asyncio.TimeoutError:
            logger.error("Request to Together API timed out")
            yield LLMResponse(llm_response="Error: Request to Together timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in TogetherLLM __call__ method: {str(e)}")
            yield LLMResponse(llm_response=f"An unexpected error occurred in TogetherLLM: {str(e)}")