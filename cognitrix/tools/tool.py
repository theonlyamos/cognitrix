import asyncio
import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, get_args, get_origin, get_type_hints

from pydantic import TypeAdapter

from cognitrix.models.tool import Tool
from cognitrix.tools.utils import ToolCallResult, ToolOutcome


def tool(*args: Any, **kwargs: Any):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                if inspect.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                # Sync tools (bash/WebFetch/Search/pyautogui/os.walk) do blocking
                # I/O; run them off the event loop so a tool call doesn't freeze
                # the whole server for every other user.
                # Note: pyautogui GUI calls run in a worker thread here — fine on
                # Windows/Linux; some macOS UI calls require the main thread.
                return await asyncio.to_thread(func, *args, **kwargs)
            except Exception:
                raise

        try:
            type_hints = get_type_hints(func)
        except (NameError, TypeError):
            # Some tools intentionally keep heavy/circular types under
            # TYPE_CHECKING. Preserve importability; their unresolved hints
            # still degrade safely to string schemas.
            type_hints = {}
        func_signatures = inspect.signature(func)

        class GenericTool(Tool):
            def validate_parameters(self, params: dict[str, Any]) -> dict[str, Any]:
                unknown = set(params) - set(func_signatures.parameters)
                if unknown:
                    raise ValueError(f"Unknown parameter(s): {', '.join(sorted(unknown))}")
                validated = dict(params)
                for name, parameter in func_signatures.parameters.items():
                    if name not in validated:
                        if parameter.default is inspect.Parameter.empty:
                            raise ValueError(f"Missing required parameter: {name}")
                        continue
                    annotation = type_hints.get(name, parameter.annotation)
                    if annotation is not inspect.Parameter.empty:
                        validated[name] = TypeAdapter(annotation).validate_python(validated[name])
                return validated

            async def run(self, *args, **kwargs):
                value = await wrapper(*args, **kwargs)
                if isinstance(value, ToolCallResult):
                    return value
                if isinstance(value, ToolOutcome):
                    return ToolCallResult(
                        content=value.text, tool_name=func.__name__, outcome=value
                    )
                return ToolCallResult(content=value, tool_name=func.__name__)


        new_tool = GenericTool(
            name=' '.join(func.__name__.split('_')).title(),
            description=str(func.__doc__),
            category=kwargs.get('category', 'general'),
            retryable=kwargs.get('retryable', True),
            max_attempts=kwargs.get('max_attempts', 3),
            supported_interfaces=kwargs.get('supported_interfaces'),
            approval_mode=kwargs.get('approval_mode', 'risk_based'),
        )

        func_parameters = func_signatures.parameters

        def get_type_name(annotation):
            if annotation is inspect.Parameter.empty:
                return "string"
            if isinstance(annotation, str):
                return annotation
            origin = get_origin(annotation)
            if origin is list:
                args = get_args(annotation)
                item = get_type_name(args[0]) if args else 'str'
                return f'list[{item}]'
            if origin is dict:
                return 'dict'
            if origin is not None:
                # Optional[T] and other unions are represented by their useful
                # non-null member; optionality is carried by required_params.
                non_null = [arg for arg in get_args(annotation) if arg is not type(None)]
                return get_type_name(non_null[0]) if non_null else 'str'
            if hasattr(annotation, '__name__'):
                return annotation.__name__
            return str(annotation).replace('typing.', '')

        new_tool.parameters = {
            key: get_type_name(type_hints.get(key, value.annotation))
            for key, value in func_parameters.items()
        }
        # Params without a default are required; optional ones must not be forced.
        new_tool.required_params = [
            key for key, value in func_parameters.items()
            if value.default is inspect.Parameter.empty
        ]

        return new_tool


    return decorator
