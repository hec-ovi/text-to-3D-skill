"""Tiny JSON Schema checker for this layer's boundary.

Covers only the keywords used by image2mesh/schema/*.json: type, required,
properties, additionalProperties, const, enum, minimum, maximum, multipleOf,
minLength, pattern, default. Deliberately local to this layer, not shared with
the others, so nothing outside the blackbox imports it.
"""

import json
import re


class SchemaError(ValueError):
    """A payload did not satisfy the layer's schema."""


_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _fail(path, msg):
    raise SchemaError(f"{path or '<root>'}: {msg}")


def _check(value, schema, path=""):
    t = schema.get("type")
    if t:
        expected = _TYPES[t]
        if t == "integer" and isinstance(value, bool):
            _fail(path, "expected integer, got boolean")
        elif t == "boolean" and not isinstance(value, bool):
            _fail(path, f"expected boolean, got {type(value).__name__}")
        elif not isinstance(value, expected):
            _fail(path, f"expected {t}, got {type(value).__name__}")

    if "const" in schema and value != schema["const"]:
        _fail(path, f"must be {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        _fail(path, f"must be one of {schema['enum']}, got {value!r}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            _fail(path, f"shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            _fail(path, f"does not match {schema['pattern']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            _fail(path, f"below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            _fail(path, f"above maximum {schema['maximum']}")
        if "multipleOf" in schema and value % schema["multipleOf"]:
            _fail(path, f"not a multiple of {schema['multipleOf']}")

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                _fail(path, f"missing required property {key!r}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    _fail(path, f"unknown property {key!r}")
        for key, sub in props.items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}" if path else key)


def validate(payload, schema):
    """Raise SchemaError unless payload satisfies schema. Returns payload."""
    if not isinstance(payload, dict):
        _fail("", f"expected object, got {type(payload).__name__}")
    _check(payload, schema)
    return payload


def with_defaults(payload, schema):
    """A copy of payload with every top-level schema default filled in."""
    out = dict(payload)
    for key, sub in schema.get("properties", {}).items():
        if key not in out and "default" in sub:
            out[key] = sub["default"]
    return out


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
