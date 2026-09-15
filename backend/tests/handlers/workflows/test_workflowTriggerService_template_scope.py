# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-023: a trigger PUT may only reference templates of pipelines the parent workflow specifies.

`_load_template_tag_schema_fields` looks a template up by bare composite key
(`<pipelineDatabaseId>:<pipelineId>` + templateId). While `validate_trigger_default_templates` ran
first — before any membership check, and with Tier-2 only ever having authorized the parent WORKFLOW —
a caller who may edit ONE workflow could name any pipeline/template composite in the whole deployment
and get its required tag-schema field names echoed back in the 400 body. That was a cross-database
read amplified by an authorized PUT: a probe over composites enumerated other databases' template tag
names.

`set_trigger` now rejects a `defaultTemplateIds` key whose composite is not in the parent workflow's
`specifiedPipelines` snapshot, and then Tier-2 GETs the referenced pipeline, both before any tag
schema is loaded — and reports either rejection without naming what it found.

Three things must NOT regress, and each has a test here:
  - The structured `{'triggerTemplateErrors': [...]}` shape is documented in VAMS_API.yaml and
    api/pipelines.md, is indexed by tests/handlers/workflows/test_workflowTriggerService.py, and is
    rendered by the web trigger form and flattened by the CLI. It must survive for IN-workflow
    templates the caller is authorized on; only the scope rejections are generic.
  - The permitted case must still save. A membership check that compares 'db:id' against the ref
    DICT rather than against `ref['pipelineDatabaseId:pipelineId']` rejects everything.
  - The built-in bundles register their triggers through this same `set_trigger` (via
    importGlobalPipelineWorkflow's lambdaCrossCall) with the composite HARDCODED in
    backendPipelines/**/vamsSchema/**/workflow.json. A membership rejection makes that coupling
    load-bearing, and a failed CDK custom-resource registration is known to be maskable, so the
    shipped bundles are pinned as a commit-time guard.

The pipeline read behind the second condition is fail-closed: it decides authorization, so a read that
raises refuses the PUT instead of authorizing it against a synthesized record. A row that is genuinely
absent is a different condition and keeps its Casbin verdict; both are pinned by
TestPipelineReadFailureFailsClosed."""

import glob
import json
import os
import re

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.common.workflows import vamsSchemaImport as vsi
from backend.backend.common.workflows import workflowRecords as wr
from backend.backend.handlers.workflows.workflowTriggerService import lambda_handler

MOD = "backend.backend.handlers.workflows.workflowTriggerService"

BASE = "/database/db1/workflows/wflow1/triggers"
TPARAMS = {"databaseId": "db1", "workflowId": "wflow1", "triggerType": "fileUpload"}

# The workflow the caller is authorized on: it specifies pipeA and nothing else.
IN_WORKFLOW_COMPOSITE = "GLOBAL:pipeA"
OUT_OF_WORKFLOW_COMPOSITE = "GLOBAL:pipeB"

# The other database's template and the tag name its schema requires. Neither may appear in a
# response body produced by a PUT on this workflow.
FOREIGN_TEMPLATE_ID = "tmplB"
FOREIGN_TAG_NAME = "secretTagName"

WF_ITEM = {
    "databaseId": "db1", "workflowId": "wflow1", "workflowName": "W",
    "specifiedPipelines": [
        {"pipelineDatabaseId": "GLOBAL", "pipelineId": "pipeA",
         "pipelineDatabaseId:pipelineId": IN_WORKFLOW_COMPOSITE,
         "jobName": "", "defaultTemplateId": ""},
    ],
}


def _event(method, path, path_params, body=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


def _enforcer(api=True, obj=True):
    inst = MagicMock()
    inst.enforceAPI.return_value = api
    inst.enforce.return_value = obj
    return inst


def _tag_schema(pipeline_database_id, pipeline_id, template_id):
    """Tag schemas keyed by composite. pipeA's 'tmplBroken' and pipeB's 'tmplB' are equally broken —
    each carries one required tag with no default — so the only difference between them is whether the
    parent workflow specifies the pipeline."""
    schemas = {
        ("GLOBAL", "pipeA", "tmplOk"): [{"tagKey": "quality", "required": True, "default": "high"}],
        ("GLOBAL", "pipeA", "tmplBroken"): [{"tagKey": "ownTagName", "required": True}],
        ("GLOBAL", "pipeB", FOREIGN_TEMPLATE_ID): [{"tagKey": FOREIGN_TAG_NAME, "required": True}],
    }
    return schemas.get((pipeline_database_id, pipeline_id, template_id))


def _pipelines_table_mock():
    """A pipelines table that resolves EVERY composite, pipeA and pipeB alike.

    Deliberately permissive: if the foreign pipeline's record were unresolvable, the leak tests below
    would pass because the READ failed rather than because the membership check rejected the key."""
    table = MagicMock()
    table.get_item.side_effect = lambda Key: {
        "Item": {"databaseId": Key["databaseId"], "pipelineId": Key["pipelineId"],
                 "pipelineName": Key["pipelineId"], "systemConfig": {},
                 "executionConfig": {"executionType": "Lambda"}}}
    return table


def _put_trigger(default_template_ids, obj_allowed=True, claims=None, cross_call=False,
                 enforcer=None, pipelines=None):
    """PUT the fileUpload trigger with `default_template_ids`. Returns (response, triggers table mock,
    the tag-schema loader mock, the pipelines table mock).

    `enforcer` lets a caller supply the CasbinEnforcer stand-in it wants to inspect afterwards, and
    `pipelines` the pipelines table stand-in — a read that fails and a row that is absent are different
    conditions, so each is supplied as the table behaviour that produces it. The templates table is
    stubbed too so no path here can reach a real table."""
    table = MagicMock()
    table.get_item.return_value = {}          # no existing trigger
    table.query.return_value = {"Items": []}  # no sibling triggers
    pipelines = pipelines if pipelines is not None else _pipelines_table_mock()
    templates = MagicMock()
    templates.query.return_value = {"Items": []}
    body = {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
            "defaultTemplateIds": default_template_ids}
    event = _event("PUT", BASE + "/fileUpload", TPARAMS, body)
    if cross_call:
        event["lambdaCrossCall"] = {"userName": "SYSTEM_USER"}
    with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer or _enforcer(obj=obj_allowed)), \
         patch(f"{MOD}.request_to_claims", return_value=claims or {"tokens": ["u"]}), \
         patch(f"{MOD}._enforce_parent_workflow", return_value=(True, WF_ITEM)), \
         patch(f"{MOD}._triggers_table", return_value=table), \
         patch(f"{MOD}._pipelines_table", return_value=pipelines), \
         patch(f"{MOD}._templates_table", return_value=templates), \
         patch(f"{MOD}._load_template_tag_schema_fields",
               side_effect=_tag_schema) as loader:
        response = lambda_handler(event, MagicMock())
    return response, table, loader, pipelines


@pytest.mark.unit
class TestOutOfWorkflowTemplateIsNotRead:
    """A composite the parent workflow does not specify is out of scope for this PUT."""

    def test_the_rejection_names_nothing_it_found(self):
        """FIX-023: an out-of-workflow key is refused without leaking the template it points at."""
        response, table, _loader, _pipelines = _put_trigger(
            {OUT_OF_WORKFLOW_COMPOSITE: FOREIGN_TEMPLATE_ID})
        assert response["statusCode"] == 400
        table.put_item.assert_not_called()
        serialized = json.dumps(response)
        assert FOREIGN_TAG_NAME not in serialized, (
            "the response leaked another database's template tag-schema field name")
        assert FOREIGN_TEMPLATE_ID not in serialized, (
            "the response echoed the caller's supplied template id (backend Rule 11)")

    def test_the_foreign_tag_schema_is_never_loaded(self):
        """FIX-023: the membership check runs BEFORE the tag schema read, so no cross-database read
        happens at all.

        This is the stronger of the two remedies in the recommendation — rejecting the key up front
        rather than only genericizing the message. It is the read, not just the echo, that lets an
        authorized editor of one workflow enumerate another database's templates."""
        _response, _table, loader, _pipelines = _put_trigger(
            {OUT_OF_WORKFLOW_COMPOSITE: FOREIGN_TEMPLATE_ID})
        loaded = [call.args for call in loader.call_args_list]
        assert ("GLOBAL", "pipeB", FOREIGN_TEMPLATE_ID) not in loaded, (
            f"the handler read a template outside the parent workflow: {loaded}")

    def test_a_pipeline_the_caller_cannot_read_is_refused_before_the_schema_load(self):
        """FIX-023: the second condition — Tier-2 GET on the referenced pipeline.

        The composite IS in the parent workflow here, so membership alone would let the tag schema be
        read. A caller who holds Tier-2 on the workflow but not on the workflow's pipeline is refused,
        and the refusal happens before the load rather than after it."""
        response, table, loader, _pipelines = _put_trigger(
            {IN_WORKFLOW_COMPOSITE: "tmplBroken"}, obj_allowed=False)
        assert response["statusCode"] == 403
        table.put_item.assert_not_called()
        assert loader.call_args_list == [], (
            "the tag schema was read for a pipeline the caller is not authorized on")
        assert "ownTagName" not in json.dumps(response)

    def test_the_pipeline_is_enforced_as_a_pipeline_object_carrying_its_constraint_fields(self):
        """FIX-023: what the second condition actually enforces on.

        A check that enforced on the ids alone would answer the same for every pipeline in a database
        and could not deny a role scoped by pipeline name or execution type — it would look like a
        Tier-2 check and decide nothing. So the object is the pipeline ROW, annotated `pipeline`, with
        the flat ABAC fields `apply_pipeline_constraint_fields` surfaces, and the action is GET."""
        enforcer = _enforcer()
        response, _table, _loader, pipelines = _put_trigger(
            {IN_WORKFLOW_COMPOSITE: "tmplOk"}, enforcer=enforcer)
        assert response["statusCode"] == 200
        pipelines.get_item.assert_called_once_with(
            Key={"databaseId": "GLOBAL", "pipelineId": "pipeA"})
        assert len(enforcer.enforce.call_args_list) == 1
        obj, action = enforcer.enforce.call_args.args
        assert action == "GET"
        assert obj["object__type"] == "pipeline"
        assert obj["databaseId"] == "GLOBAL" and obj["pipelineId"] == "pipeA"
        assert obj["name"] == "pipeA"                       # from the record's pipelineName
        assert obj["pipelineExecutionType"] == "Lambda"     # from executionConfig.executionType

    def test_an_in_workflow_template_still_reports_its_own_tag_names(self):
        """FIX-023 control: the structured error shape survives for templates in scope.

        Without this, the leak test above passes for a handler that returns an empty body for EVERY
        rejection — which would also break the documented `triggerTemplateErrors` contract that the
        web form, the CLI flattener, VAMS_API.yaml and api/pipelines.md all depend on. pipeA's
        template is broken in exactly the same way pipeB's is; the only difference is membership.

        Passes today and must keep passing after the fix."""
        response, table, _loader, _pipelines = _put_trigger({IN_WORKFLOW_COMPOSITE: "tmplBroken"})
        assert response["statusCode"] == 400
        table.put_item.assert_not_called()
        errors = json.loads(response["body"])["message"]["triggerTemplateErrors"]
        assert any("ownTagName" in error for error in errors), (
            "an in-workflow template must still say which required tag has no default")

    def test_an_in_workflow_template_still_saves(self):
        """FIX-023 control: the permitted half.

        A membership check that compares 'GLOBAL:pipeA' against the ref DICT instead of against
        ref['pipelineDatabaseId:pipelineId'] rejects every trigger, which a rejection-only test would
        never notice. Passes today and must keep passing after the fix."""
        response, table, _loader, _pipelines = _put_trigger({IN_WORKFLOW_COMPOSITE: "tmplOk"})
        assert response["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["triggerConfig"]["defaultTemplateIds"] == {IN_WORKFLOW_COMPOSITE: "tmplOk"}
        assert saved["triggerConfig"]["inputFileFilters"]["allow"] == ["*.glb"]

    def test_a_trigger_naming_no_templates_still_saves(self):
        """FIX-023 control: an empty defaultTemplateIds map is valid — a trigger never REQUIRES a
        template — so the membership check must not turn "none chosen" into a rejection."""
        response, table, _loader, _pipelines = _put_trigger({})
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["triggerConfig"]["defaultTemplateIds"] == {}

    def test_an_empty_template_id_for_an_out_of_workflow_pipeline_is_not_a_rejection(self):
        """FIX-023 control: an entry whose value is "" names no template.

        The headless-template validation already skips such an entry, so the scope check must skip it
        too — otherwise a stale key left at "no default template" turns into a hard rejection."""
        response, table, loader, _pipelines = _put_trigger({OUT_OF_WORKFLOW_COMPOSITE: ""})
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["triggerConfig"]["defaultTemplateIds"] == {
            OUT_OF_WORKFLOW_COMPOSITE: ""}
        assert loader.call_args_list == []

    def test_the_system_user_deploy_path_still_saves(self):
        """FIX-023: the CDK registration path. importGlobalPipelineWorkflow PUTs each bundle's trigger
        as a SYSTEM_USER `lambdaCrossCall` AFTER creating the workflow, so the workflow row the
        membership check reads already specifies the bundle's pipeline (pinned for every shipped bundle
        by TestShippedBundleTriggersNameTheirOwnPipeline below).

        What this pins is the membership resolution for a deploy-shaped request; the identity itself is
        supplied as claims because `handlers.auth` is a suite-wide mock. The Casbin half is covered by
        construction rather than here: SYSTEM_USER is seeded into the admin role, which carries GET on
        all pipelines, and workflowService._resolve_referenced_pipelines already runs the same pipeline
        Tier-2 GET one request earlier in the same registration."""
        response, table, _loader, _pipelines = _put_trigger(
            {IN_WORKFLOW_COMPOSITE: "tmplOk"}, claims={"tokens": ["SYSTEM_USER"]}, cross_call=True)
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["triggerConfig"]["defaultTemplateIds"] == {
            IN_WORKFLOW_COMPOSITE: "tmplOk"}


@pytest.mark.unit
class TestPipelineReadFailureFailsClosed:
    """A failed pipeline read is not an absent pipeline.

    The second condition authorizes the referenced template against the pipeline's own record, so a
    read that raises leaves that verdict undecided. Answering it from a synthesized placeholder makes
    throttling, a missing IAM grant on the pipeline table, or any transient error into a PASS — the
    check would still run, still log, and decide nothing. Casbin ALLOWS throughout this class, so a
    handler that authorized the placeholder would save the trigger and these would fail."""

    @staticmethod
    def _raising_pipelines_table():
        table = MagicMock()
        table.get_item.side_effect = RuntimeError("ProvisionedThroughputExceededException")
        return table

    @staticmethod
    def _absent_pipelines_table():
        table = MagicMock()
        table.get_item.return_value = {}       # the row is genuinely gone
        return table

    def test_a_pipeline_read_that_raises_does_not_authorize(self):
        """The fail-closed half: an unreadable pipeline refuses the PUT rather than authorizing it."""
        response, table, loader, pipelines = _put_trigger(
            {IN_WORKFLOW_COMPOSITE: "tmplOk"}, pipelines=self._raising_pipelines_table())
        assert response["statusCode"] == 500
        table.put_item.assert_not_called()
        pipelines.get_item.assert_called_once()
        # Authorization never resolved, so nothing behind it is read either.
        assert loader.call_args_list == []
        serialized = json.dumps(response)
        assert "pipeA" not in serialized and "tmplOk" not in serialized, (
            "the refusal echoed what it could not read (backend Rule 11)")

    def test_a_successful_read_on_an_authorized_pipeline_still_passes(self):
        """The control that makes the assertion above mean something: identical request, identical
        Casbin verdict, the read succeeding — 500 for everything would pass the test above."""
        response, table, _loader, pipelines = _put_trigger({IN_WORKFLOW_COMPOSITE: "tmplOk"})
        assert response["statusCode"] == 200
        pipelines.get_item.assert_called_once_with(
            Key={"databaseId": "GLOBAL", "pipelineId": "pipeA"})
        assert table.put_item.call_args.kwargs["Item"]["triggerConfig"]["defaultTemplateIds"] == {
            IN_WORKFLOW_COMPOSITE: "tmplOk"}

    def test_an_absent_pipeline_record_is_still_decided_by_casbin(self):
        """The other condition, which is NOT a failure: the row is absent, which the table answered.

        An absent row is enforced against a provisional object built from the composite ids, so a role
        that Casbin denies is still denied — an absent pipeline is not an authorization bypass either.
        Deploy relies on this staying decidable: the registration creates the pipeline one request
        before the trigger PUT, so this eventually-consistent read can legitimately miss it."""
        response, table, _loader, _pipelines = _put_trigger(
            {IN_WORKFLOW_COMPOSITE: "tmplOk"}, pipelines=self._absent_pipelines_table(),
            enforcer=_enforcer(obj=False))
        assert response["statusCode"] == 403
        table.put_item.assert_not_called()

    def test_an_absent_pipeline_record_that_casbin_allows_still_saves(self):
        """Paired with the test above: absence is answered by the Casbin verdict, not refused
        outright, so the fail-closed read failure does not leak into the absent case."""
        response, table, _loader, _pipelines = _put_trigger(
            {IN_WORKFLOW_COMPOSITE: "tmplOk"}, pipelines=self._absent_pipelines_table())
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["triggerConfig"]["defaultTemplateIds"] == {
            IN_WORKFLOW_COMPOSITE: "tmplOk"}

    def test_a_trigger_naming_no_templates_survives_an_unreadable_pipeline_table(self):
        """The read is mandatory only where a template makes it load-bearing.

        A trigger that names no template addresses no pipeline, so the scope check reads nothing and
        the same unreadable table that refuses the PUT above cannot refuse this one — only the
        required-template check touches it here, and that one stays best-effort. This is what keeps the
        fail-closed read off the shipped bundles that declare no defaultTemplateIds."""
        response, table, _loader, _pipelines = _put_trigger(
            {}, pipelines=self._raising_pipelines_table())
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["triggerConfig"]["defaultTemplateIds"] == {}


def _repo_root():
    here = os.path.dirname(__file__)
    return os.path.normpath(os.path.join(here, "..", "..", "..", ".."))


def _bundle_root():
    return os.path.join(_repo_root(), "backendPipelines")


def _registration_construct_files():
    """Every CDK file that instantiates VamsSchemaRegistration. That is where the pipelineId a bundle
    is registered under comes from — the bundle itself declares none."""
    found = []
    for path in glob.glob(os.path.join(_repo_root(), "infra", "lib", "**", "*.ts"), recursive=True):
        with open(path, encoding="utf-8") as handle:
            if "new VamsSchemaRegistration(" in handle.read():
                found.append(os.path.normpath(path))
    return sorted(found)


def _registration_string_literals():
    """The id-shaped double-quoted literals of those files."""
    literals = set()
    for path in _registration_construct_files():
        with open(path, encoding="utf-8") as handle:
            literals.update(re.findall(r'"([-_a-zA-Z0-9]{3,63})"', handle.read()))
    return literals


def _bundle_workflow_files():
    """Every shipped bundle's workflow.json. A bundle sits either directly under `vamsSchema/` or in a
    per-variant subdirectory of it, so both shapes are globbed."""
    root = _bundle_root()
    found = set()
    for pattern in ("/**/vamsSchema/**/workflow.json", "/**/vamsSchema/workflow.json"):
        found.update(os.path.normpath(f) for f in glob.glob(root + pattern, recursive=True))
    return sorted(found)


def _walked_workflow_files():
    """The same set found by walking the tree, as an independent count. A previous bundle test globbed
    the wrong directory and validated 0 of 29 templates while passing, so the glob is cross-checked
    rather than trusted."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(_bundle_root()):
        if "workflow.json" in filenames and "vamsSchema" in dirpath.split(os.sep):
            found.append(os.path.normpath(os.path.join(dirpath, "workflow.json")))
    return sorted(found)


@pytest.mark.unit
class TestShippedBundleTriggersNameTheirOwnPipeline:
    """The deploy-time trap. Built-in registration reaches `set_trigger` through the same code path a
    user PUT does, with the composite hardcoded in the bundle while the CDK supplies the pipeline id
    via `idOverrides`. They agree today; a membership rejection makes that agreement load-bearing, and
    a rejected trigger save inside a CDK custom resource can be masked (cdk deploy exits 0). These
    pass today and are the guard that the fix does not break built-in registration."""

    def _trigger_composites(self):
        """(path, composite, templateId) for every defaultTemplateIds entry in every bundle."""
        entries = []
        for path in _bundle_workflow_files():
            with open(path, encoding="utf-8") as handle:
                workflow = json.load(handle)
            for trigger in workflow.get("triggers") or []:
                for composite, template_id in (trigger.get("defaultTemplateIds") or {}).items():
                    entries.append((path, composite, template_id))
        return entries

    def test_the_bundle_discovery_is_not_vacuous(self):
        """FIX-023 control for the two tests below: they assert over a set, so an empty set passes
        both. Cross-checks the glob against an os.walk of the same tree."""
        assert _bundle_workflow_files() == _walked_workflow_files(), (
            "the bundle glob and the tree walk disagree; the glob is wrong")
        assert len(_bundle_workflow_files()) >= 20
        assert len(self._trigger_composites()) >= 10, (
            "no shipped bundle trigger declares defaultTemplateIds; check the glob")

    def test_every_bundle_trigger_composite_is_in_its_own_workflow(self):
        """FIX-023: each bundle's hardcoded composite is a pipeline that bundle's workflow specifies.

        Built through the production request builder (`vsi.build_import_requests`) with the bundle's
        own composite supplied as the id override, which is the arrangement the CDK produces — so the
        membership check the fix adds resolves the same way at deploy time as it does here."""
        for path, composite, _template_id in self._trigger_composites():
            database_id, _, pipeline_id = composite.partition(":")
            assert database_id == "GLOBAL", f"{path}: non-GLOBAL trigger composite {composite}"
            bundle_dir = os.path.dirname(path)
            with open(os.path.join(bundle_dir, "pipeline.json"), encoding="utf-8") as handle:
                pipeline = json.load(handle)
            with open(path, encoding="utf-8") as handle:
                workflow = json.load(handle)
            requests = vsi.build_import_requests(
                {"pipeline": pipeline, "workflow": workflow},
                resource_overrides={"lambdaName": "dummy-fn"},
                id_overrides={"pipelineId": pipeline_id, "workflowId": pipeline_id})
            workflow_body = next(
                r["createBody"] for r in requests if r["kind"] == "workflow")
            specified = {
                f"{ref.get('pipelineDatabaseId', '')}:{ref.get('pipelineId', '')}"
                for ref in workflow_body["specifiedPipelines"]}
            assert composite in specified, (
                f"{path}: trigger names {composite}, workflow specifies {sorted(specified)}")

    def test_every_bundle_trigger_pipeline_id_is_one_the_cdk_registers(self):
        """FIX-023: the drift the test above cannot see.

        That test supplies the trigger's OWN composite as the id override, so it pins how a composite
        resolves but not whether the two sources agree: the composite is hardcoded in the bundle JSON
        while the id the workflow is actually created with comes from `idOverrides` in the pipeline's CDK
        construct. Rename one and not the other and this bundle's trigger is rejected as out-of-workflow
        inside a CloudFormation custom resource. The haystack is exactly the files that instantiate
        VamsSchemaRegistration, which is where every such id is written."""
        construct_files = _registration_construct_files()
        literals = _registration_string_literals()
        assert len(construct_files) >= 10, "no CDK registration constructs found; check the glob"
        assert len(literals) >= 100
        checked = 0
        for path, composite, _template_id in self._trigger_composites():
            pipeline_id = composite.partition(":")[2]
            assert pipeline_id in literals, (
                f"{path}: the trigger names pipeline {pipeline_id!r}, which no VamsSchemaRegistration "
                f"construct declares")
            # The assertion is a membership test over a few hundred literals, so pin that an id the
            # CDK does NOT declare genuinely misses.
            assert f"{pipeline_id}-not-registered" not in literals
            checked += 1
        assert checked >= 10

    def test_every_bundle_trigger_template_belongs_to_that_bundle(self):
        """FIX-023: the templateId a bundle trigger picks is one the same bundle ships.

        A trigger that defaults to a template the bundle does not register would be rejected the
        moment the referenced template must be resolvable within the workflow."""
        for path, _composite, template_id in self._trigger_composites():
            template_files = sorted(glob.glob(
                os.path.join(os.path.dirname(path), "templates", "*.json")))
            shipped = set()
            for template_file in template_files:
                with open(template_file, encoding="utf-8") as handle:
                    shipped.add(json.load(handle).get("templateId"))
            assert template_id in shipped, (
                f"{path}: trigger defaults to template {template_id!r}, bundle ships "
                f"{sorted(shipped)}")


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# The id a bundle registers under when its trigger names no composite to take one from. No shipped
# pipeline.json declares a pipelineId, so the deploy-time value always comes from the construct's
# `idOverrides`.
STAND_IN_PIPELINE_ID = "swept-bundle-pipeline"


def _bundle_registration_cases():
    """(path, requests) from `vsi.build_import_requests` for every shipped bundle whose workflow
    declares a trigger — the exact request list the CDK registration custom resource drives.

    The pipeline id is taken from the trigger's own composite where it has one, which is the id the
    construct passes today (pinned against the constructs themselves by
    TestShippedBundleTriggersNameTheirOwnPipeline)."""
    cases = []
    for path in _bundle_workflow_files():
        workflow = _read_json(path)
        if not (workflow.get("triggers") or []):
            continue
        bundle_dir = os.path.dirname(path)
        pipeline_id = STAND_IN_PIPELINE_ID
        for trigger in workflow["triggers"]:
            for composite in (trigger.get("defaultTemplateIds") or {}):
                pipeline_id = composite.partition(":")[2] or pipeline_id
        cases.append((path, vsi.build_import_requests(
            {"pipeline": _read_json(os.path.join(bundle_dir, "pipeline.json")),
             "workflow": workflow,
             "templates": [_read_json(template_file) for template_file
                           in sorted(glob.glob(os.path.join(bundle_dir, "templates", "*.json")))]},
            resource_overrides={"lambdaName": "dummy-fn"},
            id_overrides={"pipelineId": pipeline_id, "workflowId": pipeline_id})))
    return cases


@pytest.mark.unit
class TestShippedBundleRegistrationSavesItsTrigger:
    """The deploy path, driven through the handler on real bundle data.

    importGlobalPipelineWorkflow._invoke PUTs each trigger request as a SYSTEM_USER `lambdaCrossCall`
    AFTER the workflow is created, so every input the scope check reads is reconstructed here from the
    shipped files: the request body and path from `vsi.build_import_requests`, the parent workflow row
    from the workflow create body normalized through `wr.build_specified_pipeline_ref` (what
    workflowService persists), the pipeline record and its default template from the pipeline/template
    create bodies, and each referenced template's tag schema from the bundle's own `tagSchema`. A
    rejection here is a rejection inside a CloudFormation custom resource, where a failed registration
    is known to be maskable — so it is asserted on the trigger ROW being written, not on a return value.

    Two identity substitutions, both matching production: `request_to_claims` returns what the real one
    returns for a cross-call event (tokens=[userName]) because tests/mocks/handlers/auth.py replaces
    that module suite-wide, and Casbin ALLOWS, which is what SYSTEM_USER gets — it is seeded into the
    admin role, whose seeded pipeline constraint is `databaseId contains .*` granting GET."""

    @staticmethod
    def _drive(requests):
        """PUT every trigger request of one bundle. Returns the list of written trigger rows."""
        pipeline_body = next(r["createBody"] for r in requests if r["kind"] == "pipeline")
        workflow_body = next(r["createBody"] for r in requests if r["kind"] == "workflow")
        template_bodies = [r["createBody"] for r in requests if r["kind"] == "template"]
        # The pipeline create body carries the resolved databaseId/pipelineId plus pipelineName,
        # executionConfig and systemConfig, so it stands in for the stored record.
        pipeline_record = dict(pipeline_body)
        workflow_item = {
            "databaseId": workflow_body["databaseId"],
            "workflowId": workflow_body["workflowId"],
            "workflowName": workflow_body["workflowName"],
            "systemConfig": workflow_body.get("systemConfig") or {},
            "specifiedPipelines": [
                wr.build_specified_pipeline_ref(
                    ref.get("pipelineDatabaseId", ""), ref.get("pipelineId", ""),
                    ref.get("jobName", "") or "", ref.get("defaultTemplateId", "") or "")
                for ref in workflow_body["specifiedPipelines"]],
        }
        tag_schemas = {body["templateId"]: body.get("tagSchema") for body in template_bodies}
        triggers = MagicMock()
        triggers.get_item.return_value = {}
        triggers.query.return_value = {"Items": []}
        pipelines = MagicMock()
        pipelines.get_item.return_value = {"Item": pipeline_record}
        templates = MagicMock()
        templates.query.return_value = {"Items": [
            {"templateId": body["templateId"], "isDefault": bool(body.get("isDefault"))}
            for body in template_bodies]}
        written = []
        for request in (r for r in requests if r["kind"] == "trigger"):
            event = {
                "requestContext": {"http": {"method": "PUT", "path": request["setPath"]}},
                "pathParameters": dict(request["setPathParameters"]),
                "queryStringParameters": {},
                "lambdaCrossCall": {"userName": "SYSTEM_USER"},
                "body": json.dumps(request["setBody"]),
            }
            with patch(f"{MOD}.CasbinEnforcer", return_value=_enforcer()), \
                 patch(f"{MOD}.request_to_claims",
                       return_value={"tokens": ["SYSTEM_USER"], "roles": [],
                                     "externalAttributes": [], "mfaEnabled": True}), \
                 patch(f"{MOD}._enforce_parent_workflow", return_value=(True, workflow_item)), \
                 patch(f"{MOD}._triggers_table", return_value=triggers), \
                 patch(f"{MOD}._pipelines_table", return_value=pipelines), \
                 patch(f"{MOD}._templates_table", return_value=templates), \
                 patch(f"{MOD}._load_template_tag_schema_fields",
                       side_effect=lambda _pdb, _pid, template_id: tag_schemas.get(template_id)):
                response = lambda_handler(event, MagicMock())
            assert response["statusCode"] == 200, (
                f"{request['setPath']}: built-in trigger registration was rejected: "
                f"{response['body']}")
            written.append(triggers.put_item.call_args.kwargs["Item"])
        return written

    def test_the_registration_sweep_is_not_vacuous(self):
        """Every assertion below is per-bundle, so an empty case list would pass them all."""
        cases = _bundle_registration_cases()
        assert len(cases) >= 10, "no shipped bundle declares a trigger; check the glob"
        with_templates = [case for case in cases
                          if any(r["kind"] == "trigger" and r["setBody"].get("defaultTemplateIds")
                                 for r in case[1])]
        assert len(with_templates) >= 10, (
            "no swept bundle trigger names a default template, so the scope check never runs")

    def test_every_shipped_bundle_trigger_row_is_written(self):
        """The row exists afterwards, with the bundle's composite intact — not merely a 200."""
        for path, requests in _bundle_registration_cases():
            rows = self._drive(requests)
            assert rows, f"{path}: no trigger row was written"
            for row, request in zip(rows, (r for r in requests if r["kind"] == "trigger")):
                assert (row["triggerConfig"]["defaultTemplateIds"]
                        == (request["setBody"].get("defaultTemplateIds") or {})), path
                assert row["workflowDatabaseId"] == request["setPathParameters"]["databaseId"]
                assert row["workflowId"] == request["setPathParameters"]["workflowId"]
