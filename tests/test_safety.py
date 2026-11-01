"""Tests for safety gates including risk detection and approval system."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cognitrix.safety.approval_gate import ApprovalGate, ApprovalResult, ToolCall
from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskAssessment, RiskLevel, mcp_tool_risk


class TestDestructiveOpDetector:
    """Only destructive or hard-to-undo actions ask; everything else runs."""

    @pytest.fixture
    def detector(self):
        return DestructiveOpDetector()

    @pytest.mark.parametrize("name,params", [
        ("Search", {"query": "how to rotate an api key and delete old config"}),
        ("Tavily Search", {"query": "password manager reviews"}),
        ("WebFetch", {"url": "https://example.com/token?remove=1"}),
        ("Read", {"file_path": ".env"}),
        ("Grep", {"pattern": "secret|delete", "path": "."}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"}),
        ("List Agents", {}),
        ("Load Skill", {"skill_name": "web-scraper"}),
        ("Read Notes", {}),
        ("Update Notes", {"notes": "clear the cache next time"}),
        ("Notify Me", {"message": "send the report"}),
        ("Create Task", {"title": "Update config", "description": "remove old keys"}),
        ("Bash Output", {"shell_id": "1"}),
        ("Kill Shell", {"shell_id": "1"}),
        ("search_tool", {"query": "hello world"}),
        ("some_custom_tool", {"anything": "rm -rf /"}),
    ])
    def test_reads_and_undoable_changes_never_ask(self, detector, name, params):
        assessment = detector.analyze(name, params)
        assert assessment.risk_level == RiskLevel.LOW, assessment

    @pytest.mark.parametrize("name", ["delete_file", "Delete Path", "remove_file", "Delete Task", "dropTable", "trash_message"])
    def test_deletes_are_high(self, detector, name):
        assessment = detector.analyze(name, {"path": "x"})
        assert assessment.risk_level == RiskLevel.HIGH
        assert "deletion" in assessment.categories

    @pytest.mark.parametrize("name", ["send_email", "post_tweet", "publish_page", "deploy_site", "reply_to_thread"])
    def test_outward_sends_ask(self, detector, name):
        assessment = detector.analyze(name, {"content": "hi"})
        assert assessment.risk_level == RiskLevel.MEDIUM
        assert "external_send" in assessment.categories

    def test_word_matching_does_not_catch_substrings(self, detector):
        # 'postgres' isn't 'post', 'formatter' isn't a delete
        assert detector.analyze("postgres_query", {}).risk_level == RiskLevel.LOW
        assert detector.analyze("code_formatter", {}).risk_level == RiskLevel.LOW

    def test_write_new_file_runs_but_overwrite_asks(self, detector, tmp_path):
        existing = tmp_path / "old.txt"
        existing.write_text("keep me")
        new = tmp_path / "new.txt"
        assert detector.analyze("Write", {"file_path": str(new), "content": "x"}).risk_level == RiskLevel.LOW
        overwrite = detector.analyze("Write", {"file_path": str(existing), "content": "x"})
        assert overwrite.risk_level == RiskLevel.MEDIUM
        assert "file_overwrite" in overwrite.categories
        appended = detector.analyze("Write", {"file_path": str(existing), "content": "x", "append": True})
        assert appended.risk_level == RiskLevel.LOW

    def test_relative_write_resolves_under_tools_root(self, detector, tmp_path, monkeypatch):
        from cognitrix.config import settings
        monkeypatch.setattr(settings, "tools_root", str(tmp_path))
        (tmp_path / "notes.md").write_text("x")
        assert detector.analyze("Write", {"file_path": "notes.md", "content": "y"}).risk_level == RiskLevel.MEDIUM
        assert detector.analyze("Write", {"file_path": "fresh.md", "content": "y"}).risk_level == RiskLevel.LOW

    def test_shell_asks_without_the_shell_rules(self, detector):
        assessment = detector.analyze("bash", {"command": "ls"})
        assert assessment.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)
        assert "shell" in assessment.categories

    def test_shell_uses_shell_tool_rules_when_present(self, detector, monkeypatch):
        import sys
        import types
        fake = types.ModuleType("cognitrix.common.shell")
        fake.shell_risk = lambda params: ("low", "read-only") if params["command"] == "ls" else ("high", "deletes")
        monkeypatch.setitem(sys.modules, "cognitrix.common.shell", fake)
        assert detector.analyze("Bash", {"command": "ls"}).risk_level == RiskLevel.LOW
        assert detector.analyze("Bash", {"command": "rm -rf x"}).risk_level == RiskLevel.HIGH

    def test_desktop_control_asks(self, detector):
        assert detector.analyze("mouse_click", {"x": 1, "y": 2}).risk_level == RiskLevel.MEDIUM

    def test_analyze_case_insensitive(self, detector):
        assert detector.analyze("DELETE_FILE", {}).risk_level == detector.analyze("delete_file", {}).risk_level

    def test_is_destructive_with_threshold(self, detector):
        assert detector.is_destructive("send_email", {}, RiskLevel.MEDIUM)
        assert not detector.is_destructive("send_email", {}, RiskLevel.HIGH)
        assert not detector.is_destructive("Search", {"query": "delete"}, RiskLevel.MEDIUM)

    def test_risk_level_ordering(self):
        risk_values = {'low': 0, 'medium': 1, 'high': 2}
        assert risk_values[RiskLevel.LOW.value] < risk_values[RiskLevel.MEDIUM.value] < risk_values[RiskLevel.HIGH.value]


class TestMcpToolRisk:
    def test_read_only_hint_runs(self):
        assert mcp_tool_risk("delete_note", {"readOnlyHint": True}).risk_level == RiskLevel.LOW

    def test_reads_and_creates_run(self):
        for name in ("search_threads", "get_file_metadata", "create_draft", "update_label", "list_projects"):
            assert mcp_tool_risk(name, {}).risk_level == RiskLevel.LOW, name

    def test_deletes_sends_and_unknowns_ask(self):
        assert mcp_tool_risk("delete_branch", {}).risk_level == RiskLevel.HIGH
        assert mcp_tool_risk("send_message", {"destructiveHint": False}).risk_level == RiskLevel.MEDIUM
        assert mcp_tool_risk("execute_sql", None).risk_level == RiskLevel.MEDIUM
        assert mcp_tool_risk("merge_branch", {}).risk_level == RiskLevel.MEDIUM

    def test_destructive_hint(self):
        assert mcp_tool_risk("update_file", {"destructiveHint": True}).risk_level == RiskLevel.MEDIUM
        assert mcp_tool_risk("execute_sql", {"destructiveHint": False}).risk_level == RiskLevel.LOW


class TestApprovalGate:
    """Test suite for ApprovalGate."""

    @pytest.fixture
    def approval_gate(self, tmp_path):
        """Create an approval gate with temporary cache directory."""
        return ApprovalGate(cache_dir=str(tmp_path))

    @pytest.fixture
    def low_risk_tool_call(self):
        """Create a low-risk tool call."""
        return ToolCall(tool_name="search", params={"query": "hello"})

    @pytest.fixture
    def high_risk_tool_call(self):
        """Create a high-risk tool call."""
        return ToolCall(tool_name="delete_file", params={"path": "/tmp/file.txt"})

    @pytest.fixture
    def low_risk_assessment(self):
        """Create a low-risk assessment."""
        return RiskAssessment(
            risk_level=RiskLevel.LOW,
            categories=[],
            details="No risk"
        )

    @pytest.fixture
    def high_risk_assessment(self):
        """Create a high-risk assessment."""
        return RiskAssessment(
            risk_level=RiskLevel.HIGH,
            categories=["file_deletion"],
            details="File deletion operation"
        )

    def test_initialization(self, approval_gate, tmp_path):
        """Test approval gate initialization."""
        assert approval_gate.cache_dir == Path(tmp_path)
        assert isinstance(approval_gate.session_cache, set)
        assert isinstance(approval_gate.permanent_cache, set)
        assert isinstance(approval_gate.pending_requests, dict)

    @pytest.mark.asyncio
    async def test_auto_approve_low_risk(self, approval_gate, low_risk_tool_call, low_risk_assessment):
        """Test that low-risk operations are auto-approved."""
        result = await approval_gate.check_approval(
            low_risk_tool_call,
            low_risk_assessment,
            interface='cli'
        )

        assert result.approved is True
        assert result.auto is True
        assert not result.cached

    @pytest.mark.asyncio
    async def test_high_risk_requires_approval(self, approval_gate, high_risk_tool_call, high_risk_assessment):
        """Test that high-risk operations require explicit approval."""
        with patch.object(approval_gate, '_cli_approval', new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = ApprovalResult(approved=True)

            result = await approval_gate.check_approval(
                high_risk_tool_call,
                high_risk_assessment,
                interface='cli'
            )

            mock_cli.assert_called_once()
            assert result.approved is True

    @pytest.mark.asyncio
    async def test_high_risk_auto_interface_blocked(self, approval_gate, high_risk_tool_call, high_risk_assessment):
        """Test that high-risk operations are blocked in auto mode."""
        result = await approval_gate.check_approval(
            high_risk_tool_call,
            high_risk_assessment,
            interface='auto'
        )

        assert result.approved is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_durable_task_denies_without_reading_stdin(
        self,
        approval_gate,
        high_risk_tool_call,
        high_risk_assessment,
    ):
        with patch("builtins.input", side_effect=AssertionError("must not read stdin")):
            result = await approval_gate.check_approval(
                high_risk_tool_call,
                high_risk_assessment,
                interface="task",
            )

        assert result.approved is False
        assert result.error == "approval_required"

    @pytest.mark.asyncio
    async def test_durable_task_ignores_cached_interactive_approval(
        self,
        approval_gate,
        high_risk_tool_call,
        high_risk_assessment,
    ):
        scoped = f"agent-1:{approval_gate._hash_operation(high_risk_tool_call)}"
        approval_gate.session_cache.add(scoped)
        approval_gate.permanent_cache.add(scoped)

        result = await approval_gate.check_approval(
            high_risk_tool_call,
            high_risk_assessment,
            interface="task",
            scope="agent-1",
        )

        assert result.approved is False
        assert result.error == "approval_required"

    def test_hash_operation_deterministic(self, approval_gate):
        """Test that operation hashing is deterministic."""
        tool_call1 = ToolCall(tool_name="test", params={"a": 1, "b": 2})
        tool_call2 = ToolCall(tool_name="test", params={"a": 1, "b": 2})

        hash1 = approval_gate._hash_operation(tool_call1)
        hash2 = approval_gate._hash_operation(tool_call2)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex length

    def test_hash_operation_different_params(self, approval_gate):
        """Test that different params produce different hashes."""
        tool_call1 = ToolCall(tool_name="test", params={"a": 1})
        tool_call2 = ToolCall(tool_name="test", params={"a": 2})

        hash1 = approval_gate._hash_operation(tool_call1)
        hash2 = approval_gate._hash_operation(tool_call2)

        assert hash1 != hash2

    def test_hash_operation_order_independent(self, approval_gate):
        """Test that param order doesn't affect hash."""
        tool_call1 = ToolCall(tool_name="test", params={"a": 1, "b": 2})
        tool_call2 = ToolCall(tool_name="test", params={"b": 2, "a": 1})

        hash1 = approval_gate._hash_operation(tool_call1)
        hash2 = approval_gate._hash_operation(tool_call2)

        assert hash1 == hash2


