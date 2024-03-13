import asyncio
from abc import ABC, abstractmethod
from functools import wraps
from inspect import signature
import json


class BaseAIAssistantAgent(ABC):
    def __init__(self, llm):
        self.llm = llm

    @abstractmethod
    async def on_activate(self):
        pass

    @abstractmethod
    async def on_deactivate(self):
        pass

    async def handle_request(self, request):
        method_name = request["method_name"]
        params = request["params"]

        if hasattr(self, method_name):
            method = getattr(self, method_name)

            if callable(method):
                return await self.call_method(method, params)
            else:
                raise ValueError(f"{method_name} is not a callable method")
        else:
            raise ValueError(f"Method {method_name} not found")

    async def call_method(self, method, params):
        if asyncio.iscoroutinefunction(method):
            return await method(**params)
        else:
            return method(**params)
    
    async def create_task(self, target, name=None):
        task = asyncio.create_task(target)

        if name is not None:
            task.set_name(name)

        return task


def catch_exceptions(func):
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except Exception as e:
            print(f"Error in {func.__name__}: {str(e)}")
    return wrapper

def json_to_python_dict(json_object):
    """Converts a JSON object to a Python dictionary.

    Args:
        json_object: A JSON object.

    Returns:
        A Python dictionary.
    """

    if isinstance(json_object, dict):
        return {key: json_to_python_dict(value) for key, value in json_object.items()}
    elif isinstance(json_object, list):
        return [json_to_python_dict(value) for value in json_object]
    else:
        return json_object

class AIAssistantAgent(BaseAIAssistantAgent):
    @catch_exceptions
    async def on_activate(self):
        print("AI Assistant activated")

    @catch_exceptions
    async def on_deactivate(self):
        print("AI Assistant deactivated")

    @catch_exceptions
    async def handle_request(self, request):
        return await super().handle_request(request)

    @catch_exceptions
    async def call_method(self, method, params):
        return await super().call_method(method, params)