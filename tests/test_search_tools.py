from pathlib import Path

import pytest
import requests

from cognitrix.config import settings
from cognitrix.skills.parser import SkillParser
from cognitrix.tools.base import ToolManager, clear_tool_cache


@pytest.fixture(autouse=True)
def refresh_tool_registry():
    clear_tool_cache()
    yield
    clear_tool_cache()


def test_native_search_tools_are_registered_by_provider_role():
    search = ToolManager.get_by_name("Search")
    tavily = ToolManager.get_by_name("Tavily Search")

    assert search is not None
    assert "Brave Search API" in search.description
    assert tavily is not None
    assert "Tavily API" in tavily.description


@pytest.mark.asyncio
async def test_search_uses_brave_search_api_and_preserves_unicode(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "brave-key")
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "web": {
                    "results": [
                        {
                            "title": "AI Agents — Today",
                            "description": "Clinical‑dataset agents <strong>improved</strong>.",
                            "url": "https://example.com/agents",
                        }
                    ]
                }
            }

    def fake_get(url, *, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(requests, "get", fake_get)

    result = await ToolManager.get_by_name("Search").run(
        query="latest AI agents", max_results=50
    )

    assert captured == {
        "url": "https://api.search.brave.com/res/v1/web/search",
        "params": {"q": "latest AI agents", "count": 20},
        "headers": {
            "Accept": "application/json",
            "X-Subscription-Token": "brave-key",
        },
        "timeout": 15,
    }
    assert "AI Agents — Today" in result.content
    assert "Clinical‑dataset agents improved." in result.content
    assert "https://example.com/agents" in result.content


@pytest.mark.asyncio
async def test_search_supports_only_brave_search_api_key(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "")
    monkeypatch.setenv("BRAVE_API_KEY", "legacy-key")

    result = await ToolManager.get_by_name("Search").run(query="agents")

    assert result.outcome.status == "error"
    assert result.outcome.error.code == "brave_search_not_configured"
    assert "BRAVE_SEARCH_API_KEY" in result.outcome.text


@pytest.mark.asyncio
async def test_search_reports_brave_http_errors_as_failures(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "brave-key")
    response = requests.Response()
    response.status_code = 401
    response.url = "https://api.search.brave.com/res/v1/web/search"
    response._content = b'{"message":"invalid token"}'
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)

    result = await ToolManager.get_by_name("Search").run(query="agents")

    assert result.outcome.status == "error"
    assert result.outcome.error.code == "brave_search_http_error"
    assert result.outcome.error.retryable is False
    assert "401" in result.outcome.text


@pytest.mark.asyncio
async def test_tavily_search_retains_existing_provider(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "tavily-key")
    captured = {}

    class Client:
        def __init__(self, api_key):
            captured["api_key"] = api_key

        def search(self, **kwargs):
            captured.update(kwargs)
            return {
                "results": [
                    {
                        "title": "Tavily result",
                        "content": "Provider retained",
                        "url": "https://example.com/tavily",
                    }
                ]
            }

    monkeypatch.setattr("tavily.TavilyClient", Client)

    result = await ToolManager.get_by_name("Tavily Search").run(
        query="provider test", max_results=3
    )

    assert captured == {
        "api_key": "tavily-key",
        "query": "provider test",
        "max_results": 3,
    }
    assert "Tavily result" in result.content


@pytest.mark.asyncio
async def test_tavily_search_reports_missing_key_as_failure(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = await ToolManager.get_by_name("Tavily Search").run(query="agents")

    assert result.outcome.status == "error"
    assert result.outcome.error.code == "tavily_search_not_configured"
    assert "TAVILY_API_KEY" in result.outcome.text


def test_brave_skill_is_retired_and_internet_search_uses_tavily_tool():
    builtin = Path(__file__).parents[1] / "cognitrix" / "skills" / "builtin"

    assert not (builtin / "brave-search" / "SKILL.md").exists()
    manifest = SkillParser().parse_file(builtin / "internet-search" / "SKILL.md")
    assert manifest.allowed_tools == ["Tavily Search"]
    assert "Tavily Search" in manifest.body
