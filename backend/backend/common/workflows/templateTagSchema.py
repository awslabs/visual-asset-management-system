# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared validation for pipeline template TAG SCHEMAS.

A template declares a tag schema: a list of tag definitions the caller fills in at execute time,
layered on top of the built-in system tags (common/workflows/templateTags.py) that the renderer
resolves itself. This module has NO AWS or environment dependency so it unit-tests in isolation.

Type set — the shared PRIMITIVE subset, deliberately NOT the specialized metadata types (xyz, matrix,
geo, …). It parallels models.metadata primitives (string / number / boolean) and adds the tag-only
distinctions the metadata form does not need: integer (distinct from number), string-list (a real
list), and enum (a closed set via enumValues). The metadata validator is string-in/string-out (form
values are always strings); template tags carry typed values, so this is an intentionally parallel
implementation sharing the same "empty allowed unless required" philosophy rather than a literal
reuse of validate_metadata_value_common.

Two entry points:
  - validate_tag_schema(fields): structural validation of a DECLARED schema (keys unique, types
    known, no reserved-key collisions, enum has values, a type with no blank form is required or
    carries a default). Used at template create/update + CDK ingest.
  - validate_tags(tag_schema, provided_tags): validate CALLER-supplied values against a schema —
    required present, types coerce, reserved keys rejected, defaults filled. Returns
    (errors, filled_tags) where filled_tags is the {key: value} map the renderer consumes.
