"""Secret-free immutable runtime data persisted with task steps.

The snapshot is the execution contract.  It intentionally stores enough data
to recreate an attempt, but never provider credentials, clients, callbacks, or
callable tool implementations.
"""

import copy
import inspect
import json
import re
from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cognitrix.models import Agent
from cognitrix.models.tool import MCPTool, Tool
from cognitrix.providers.base import LLM
from cognitrix.tools.base import ToolManager


class MissingRequiredTools(ValueError):
    """An exact task capability allowlist cannot be satisfied."""


class RuntimeInstantiationError(RuntimeError):
    """A persisted runtime can no longer be materialized safely."""


class TaskCapabilityRegistry:
    """Stable, per-run capability bindings partitioned by assigned agent.

    A persisted snapshot deliberately stores schemas rather than executable
    callables.  This registry is the worker-local half of that contract: it is
    built once from the authoritative roster, enforces user ownership, and
    gives each snapshot an exact-name resolver that never searches another
    agent's tools.
    """

    def __init__(self, bindings: Mapping[str, Mapping[str, Tool]]):
        self._bindings = {
            str(agent_id): dict(agent_bindings)
            for agent_id, agent_bindings in bindings.items()
        }

    def resolver_for(self, agent_id: str) -> Callable[[str], Tool | None]:
        bindings = self._bindings.get(str(agent_id), {})

        def resolve(name: str) -> Tool | None:
            return bindings.get(name)

        return resolve


def _has_executable_delegate(capability: Tool) -> bool:
    """Return whether a hydrated capability still owns runnable code."""
    if callable(capability.__dict__.get("run")) or callable(capability.__dict__.get("arun")):
        return True
    capability_type = type(capability)
    return (
        getattr(capability_type, "run", Tool.run) is not Tool.run
        or getattr(capability_type, "arun", Tool.arun) is not Tool.arun
    )


async def _reconstruct_agent_mcp_tools(agent: Agent, names: set[str]) -> dict[str, Tool]:
    """Rebuild only already-assigned MCP names from the agent's server allowlist."""
    if not names or not getattr(agent, "mcp_servers", None):
        return {}
    try:
        from cognitrix.mcp.client import get_dynamic_client
        from cognitrix.mcp.server_manager import mcp_server_manager
        from cognitrix.mcp.tools import create_mcp_tool_wrapper

        client = await get_dynamic_client()
        rebuilt: dict[str, Tool] = {}
        for server in list(agent.mcp_servers or []):
            if not client.is_connected(server):
                config = mcp_server_manager.get_server(server)
                if config is None or not config.enabled:
                    continue
                if not await client.connect_to_server(config):
                    continue
            for tool_info in await client.list_tools(server) or []:
                candidate = create_mcp_tool_wrapper(server, tool_info)
                if candidate.name in names:
                    rebuilt[candidate.name] = candidate
        return rebuilt
    except Exception:
        # Missing/unavailable capabilities fail closed later when the snapshot
        # is instantiated.  Do not turn a global MCP outage into global lookup.
        return {}


