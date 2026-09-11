"""inputSchema structural validation + synthetic argument generation.

Catches the schema bug classes that break MCP servers in production without any
server-side error -- e.g. the empty ``{"type": "object"}`` schema class
(modelcontextprotocol/typescript-sdk#2627) where clients silently send no
arguments.
"""
from __future__ import annotations

JSON_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}


def _type_list(t):
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    return []


def validate_schema(schema):
    """Return [(severity, message)] for a tool inputSchema. severity in
    {'fail', 'warn'}; empty list means structurally fine."""
    findings = []
    if not isinstance(schema, dict):
        return [("fail", f"inputSchema is not an object (got {type(schema).__name__})")]
    stype = schema.get("type", "object")
    for t in _type_list(stype):
        if t not in JSON_TYPES:
            findings.append(("fail", f"unknown JSON Schema type {t!r}"))
    if stype != "object" and "object" not in _type_list(stype):
        findings.append(
            ("warn", f"tool inputSchema type is {stype!r}, expected 'object'")
        )
        return findings
    properties = schema.get("properties", {})
    if "properties" in schema and not isinstance(properties, dict):
        findings.append(("fail", "'properties' must be an object"))
        properties = {}
    required = schema.get("required", [])
    if "required" in schema:
        if not isinstance(required, list) or not all(
            isinstance(r, str) for r in required
        ):
            findings.append(("fail", "'required' must be an array of strings"))
            required = []
        else:
            for r in required:
                if r not in properties:
                    findings.append(
                        ("fail", f"required property {r!r} not defined in 'properties'")
                    )
    if not properties and not schema.get("$ref") and not schema.get("allOf"):
        findings.append(
            (
                "warn",
                "empty inputSchema (no 'properties'): clients will send no "
                "arguments; often a schema-builder bug (e.g. passing a Zod "
                "object where a raw shape was expected)",
            )
        )
    for name, subschema in properties.items():
        findings.extend(
            ("fail", f"property {name!r}: {msg}")
            for sev, msg in _validate_subschema(subschema)
            if sev == "fail"
        )
        findings.extend(
            ("warn", f"property {name!r}: {msg}")
            for sev, msg in _validate_subschema(subschema)
            if sev == "warn"
        )
    return findings


def _validate_subschema(sub):
    out = []
    if isinstance(sub, bool):
        return out
    if not isinstance(sub, dict):
        return [("fail", f"schema must be object/boolean, got {type(sub).__name__}")]
    for t in _type_list(sub.get("type", "string")):
        if t not in JSON_TYPES:
            out.append(("fail", f"unknown type {t!r}"))
    if "enum" in sub and not isinstance(sub["enum"], list):
        out.append(("fail", "'enum' must be an array"))
    return out


def synth_args(schema, _depth=0):
    """Build minimal synthetic arguments from an inputSchema.

    Only *required* properties are filled, with type-appropriate dummy values.
    Returns {} when nothing is required. Never raises on weird schemas.
    """
    if not isinstance(schema, dict) or _depth > 3:
        return {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    if not isinstance(required, list):
        required = []
    args = {}
    for name in required:
        if not isinstance(name, str):
            continue
        args[name] = _dummy(properties.get(name, {}), _depth)
    return args


def _dummy(subschema, depth):
    if not isinstance(subschema, dict):
        return "test"
    types = _type_list(subschema.get("type", "string")) or ["string"]
    t = types[0]
    if "enum" in subschema and isinstance(subschema["enum"], list) and subschema["enum"]:
        return subschema["enum"][0]
    if "const" in subschema:
        return subschema["const"]
    if t == "string":
        return "test"
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "null":
        return None
    if t == "array":
        items = subschema.get("items")
        if isinstance(items, dict):
            return [_dummy(items, depth + 1)]
        return []
    if t == "object":
        return synth_args(subschema, depth + 1)
    return "test"
