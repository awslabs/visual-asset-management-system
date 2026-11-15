# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A schema whose `extends` names a parent the evaluation would not find saves with a warning (D150).

A schema extending a missing schema used to save silently and then evaluate with its own rules only
(`evaluationEngine.resolve_rules`). Create and update now answer an `extendsSchemaNotFound` warning
in `warnings` when the parent is missing or not vams-rules-v1, as the evaluation would find it (the
parent's latest version), and the save still succeeds. The warning is decided only from what the
caller may read through the API: a parent that exists but the caller may not read (Tier 1 GET on
`/compliance/schemas/{schemaName}`, Tier 2 GET on the schema in its scope) gets the same warning
and the same message as a missing one, so the answer confirms nothing about it. The save reads the
parent once, as `GET /compliance/schemas/{schemaName}` would, and nothing else for the warning.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    RULES_SCHEMA_BODY, SCHEMA, USER, body_of, claims_for, put_items, rest_event, schema_row,
)
from handlers.compliance import complianceSchemaService as svc

MOD = "handlers.compliance.complianceSchemaService"
PARENT = "parent-schema"
LEGACY_BODY = {"type": "object", "properties": {"partNumber": {"type": "string"}}}


def _child(extends=PARENT):
    body = {"schemaFormat": "vams-rules-v1",
            "rules": {name: rule for name, rule in RULES_SCHEMA_BODY["rules"].items()
                      if rule["ruleType"] != "pipeline"}}
    if extends is not None:
        body["extends"] = extends
    return body


def _enforcer(denied_schemas=(), deny_route=False):
    """Every check passes except a GET on a schema named in `denied_schemas` (Tier 2) or, with
    `deny_route`, the GET route of a schema (Tier 1)."""
    instance = MagicMock(name="CasbinEnforcer")

    def enforce(obj, action):
        if obj.get("object__type") == "api":
            return not (deny_route and action == "GET"
                        and obj.get("route__path", "").startswith("/compliance/schemas/"))
        return not (action == "GET" and obj.get("complianceSchemaName") in denied_schemas)
    instance.enforce.side_effect = enforce
    return instance


def _run(event, rows_by_name=None, enforcer=None):
    """The schema service over mock tables whose version query answers `rows_by_name[name]`."""
    rows_by_name = rows_by_name or {}
    schema_table = MagicMock(name="schema_table")
    queried = []

    def query(**kwargs):
        name = kwargs["KeyConditionExpression"]._values[1]
        queried.append(name)
        return {"Items": list(rows_by_name.get(name, []))}
    schema_table.query.side_effect = query
    others = {name: MagicMock(name=name) for name in ("asset_state_table", "database_table",
                                                       "audit_table", "evaluation_table",
                                                       "workflow_table", "pipeline_table")}
    others["database_table"].get_item.return_value = {"Item": {"databaseId": "db1"}}
    with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer or _enforcer()), \
            patch(f"{MOD}.may_run_workflow", return_value=True), \
            patch(f"{MOD}.schema_table", schema_table), \
            patch.object(svc.databaseRetirement, "move_in_progress", return_value=False), \
            patch.multiple(MOD, **others):
        response = svc.lambda_handler(event, MagicMock())
    return response, schema_table, queried, others


def _create(body, **kwargs):
    return _run(rest_event("POST", "/compliance/schemas",
                           body={"schemaName": SCHEMA, "schemaBody": body}), **kwargs)


def _expected(name=PARENT):
    return [{"code": "extendsSchemaNotFound",
             "message": svc.MISSING_PARENT_WARNING_MESSAGE.format(name=name), "extends": name}]


@pytest.mark.unit
class TestTheMissingParentWarning:

    def test_a_missing_parent_saves_and_warns(self):
        response, schema_table, queried, _ = _create(_child())
        assert response["statusCode"] == 200, response
        assert json.loads(put_items(schema_table)[0]["schemaBody"])["extends"] == PARENT
        assert body_of(response)["warnings"] == _expected()
        assert "evaluated with its own rules only" in body_of(response)["warnings"][0]["message"]

    def test_a_parent_of_another_format_warns(self):
        response, _, _, _ = _create(_child(), rows_by_name={
            PARENT: [schema_row(PARENT, body=LEGACY_BODY)]})
        assert response["statusCode"] == 200
        assert body_of(response)["warnings"] == _expected()

    @pytest.mark.parametrize("enforcer", [
        _enforcer(denied_schemas={PARENT}), _enforcer(deny_route=True)],
        ids=["schema-denied", "route-denied"])
    def test_a_parent_the_caller_may_not_read_gets_the_missing_parents_warning(self, enforcer):
        rows = {PARENT: [schema_row(PARENT, body=_child(extends=None), database_id="db2")]}
        hidden, _, _, _ = _create(_child(), rows_by_name=rows, enforcer=enforcer)
        missing, _, _, _ = _create(_child())
        assert hidden["statusCode"] == 200
        # Word for word what a missing parent gets: the answer says nothing of the hidden schema.
        assert body_of(hidden)["warnings"] == body_of(missing)["warnings"] == _expected()
        assert "db2" not in json.dumps(body_of(hidden))

    def test_a_readable_vams_rules_parent_gets_none(self):
        response, _, _, _ = _create(_child(), rows_by_name={
            PARENT: [schema_row(PARENT, body=_child(extends=None), database_id="db1")]})
        assert response["statusCode"] == 200
        assert body_of(response)["warnings"] == []

    def test_no_extends_gets_none_and_reads_no_parent(self):
        response, _, queried, _ = _create(_child(extends=None))
        assert response["statusCode"] == 200
        assert body_of(response)["warnings"] == []
        assert PARENT not in queried

    def test_an_update_warns_too(self):
        response, schema_table, _, _ = _run(
            rest_event("PUT", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA},
                       body={"schemaBody": _child()}),
            rows_by_name={SCHEMA: [schema_row(SCHEMA, body=_child(extends=None))]})
        assert response["statusCode"] == 200, response
        assert put_items(schema_table)
        assert body_of(response)["warnings"] == _expected()

    def test_the_parent_is_read_once_and_nothing_else_for_the_warning(self):
        """The warning reads the parent's latest version once (the query `GET
        /compliance/schemas/{schemaName}` makes) and no other table."""
        rows = {PARENT: [schema_row(PARENT, body=_child(extends=None), database_id="db2")]}
        response, _, queried, others = _create(_child(), rows_by_name=rows,
                                               enforcer=_enforcer(denied_schemas={PARENT}))
        assert response["statusCode"] == 200
        assert queried.count(PARENT) == 1
        for name in ("asset_state_table", "evaluation_table", "workflow_table", "pipeline_table"):
            for method in ("get_item", "query", "scan", "batch_get_item"):
                getattr(others[name], method).assert_not_called()

    def test_both_warnings_together(self):
        body = _child()
        pipeline = dict(RULES_SCHEMA_BODY["rules"]["residual-bound"])
        body["rules"].update({"a": pipeline, "b": dict(pipeline)})
        response, _, _, _ = _create(body)
        assert [w["code"] for w in body_of(response)["warnings"]] == [
            svc.SHARED_WORKFLOW_WARNING_CODE, "extendsSchemaNotFound"]