async def build_task_capability_registry(
    roster: list[Agent],
    *,
    actor_user_id: str | None,
) -> TaskCapabilityRegistry:
    """Bind assigned tools once without leaking user-scoped capabilities.

    Dynamic MCP tools are reconstructed only from each agent's own server
    allowlist.  Name lookup through ``ToolManager`` is reserved for unowned
    built-ins that lost their decorated callable during database hydration.
    """
    registry: dict[str, dict[str, Tool]] = {}
    for agent in roster:
        assigned = list(getattr(agent, "tools", None) or [])
        assigned_by_name = {tool.name: tool for tool in assigned}
        dynamic_names = {
            tool.name
            for tool in assigned
            if isinstance(tool, MCPTool) or tool.category == "mcp_dynamic"
        }
        rebuilt_mcp = await _reconstruct_agent_mcp_tools(agent, dynamic_names)
        bindings: dict[str, Tool] = {}
        for name, assigned_tool in assigned_by_name.items():
            owner = assigned_tool.user_id
            if owner is not None and str(owner) != str(actor_user_id or ""):
                raise RuntimeInstantiationError(
                    f"Actor is not authorized for assigned capability '{name}'"
                )

            if _has_executable_delegate(assigned_tool):
                bindings[name] = assigned_tool
                continue
            if name in rebuilt_mcp:
                bindings[name] = rebuilt_mcp[name]
                continue

            # User-owned and dynamic capabilities may never rebind through a
            # process-global registry by name.
            if owner is not None or name in dynamic_names:
                continue
            builtin = ToolManager.get_by_name(name)
            if (
                builtin is not None
                and builtin.name == name
                and builtin.user_id is None
                and builtin.category != "mcp_dynamic"
            ):
                bindings[name] = builtin
        registry[str(agent.id)] = bindings
    return TaskCapabilityRegistry(registry)


