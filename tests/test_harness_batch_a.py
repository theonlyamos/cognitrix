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


class TestToolSchemaQuality:
    def test_optional_params_not_required_and_descriptions(self):
        from cognitrix.tools.tool import tool

        @tool(category="general")
        def sample(required_arg: str, optional_arg: int = 5):
            """Do a thing.

            :param required_arg: the required one
            :param optional_arg: the optional one
            """
            return "ok"

        fn = sample.to_dict_format()["function"]
        assert fn["parameters"]["required"] == ["required_arg"]
        assert set(fn["parameters"]["properties"]) == {"required_arg", "optional_arg"}
        assert fn["parameters"]["properties"]["required_arg"]["description"] == "the required one"


class TestToolSchemaTrimming:
    def test_description_trimmed_to_first_paragraph(self):
        from cognitrix.tools.tool import tool

        @tool(category='general')
        def verbose(path: str, limit: int = 5):
            """Do the thing to a path.

            Args:
                path (str): Which path to do the thing to.
                limit (int, optional): How many things. Defaults to 5.

            Returns:
                str: The result.

            Examples:
                - verbose("a/b")
            """
            return "ok"

        fn = verbose.to_dict_format()["function"]
        # Function description is the summary only — Args/Returns/Examples live
        # in the parameters schema, not duplicated in prose.
        assert fn["description"] == "Do the thing to a path."
        # Google-style Args lines feed per-parameter descriptions.
        assert fn["parameters"]["properties"]["path"]["description"] == "Which path to do the thing to."
        assert fn["parameters"]["required"] == ["path"]