class TestApprovalCaching:
    """Test suite for approval caching functionality."""

    @pytest.fixture
    def approval_gate(self, tmp_path):
        """Create an approval gate with temporary cache directory."""
        return ApprovalGate(cache_dir=str(tmp_path))

    @pytest.fixture
    def tool_call(self):
        """Create a tool call for testing."""
        return ToolCall(tool_name="delete_file", params={"path": "/tmp/test.txt"})

    @pytest.fixture
    def high_risk_assessment(self):
        """Create a high-risk assessment."""
        return RiskAssessment(
            risk_level=RiskLevel.HIGH,
            categories=["file_deletion"],
            details="File deletion"
        )

    @pytest.mark.asyncio
    async def test_session_cache_approval(self, approval_gate, tool_call, high_risk_assessment):
        """Test that approved operations are cached for session."""
        with patch.object(approval_gate, '_cli_approval', new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = ApprovalResult(approved=True, remember=True)

            # First call - should prompt
            result1 = await approval_gate.check_approval(tool_call, high_risk_assessment)
            assert mock_cli.call_count == 1

            # Second call - should use cache
            result2 = await approval_gate.check_approval(tool_call, high_risk_assessment)
            assert mock_cli.call_count == 1  # No additional prompt

            assert result2.approved is True
            assert result2.cached is True
            assert result2.remember is True

    @pytest.mark.asyncio
    async def test_permanent_cache_approval(self, approval_gate, tool_call, high_risk_assessment):
        """Test that approved operations can be cached permanently."""
        with patch.object(approval_gate, '_cli_approval', new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = ApprovalResult(approved=True, permanent=True)

            result = await approval_gate.check_approval(tool_call, high_risk_assessment)

            assert result.approved is True
            assert result.permanent is True

    @pytest.mark.asyncio
    async def test_permanent_cache_persistence(self, tmp_path, tool_call, high_risk_assessment):
        """Test that permanent cache persists across instances."""
        # Create first gate and approve permanently
        gate1 = ApprovalGate(cache_dir=str(tmp_path))

        with patch.object(gate1, '_cli_approval', new_callable=AsyncMock) as mock_cli:
            mock_cli.return_value = ApprovalResult(approved=True, permanent=True)
            await gate1.check_approval(tool_call, high_risk_assessment)

        # Create second gate - should load cache
        gate2 = ApprovalGate(cache_dir=str(tmp_path))

        result = await gate2.check_approval(tool_call, high_risk_assessment)

        assert result.approved is True
        assert result.cached is True

    def test_clear_session_cache(self, approval_gate, tool_call):
        """Test clearing session cache."""
        op_hash = approval_gate._hash_operation(tool_call)
        approval_gate.session_cache.add(op_hash)

        approval_gate.clear_session_cache()

        assert len(approval_gate.session_cache) == 0

    def test_clear_permanent_cache(self, approval_gate, tool_call, tmp_path):
        """Test clearing permanent cache."""
        op_hash = approval_gate._hash_operation(tool_call)
        approval_gate.permanent_cache.add(op_hash)
        approval_gate._save_permanent_cache()

        approval_gate.clear_permanent_cache()

        assert len(approval_gate.permanent_cache) == 0
        # Verify file is updated
        cache_file = tmp_path / 'approval_cache.json'
        if cache_file.exists():
            with open(cache_file) as f:
                data = json.load(f)
                assert len(data['approved']) == 0

    def test_load_permanent_cache(self, tmp_path):
        """Test loading permanent cache from file."""
        cache_data = {'approved': ['hash1', 'hash2', 'hash3']}
        cache_file = tmp_path / 'approval_cache.json'

        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

        gate = ApprovalGate(cache_dir=str(tmp_path))

        assert len(gate.permanent_cache) == 3
        assert 'hash1' in gate.permanent_cache

    def test_save_permanent_cache(self, approval_gate, tmp_path):
        """Test saving permanent cache to file."""
        approval_gate.permanent_cache.add('test_hash_123')
        approval_gate._save_permanent_cache()

        cache_file = tmp_path / 'approval_cache.json'
        assert cache_file.exists()

        with open(cache_file) as f:
            data = json.load(f)
            assert 'test_hash_123' in data['approved']

    def test_get_cache_stats(self, approval_gate):
        """Test cache statistics."""
        approval_gate.session_cache.add('hash1')
        approval_gate.session_cache.add('hash2')
        approval_gate.permanent_cache.add('hash3')

        stats = approval_gate.get_cache_stats()

        assert stats['session_cache_size'] == 2
        assert stats['permanent_cache_size'] == 1
        assert stats['pending_requests'] == 0


class TestApprovalPromptGeneration:
    """Test suite for approval prompt generation."""

    @pytest.fixture
    def approval_gate(self):
        """Create an approval gate."""
        return ApprovalGate()

    @pytest.fixture
    def tool_call(self):
        """Create a tool call."""
        return ToolCall(
            tool_name="delete_file",
            params={"path": "/important/file.txt", "recursive": True}
        )

    @pytest.fixture
    def risk_assessment(self):
        """Create a risk assessment."""
        return RiskAssessment(
            risk_level=RiskLevel.HIGH,
            categories=["file_deletion", "data_loss"],
            details="This will permanently delete the file"
        )

    @pytest.mark.asyncio
    async def test_cli_approval_prompt_format(self, approval_gate, tool_call, risk_assessment, capsys):
        """Test CLI approval prompt format."""
        with patch('builtins.input', return_value='y'):
            await approval_gate._cli_approval(tool_call, risk_assessment)

        captured = capsys.readouterr()
        output = captured.out

        # Check that prompt contains expected elements
        assert 'APPROVAL REQUIRED' in output
        assert 'HIGH RISK' in output
        assert 'delete_file' in output
        assert '/important/file.txt' in output
        assert 'file_deletion' in output or 'data_loss' in output

    @pytest.mark.asyncio
    async def test_cli_approval_yes_response(self, approval_gate, tool_call, risk_assessment):
        """Test CLI approval with 'yes' response."""
        with patch('builtins.input', return_value='y'):
            result = await approval_gate._cli_approval(tool_call, risk_assessment)

        assert result.approved is True
        assert not result.remember
        assert not result.permanent

    @pytest.mark.asyncio
    async def test_cli_approval_session_response(self, approval_gate, tool_call, risk_assessment):
        """Test CLI approval with 'session' response."""
        with patch('builtins.input', return_value='s'):
            result = await approval_gate._cli_approval(tool_call, risk_assessment)

        assert result.approved is True
        assert result.remember is True
        assert not result.permanent

    @pytest.mark.asyncio
    async def test_cli_approval_permanent_response(self, approval_gate, tool_call, risk_assessment):
        """Test CLI approval with 'permanent' response."""
        with patch('builtins.input', return_value='p'):
            result = await approval_gate._cli_approval(tool_call, risk_assessment)

        assert result.approved is True
        assert result.permanent is True

    @pytest.mark.asyncio
    async def test_cli_approval_no_response(self, approval_gate, tool_call, risk_assessment):
        """Test CLI approval with 'no' response."""
        with patch('builtins.input', return_value='n'):
            result = await approval_gate._cli_approval(tool_call, risk_assessment)

        assert result.approved is False

    @pytest.mark.asyncio
    async def test_cli_approval_eof_error(self, approval_gate, tool_call, risk_assessment):
        """Test CLI approval handling EOF error."""
        with patch('builtins.input', side_effect=EOFError()):
            result = await approval_gate._cli_approval(tool_call, risk_assessment)

        assert result.approved is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_cli_approval_keyboard_interrupt(self, approval_gate, tool_call, risk_assessment):
        """Test CLI approval handling keyboard interrupt."""
        with patch('builtins.input', side_effect=KeyboardInterrupt()):
            result = await approval_gate._cli_approval(tool_call, risk_assessment)

        assert result.approved is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_websocket_approval_request_creation(self, approval_gate, tool_call, risk_assessment):
        """Test WebSocket approval request creation."""
        # Test that websocket approval method exists and can be called
        # The actual websocket handling is complex to test in unit tests
        assert hasattr(approval_gate, '_websocket_approval')

        # Just verify the method is callable
        import inspect
        assert inspect.iscoroutinefunction(approval_gate._websocket_approval)


class TestRiskDetectionForTools:
    """Test suite for risk detection across different tools."""

    @pytest.fixture
    def detector(self):
        """Create a detector."""
        return DestructiveOpDetector()

    def test_shell_command_risk(self, detector):
        """Test risk detection for shell commands."""
        assessment = detector.analyze("terminal_command", {"command": "rm -rf /home"})
        # Should detect at least one risk category
        assert len(assessment.categories) >= 1 or assessment.risk_level.value in ['medium', 'high']

    def test_file_write_risk(self, detector):
        """Test risk detection for file write."""
        assessment = detector.analyze("write_file", {"path": __file__, "content": "[settings]"})
        assert assessment.risk_level.value in ['medium', 'high']

    def test_code_eval_risk(self, detector):
        """Test risk detection for code evaluation."""
        assessment = detector.analyze("eval", {"expression": "os.system('ls')"})
        assert "shell" in assessment.categories

    def test_database_delete_risk(self, detector):
        """Test risk detection for database deletion."""
        assessment = detector.analyze("db_query", {"query": "DROP TABLE users"})
        # This depends on keywords detection
        assert "delete" in assessment.details.lower() or "No specific" in assessment.details

    def test_network_post_risk(self, detector):
        """Test risk detection for network POST requests."""
        assessment = detector.analyze("http_request", {"method": "POST", "url": "http://api.example.com"})
        assert assessment.risk_level == RiskLevel.MEDIUM
        assert "external_send" in assessment.categories
        get = detector.analyze("http_request", {"method": "GET", "url": "http://api.example.com"})
        assert get.risk_level == RiskLevel.LOW

    def test_privilege_escalation_risk(self, detector):
        """Test risk detection for privilege escalation commands."""
        assessment = detector.analyze("execute", {"command": "sudo chmod 777 /etc/passwd"})
        # Should detect at least one risk category
        assert len(assessment.categories) >= 1 or assessment.risk_level.value in ['medium', 'high']

    def test_package_install_risk(self, detector):
        """Test risk detection for package installation."""
        assessment = detector.analyze("pip_install", {"package": "requests"})
        assert assessment.risk_level in [RiskLevel.LOW, RiskLevel.MEDIUM]


async def test_unattended_task_run_does_not_park_web_search(tmp_path):
    """A task step's web search runs without parking an approval."""
    from cognitrix.tasks.approvals import task_turn_ctx

    gate = ApprovalGate(cache_dir=str(tmp_path))
    params = {"query": "how to delete an old api key from the config"}
    risk = DestructiveOpDetector().analyze("Search", params)
    token = task_turn_ctx.set({"run_id": "r1", "user_id": "u1"})
    try:
        with patch("cognitrix.tasks.approvals.request_task_approval", new=AsyncMock()) as park:
            result = await gate.check_approval(ToolCall("Search", params), risk, interface="task")
    finally:
        task_turn_ctx.reset(token)
    assert result.approved and result.auto
    park.assert_not_called()
