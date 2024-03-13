from functools import wraps
from typing import Callable
from ..tools import Tool
import inspect

def tool(func: Callable, *args, **kwargs):
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
        description=str(func.__doc__)
    )
    return new_tool
