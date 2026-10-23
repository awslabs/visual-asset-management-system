# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`build_audit_item` is the only way an audit row is assembled.

The unfiltered audit listing is one query on `AuditByDateGSI`, partitioned on the constant
`allListPartition` every audit row carries. A row written without it is not an error anywhere: the
put succeeds, the per-asset and per-event-type listings still show the row, and only the global
listing silently omits it. The builder sets the constant; this walk proves every `put_item` into the
audit table under `handlers/compliance/` puts an item the builder returned, and that nothing writes
the table through a `batch_writer`, which the builder cannot sit behind.

The walk reads the AST rather than grepping: a put whose `Item=` is a name is followed back to the
assignment that bound it in the same function, so `item = build_audit_item(...)` followed by
`audit_table.put_item(Item=item)` passes, while a name bound to a dict literal fails.
"""

import ast
import pathlib

import pytest

import common.compliance.auditRecord as audit_record

HANDLERS_ROOT = pathlib.Path(__file__).resolve().parents[3] / "backend" / "handlers" / "compliance"

BUILDER_NAME = "build_audit_item"


def _is_audit_table(node):
    """Whether a call target such as `audit_table.put_item` names the audit table."""
    return isinstance(node, ast.Name) and "audit" in node.id.lower()


def _builder_call(node):
    """Whether `node` is a call of the builder, imported bare or through its module."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == BUILDER_NAME
    return isinstance(func, ast.Attribute) and func.attr == BUILDER_NAME


def _bound_to_builder(name, scope):
    """Whether every assignment of `name` within `scope` binds a builder call."""
    bindings = [
        stmt.value for stmt in ast.walk(scope)
        if isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)
    ]
    return bool(bindings) and all(_builder_call(value) for value in bindings)


def _enclosing_scopes(tree):
    """Map every node to its nearest enclosing function, or the module when it is at top level."""
    scopes = {}

    def visit(node, scope):
        scopes[node] = scope
        inner = node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(tree, tree)
    return scopes


def _audit_writes(root=None):
    """Every write into the audit table under root, as (file:line, verdict) pairs, where the verdict
    is "builder" for a put of a builder-built item, "raw" for a put of anything else, and
    "batch_writer" for a batch writer opened on the table."""
    root = root or HANDLERS_ROOT
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scopes = _enclosing_scopes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not _is_audit_table(node.func.value):
                continue
            site = f"{path.relative_to(root).as_posix()}:{node.lineno}"
            if node.func.attr == "batch_writer":
                found.append((site, "batch_writer"))
            elif node.func.attr == "put_item":
                item = next((kw.value for kw in node.keywords if kw.arg == "Item"), None)
                if _builder_call(item) or (
                        isinstance(item, ast.Name) and _bound_to_builder(item.id, scopes[node])):
                    found.append((site, "builder"))
                else:
                    found.append((site, "raw"))
    return sorted(found)


@pytest.mark.unit
class TestBuildAuditItem:

    def test_the_row_is_keyed_for_every_index(self):
        item = audit_record.build_audit_item("db1", "asset.glb", "compliance_check", "user1")
        assert item["databaseId:assetId"] == "db1:asset.glb"
        assert item["eventType"] == "compliance_check"
        assert item["allListPartition"] == audit_record.AUDIT_LIST_PARTITION == "audit"
        assert isinstance(item["entryId"], str) and len(item["entryId"]) == 36
        assert item["timestamp"].endswith("+00:00")
        assert item["databaseId"] == "db1"
        assert item["assetId"] == "asset.glb"
        assert item["actor"] == "user1"
        assert item["details"] == "{}"

    def test_optional_fields_are_absent_unless_given(self):
        bare = audit_record.build_audit_item("db1", "a", "compliance_check", "user1")
        assert set(bare) == {"entryId", "databaseId:assetId", "timestamp", "eventType",
                             "allListPartition", "databaseId", "assetId", "actor", "details"}
        full = audit_record.build_audit_item(
            "db1", "a", "compliance_check", "user1", {"ruleNames": ["r"]},
            previous_state="quarantined", new_state="compliant", schema_name="s",
            evaluation_id="e", cascade_id="c", timestamp="2026-01-01T00:00:00+00:00")
        assert full["details"] == '{"ruleNames": ["r"]}'
        assert full["previousState"] == "quarantined"
        assert full["newState"] == "compliant"
        assert full["schemaName"] == "s"
        assert full["evaluationId"] == "e"
        assert full["cascadeId"] == "c"
        assert full["timestamp"] == "2026-01-01T00:00:00+00:00"

    def test_each_row_gets_its_own_entry_id(self):
        first = audit_record.build_audit_item("db1", "a", "compliance_check", "user1")
        second = audit_record.build_audit_item("db1", "a", "compliance_check", "user1")
        assert first["entryId"] != second["entryId"]


@pytest.mark.unit
class TestEveryAuditWriteUsesTheBuilder:

    def test_the_walk_finds_the_put_sites(self):
        """Non-vacuous: the evaluation store's `write_audit` and the schema service's
        `schema_deleted` put are the two writers. Fewer means the walk looked at the wrong tree."""
        sites = _audit_writes()
        assert len(sites) >= 2, f"expected the audit put sites, found {sites}"
        assert any(site.startswith("complianceEvaluationStore.py:") for site, _ in sites)
        assert any(site.startswith("complianceSchemaService.py:") for site, _ in sites)

    def test_the_walk_reports_a_put_that_bypasses_the_builder(self, tmp_path):
        """Positive control over a tree that DOES bypass the builder: a dict literal, a name bound to
        a dict, and a batch writer are each reported; a direct builder call and a name bound to one
        are not."""
        (tmp_path / "handler.py").write_text(
            "def ok_direct():\n"
            "    audit_table.put_item(Item=build_audit_item('db', 'a', 'x', 'u'))\n"
            "def ok_bound():\n"
            "    item = audit_record.build_audit_item('db', 'a', 'x', 'u')\n"
            "    audit_table.put_item(Item=item)\n"
            "def bad_literal():\n"
            "    audit_table.put_item(Item={'entryId': 'e'})\n"
            "def bad_bound():\n"
            "    item = {'entryId': 'e'}\n"
            "    audit_table.put_item(Item=item)\n"
            "def bad_batch():\n"
            "    with audit_table.batch_writer() as batch:\n"
            "        batch.put_item(Item={'entryId': 'e'})\n"
            "def other_table():\n"
            "    schema_table.put_item(Item={'schemaName': 's'})\n",
            encoding="utf-8")
        assert _audit_writes(tmp_path) == [
            ("handler.py:10", "raw"),
            ("handler.py:12", "batch_writer"),
            ("handler.py:2", "builder"),
            ("handler.py:5", "builder"),
            ("handler.py:7", "raw"),
        ]

    def test_no_audit_write_bypasses_the_builder(self):
        offenders = [(site, verdict) for site, verdict in _audit_writes() if verdict != "builder"]
        assert not offenders, (
            "every audit row must come from common.compliance.auditRecord.build_audit_item, which "
            f"sets allListPartition for AuditByDateGSI; these writes bypass it: {offenders}")
