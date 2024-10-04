import asyncio
import os
import sys
import logging
from together import Together
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

class TogetherAI(LLM):
    """Together large language models.""" 
    
    model: str = "meta-llama/Llama-3.2-90B-Vision-Instruct-Turbo"
    """model endpoint to use""" 
    
    base_url: str = "https://together.helicone.ai/v1"
    """Base URL for the Together API"""

    api_key: str = os.getenv("TOGETHER_API_KEY", "")
    """Together API key""" 

    temperature: float = 0.1
    """What sampling temperature to use.""" 
    
    max_tokens: int = 8193
    """The maximum number of tokens to generate in the completion.""" 
    
    chat_history: list[str] = []
    """Chat history"""
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""

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
    
    # def format_query(self, message: dict[str, str], system_prompt: str, chat_history: List[Dict[str, str]] = []) -> str:
    #     """Formats a message for the Claude API.

    #     Args:
    #         message (dict[str, str]): The message to be formatted for the Claude API.

    #     Returns:
    #         list: A list of formatted messages for the Claude API.
    #     """
        
        
        
    #     formatted_message = [*chat_history, message]
        
    #     messages = B_INST + B_SYS + system_prompt + E_SYS
        
    #     for fm in formatted_message:
    #         if fm['type'] == 'text':
    #             messages += f"\n{fm['message']}"
    #     messages += E_INST
        
    #     return messages

    # async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], **kwds: Any):
        try:
            if not self.client:
                self.client = Together(api_key=self.api_key)
                if self.base_url:
                    self.client.base_url = self.base_url
                if 'helicone' in self.base_url:
                    logger.info(f"Using Helicone with base url {self.base_url}")
                    self.client = Together(
                        api_key=self.api_key, 
                        default_headers={'Helicone-Auth': f'Bearer {os.getenv("HELICONE_API_KEY")}'}
                    )
            
            formatted_messages = self.format_query(query, chat_history)
            
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *formatted_messages
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                top_p=0.7,
                top_k=50,
                repetition_penalty=1,
                stop=["<|eot_id|>","<|eom_id|>"],
                # stream=True,
            )
            print(stream.choices[0].message.content)
            response = LLMResponse()
            response.add_chunk(stream.choices[0].message.content)
            yield response
            # for chunk in stream:
            #     response.add_chunk(chunk.choices[0].delta.content or "")
            #     yield response

        except TogetherException as e:
            logger.error(f"Together API error: {str(e)}")
            yield LLMResponse(llm_response=f"Error: Together API encountered an issue - {str(e)}")
        except asyncio.TimeoutError:
            logger.error("Request to Together API timed out")
            yield LLMResponse(llm_response="Error: Request to Together timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Unexpected error in TogetherLLM __call__ method: {str(e)}")
            yield LLMResponse(llm_response=f"An unexpected error occurred in TogetherLLM: {str(e)}")