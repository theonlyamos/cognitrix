from functools import wraps
from typing import Callable
from cognitrix.tools import Tool
from typing import Any
import inspect

def tool(*args: Any, **kwargs: Any):
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        class GenericTool(Tool):
            def run(self, *args, **kwargs):
                if inspect.iscoroutinefunction(func):
                    raise NotImplementedError("Asynchronous execution is not supported for coroutine functions")
                return wrapper(*args, **kwargs)
            
            async def arun(self, *args, **kwargs):
                if inspect.iscoroutinefunction(func):
                    return await wrapper(*args, **kwargs)
                raise NotImplementedError("Synchronous execution is not supported for synchronous functions")

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
