"""MCP integration regression tests.

- Dynamic MCP tool wrappers must advertise the server's real parameters (the
  old wrapper exposed a single catch-all `kwargs`).
- External tool metadata is sanitized before use in tool names / schema.
- MCP server calls are timeout-bounded.
"""

import asyncio

import pytest

from cognitrix.mcp.tools import create_mcp_tool_wrapper


def test_wrapper_advertises_real_parameters():
    info = {
        'name': 'get_forecast',
        'description': 'Weather forecast',
        'input_schema': {
            'type': 'object',
            'properties': {
                'city': {'type': 'string', 'description': 'city name'},
                'days': {'type': 'integer'},
            },
            'required': ['city'],
        },
    }
    schema = create_mcp_tool_wrapper('srv', info).to_dict_format()['function']
    props = schema['parameters']['properties']
    assert set(props) == {'city', 'days'}          # real params, not "kwargs"
    assert 'kwargs' not in props
    assert props['city']['type'] == 'string'
    assert props['days']['type'] == 'integer'
    assert schema['parameters']['required'] == ['city']
    assert props['city']['description'] == 'city name'


def test_wrapper_sanitizes_hostile_names_and_bad_schema():
    # A dotted/slashed tool name must become a valid provider tool name, and a
    # non-dict input_schema must not crash.
    info = {'name': '../evil.name', 'description': 'x', 'input_schema': 'not-a-dict'}
    schema = create_mcp_tool_wrapper('srv', info).to_dict_format()['function']
    import re
    assert re.fullmatch(r'[A-Za-z0-9_-]+', schema['name'])
    assert schema['parameters']['properties'] == {}


@pytest.mark.asyncio
async def test_call_tool_times_out(monkeypatch):
    from cognitrix.mcp import client as mcp_client
    from cognitrix.mcp.client import DynamicMCPClient

    monkeypatch.setattr(mcp_client, "DEFAULT_MCP_TIMEOUT", 0.05)

    class HungSession:
        async def call_tool(self, name, args):
            await asyncio.sleep(5)  # never returns in time

    c = DynamicMCPClient()
    c.sessions['srv'] = HungSession()
    result = await c.call_tool('srv', 'x', {})
    assert isinstance(result, str) and 'timed out' in result.lower()
