from cognitrix.llms import OpenAI
from openai import OpenAI as OpenAILLM
from typing import Optional
import os

class MindsDB(OpenAI):
    """A class for interacting with the MindsDB API."""
    
    model: str ='gemini-1.5-pro'
    
    api_key: str = os.getenv('MINDSDB_API_KEY', '')
    
    base_url: str = 'https://llm.mdb.ai'
    
    def __call__(self, query: dict, **kwds: dict)->Optional[str]:
        """Generates a response to a query using the OpenAI API.

        Args:
            query (dict): The query to generate a response to.
            kwds (dict): Additional keyword arguments to pass to the OpenAI API.

        Returns:
            A string containing the generated response.
        """

        client = OpenAILLM(api_key=self.api_key, base_url=self.base_url)
            
        formatted_messages = self.format_query(query)
        
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "user", "content": self.system_prompt},
                *formatted_messages
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )
        
        return response.choices[0].message.content