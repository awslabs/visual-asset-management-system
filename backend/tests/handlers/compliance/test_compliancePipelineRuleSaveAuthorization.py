# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Saving a pipeline rule requires the right to run its workflow.

The schema service runs against moto tables built from the CDK definitions. On create and update,
each pipeline rule whose `pipelineRef` is new or changed is decided as the execute route decides a
run at workflow level: Tier 1 POST on the workflow's execute route, Tier 2 GET on the workflow, on
every pipeline it references and on the rule's own pipeline. A refused save answers 403 naming the
rule only, the same for a workflow that does not exist, and the refusal is recorded. An unchanged
rule is not re-checked, and the system user is not checked. Covered with stand-in enforcers and with
the shipped templates: Compliance Admin and Global Compliance Admin alone (they carry the run rights
of their scopes), the Compliance Admin template as it was before those rights (refused), and what
the new constraints allow and do not allow.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    RULES_SCHEMA_BODY, USER, body_of, claims_for, real_constants, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceTemplateRolesEndToEnd import _real
from backend.tests.handlers.auth import test_permission_templates_compliance_binding as template_tests
from backend.tests.handlers.auth.test_permission_templates_compliance_binding import (  # noqa: F401
    USER_ID, roles_enforcer,
)
from handlers.compliance import complianceSchemaService as svc

DB, OTHER = "rule-db", "other-db"
SCHEMA = "pipeline-schema"
# The constraints the run rights added to the two templates (2.1 / 1.1).
RUN_RIGHT_SUFFIXES = ("-workflow-execute-route", "-workflows-run-scoped", "-workflows-run-global",
                      "-pipelines-run-scoped", "-pipelines-run-global")


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


def _rule(workflow_db=DB, workflow_id="wf1", pipeline_db=DB, pipeline_id="pipe-1", **extra):
    return dict(RULES_SCHEMA_BODY["rules"]["residual-bound"],
                pipelineRef={"databaseId": workflow_db, "workflowId": workflow_id,
                             "pipelineDatabaseId": pipeline_db, "pipelineId": pipeline_id},
                **extra)


def _body(**rules):
    kept = {name: rule for name, rule in RULES_SCHEMA_BODY["rules"].items()
            if rule["ruleType"] != "pipeline"}
    kept.update(rules)
    return {"schemaFormat": "vams-rules-v1", "rules": kept}


class StandIn:
    """A Casbin stand-in: Tier 1 passes (the service's own routes); each `enforce` passes unless
    its (object type, action) or execute route is refused."""

    def __init__(self, refuse=()):
        self.refuse = set(refuse)
        self.instance = MagicMock(name="CasbinEnforcer")
        self.instance.enforceAPI.return_value = True
        self.instance.enforce.side_effect = self.enforce
        self.checked = []

    def enforce(self, obj, action):
        kind = obj.get("object__type")
        self.checked.append((kind, obj.get("route__path") or obj.get("databaseId"), action))
        if kind == "api":
            return "execute" not in self.refuse
        return (kind, action) not in self.refuse


class World:

    def __init__(self, enforcer_factory, tokens=(USER,)):
        self.factory = enforcer_factory
        self.tokens = tokens

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, self.t = _cdkTables.table_doubles(resource, (svc,))
        self.audit = MagicMock(name="log_authorization")
        self.patches += [patch.object(svc, "CasbinEnforcer", side_effect=self.factory),
                         patch.object(svc, "request_to_claims", claims_for(*self.tokens)),
                         patch.object(svc, "log_authorization", self.audit, create=True)]
        for active in self.patches:
            active.start()
        for database_id in (DB, OTHER):
            self.t["databaseStorage"].put_item(Item={"databaseId": database_id})
        for database_id in (DB, OTHER, "GLOBAL"):
            self.t["workflowStorageV2"].put_item(Item={
                "databaseId": database_id, "workflowId": "wf1", "workflowName": "Convert",
                "specifiedPipelines": [{"pipelineId": "pipe-1", "pipelineDatabaseId": database_id}]})
            self.t["pipelineStorageV2"].put_item(Item={
                "databaseId": database_id, "pipelineId": "pipe-1", "pipelineName": "Convert"})
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()

    def create(self, body, scope=DB, name=SCHEMA):
        return self._call("POST", "/compliance/schemas", None,
                          {"schemaName": name, "databaseId": scope, "schemaBody": body})

    def update(self, body, name=SCHEMA):
        return self._call("PUT", f"/compliance/schemas/{name}", {"schemaName": name},
                          {"schemaBody": body})

    def _call(self, method, path, params, body):
        response = svc.lambda_handler(rest_event(method, path, params, body=body), MagicMock())
        return response["statusCode"], body_of(response)

    def versions(self, name=SCHEMA):
        return len([r for r in self.t["complianceSchemaStorage"].scan()["Items"]
                    if r["schemaName"] == name])


