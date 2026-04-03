"""Tests for allowed-tools parser and executor functionality."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from cognitrix.skills.parser import SkillParser
from cognitrix.skills.executor import SkillExecutor, TOOL_ALIASES


class TestAllowedToolsParser:
    """Test suite for parsing allowed-tools in SKILL.md frontmatter."""

    @pytest.fixture
    def parser(self):
        return SkillParser()

    def test_array_format(self, parser):
        """Test allowed-tools as array ['Bash', 'Read']"""
        content = """---
name: test-skill
description: Test skill
allowed-tools:
  - Bash
  - Read
---

Test body"""
        manifest = parser.parse(content)
        assert manifest.allowed_tools == ['Bash', 'Read']

    def test_string_format_single(self, parser):
        """Test allowed-tools as single string 'Bash'"""
        content = """---
name: test-skill
description: Test skill
allowed-tools: Bash
---

Test body"""
        manifest = parser.parse(content)
        assert manifest.allowed_tools == ['Bash']

    def test_string_format_multiple(self, parser):
        """Test allowed-tools as space-delimited string 'Bash Read Glob'"""
        content = """---
name: test-skill
description: Test skill
allowed-tools: Bash Read Glob
---

Test body"""
        manifest = parser.parse(content)
        assert manifest.allowed_tools == ['Bash', 'Read', 'Glob']

    def test_string_format_with_restrictions(self, parser):
        """Test allowed-tools with restrictions 'Bash(git *) Read'"""
        content = """---
name: test-skill
description: Test skill
allowed-tools: Bash(git *) Read
---

Test body"""
        manifest = parser.parse(content)
        assert manifest.allowed_tools == ['Bash(git *)', 'Read']

    def test_missing_allowed_tools(self, parser):
        """Test when allowed-tools is not specified"""
        content = """---
name: test-skill
description: Test skill
---

Test body"""
        manifest = parser.parse(content)
        assert manifest.allowed_tools is None


class TestToolAliases:
    """Test suite for tool alias resolution."""

    def test_alias_mapping(self):
        """Test that TOOL_ALIASES maps standard names to Cognitrix tools"""
        assert TOOL_ALIASES['bash'] == 'Bash'
        assert TOOL_ALIASES['shell'] == 'Bash'
        assert TOOL_ALIASES['read'] == 'Read'
        assert TOOL_ALIASES['write'] == 'Write'
        assert TOOL_ALIASES['edit'] == 'Edit'
        assert TOOL_ALIASES['grep'] == 'Grep'
        assert TOOL_ALIASES['glob'] == 'Glob'

    def test_unknown_alias_resolves_to_title(self):
        """Test unknown aliases resolve via .title()"""
        assert TOOL_ALIASES.get('unknown') is None


class TestResolveAllowedTools:
    """Test suite for _resolve_allowed_tools method."""

    @pytest.fixture
    def executor(self):
        mock_agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent_manager.agent = mock_agent
        mock_llm = MagicMock()
        return SkillExecutor(mock_agent_manager, mock_llm)

    def test_simple_list(self, executor):
        """Test resolving ['Bash', 'Read']"""
        allowed_set, restriction_map = executor._resolve_allowed_tools(['Bash', 'Read'])
        assert 'Bash' in allowed_set
        assert 'Read' in allowed_set

    def test_alias_names(self, executor):
        """Test using alias names 'bash' -> 'Bash'"""
        allowed_set, restriction_map = executor._resolve_allowed_tools(['bash', 'read'])
        assert 'Bash' in allowed_set
        assert 'Read' in allowed_set

    def test_restrictions_extracted(self, executor):
        """Test extracting restrictions from 'Bash(git *)'"""
        allowed_set, restriction_map = executor._resolve_allowed_tools(['Bash(git *)'])
        assert 'Bash' in allowed_set
        assert 'git' in restriction_map.get('Bash', [])
        assert '*' in restriction_map.get('Bash', [])

    def test_mixed_alias_and_restriction(self, executor):
        """Test 'bash(python *)' resolves correctly"""
        allowed_set, restriction_map = executor._resolve_allowed_tools(['bash(python *)'])
        assert 'Bash' in allowed_set
        assert 'python' in restriction_map.get('Bash', [])
        assert '*' in restriction_map.get('Bash', [])

    def test_empty_list(self, executor):
        """Test empty list returns empty sets"""
        allowed_set, restriction_map = executor._resolve_allowed_tools([])
        assert allowed_set == set()
        assert restriction_map == {}


class TestApplyToolRestrictions:
    """Test suite for _apply_tool_restrictions method."""

    @pytest.fixture
    def executor(self):
        mock_agent_manager = MagicMock()
        mock_agent = MagicMock()
        mock_agent_manager.agent = mock_agent
        mock_llm = MagicMock()
        return SkillExecutor(mock_agent_manager, mock_llm)

    def test_no_restrictions(self, executor):
        """Test when no restrictions - passes through"""
        tool_calls = [{'name': 'Bash', 'arguments': {'command': 'ls'}}]
        result = executor._apply_tool_restrictions(tool_calls, {})
        assert result == tool_calls

    def test_allowed_command(self, executor):
        """Test allowed command passes through"""
        tool_calls = [{'name': 'Bash', 'arguments': {'command': 'git status'}}]
        restriction_map = {'Bash': ['git', '*']}
        result = executor._apply_tool_restrictions(tool_calls, restriction_map)
        assert result[0].get('name') == 'Bash'

    def test_blocked_command(self, executor):
        """Test blocked command returns error when no pattern matches"""
        tool_calls = [{'name': 'Bash', 'arguments': {'command': 'rm -rf /'}}]
        restriction_map = {'Bash': ['git status', 'python *']}
        result = executor._apply_tool_restrictions(tool_calls, restriction_map)
        assert 'error' in result[0]

    def test_wildcard_allows_all(self, executor):
        """Test '*' wildcard allows any command"""
        tool_calls = [{'name': 'Bash', 'arguments': {'command': 'any command'}}]
        restriction_map = {'Bash': ['*']}
        result = executor._apply_tool_restrictions(tool_calls, restriction_map)
        assert result[0].get('name') == 'Bash'

    def test_unrestricted_tool_passes(self, executor):
        """Test tool not in restriction_map passes through"""
        tool_calls = [{'name': 'Bash', 'arguments': {'command': 'ls'}}]
        restriction_map = {'OtherTool': ['*']}
        result = executor._apply_tool_restrictions(tool_calls, restriction_map)
        assert result[0].get('name') == 'Bash'


class TestAllowedToolsSerialization:
    """Test serialization of allowed-tools."""

    @pytest.fixture
    def parser(self):
        return SkillParser()

    def test_serialize_array(self, parser):
        """Test serializing allowed-tools as array"""
        from cognitrix.skills.models import SkillManifest, SkillSafety
        manifest = SkillManifest(
            name='test',
            description='Test',
            allowed_tools=['Bash', 'Read'],
            safety=SkillSafety(),
        )
        result = parser.serialize(manifest)
        assert 'allowed-tools:' in result
        assert 'Bash' in result
        assert 'Read' in result
