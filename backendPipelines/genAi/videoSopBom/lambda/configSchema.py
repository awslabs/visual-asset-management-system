#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Validates the rendered template configuration against config_schema.json.

The Lambda runtime carries no jsonschema package (the shared base layer is boto3, powertools and
pydantic v1, and the code asset is copied without a pip step), so this module interprets the subset
of JSON Schema the pipeline's schema uses: the object and property keywords, and one top-level
if/then conditional whose `if` node holds `properties` (with `const`) and `required`. The schema
file is a byte-identical copy of the container's, so both validators read one document. Every
message is one operator-readable sentence naming the offending value and the bound it broke; the
caller joins them into a task-token cause."""

import json
import os

SCHEMA_FILENAME = "config_schema.json"

# Keywords this validator interprets. Annotation keywords are accepted and ignored. A schema
# carrying any other keyword (an `else`, a pattern, a union type) is reported by
# unsupported_keywords, which the tests hold at [] so a schema change cannot leave a rule enforced
# only in the container.
SUPPORTED_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "enum", "const",
    "minimum", "maximum", "minLength", "maxLength", "if", "then",
})
ANNOTATION_KEYWORDS = frozenset({"$schema", "$id", "title", "description", "default", "examples"})

# Keywords whose value is a sub-schema the walker descends into.
_NODE_KEYWORDS = ("if", "then")

# The `type` names _type_ok tests; any other value (a union list, an unknown name) is reported.
_TYPES = ("string", "integer", "number", "boolean", "object", "array")

_ARTICLE = {
    "string": "a string", "integer": "an integer", "number": "a number",
    "boolean": "a boolean", "object": "an object", "array": "an array",
}


def load_config_schema(schema_path=None):
    """The parsed schema, from the file beside this module unless a path is given."""
    path = schema_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), SCHEMA_FILENAME)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def unsupported_keywords(schema):
    """Every keyword in the schema, at any depth, outside the supported and annotation sets, plus
    any `type` whose value is not one of the six names this validator tests (as `type:<repr>`)."""
    found = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key not in SUPPORTED_KEYWORDS and key not in ANNOTATION_KEYWORDS:
                found.add(key)
            elif key == "type" and value not in _TYPES:
                found.add(f"type:{value!r}")
            elif key == "properties" and isinstance(value, dict):
                for sub_schema in value.values():
                    walk(sub_schema)
            elif key in _NODE_KEYWORDS:
                walk(value)

    walk(schema)
    return sorted(found)


def _describe(value):
    return json.dumps(value)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_ok(expected, value):
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return _is_number(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True


def _kind(value):
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, list):
        return "a list"
    return f"a {type(value).__name__}"


def _condition(if_schema):
    """The `if` node as a clause: `mode is "full"` for {"properties": {"mode": {"const": "full"}}}."""
    clauses = [f"{key} is {_describe(rule['const'])}"
               for key, rule in (if_schema.get("properties") or {}).items()
               if isinstance(rule, dict) and "const" in rule]
    return " and ".join(clauses) or "the condition holds"


def _node_errors(config, node, condition=None):
    """Sentences for every way the object `config` violates the object keywords of `node`;
    `condition` names the `if` clause a `then` node's `required` keys depend on."""
    errors = []
    properties = node.get("properties") or {}
    for key in node.get("required") or []:
        if key in config:
            continue
        if condition:
            errors.append(
                f"the rendered configuration is missing {_describe(key)}, which is required when "
                f"{condition}; the template body must reference every typed tag.")
        else:
            errors.append(f"{key} is required but the rendered configuration does not set it.")
    if node.get("additionalProperties") is False:
        for key in sorted(set(config) - set(properties)):
            errors.append(
                f"unknown configuration key {_describe(key)}; allowed keys are "
                f"{', '.join(sorted(properties))}.")
    for key, rule in properties.items():
        if key not in config:
            continue
        value = config[key]
        expected = rule.get("type")
        if expected and not _type_ok(expected, value):
            errors.append(f"{key} is {_describe(value)}; expected {_ARTICLE.get(expected, expected)}.")
            continue
        if "enum" in rule and value not in rule["enum"]:
            errors.append(
                f"{key} is {_describe(value)}; allowed values are "
                f"{', '.join(_describe(member) for member in rule['enum'])}.")
        if "const" in rule and value != rule["const"]:
            errors.append(
                f"{key} is {_describe(value)}; the only allowed value is {_describe(rule['const'])}.")
        if _is_number(value):
            if "minimum" in rule and value < rule["minimum"]:
                errors.append(f"{key} is {value}; this pipeline allows at least {rule['minimum']}.")
            if "maximum" in rule and value > rule["maximum"]:
                errors.append(f"{key} is {value}; this pipeline allows at most {rule['maximum']}.")
        if isinstance(value, (str, list)):
            if "minLength" in rule and len(value) < rule["minLength"]:
                errors.append(
                    f"{key} is {len(value)} characters; this pipeline needs at least "
                    f"{rule['minLength']}.")
            if "maxLength" in rule and len(value) > rule["maxLength"]:
                errors.append(
                    f"{key} is {len(value)} characters; this pipeline allows at most "
                    f"{rule['maxLength']}.")
    return errors


def validate_config(config, schema):
    """Sentences describing every way `config` violates `schema`; empty when it conforms. The
    top-level `then` node applies when the `if` node holds under the same object rules."""
    if not isinstance(config, dict):
        return [f"the rendered configuration is {_kind(config)}, not a JSON object."]
    errors = _node_errors(config, schema)
    if "if" in schema and not _node_errors(config, schema["if"]):
        errors.extend(_node_errors(config, schema.get("then") or {}, condition=_condition(schema["if"])))
    return errors
