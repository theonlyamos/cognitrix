import inspect
import logging
from pathlib import Path
import sys
from pydantic import BaseModel, Field
from typing import Optional

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
    
    @classmethod
    def get_by_name(cls, name: str)-> Optional[type['Tool']]:
        """Dynamically load tool by name"""
        try:
            tool_name = name.lower()
            module_path = Path(__file__, '..').resolve()
            sys.path.append(str(module_path))
            module = __import__(str(inspect.getmodulename(Path('__init__.py'))))
            tool: type['Tool'] = [f[1] for f in inspect.getmembers(module, inspect.isclass) if f[0].lower() == tool_name][0]
            return tool
        except Exception as e:
            logging.error(str(e))
            return None