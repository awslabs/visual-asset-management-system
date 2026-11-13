# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every compliance audit event type the code writes is the one list the documentation, the
OpenAPI schema and the web Audit Log filter offer.

The event types are read from the source: each `write_audit(...)` / `build_audit_item(...)` call
in the compliance handlers and common modules, its `event_type` keyword or third positional
argument, a string literal or a module-level constant. The web filter is checked against the user
guide's table by its own test (web/src/pages/ComplianceAuditLog.test.tsx), so a type added here
without a doc row fails here, and one added to the docs without a filter option fails there."""

import ast
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SOURCE_DIRS = (REPO_ROOT / "backend" / "backend" / "handlers" / "compliance",
               REPO_ROOT / "backend" / "backend" / "common" / "compliance")
API_DOC = REPO_ROOT / "documentation" / "docusaurus-site" / "docs" / "api" / "compliance.md"
USER_GUIDE = REPO_ROOT / "documentation" / "docusaurus-site" / "docs" / "user-guide" / "compliance.md"
OPENAPI = REPO_ROOT / "documentation" / "VAMS_API.yaml"
AUDIT_CALLS = {"write_audit", "build_audit_item"}


def _module_constants(tree):
    constants = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            constants[node.targets[0].id] = node.value.value
    return constants


def _value(node, constants):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute):
        return constants.get(node.attr)
    return None


def written_event_types():
    sources = [path for directory in SOURCE_DIRS for path in sorted(directory.glob("*.py"))]
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in sources}
    constants = {}
    for tree in trees.values():
        constants.update(_module_constants(tree))
    found, unresolved = set(), []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in AUDIT_CALLS:
                continue
            arg = next((kw.value for kw in node.keywords if kw.arg == "event_type"), None)
            if arg is None and len(node.args) >= 3:
                arg = node.args[2]
            if arg is None:
                continue
            value = _value(arg, constants)
            if value is None:
                # A pass-through (`event_type=event_type` inside a helper) names no type itself.
                if isinstance(arg, ast.Name) and arg.id == "event_type":
                    continue
                unresolved.append(f"{path.name}:{node.lineno}")
            else:
                found.add(value)
    return found, unresolved


def _table_event_types(path):
    text = path.read_text(encoding="utf-8")
    section = text[text.index("### Event types"):]
    section = section[:section.index("\n## ") if "\n## " in section else len(section)]
    return set(re.findall(r"^\|\s*`([a-z]+(?:_[a-z]+)+)`\s*\|", section, re.MULTILINE))


def _openapi_event_types():
    text = OPENAPI.read_text(encoding="utf-8")
    block = text[text.index("                eventType:\n                    type: string\n"
                            "                    enum:\n"):]
    values = []
    for line in block.splitlines()[3:]:
        stripped = line.strip()
        if not stripped.startswith("- "):
            break
        values.append(stripped[2:])
    return set(values)


@pytest.mark.unit
class TestOneListOfAuditEventTypes:

    def test_every_audit_call_names_a_resolvable_event_type(self):
        found, unresolved = written_event_types()
        assert unresolved == []
        # The control: the scan finds the types the handlers are known to write.
        assert {"compliance_check", "schema_deleted", "evaluation_error",
                "exception_superseded", "asset_deleted"} <= found

    def test_the_api_reference_lists_exactly_the_written_types(self):
        found, _ = written_event_types()
        assert _table_event_types(API_DOC) == found

    def test_the_user_guide_lists_exactly_the_written_types(self):
        found, _ = written_event_types()
        assert _table_event_types(USER_GUIDE) == found

    def test_the_openapi_enum_lists_exactly_the_written_types(self):
        found, _ = written_event_types()
        assert _openapi_event_types() == found