def _stand_in(refuse=()):
    stand_in = StandIn(refuse)
    return stand_in, (lambda _claims: stand_in.instance)


REFUSED = {"message": "Not authorized to run the workflow of pipeline rule 'convert'",
           "ruleName": "convert"}


@pytest.mark.unit
class TestTheCheckOnCreate:

    def test_a_caller_who_may_run_the_workflow_saves_it(self):
        stand_in, factory = _stand_in()
        with World(factory) as world:
            status, _ = world.create(_body(convert=_rule()))
            assert status == 200
            assert ("api", f"/workflows/{DB}/wf1/execute", "POST") in stand_in.checked
            assert ("workflow", DB, "GET") in stand_in.checked
            assert ("pipeline", DB, "GET") in stand_in.checked

    @pytest.mark.parametrize("refuse", [("execute",), (("workflow", "GET"),), (("pipeline", "GET"),)],
                             ids=["no execute route", "no workflow GET", "no pipeline GET"])
    def test_a_caller_who_may_not_is_refused_naming_the_rule(self, refuse):
        _, factory = _stand_in(refuse)
        with World(factory) as world:
            status, body = world.create(_body(convert=_rule()))
            assert (status, body) == (403, REFUSED)
            assert world.versions() == 0

    def test_the_rules_own_pipeline_is_checked_too(self):
        stand_in, factory = _stand_in()
        with World(factory) as world:
            world.t["pipelineStorageV2"].put_item(Item={"databaseId": DB, "pipelineId": "pipe-2"})
            status, _ = world.create(_body(convert=_rule(pipeline_id="pipe-2")))
            assert status == 200
            pipelines = [entry for entry in stand_in.checked if entry[0] == "pipeline"]
            assert len(pipelines) == 2

    @pytest.mark.parametrize("missing", ["workflow", "workflow pipeline", "rule pipeline"])
    def test_a_missing_workflow_or_pipeline_answers_as_a_refused_one(self, missing):
        _, factory = _stand_in()
        with World(factory) as world:
            if missing == "workflow":
                ref = _rule(workflow_id="gone")
            elif missing == "workflow pipeline":
                world.t["pipelineStorageV2"].delete_item(Key={"databaseId": DB, "pipelineId": "pipe-1"})
                ref = _rule()
            else:
                ref = _rule(pipeline_id="gone")
            status, body = world.create(_body(convert=ref))
            assert (status, body) == (403, REFUSED)
            # The refusal is recorded as a refused check is.
            world.audit.assert_called_once()
            assert world.audit.call_args.args[1] is False

    def test_the_system_user_is_not_checked(self):
        _, factory = _stand_in(("execute",))
        with World(factory, tokens=("SYSTEM_USER",)) as world:
            status, _ = world.create(_body(convert=_rule()))
            assert status == 200

    def test_one_decision_per_new_pipeline_rule(self):
        stand_in, factory = _stand_in()
        with World(factory) as world:
            status, _ = world.create(_body(convert=_rule(), again=_rule()))
            assert status == 200
            assert len([c for c in stand_in.checked if c[0] == "api"]) == 2


@pytest.mark.unit
class TestTheCheckOnUpdate:

    def _stored(self, world):
        world.t["complianceSchemaStorage"].put_item(Item=dict(
            schema_row(SCHEMA, database_id=DB), schemaBody=json.dumps(_body(convert=_rule()))))

    def test_an_unchanged_pipeline_rule_is_not_rechecked(self):
        stand_in, factory = _stand_in(("execute",))
        with World(factory) as world:
            self._stored(world)
            body = _body(convert=_rule())
            body["rules"]["owner-present"]["enforcement"] = "quarantine"
            status, _ = world.update(body)
            assert status == 200
            assert not [c for c in stand_in.checked if c[0] == "api"]

    def test_a_changed_pipeline_ref_is_checked(self):
        _, factory = _stand_in(("execute",))
        with World(factory) as world:
            self._stored(world)
            status, body = world.update(_body(convert=_rule(pipeline_id="pipe-1", workflow_id="wf1",
                                                            workflow_db="GLOBAL",
                                                            pipeline_db="GLOBAL")))
            assert (status, body) == (403, REFUSED)
            assert world.versions() == 1

    def test_a_new_version_through_create_compares_with_the_stored_one(self):
        stand_in, factory = _stand_in(("execute",))
        with World(factory) as world:
            self._stored(world)
            status, _ = world.create(_body(convert=_rule()))
            assert status == 200
            assert world.versions() == 2


def _without_run_rights(template_name):
    """The template as it was before the run rights were added (Compliance Admin 2.0)."""
    template = json.loads((template_tests.TEMPLATES_DIR / template_name).read_text())
    template["constraints"] = [c for c in template["constraints"]
                               if not c["name"].endswith(RUN_RIGHT_SUFFIXES)]
    return template


