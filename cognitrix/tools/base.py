import inspect
import logging
from pathlib import Path
import sys
from odbms import Model
from typing import Any, Dict, Optional, Self, get_type_hints
from rich import print

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
            tool = next((t for t in tools if t.name.lower() == name.replace('_', ' ').lower()), None)
            
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
    
    def to_dict_format(self) -> Dict[str, Any]:
        """
        Converts an instance of the Tool class to a dictionary in the OpenAI API format.
        
        Returns:
            Dict[str, Any]: A dictionary representing the tool in the OpenAI API format.
        """
        def python_type_to_json_type(py_type: str) -> str:
            """Convert Python types to JSON Schema types"""
            type_mapping = {
                'str': 'string',
                'int': 'integer',
                'float': 'number',
                'bool': 'boolean',
                'list': 'array',
                'dict': 'object',
                'None': 'null'
            }
            return type_mapping.get(py_type, 'string')  # default to string if unknown type

        tool_json = {}
        # For function tools, use the parameters from self.parameters
        if hasattr(self, 'parameters') and self.parameters:
            parameters = self.parameters
            # Assuming required parameters are all parameters for now
            required = list(parameters.keys())
            
            # Convert parameters to OpenAI format
            properties = {}
            for name, param_type in parameters.items():
                vtype = python_type_to_json_type(param_type)
                prop = {"type": vtype, "description": ""}
                if vtype == 'array':
                    prop['items'] = {"type": "string"} # type: ignore
                properties[name] = prop
        # else:
        #     # For class-based tools, inspect the class definition
        #     cls = self.__class__
        #     # Get the original run method (not the instance method)
        #     run_method = cls.run
        #     sig = inspect.signature(run_method)
            
        #     # Get type hints from the class's run method
        #     type_hints = get_type_hints(cls.run)
            
        #     print(type_hints)
            
        #     required = [
        #         name for name, param in sig.parameters.items() 
        #         if param.default == inspect.Parameter.empty 
        #         and name not in ['self', 'args', 'kwargs']
        #     ]
            
        #     properties = {
        #         name: {"type": python_type_to_json_type(type_hints[name].__name__)}
        #         for name, param in sig.parameters.items()
        #         if name not in ['self', 'args', 'kwargs'] 
        #         and name in type_hints  # Only include parameters with type hints
        #     }
        
            tool_json = {
                "type": "function",
                "function": {
                    "name": self.name.replace(' ', '_'),
                    "description": self.description[:1024],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                }
            }
        
        return tool_json