class _FrozenDict(dict):
    """A JSON-compatible mapping that rejects mutation at every nesting level."""

    @staticmethod
    def _immutable(*args, **kwargs):
        raise TypeError("persisted task runtime schemas are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return copy.deepcopy(value)


_SECRET_OPTION_KEYS = {
    "api_key", "apikey", "authorization", "authentication", "auth",
    "token", "secret", "password", "passwd", "headers", "header",
    "cookies", "cookie", "credentials", "credential", "x_api_key",
    "helicone_auth", "private_key", "privatekey",
}
_SECRET_KEY_SUFFIXES = (
    "_api_key", "_token", "_secret", "_password", "_passwd",
    "_authorization", "_authentication", "_credentials", "_credential",
    "_cookies", "_cookie", "_headers", "_header", "_private_key",
)
_SCHEMA_LITERAL_KEYS = {"default", "example", "examples", "const"}
_SAFE_EXTRA_BODY_KEYS = {
    "top_p", "top_k", "min_p", "seed", "stop", "presence_penalty",
    "frequency_penalty", "reasoning_effort", "thinking", "thinking_config",
    "response_modalities", "modalities", "image_config",
}


def _normalized_option_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


def _is_secret_option_key(value: Any) -> bool:
    normalized = _normalized_option_key(value)
    return (
        normalized in _SECRET_OPTION_KEYS
        or normalized.endswith(_SECRET_KEY_SUFFIXES)
        or normalized.startswith((
            "authorization_", "authentication_", "credential_",
            "credentials_", "cookie_", "cookies_", "header_", "headers_",
        ))
    )


def _validate_secret_free_tool_schema(value: Any) -> None:
    """Reject credentials and example literals from persisted tool schemas."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _is_secret_option_key(key):
                raise ValueError("secret-bearing tool schema fields cannot be persisted")
            if _normalized_option_key(key) in _SCHEMA_LITERAL_KEYS:
                raise ValueError(
                    "tool schemas with literal defaults or examples cannot be persisted"
                )
            _validate_secret_free_tool_schema(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _validate_secret_free_tool_schema(nested)


def _safe_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(str(value))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("task runtime provider endpoint must be a non-secret HTTP(S) URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _safe_json_options(value: Any, *, extra_body: bool = False) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("task runtime generation options must be an object")
    if extra_body:
        unknown = set(value) - _SAFE_EXTRA_BODY_KEYS
        if unknown:
            raise ValueError(
                "unsupported persisted generation option(s): " + ", ".join(sorted(unknown))
            )

    def inspect_value(item: Any) -> Any:
        if isinstance(item, Mapping):
            output = {}
            for key, nested in item.items():
                if _is_secret_option_key(key):
                    raise ValueError("secret-bearing generation options cannot be persisted")
                output[str(key)] = inspect_value(nested)
            return output
        if isinstance(item, (list, tuple)):
            return [inspect_value(nested) for nested in item]
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        raise ValueError("generation options must contain JSON-safe values")

    result = inspect_value(value)
    json.dumps(result, allow_nan=False)
    return result


def _validate_tool_schema_contract(
    tool_names: tuple[str, ...],
    tool_schemas: tuple[dict[str, Any], ...],
) -> None:
    if len(tool_names) != len(tool_schemas):
        raise ValueError("each persisted tool name must have exactly one persisted schema")
    if len(set(tool_names)) != len(tool_names):
        raise ValueError("persisted task runtime tool names must be unique")

    for tool_name, schema in zip(tool_names, tool_schemas, strict=True):
        _validate_secret_free_tool_schema(schema)
        if not isinstance(schema, Mapping) or schema.get("type") != "function":
            raise ValueError(f"persisted schema for tool '{tool_name}' must be a function schema")
        function = schema.get("function")
        if not isinstance(function, Mapping):
            raise ValueError(f"persisted schema for tool '{tool_name}' has no function object")
        advertised_name = function.get("name")
        expected_name = tool_name.replace(" ", "_")
        if advertised_name != expected_name:
            raise ValueError(
                f"persisted schema name '{advertised_name}' does not match tool '{tool_name}'"
            )
        if not isinstance(function.get("description"), str):
            raise ValueError(f"persisted schema for tool '{tool_name}' needs a description")
        parameters = function.get("parameters")
        if not isinstance(parameters, Mapping):
            raise ValueError(f"persisted schema for tool '{tool_name}' needs parameter schema")


class TaskPromptProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["task-step-v1"] = "task-step-v1"
    memory: Literal["none"] = "none"
    skills: bool = False
    subagents: bool = False
    include_date: bool = False


class LLMRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_version: Literal[1] = 1
    provider: str
    model: str
    base_url: str | None = None
    temperature: float = 0.7
    max_tokens: int = Field(default=4096, ge=1)
    context_window: int = Field(default=0, ge=0)
    supports_tool_use: bool = True
    is_multimodal: bool = True
    extra_body: dict[str, Any] = Field(default_factory=dict)
    response_format: dict[str, Any] | None = None

    @field_validator("base_url", mode="before")
    @classmethod
    def _endpoint_is_secret_free(cls, value):
        return _safe_endpoint(value)

    @field_validator("extra_body", mode="before")
    @classmethod
    def _extra_body_is_approved(cls, value):
        return _safe_json_options(value, extra_body=True)

    @field_validator("response_format", mode="before")
    @classmethod
    def _response_format_is_safe(cls, value):
        return None if value is None else _safe_json_options(value)

    @field_validator("extra_body", "response_format")
    @classmethod
    def _freeze_generation_options(cls, value):
        return None if value is None else _freeze_json(value)


class CapabilityPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal[1] = 1
    name: str
    capability_id: str | None = None
    capability_version: str | None = None
    category: str = "general"
    owner_user_id: str | None = None
    retryable: bool = True
    max_attempts: int = Field(default=1, ge=1)
    supported_interfaces: tuple[str, ...] | None = None
    approval_mode: Literal["risk_based", "assigned_only", "always"] = "risk_based"


class AgentRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    agent_id: str
    name: str
    system_prompt: str
    llm: LLMRuntimeSnapshot
    tool_names: tuple[str, ...] = ()
    tool_schemas: tuple[dict[str, Any], ...] = ()
    tool_policies: tuple[CapabilityPolicySnapshot, ...] = ()
    prompt_profile: TaskPromptProfile = Field(default_factory=TaskPromptProfile)

    @field_validator("tool_schemas", mode="before")
    @classmethod
    def _schemas_must_be_json(cls, value):
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("tool schemas must contain JSON-safe values") from exc
        return value

    @field_validator("tool_schemas")
    @classmethod
    def _freeze_tool_schemas(cls, value):
        return tuple(_freeze_json(schema) for schema in value)

    @model_validator(mode="after")
    def _validate_tool_schemas(self):
        _validate_tool_schema_contract(self.tool_names, self.tool_schemas)
        if self.tool_policies:
            if len(self.tool_policies) != len(self.tool_names):
                raise ValueError("each persisted tool must have exactly one capability policy")
            if tuple(policy.name for policy in self.tool_policies) != self.tool_names:
                raise ValueError("persisted capability policy names must match tool names")
        return self


class _SnapshotBoundTool(MCPTool):
    """Runtime-only adapter binding a callable to its persisted provider schema.

    Inheriting from ``MCPTool`` is deliberate: AgentManager treats MCP tools as
    already-resolved assigned capabilities and therefore will not replace this
    adapter from the global ToolManager registry at invocation time.
    """

    def __init__(
        self,
        delegate: Tool,
        schema: dict[str, Any],
        policy: CapabilityPolicySnapshot | None = None,
    ):
        schema_data = _thaw_json(schema)
        function = schema_data["function"]
        parameters = function["parameters"]
        required = parameters.get("required")
        if policy is None:
            name = delegate.name
            category = delegate.category
            owner = delegate.user_id
            retryable = delegate.retryable
            max_attempts = delegate.max_attempts
            interfaces = copy.deepcopy(delegate.supported_interfaces)
            approval_mode = delegate.approval_mode
        else:
            if policy.owner_user_id is not None:
                if str(delegate.user_id or "") != policy.owner_user_id:
                    raise RuntimeInstantiationError(
                        f"Capability owner changed for persisted tool '{policy.name}'"
                    )
                if policy.capability_id and str(delegate.id) != policy.capability_id:
                    raise RuntimeInstantiationError(
                        f"Capability identity changed for persisted tool '{policy.name}'"
                    )
                live_version = str(getattr(delegate, "updated_at", "") or "") or None
                if policy.capability_version and live_version != policy.capability_version:
                    raise RuntimeInstantiationError(
                        f"Capability version changed for persisted tool '{policy.name}'"
                    )
            name = policy.name
            category = policy.category
            owner = policy.owner_user_id
            retryable = policy.retryable and bool(delegate.retryable)
            max_attempts = min(policy.max_attempts, int(delegate.max_attempts or 1))
            approval_order = {"assigned_only": 0, "risk_based": 1, "always": 2}
            approval_mode = max(
                (policy.approval_mode, str(delegate.approval_mode)),
                key=lambda value: approval_order.get(value, 2),
            )
            stored_interfaces = (
                None if policy.supported_interfaces is None
                else set(policy.supported_interfaces)
            )
            live_interfaces = (
                None if delegate.supported_interfaces is None
                else set(delegate.supported_interfaces)
            )
            if stored_interfaces is None:
                intersection = live_interfaces
            elif live_interfaces is None:
                intersection = stored_interfaces
            else:
                intersection = stored_interfaces & live_interfaces
            interfaces = (
                None if intersection is None
                else sorted(intersection) or ["__no_supported_interface__"]
            )
        super().__init__(
            name=name,
            description=function["description"],
            category=category,
            parameters={},
            required_params=list(required) if isinstance(required, list) else None,
            user_id=owner,
            retryable=retryable,
            max_attempts=max_attempts,
            supported_interfaces=interfaces,
            approval_mode=approval_mode,
            mcp_schema=parameters,
        )
        # odbms models intercept unknown/private attributes, so runtime-only
        # state lives directly in ``__dict__`` (the same pattern Agent uses for
        # its lazy context manager).
        self.__dict__["_delegate"] = delegate
        self.__dict__["_persisted_schema"] = _freeze_json(schema_data)

    def to_dict_format(self) -> dict[str, Any]:
        # Return a fresh value so provider adapters cannot mutate the snapshot.
        return _thaw_json(self.__dict__["_persisted_schema"])

    def validate_parameters(self, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise ValueError("tool parameters must be an object")
        schema = self.__dict__["_persisted_schema"]["function"]["parameters"]
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        missing = [name for name in required if name not in params]
        if missing:
            raise ValueError("Missing required parameter(s): " + ", ".join(missing))
        if schema.get("additionalProperties") is False:
            unknown = set(params) - set(properties)
            if unknown:
                raise ValueError("Unknown parameter(s): " + ", ".join(sorted(unknown)))
        expected_types = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for name, value in params.items():
            definition = properties.get(name)
            if not isinstance(definition, Mapping):
                continue
            expected = expected_types.get(definition.get("type"))
            if expected is not None and not isinstance(value, expected):
                raise ValueError(f"Invalid type for parameter: {name}")
            if "enum" in definition and value not in definition["enum"]:
                raise ValueError(f"Invalid value for parameter: {name}")
        return dict(params)

    async def run(self, *args, **kwargs):
        result = self.__dict__["_delegate"].run(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def arun(self, *args, **kwargs):
        result = self.__dict__["_delegate"].arun(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result


_LEGACY_TASK_LINES = {
    '- Break down each task into a simple, actionable todo list and update it as you work.',
    '- Update your "scratchpad" and "todo" fields as you work.',
    '- Always break down complex tasks into simple, actionable todo items in the "todo" field.',
    '- If you need to update your plan, edit the "todo" and "scratchpad" fields.',
    '"scratchpad": "{your running notes, calculations, or thoughts}",',
    '"todo": ["{first subtask or next step}", "{second subtask or next step}"],',
    '"scratchpad": "[All your running notes, observations, reasoning and planning]",',
    '"todo": ["First subtask or next step", "Second subtask or next step"],',
}


def strip_legacy_task_boilerplate(prompt: str) -> str:
    """Remove only clauses emitted by the historical agent generators.

    This is deliberately an exact-line migration.  Broad matching for words
    such as ``scratchpad`` or ``todo`` would silently rewrite user-authored
    prompts, which are immutable source data.
    """
    kept = [line for line in str(prompt or "").splitlines() if line.strip() not in _LEGACY_TASK_LINES]
    cleaned = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _select_tools(agent: Agent, required_tools: list[str] | None) -> list[Tool]:
    assigned = list(agent.tools or [])
    if required_tools is None:
        return assigned
    if not required_tools:
        return []

    by_exact_name = {tool.name: tool for tool in assigned}
    missing = [name for name in required_tools if name not in by_exact_name]
    if missing:
        raise MissingRequiredTools(
            "Required tools are not assigned with exact names: " + ", ".join(missing)
        )
    # Preserve the plan's order while de-duplicating repeated requirements.
    selected: list[Tool] = []
    seen: set[str] = set()
    for name in required_tools:
        if name not in seen:
            selected.append(by_exact_name[name])
            seen.add(name)
    return selected


def build_runtime_snapshot(
    agent: Agent,
    required_tools: list[str] | None,
) -> AgentRuntimeSnapshot:
    """Freeze one step's prompt, LLM settings, and exact tool allowlist."""
    if required_tools and not bool(agent.llm.supports_tool_use):
        raise MissingRequiredTools(
            f"Agent '{agent.name}' model does not support required tool use"
        )
    tools = _select_tools(agent, required_tools)
    llm = agent.llm
    return AgentRuntimeSnapshot(
        agent_id=str(agent.id),
        name=agent.name,
        system_prompt=strip_legacy_task_boilerplate(agent.system_prompt),
        llm=LLMRuntimeSnapshot(
            provider=llm.provider,
            model=llm.model,
            base_url=llm.base_url,
            temperature=llm.temperature,
            max_tokens=llm.max_tokens,
            context_window=llm.context_window,
            supports_tool_use=llm.supports_tool_use,
            is_multimodal=llm.is_multimodal,
            extra_body=copy.deepcopy(llm.extra_body or {}),
            response_format=copy.deepcopy(llm.response_format),
        ),
        tool_names=tuple(tool.name for tool in tools),
        tool_schemas=tuple(copy.deepcopy(tool.to_dict_format()) for tool in tools),
        tool_policies=tuple(
            CapabilityPolicySnapshot(
                name=tool.name,
                capability_id=str(tool.id) if tool.user_id is not None else None,
                capability_version=(
                    str(getattr(tool, "updated_at", "") or "") or None
                    if tool.user_id is not None else None
                ),
                category=tool.category,
                owner_user_id=str(tool.user_id) if tool.user_id is not None else None,
                retryable=tool.retryable,
                max_attempts=tool.max_attempts,
                supported_interfaces=(
                    tuple(tool.supported_interfaces)
                    if tool.supported_interfaces is not None else None
                ),
                approval_mode=tool.approval_mode,
            )
            for tool in tools
        ),
    )


def instantiate_runtime(
    snapshot: AgentRuntimeSnapshot,
    *,
    tool_resolver: Callable[[str], Tool | None] | None = None,
) -> Agent:
    """Create an isolated in-memory Agent for a single execution attempt."""
    # Defend even when a caller used Pydantic's validation-bypassing
    # ``model_construct`` to hydrate a snapshot.
    _validate_tool_schema_contract(snapshot.tool_names, snapshot.tool_schemas)
    loaded = LLM.load_llm(snapshot.llm.provider)
    if loaded is None:
        raise RuntimeInstantiationError(
            f"Provider '{snapshot.llm.provider}' is unavailable for task runtime"
        )
    llm = loaded.model_copy(deep=True)
    llm.model = snapshot.llm.model
    if snapshot.llm.base_url is not None:
        llm.base_url = snapshot.llm.base_url
    llm.temperature = snapshot.llm.temperature
    llm.max_tokens = snapshot.llm.max_tokens
    llm.context_window = snapshot.llm.context_window
    llm.supports_tool_use = snapshot.llm.supports_tool_use
    llm.is_multimodal = snapshot.llm.is_multimodal
    llm.extra_body = _thaw_json(snapshot.llm.extra_body)
    llm.response_format = (
        None if snapshot.llm.response_format is None
        else _thaw_json(snapshot.llm.response_format)
    )

    resolver = ToolManager.get_by_name if tool_resolver is None else tool_resolver
    tools: list[Tool] = []
    missing: list[str] = []
    policies: tuple[CapabilityPolicySnapshot | None, ...] = (
        tuple(snapshot.tool_policies)
        if snapshot.tool_policies
        else tuple(None for _ in snapshot.tool_names)
    )
    for name, schema, policy in zip(
        snapshot.tool_names,
        snapshot.tool_schemas,
        policies,
        strict=True,
    ):
        try:
            resolved = resolver(name)
        except Exception as exc:
            raise RuntimeInstantiationError(
                f"Tool resolver failed for persisted runtime tool '{name}'"
            ) from exc
        if resolved is None or resolved.name != name:
            missing.append(name)
            continue
        tools.append(
            _SnapshotBoundTool(
                resolved.model_copy(deep=True),
                schema,
                policy,
            )
        )
    if missing:
        raise MissingRequiredTools(
            "Persisted runtime tools are unavailable with exact names: " + ", ".join(missing)
        )

    runtime = Agent(
        name=snapshot.name,
        llm=llm,
        tools=tools,
        system_prompt=snapshot.system_prompt,
        sub_agents=[],
        mcp_servers=[],
        is_sub_agent=False,
    )
    from cognitrix.tasks.context import TaskContextManager

    runtime.__dict__["_ctx_mgr"] = TaskContextManager(snapshot)
    runtime.__dict__["_ctx_mgr_config"] = None
    return runtime