@pytest.mark.unit
class TestTheShippedTemplates:

    def test_compliance_admin_alone_saves_a_rule_naming_its_databases_or_a_global_workflow(
            self, roles_enforcer):
        enforcer = roles_enforcer({"ca": ("compliance-admin.json", DB)})
        with World(_real(enforcer)) as world:
            assert world.create(_body(convert=_rule()))[0] == 200
            assert world.create(_body(convert=_rule("GLOBAL", pipeline_db="GLOBAL")),
                                name="global-rule")[0] == 200

    def test_compliance_admin_may_not_run_another_databases_workflow(self, roles_enforcer):
        enforcer = roles_enforcer({"ca": ("compliance-admin.json", DB)})
        with World(_real(enforcer)) as world:
            assert svc.may_run_workflow(enforcer, _rule(OTHER, pipeline_db=OTHER)["pipelineRef"]) \
                is False
            # A schema of the database may not name another database's workflow at all.
            assert world.create(_body(convert=_rule(OTHER, pipeline_db=OTHER)))[0] == 400

    def test_the_global_compliance_admin_saves_a_global_rule(self, roles_enforcer):
        enforcer = roles_enforcer({"cga": ("compliance-global-admin.json", "")})
        with World(_real(enforcer)) as world:
            body = _body(convert=_rule("GLOBAL", pipeline_db="GLOBAL"))
            assert world.create(body, scope="GLOBAL")[0] == 200
            assert svc.may_run_workflow(enforcer, _rule()["pipelineRef"]) is False

    def test_a_global_workflow_using_a_databases_pipeline_needs_that_databases_rights(
            self, roles_enforcer):
        enforcer = roles_enforcer({"cga": ("compliance-global-admin.json", "")})
        with World(_real(enforcer)) as world:
            world.t["workflowStorageV2"].put_item(Item={
                "databaseId": "GLOBAL", "workflowId": "mixed", "workflowName": "Mixed",
                "specifiedPipelines": [{"pipelineId": "pipe-1", "pipelineDatabaseId": DB}]})
            status, body = world.create(
                _body(convert=_rule("GLOBAL", "mixed", "GLOBAL")), scope="GLOBAL")
            assert (status, body) == (403, REFUSED)

    def test_a_role_made_from_the_template_before_the_run_rights_is_refused(
            self, roles_enforcer, monkeypatch):
        real_template = template_tests._template
        monkeypatch.setattr(template_tests, "_template", lambda name: (
            _without_run_rights(name[:-len(".old")]) if name.endswith(".old") else real_template(name)))
        enforcer = roles_enforcer({"ca": ("compliance-admin.json.old", DB)})
        with World(_real(enforcer)) as world:
            assert world.create(_body(convert=_rule())) == (403, REFUSED)
        # Adding the database-user template of the workflow's database grants the run.
        enforcer = roles_enforcer({"ca": ("compliance-admin.json.old", DB),
                                   "du": ("database-user.json", DB)})
        with World(_real(enforcer)) as world:
            assert world.create(_body(convert=_rule()))[0] == 200

    @pytest.mark.parametrize("template,database_id,scopes", [
        ("compliance-admin.json", DB, (DB, "GLOBAL")),
        ("compliance-global-admin.json", "", ("GLOBAL",)),
    ])
    def test_the_run_rights_allow_the_execute_route_and_reads_only(
            self, roles_enforcer, template, database_id, scopes):
        enforcer = roles_enforcer({"r": (template, database_id)})

        def api(method, path):
            return enforcer.enforce({"object__type": "api", "route__path": path}, method)

        for scope in scopes:
            assert api("POST", f"/workflows/{scope}/wf1/execute")
            for kind in ("workflow", "pipeline"):
                assert enforcer.enforce({"object__type": kind, "databaseId": scope,
                                         "workflowId": "wf1", "pipelineId": "pipe-1"}, "GET")
                for action in ("POST", "PUT", "DELETE"):
                    assert not enforcer.enforce({"object__type": kind, "databaseId": scope,
                                                 "workflowId": "wf1", "pipelineId": "pipe-1"}, action)
        assert not api("POST", f"/workflows/{OTHER}/wf1/execute")
        for scope in scopes:
            for method, path in (
                    ("POST", f"/database/{scope}/workflows"),
                    ("PUT", f"/database/{scope}/workflows/wf1"),
                    ("DELETE", f"/database/{scope}/workflows/wf1"),
                    ("POST", f"/database/{scope}/pipelines"),
                    ("PUT", f"/database/{scope}/pipelines/pipe-1"),
                    ("POST", "/workflows/executions/e1/rerun"),
                    ("GET", f"/database/{scope}/workflows"),
                    ("GET", "/workflows/executions"),
                    ("PUT", f"/database/{scope}/assets/a1")):
                assert not api(method, path), (method, path)
            for action in ("POST", "PUT", "DELETE"):
                assert not enforcer.enforce({"object__type": "asset", "databaseId": scope,
                                             "assetId": "a1"}, action)