"""

import math
from decimal import Decimal, InvalidOperation
import re

from common.workflows.templateTags import is_reserved_tag_key

# A tag key must be substitutable: the resolver and renderer only capture {{ tagName }} names made of
# these characters, so a key outside the set can be declared but never rendered.
_TAG_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")

# The primitive tag types (mirror models.pipelines.TemplateTagType values).
TAG_TYPE_STRING = "string"
TAG_TYPE_INTEGER = "integer"
TAG_TYPE_NUMBER = "number"
TAG_TYPE_BOOLEAN = "boolean"
TAG_TYPE_STRING_LIST = "string-list"
TAG_TYPE_ENUM = "enum"

TAG_TYPES = frozenset(
    {
        TAG_TYPE_STRING,
        TAG_TYPE_INTEGER,
        TAG_TYPE_NUMBER,
        TAG_TYPE_BOOLEAN,
        TAG_TYPE_STRING_LIST,
        TAG_TYPE_ENUM,
    }
)

# The empty value each type materializes to for a blank OPTIONAL tag with no declared default, so a
# {{tag}} referencing it renders empty rather than failing resolution as unmatched. integer, number
# and boolean have no representable empty value and are therefore absent from this map.
_TYPE_EMPTY_VALUES = {
    TAG_TYPE_STRING: "",
    TAG_TYPE_ENUM: "",
    TAG_TYPE_STRING_LIST: [],
}

# The complement of _TYPE_EMPTY_VALUES: the types a blank tag cannot materialize as. Such a tag is
# supplyable only by a value — the caller's or a declared default — so a schema declaring one
# optional-and-defaultless is rejected at declaration (validate_tag_schema) rather than rendering an
# unmatched {{tag}} on every execution, which is a failure with no name attached to it.
TYPES_WITHOUT_EMPTY_VALUE = frozenset(TAG_TYPES - set(_TYPE_EMPTY_VALUES))


def normalize_tag_type(raw) -> str:
    """Lower-case a declared type (accepts a plain string or an Enum whose value is the type)."""
    if raw is None:
        return TAG_TYPE_STRING
    # Enum (e.g. models.pipelines.TemplateTagType) -> its string value, not "TemplateTagType.STRING".
    value = getattr(raw, "value", raw)
    return str(value).strip().lower()


def validate_tag_schema(fields):
    """Validate a DECLARED tag schema (a list of tag-definition dicts). Returns an errors list
    (empty = valid). Checks: each has a non-empty tagKey; the key is substitutable ([A-Za-z0-9_]+,
    the {{tag}} name charset); keys unique; type is a known primitive; no key collides with a
    reserved system tag name / dynamic-metadata prefix; enum declares a non-empty enumValues; a
    declared default is itself valid for the type."""
    errors = []
    if fields is None:
        return errors
    if not isinstance(fields, list):
        return ["tag schema must be a list of tag definitions"]

    seen_keys = set()
    for index, field in enumerate(fields):
        where = f"tag[{index}]"
        if not isinstance(field, dict):
            errors.append(f"{where}: each tag definition must be an object")
            continue

        key = field.get("tagKey")
        if not key or not str(key).strip():
            errors.append(f"{where}: tagKey is required")
            continue
        where = f"tag '{key}'"

        if not _TAG_KEY_PATTERN.match(str(key)):
            errors.append(
                f"{where}: tagKey must contain only letters, digits and underscores so a "
                "{{tagKey}} placeholder can be substituted"
            )
            continue

        if key in seen_keys:
            errors.append(f"{where}: duplicate tagKey")
        seen_keys.add(key)

        if is_reserved_tag_key(key):
            errors.append(
                f"{where}: tagKey collides with a reserved system tag name or the reserved "
                "'metadata_' prefix; choose a different key"
            )

        tag_type = normalize_tag_type(field.get("type"))
        if tag_type not in TAG_TYPES:
            errors.append(
                f"{where}: unknown type '{tag_type}' (allowed: {', '.join(sorted(TAG_TYPES))})"
            )
            continue

        if tag_type == TAG_TYPE_ENUM:
            enum_values = field.get("enumValues")
            if not enum_values or not isinstance(enum_values, list):
                errors.append(f"{where}: enum type requires a non-empty enumValues list")

        if tag_type in TYPES_WITHOUT_EMPTY_VALUE and not bool(field.get("required")) \
                and field.get("default") is None:
            errors.append(
                f"{where}: type '{tag_type}' has no blank form, so the tag must either be required "
                "or declare a default value"
            )

        # A declared default must itself be valid for the type.
        if field.get("default") is not None:
            default_error = _check_value(tag_type, field.get("default"), field.get("enumValues"))
            if default_error:
                errors.append(f"{where}: default value invalid — {default_error}")

    return errors


def _coerce_and_check(tag_type, value, enum_values):
    """Return (coerced_value, error). Coerces strings to int/number/bool where unambiguous so a tag
    supplied as a form string still satisfies a typed schema; returns an error message on mismatch."""
    if tag_type == TAG_TYPE_STRING:
        if isinstance(value, (dict, list)):
            return None, "expected a string"
        return ("" if value is None else str(value)), None

    if tag_type == TAG_TYPE_INTEGER:
        if isinstance(value, bool):
            return None, "expected an integer"
        if isinstance(value, int):
            return value, None
        if isinstance(value, (float, Decimal)):
            # A JSON number without a fractional part decodes to float, and DynamoDB returns
            # Decimal; accept either when integral.
            try:
                if math.isfinite(float(value)) and float(value).is_integer():
                    return int(value), None
            except (ValueError, TypeError, InvalidOperation):
                pass
            return None, "expected an integer"
        try:
            return int(str(value).strip()), None
        except (ValueError, TypeError):
            return None, "expected an integer"

    if tag_type == TAG_TYPE_NUMBER:
        if isinstance(value, bool):
            return None, "expected a number"
        if isinstance(value, int):
            return value, None
        if isinstance(value, float):
            return (value, None) if math.isfinite(value) else (None, "expected a finite number")
        try:
            parsed = float(str(value).strip())
        except (ValueError, TypeError):
            return None, "expected a number"
        # Reject nan/inf/-inf: json.dumps would emit invalid strict JSON (NaN/Infinity) when the
        # renderer serializes the filled tag map.
        if not math.isfinite(parsed):
            return None, "expected a finite number"
        return parsed, None

    if tag_type == TAG_TYPE_BOOLEAN:
        if isinstance(value, bool):
            return value, None
        text = str(value).strip().lower()
        if text in ("true", "1", "yes"):
            return True, None
        if text in ("false", "0", "no"):
            return False, None
        return None, "expected a boolean"

    if tag_type == TAG_TYPE_STRING_LIST:
        if not isinstance(value, list):
            return None, "expected a list of strings"
        if any(isinstance(item, (dict, list)) for item in value):
            return None, "expected a list of strings"
        return [("" if item is None else str(item)) for item in value], None

    if tag_type == TAG_TYPE_ENUM:
        text = "" if value is None else str(value)
        if not enum_values or text not in [str(v) for v in enum_values]:
            return None, f"must be one of {enum_values}"
        return text, None

    return None, f"unknown type '{tag_type}'"


def _check_value(tag_type, value, enum_values):
    """Return an error message if value is invalid for the type, else None."""
    _, error = _coerce_and_check(tag_type, value, enum_values)
    return error


def _is_absent(value):
    """A provided tag value counts as 'absent' (so a required tag still errors) when it is None, an
    empty string, or an empty list — treating an empty collection the same as a blank string."""
    return value is None or value == "" or value == []


def _provided_map(provided_tags):
    """Accept provided tags as either a {key: value} dict or a [{key, value}] list; return a dict."""
    if provided_tags is None:
        return {}
    if isinstance(provided_tags, dict):
        return dict(provided_tags)
    result = {}
    for entry in provided_tags:
        if isinstance(entry, dict) and "key" in entry:
            result[entry["key"]] = entry.get("value")
    return result


def validate_tags(tag_schema, provided_tags):
    """Validate caller-supplied tag values against a declared schema.

    Returns (errors, filled_tags):
      - errors: list of human-readable strings (empty = valid).
      - filled_tags: {tagKey: coerced_value} with declared defaults applied for absent optional
        tags; the {{tag}} renderer consumes this map.

    Rules:
      - A provided key that is a reserved system tag name is rejected (the engine owns those; the
        caller may not supply them).
      - Every required tag must be present (or have a default); missing required is an error.
      - Each provided value must match its declared type (coerced where unambiguous).
      - An OPTIONAL tag left blank (absent or empty) with no declared default still materializes as
        its type's empty value — "" for string/enum, [] for string-list — so a {{tag}} referencing it
        renders empty instead of failing resolution as an unmatched tag. integer / number / boolean
        have no representable empty value and stay absent; validate_tag_schema is what keeps such a
        tag from being declared optional-and-defaultless, so a body may always reference one.
      - EXTRA provided tags (no matching schema entry) are IGNORED, not an error. The only
        render-time tag error is an unmatched {{tag}} in the body, enforced by the renderer.
    """
    errors = []
    filled = {}
    schema = tag_schema or []
    provided = _provided_map(provided_tags)

    # Reserved-key rejection: a caller may not supply a value for a system-owned tag.
    for key in provided:
        if is_reserved_tag_key(key):
            errors.append(
                f"tag '{key}' is a reserved system tag and cannot be supplied by the caller"
            )

    schema_keys = set()
    for field in schema:
        if not isinstance(field, dict):
            continue
        key = field.get("tagKey")
        if not key:
            continue
        schema_keys.add(key)
        tag_type = normalize_tag_type(field.get("type"))
        enum_values = field.get("enumValues")
        required = bool(field.get("required"))

        if key in provided and not _is_absent(provided[key]):
            coerced, error = _coerce_and_check(tag_type, provided[key], enum_values)
            if error:
                errors.append(f"tag '{key}': {error}")
            else:
                filled[key] = coerced
        elif field.get("default") is not None:
            # Fill the declared default (already validated at schema-declaration time).
            coerced, error = _coerce_and_check(tag_type, field.get("default"), enum_values)
            filled[key] = coerced if not error else field.get("default")
        elif required:
            errors.append(f"tag '{key}' is required")
        elif tag_type in _TYPE_EMPTY_VALUES:
            # Blank optional tag, no default: materialize the type's empty value so a body
            # referencing it renders empty instead of erroring as an unmatched tag.
            empty = _TYPE_EMPTY_VALUES[tag_type]
            filled[key] = list(empty) if isinstance(empty, list) else empty

    # EXTRA provided tags (not in schema, not reserved) are ignored — no error.
    return errors, filled


def required_tags_without_default(tag_schema):
    """Return the tagKeys in a schema that no headless run could supply a value for.

    A headless run (an auto-triggered workflow) has no person to supply tag values, so a template
    with such a tag can never render for a trigger — every triggered execution would fail, at
    validate_tags for a required tag and at the renderer's unmatched-tag check for a tag with no
    blank form. Callers use this to reject saving a trigger (or a trigger-referenced template) that
    would be dead-on-arrival. A default of None (or an absent default) counts as 'no default'; any
    other value (including False/0/"") is a usable default.

    Two shapes qualify: a required tag with no default, and a tag of a type with no blank form
    (TYPES_WITHOUT_EMPTY_VALUE) with no default, which is equally unsupplyable whether or not it is
    marked required."""
    missing = []
    for field in tag_schema or []:
        if not isinstance(field, dict):
            continue
        key = field.get("tagKey")
        if not key:
            continue
        if field.get("default") is not None:
            continue
        if bool(field.get("required")) \
                or normalize_tag_type(field.get("type")) in TYPES_WITHOUT_EMPTY_VALUE:
            missing.append(key)
    return missing
