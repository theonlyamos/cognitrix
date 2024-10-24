from openai import OpenAI
from cognitrix.providers import OpenAI as OpenAILLM
from typing import List, Dict, Any
from openai import OpenAIError
import asyncio
import logging
import os



from cognitrix.providers.base import LLMResponse

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

class AIMLAPI(OpenAILLM):
    """A class for interacting with the aimlapi API."""
    
    model: str ='gpt-4o'
    
    api_key: str = os.getenv('AIMLAPI_API_KEY', '')
    
    base_url: str = 'https://gateway.helicone.ai'
    
    is_multimodal: bool = True
    """Whether the model is multimodal."""
    
    async def __call__(self, query: dict, system_prompt: str, chat_history: List[Dict[str, str]] = [], stream: bool = False, **kwds: Any):
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
                        default_headers={
                            "Helicone-Auth": f"Bearer {os.environ.get('HELICONE_API_KEY', '')}",
                            "Helicone-Target-Url": "https://api.aimlapi.com",
                            "Helicone-Target-Provider": "AIMLAPI",
                        }
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
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    stream=stream
                )
                
            response = LLMResponse()
            if stream:
                for chunk in completion:
                    if chunk.choices and len(chunk.choices) and chunk.choices[0].delta.content is not None:
                        response.add_chunk(chunk.choices[0].delta.content)
                    yield response
            else:
                response.add_chunk(completion.choices[0].message.content)
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
            