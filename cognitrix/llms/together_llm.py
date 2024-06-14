import os
import sys
import together 
from cognitrix.llms.base import LLM
from dotenv import load_dotenv
from pydantic import Extra, Field, root_validator
from typing import Any, Dict, List, Mapping, Optional 

load_dotenv()

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
    
    def format_query(self, message: dict[str, str]) -> str:
        """Formats a message for the Claude API.

        Args:
            message (dict[str, str]): The message to be formatted for the Claude API.

        Returns:
            list: A list of formatted messages for the Claude API.
        """
        
        
        
        formatted_message = [*self.chat_history, message]
        
        messages = B_INST + B_SYS + self.system_prompt + E_SYS
        
        for fm in formatted_message:
            if fm['type'] == 'text':
                messages += f"\n{fm['message']}"
        messages += E_INST
        
        return messages

    def __call__(
        self,
        prompt: dict,
        **kkwargs: Any,
    ) -> str:
        """Call to Together endpoint."""
        if not self.client:
            self.client = together
            
        self.client.api_key = self.api_key
        
        output = self.client.Complete.create(
            self.format_query(prompt),
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        
        return output['output']['choices'][0]['text']   # type: ignore