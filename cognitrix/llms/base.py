from pydantic import BaseModel, Field
from typing import Any, List, Dict
from pathlib import Path
import logging
import inspect
import sys

from cognitrix.tools.base import Tool

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.WARNING
)
logger = logging.getLogger('cognitrix.log')

PROMPT_TEMPLATE = """
You are a helpful, respectful and honest assistant.
Always answer as helpfully as possible, while being safe.
Your answers should not include any harmful, unethical,
racist, sexist, toxic, dangerous, or illegal content.

Please ensure your responses are socially unbiased and
positive in nature.

If a question does not make any sense, or is not factually coherent,
explain why instead of answering something not corrent.

Always check your answer against the current results from the
current search tool.
Always return the most updated and correct answer.
If you do not come up with any answer, just tell me you don't know.

Never share false information

The chatbot assistant can perform a variety of tasks, including:
Answering questions in a comprehensive and informative way
Generating different creative text formats of text content
Translating languages
Performing mathematical calculations
Summarizing text
Accessing and using external tools

Tools:
{tools}

The chatbot assistant should always follow chain of thought reasoning and use its knowledge and abilities to provide the best possible response to the user.

Use the following format:

query: the input query you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of {available_tools}
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input query

Begin!

query: {query}
Thought:

The response should be in a valid json format which can
be directed converted into a python dictionary with 
json.loads()
Return the response in the following format:
{
  "thought": "{thought}",
  "action": "{action}",
  "action_input": "{action_input}",
  "observation": "{observation}",
  "final_answer": "{actionable_response}"
}
"""

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
    
    max_tokens: int = Field(default=512)
    """The maximum number of tokens to generate in the completion.""" 
    
    supports_system_prompt: bool = Field(default=False)
    """Whether the model supports system prompts."""
    
    system_prompt: str = Field(default='')
    """System prompt to use for context."""
    
    is_multimodal: bool = Field(default=False)
    """Whether the model is multimodal."""
    
    platform: str = Field(default="")
    """This is set to the name of the class"""
    
    chat_history: List[Dict[str, str]] = []
    """Chat history stored as a list of responses"""
    
    tools: List[Tool] = []
    """Functions calling tools"""
    
    def __init__(self, **data):
        super().__init__(**data)
        self.platform = self.__class__.__name__
    
    @staticmethod
    def list_llms():
        """List all supported LLMs"""
        try:
            module = __import__(__package__, fromlist=['__init__'])
            return [f[0] for f in inspect.getmembers(module, inspect.isclass) if f[0] != 'LLM']
        except Exception as e:
            logging.exception(e)
            return []
    
    @staticmethod
    def load_llm(model_name: str):
        """Dynamically load LLMs based on name"""
        try:
            model_name = model_name.lower()
            module = __import__(__package__, fromlist=[model_name])
            llm: type[LLM] = [f[1] for f in inspect.getmembers(module, inspect.isclass) if f[0].lower() == model_name][0]
            return llm
        except Exception as e:
            logging.exception(e)
            return None
    
    def __call__(*args, **kwargs):
        pass