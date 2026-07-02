"""Batch A harness fixes: risk ordinal, detector tool names, zero-arg tool schema."""

from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel


class TestRiskOrdinal:
    def test_high_only_op_rated_high(self):
        # A code-execution tool (HIGH) that matches no MEDIUM category must be
        # rated HIGH — the old lexicographic compare left it at LOW (auto-approved).
        detector = DestructiveOpDetector()
        risk = detector.analyze("bash", {"command": "ls"})
        assert risk.risk_level == RiskLevel.HIGH

    def test_write_edit_flagged(self):
        detector = DestructiveOpDetector()
        order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        for name in ("Write", "Edit"):
            risk = detector.analyze(name, {"file_path": "notes.txt", "content": "hi"})
            assert order[risk.risk_level] >= order[RiskLevel.MEDIUM], f"{name} not flagged"

    def test_delete_flagged_high(self):
        detector = DestructiveOpDetector()
        risk = detector.analyze("delete path", {"path": "notes.txt"})
        assert risk.risk_level == RiskLevel.HIGH


class TestZeroArgToolSchema:
    def test_parameterless_tool_has_valid_schema(self):
        from cognitrix.models.tool import Tool

        t = Tool(name="Ping", description="Ping something", parameters={})
        schema = t.to_dict_format()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "Ping"
        assert schema["function"]["parameters"] == {
            "type": "object", "properties": {}, "required": [],
        }
