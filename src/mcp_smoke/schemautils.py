"""inputSchema structural validation + synthetic argument generation.

Catches the schema bug classes that break MCP servers in production without any
server-side error -- e.g. the empty ``{"type": "object"}`` schema class
(modelcontextprotocol/typescript-sdk#2627) where clients silently send no
arguments.
"""

from __future__ import annotations

from typing import Any

JSON_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}
_MAX_SYNTH_DEPTH = 3  # recursion guard for self-referential schemas


def _type_list(t: Any) -> list[str]:
    """Normalize a JSON Schema 'type' (string or list) to a list of strings."""
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    return []


def validate_schema(schema: Any) -> list[tuple[str, str]]:
    """Return [(severity, message)] for a tool inputSchema.

    severity in {'fail', 'warn'}; empty list means structurally fine.
    """
    findings: list[tuple[str, str]] = []
    if not isinstance(schema, dict):
        return [("fail", f"inputSchema is not an object (got {type(schema).__name__})")]
    stype = schema.get("type", "object")
    findings.extend(
        ("fail", f"unknown JSON Schema type {t!r}")
        for t in _type_list(stype)
        if t not in JSON_TYPES
    )
    if stype != "object" and "object" not in _type_list(stype):
        findings.append(
            ("warn", f"tool inputSchema type is {stype!r}, expected 'object'")
        )
        return findings
    properties = _coerce_properties(schema, findings)
    _check_required(schema, properties, findings)
    if not properties and not schema.get("$ref") and not schema.get("allOf"):
        findings.append(
            (
                "warn",
                (
                    "empty inputSchema (no 'properties'): clients will send no "
                    "arguments; often a schema-builder bug (e.g. passing a Zod "
                    "object where a raw shape was expected)"
                ),
            )
        )
    for name, subschema in properties.items():
        findings.extend(
            (sev, f"property {name!r}: {msg}")
            for sev, msg in _validate_subschema(subschema)
        )
    return findings


def _coerce_properties(
    schema: dict[str, Any], findings: list[tuple[str, str]]
) -> dict[str, Any]:
    """Return the schema's properties dict, recording a finding if malformed."""
    properties = schema.get("properties", {})
    if "properties" in schema and not isinstance(properties, dict):
        findings.append(("fail", "'properties' must be an object"))
        return {}
    return properties


def _check_required(
    schema: dict[str, Any],
    properties: dict[str, Any],
    findings: list[tuple[str, str]],
) -> None:
    """Validate the schema's 'required' array against its properties."""
    if "required" not in schema:
        return
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
        findings.append(("fail", "'required' must be an array of strings"))
        return
    findings.extend(
        ("fail", f"required property {r!r} not defined in 'properties'")
        for r in required
        if r not in properties
    )


def _validate_subschema(sub: Any) -> list[tuple[str, str]]:
    """Validate one property subschema."""
    if isinstance(sub, bool):
        return []
    if not isinstance(sub, dict):
        return [("fail", f"schema must be object/boolean, got {type(sub).__name__}")]
    out = [
        ("fail", f"unknown type {t!r}")
        for t in _type_list(sub.get("type", "string"))
        if t not in JSON_TYPES
    ]
    if "enum" in sub and not isinstance(sub["enum"], list):
        out.append(("fail", "'enum' must be an array"))
    return out


def synth_args(schema: Any, _depth: int = 0) -> dict[str, Any]:
    """Build minimal synthetic arguments from an inputSchema.

    Only *required* properties are filled, with type-appropriate dummy values.
    Returns {} when nothing is required. Never raises on weird schemas.
    """
    if not isinstance(schema, dict) or _depth > _MAX_SYNTH_DEPTH:
        return {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    if not isinstance(required, list):
        required = []
    return {
        name: _dummy(properties.get(name, {}), _depth)
        for name in required
        if isinstance(name, str)
    }


def _dummy(subschema: Any, depth: int) -> Any:
    """Return a dummy JSON value matching a subschema's type."""
    if not isinstance(subschema, dict):
        return "test"
    if (
        "enum" in subschema
        and isinstance(subschema["enum"], list)
        and subschema["enum"]
    ):
        return subschema["enum"][0]
    if "const" in subschema:
        return subschema["const"]
    types = _type_list(subschema.get("type", "string")) or ["string"]
    dummy_fn = _DUMMY_BY_TYPE.get(types[0], _dummy_string)
    return dummy_fn(subschema, depth)


def _dummy_string(_subschema: Any, _depth: int) -> Any:
    return "test"


def _dummy_int(_subschema: Any, _depth: int) -> Any:
    return 1


def _dummy_float(_subschema: Any, _depth: int) -> Any:
    return 1.0


def _dummy_bool(_subschema: Any, _depth: int) -> Any:
    return True


def _dummy_null(_subschema: Any, _depth: int) -> Any:
    return None


def _dummy_array(subschema: Any, depth: int) -> Any:
    items = subschema.get("items")
    if isinstance(items, dict):
        return [_dummy(items, depth + 1)]
    return []


def _dummy_object(subschema: Any, depth: int) -> Any:
    return synth_args(subschema, depth + 1)


_DUMMY_BY_TYPE = {
    "string": _dummy_string,
    "integer": _dummy_int,
    "number": _dummy_float,
    "boolean": _dummy_bool,
    "null": _dummy_null,
    "array": _dummy_array,
    "object": _dummy_object,
}
