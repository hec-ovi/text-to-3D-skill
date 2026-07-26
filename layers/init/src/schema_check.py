"""Small JSON Schema checker local to the init boundary."""

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


def _fail(path, message):
    raise SchemaError(f"{path or '<root>'}: {message}")


def _resolve(schema, root):
    ref = schema.get("$ref")
    if not ref:
        return schema
    if not ref.startswith("#/"):
        _fail("", f"unsupported reference {ref}")
    value = root
    for part in ref[2:].split("/"):
        value = value[part]
    return value


def _check(value, schema, path="", root=None):
    root = root or schema
    schema = _resolve(schema, root)
    expected_type = schema.get("type")
    if expected_type:
        expected = _TYPES[expected_type]
        if expected_type == "integer" and isinstance(value, bool):
            _fail(path, "expected integer, got boolean")
        elif expected_type == "boolean" and not isinstance(value, bool):
            _fail(path, f"expected boolean, got {type(value).__name__}")
        elif not isinstance(value, expected):
            _fail(path, f"expected {expected_type}, got {type(value).__name__}")

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

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                _fail(path, f"missing required property {key!r}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    _fail(path, f"unknown property {key!r}")
        for key, child in properties.items():
            if key in value:
                _check(value[key], child, f"{path}.{key}" if path else key, root)

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            _fail(path, f"needs at least {schema['minItems']} items")
        for index, item in enumerate(value):
            if "items" in schema:
                _check(item, schema["items"], f"{path}[{index}]", root)


def validate(payload, schema):
    """Raise SchemaError unless payload satisfies schema."""
    if not isinstance(payload, dict):
        _fail("", f"expected object, got {type(payload).__name__}")
    _check(payload, schema, root=schema)
    return payload


def with_defaults(payload, schema):
    """Return a copy with top-level static defaults filled in."""
    result = dict(payload)
    for key, child in schema.get("properties", {}).items():
        if key not in result and "default" in child:
            result[key] = child["default"]
    return result


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
