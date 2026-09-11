"""Unit tests for schema validation and synthetic argument generation."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_smoke.schemautils import synth_args, validate_schema  # noqa: E402


def test_valid_schema_has_no_findings():
    schema = {"type": "object",
              "properties": {"q": {"type": "string"}},
              "required": ["q"]}
    assert validate_schema(schema) == []


def test_empty_schema_warns():
    findings = validate_schema({"type": "object"})
    assert any(sev == "warn" and "empty inputSchema" in msg
               for sev, msg in findings)


def test_dangling_required_fails():
    findings = validate_schema({"type": "object",
                                "properties": {"a": {"type": "string"}},
                                "required": ["b"]})
    assert any(sev == "fail" and "'b' not defined" in msg
               for sev, msg in findings)


def test_unknown_type_fails():
    findings = validate_schema({"type": "object",
                                "properties": {"x": {"type": "frobnicate"}}})
    assert any(sev == "fail" and "unknown type" in msg
               for sev, msg in findings)


def test_non_dict_schema_fails():
    assert validate_schema("nope")[0][0] == "fail"


def test_synth_args_fills_required_only():
    schema = {"type": "object",
              "properties": {"req": {"type": "string"},
                             "opt": {"type": "integer"}},
              "required": ["req"]}
    assert synth_args(schema) == {"req": "test"}


def test_synth_args_types():
    schema = {"type": "object",
              "properties": {
                  "s": {"type": "string"},
                  "i": {"type": "integer"},
                  "n": {"type": "number"},
                  "b": {"type": "boolean"},
                  "a": {"type": "array", "items": {"type": "string"}},
                  "o": {"type": "object",
                        "properties": {"x": {"type": "integer"}},
                        "required": ["x"]},
                  "e": {"type": "string", "enum": ["a", "b"]},
              },
              "required": ["s", "i", "n", "b", "a", "o", "e"]}
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
