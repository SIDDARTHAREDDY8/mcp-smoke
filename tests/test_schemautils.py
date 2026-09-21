"""Unit tests for schema validation and synthetic argument generation."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_smoke.schemautils import synth_args, validate_schema


def test_valid_schema_has_no_findings():
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    }
    assert validate_schema(schema) == []


def test_empty_schema_warns():
    findings = validate_schema({"type": "object"})
    assert any(sev == "warn" and "empty inputSchema" in msg for sev, msg in findings)


def test_dangling_required_fails():
    findings = validate_schema(
        {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"]}
    )
    assert any(sev == "fail" and "'b' not defined" in msg for sev, msg in findings)


def test_unknown_type_fails():
    findings = validate_schema(
        {"type": "object", "properties": {"x": {"type": "frobnicate"}}}
    )
    assert any(sev == "fail" and "unknown type" in msg for sev, msg in findings)


def test_non_dict_schema_fails():
    assert validate_schema("nope")[0][0] == "fail"


def test_synth_args_fills_required_only():
    schema = {
        "type": "object",
        "properties": {"req": {"type": "string"}, "opt": {"type": "integer"}},
        "required": ["req"],
    }
    assert synth_args(schema) == {"req": "test"}


def test_synth_args_types():
    schema = {
        "type": "object",
        "properties": {
            "s": {"type": "string"},
            "i": {"type": "integer"},
            "n": {"type": "number"},
            "b": {"type": "boolean"},
            "a": {"type": "array", "items": {"type": "string"}},
            "o": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
            "e": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["s", "i", "n", "b", "a", "o", "e"],
    }
    args = synth_args(schema)
    assert args["s"] == "test"
    assert args["i"] == 1 and isinstance(args["i"], int)
    assert args["n"] == 1.0
    assert args["b"] is True
    assert args["a"] == ["test"]
    assert args["o"] == {"x": 1}
    assert args["e"] == "a"


def test_synth_args_never_raises_on_weird_schema():
    assert synth_args({"type": "object"}) == {}
    assert synth_args(None) == {}
    assert synth_args({"properties": None, "required": "nope"}) == {}


def test_validate_schema_rejects_non_dict():
    sevs = validate_schema(["not", "a", "dict"])
    assert sevs and sevs[0][0] == "fail" and "not an object" in sevs[0][1]


def test_validate_schema_unknown_type():
    sevs = validate_schema({"type": "frobnicate"})
    assert any(s == "fail" and "frobnicate" in m for s, m in sevs)


def test_validate_schema_non_object_type_warns():
    sevs = validate_schema({"type": "string"})
    assert any(s == "warn" and "expected 'object'" in m for s, m in sevs)


def test_validate_schema_properties_must_be_object():
    sevs = validate_schema({"type": "object", "properties": ["x"]})
    assert any(s == "fail" and "'properties' must be an object" in m for s, m in sevs)


def test_validate_schema_required_must_be_string_array():
    sevs = validate_schema({"type": "object", "required": "name"})
    assert any(
        s == "fail" and "'required' must be an array of strings" in m for s, m in sevs
    )
    sevs = validate_schema({"type": "object", "required": [1, 2]})
    assert any(
        s == "fail" and "'required' must be an array of strings" in m for s, m in sevs
    )


def test_validate_schema_property_subschema_branches():
    # boolean subschema is fine
    assert validate_schema({"type": "object", "properties": {"a": True}}) == []
    sevs = validate_schema({"type": "object", "properties": {"a": 42}})
    assert any(s == "fail" and "object/boolean" in m for s, m in sevs)
    sevs = validate_schema(
        {"type": "object", "properties": {"a": {"type": "string", "enum": "x"}}}
    )
    assert any(s == "fail" and "'enum' must be an array" in m for s, m in sevs)


def test_synth_args_non_dict_subschema_and_empty_enum():
    assert synth_args(None) == {}
    args = synth_args(
        {
            "type": "object",
            "properties": {"e": {"type": "string", "enum": []}},
            "required": ["e"],
        }
    )
    assert args["e"] == "test"
