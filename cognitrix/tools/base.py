import inspect
import logging
from pathlib import Path
import sys
from odbms import Model
from typing import Any, Optional, Self

from pydantic import Field

class Tool(Model):
    """
    Base tool class
    """
    
    name: str 
    """Name of the tool"""
    
    description: str
    """Description of what the tool does and how to use it"""
    
    category: str = "general"
    """Category of the tool. Used for grouping"""
    
    parameters: Any = {}
    """Used for type hinting for function tools"""
    
    user_id: Optional[str] = Field(default=None)
    """User ID of the user the tool belongs to"""
    
    class Config:
        arbitrary_types_allowed = True
    
    def run(self, *args, **kwargs):
        """Here is where you code what the tool does"""
        pass
    
    async def arun(self, *args, **kwargs):
        """Asynchronous implementation"""
        pass
    
    @staticmethod
    def list_all_tools():
        """List all tools"""
        try:
            module = __import__(__package__, fromlist=['__init__']) # type: ignore
            tools = []
            func_tools  = [f[1] for f in inspect.getmembers(module) if not f[0].startswith('__') and f[0].lower() != 'tool' and isinstance(f[1], Tool)]
            tools.extend(func_tools)
            class_tools  = [f[1]() for f in inspect.getmembers(module, inspect.isclass) if not f[0].startswith('__') and f[0].lower() != 'tool']
            tools.extend(class_tools)
            return tools
        except Exception as e:
            logging.exception(e)
            return []
    
    @staticmethod
    def get_tools_by_category(category: str):
        """Retrieve all tools by category"""
        tools: list[Tool] = []
        try:
            if category == 'all':
                return Tool.list_all_tools()
            
            module = __import__(__package__, fromlist=['__init__'])  # type: ignore
            tools_by_category: list[Tool] = [f[1] for f in inspect.getmembers(module) if not f[0].startswith('__') and f[0].lower() != 'tool' and isinstance(f[1], Tool) and f[1].category == category]
            class_tools_by_category: list[Tool] = [f[1]() for f in inspect.getmembers(module, inspect.isclass) if not f[0].startswith('__') and f[0].lower() != 'tool'  and isinstance(f[1](), Tool) and f[1]().category == category]
            tools.extend(tools_by_category)
            tools.extend(class_tools_by_category)
            
            return tools
        except Exception as e:
            logging.exception(e)
            return tools
    
    @classmethod
    def get_by_name(cls, name: str)-> Optional['Tool']:
        """Dynamically load tool by name"""
        try:
            module = __import__(__package__, fromlist=[name]) # type: ignore
            tools  = [f[1] for f in inspect.getmembers(module) if not f[0].startswith('__') and f[0].lower() != 'tool' and isinstance(f[1], Tool)]
            class_tools  = [f[1]() for f in inspect.getmembers(module, inspect.isclass) if not f[0].startswith('__') and f[0].lower() != 'tool']
            tools.extend(class_tools)
            tool = next((t for t in tools if t.name.lower() == name.lower()), None)
            
            return tool
        except IndexError:
            return None
        except Exception as e:
            logging.exception(e)
            return None
    
    @classmethod
    def get_by_user_id(cls, user_id: str) -> list[Self]:
        """Retrieve all tools by user ID"""
        return [cls(**tool.model_dump()) for tool in Tool.find({"user_id": user_id})]
