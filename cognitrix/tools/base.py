import inspect
import logging
from pathlib import Path
import sys
from pydantic import BaseModel, Field
from typing import Any, Optional

class Tool(BaseModel):
    """
    Base tool class

    Args:
        BaseModel (_type_): _description_
    """
    
    name: str
    """Name of the tool"""
    
    description: str
    """Description of what the tool does and how to use it"""
    
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
            module = __import__(__package__, fromlist=['__init__'])
            tools  = [f[1] for f in inspect.getmembers(module) if not f[0].startswith('__') and f[0].lower() != 'tool' and isinstance(f[1], Tool)]
            class_tools  = [f[1]() for f in inspect.getmembers(module, inspect.isclass) if not f[0].startswith('__') and f[0].lower() != 'tool']
            tools.extend(class_tools)
            return tools
        except Exception as e:
            logging.exception(e)
            return []
    
    @classmethod
    def get_by_name(cls, name: str)-> Optional['Tool']:
        """Dynamically load tool by name"""
        try:
            module = __import__(__package__, fromlist=[name])
            tools  = [f[1] for f in inspect.getmembers(module) if not f[0].startswith('__') and f[0].lower() != 'tool' and isinstance(f[1], Tool)]
            class_tools  = [f[1]() for f in inspect.getmembers(module, inspect.isclass) if not f[0].startswith('__') and f[0].lower() != 'tool']
            tools.extend(class_tools)
            tool = [t for t in tools if t.name.lower() == name.lower()][0]
            return tool
        except IndexError:
            return None
        except Exception as e:
            logging.exception(e)
            return None