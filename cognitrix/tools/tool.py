import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from cognitrix.models.tool import Tool
from cognitrix.tools.utils import ToolCallResult


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

        def get_type_name(annotation):
            if annotation is inspect.Parameter.empty:
                return "string"
            if isinstance(annotation, str):
                return annotation
            if hasattr(annotation, '__name__'):
                return annotation.__name__
            return str(annotation).replace('typing.', '')
        
        new_tool.parameters = {
            key: get_type_name(value.annotation)
            for key, value in func_parameters.items()
        }

        return new_tool


    return decorator
