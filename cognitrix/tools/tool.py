from functools import wraps
from typing import Callable
from cognitrix.tools import Tool
from cognitrix.tools.utils import ToolCallResult
from typing import Any
import inspect

def tool(*args: Any, **kwargs: Any):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                if inspect.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                return func(*args, **kwargs)
            except Exception as e:
                return str(e)
        
        class GenericTool(Tool):
            async def run(self, *args, **kwargs):
                return ToolCallResult(content=await wrapper(*args, **kwargs), tool_name=func.__name__)
            

        new_tool = GenericTool(
            name=' '.join(func.__name__.split('_')).title(),
            description=str(func.__doc__),
            category=kwargs.get('category', 'general')
        )
        
        func_signatures = inspect.signature(func)
        func_parameters = func_signatures.parameters
        
        new_tool.parameters = {key: value.annotation.__name__ for key, value in func_parameters.items()}

        return new_tool
    
    
    return decorator
