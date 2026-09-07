# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for common.workflows.triggerTemplateValidation + required_tags_without_default.

Guards FIX-042 (S2-BACKEND-017): a trigger saved against a ``defaultTemplateIds`` template that
does not exist fails every auto-triggered execution in the background, with only a log line.
"""

import dis
import glob
import inspect
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import triggerMatching as tm
from backend.backend.common.workflows import vamsSchemaImport as vsi

# Imported at module scope, and only for its side effect, so `TestTriggerSaveWiring` can reach it.
# The root conftest's autouse fixture installs a plain module object at
# `sys.modules['backend.backend.handlers']` when nothing has claimed that name yet, and a plain
# module has no `__path__` — so a submodule import performed inside a test raises
# "'backend.backend.handlers' is not a package". Importing here means the real package is in
# sys.modules from collection, which is when the conftest guard reads it. Without this the class
# passes only in a run that happens to also collect tests/handlers/workflows/, and fails on any
# narrower selection.
from backend.backend.handlers.workflows import workflowTriggerService  # noqa: F401
from backend.backend.common.workflows.templateTagSchema import required_tags_without_default
from backend.backend.common.workflows.triggerTemplateValidation import (
    trigger_supplied_pipeline_ids,
    validate_trigger_default_templates,
    validate_trigger_required_templates,
    validate_template_not_breaking_triggers,
    pipeline_trigger_template_warnings,
    triggers_referencing_template,
)


@pytest.mark.unit
class TestRequiredTagsWithoutDefault:
    def test_none_and_empty(self):
        assert required_tags_without_default(None) == []
        assert required_tags_without_default([]) == []

    def test_required_without_default_flagged(self):
        schema = [
            {"tagKey": "a", "required": True},
            {"tagKey": "b", "required": True, "default": "x"},
            {"tagKey": "c", "required": False},
            {"tagKey": "d", "required": True, "default": None},
        ]
        assert required_tags_without_default(schema) == ["a", "d"]

    def test_falsey_default_counts_as_default(self):
        # default of False / 0 / "" is a usable default (only None/absent counts as missing).
        schema = [
            {"tagKey": "flag", "required": True, "default": False},
            {"tagKey": "n", "required": True, "default": 0},
            {"tagKey": "s", "required": True, "default": ""},
        ]
        assert required_tags_without_default(schema) == []


@pytest.mark.unit
class TestValidateTriggerDefaultTemplates:
    def test_no_defaults_no_errors(self):
        assert validate_trigger_default_templates({}, lambda *a: None) == []

    def test_flags_required_without_default(self):
        loader = lambda db, p, t: [{"tagKey": "q", "required": True}]
        errors = validate_trigger_default_templates({"db1:pipe1": "tmpl1"}, loader)
        assert errors, "the required tag with no default was not flagged"
        assert any("q" in error for error in errors), errors

    def test_ok_when_default_present(self):
        loader = lambda db, p, t: [{"tagKey": "q", "required": True, "default": "hi"}]
        assert validate_trigger_default_templates({"db1:pipe1": "tmpl1"}, loader) == []

    def test_empty_template_id_skipped(self):
        called = []
        loader = lambda db, p, t: called.append((db, p, t)) or None
        assert validate_trigger_default_templates({"db1:pipe1": ""}, loader) == []
        assert called == []  # loader never invoked for an empty templateId


def _step(pipeline_id="pipe1", require_template=True, default_template_id=None,
          pipeline_database_id="db1", system_config=True, pipeline_default_template_id=None):
    """One workflow pipeline step as the validation reads it (what
    workflowTriggerService._load_workflow_pipeline_steps assembles from the parent workflow's
    specifiedPipelines snapshot plus the pipeline record).

    `system_config=False` omits the block entirely — the shape a caller produces when it could not
    load the pipeline record."""
    ref = {"pipelineDatabaseId": pipeline_database_id, "pipelineId": pipeline_id}
    if system_config:
        ref["systemConfig"] = {"requireTemplate": require_template}
    if default_template_id is not None:
        ref["defaultTemplateId"] = default_template_id
    if pipeline_default_template_id is not None:
        ref["pipelineDefaultTemplateId"] = pipeline_default_template_id
    return ref


@pytest.mark.unit
class TestValidateTriggerRequiredTemplates:
    """The rule is narrow on purpose: a trigger never has to name a template, and only a pipeline that
    REQUIRES one while nothing supplies it is unrunnable headlessly. Every 'no error' case below is a
    configuration that must stay saveable — the same code path the deploy-time built-in registration
    drives, where a spurious rejection fails a CloudFormation custom resource rather than a user."""

    def test_require_template_with_no_default_anywhere_is_rejected(self):
        """The positive control: requireTemplate, an empty trigger map, and no fallback."""
        errors = validate_trigger_required_templates({}, [_step()])
        assert errors, "the pipeline needing a template was not flagged"
        assert any("pipe1" in error for error in errors), (
            f"the error must name the pipeline that needs the template: {errors}")

    def test_the_same_workflow_is_accepted_once_the_pipeline_requires_no_template(self):
        """The narrowing the owner asked for, proved by flipping ONE field.

        Identical trigger and identical workflow step; only requireTemplate changes. A blanket
        'a default template must exist' rule would reject both."""
        assert validate_trigger_required_templates({}, [_step(require_template=False)]) == []

    def test_a_pipeline_with_no_templates_at_all_stays_valid(self):
        """A pipeline whose systemConfig sets neither key — the template-less pipeline the owner's
        constraint protects."""
        assert validate_trigger_required_templates(
            {}, [{"pipelineDatabaseId": "db1", "pipelineId": "pipe1", "systemConfig": {}}]) == []

    def test_a_step_with_no_system_config_is_not_rejected(self):
        """An unknown must not read as 'requires a template'. The caller loads each pipeline record to
        fill systemConfig; a read that failed would otherwise turn into a rejection."""
        assert validate_trigger_required_templates({}, [_step(system_config=False)]) == []

    def test_the_trigger_default_satisfies_the_requirement(self):
        assert validate_trigger_required_templates({"db1:pipe1": "tmpl1"}, [_step()]) == []

    def test_the_workflow_reference_fallback_satisfies_the_requirement(self):
        """`defaultTemplateId` on the workflow reference is the first thing an execution falls back to
        when the trigger names none — so it satisfies the requirement even though the trigger map is
        empty."""
        assert validate_trigger_required_templates(
            {}, [_step(default_template_id="tmplFallback")]) == []

    def test_an_empty_string_fallback_does_not_satisfy_the_requirement(self):
        """The stored shape of 'no fallback' is `''`, not an absent key (build_specified_pipeline_ref
        writes the empty string), so an emptiness test is what distinguishes the two."""
        errors = validate_trigger_required_templates({}, [_step(default_template_id="")])
        assert errors, "the invalid configuration was not flagged"

    def test_a_default_named_for_a_different_pipeline_does_not_satisfy(self):
        """A key naming another PIPELINE does not satisfy this step — a test that matched any entry
        would accept another step's default."""
        errors = validate_trigger_required_templates({"db1:pipeOther": "tmpl1"}, [_step()])
        assert errors, "the invalid configuration was not flagged"

    def test_a_default_whose_database_half_differs_still_satisfies(self):
        """Runtime parity, and the reason this is not a composite-key membership test.

        triggerMatching._default_template_params keys the execute request's pipelineExecutionParameters
        by the part of the key after the last ':' and DROPS the database half, and
        executeWorkflow._resolve_pipeline_configs then looks those parameters up by the pipeline
        record's own pipelineId. So this template does reach the step at run time; rejecting the save
        would refuse a configuration that works — on the deploy path, a failed registration."""
        assert validate_trigger_required_templates({"db2:pipe1": "tmpl1"}, [_step()]) == []

    def test_a_key_with_no_database_half_is_read_as_a_bare_pipeline_id(self):
        """The same branch _default_template_params takes for a key carrying no ':' — it uses the whole
        key as the pipelineId rather than discarding the entry. SetTriggerRequestModel rejects such a key
        on the API, but the runtime branch reads STORED rows, so the two must agree on it either way."""
        assert validate_trigger_required_templates({"pipe1": "tmpl1"}, [_step()]) == []

    def test_an_empty_template_id_in_the_trigger_map_does_not_satisfy(self):
        """An empty value means "no default template for this pipeline" and is skipped everywhere
        downstream, so the key's presence alone must not count as a template."""
        errors = validate_trigger_required_templates({"db1:pipe1": ""}, [_step()])
        assert errors, "the invalid configuration was not flagged"

    def test_the_pipelines_own_default_template_satisfies_the_requirement(self):
        """The third production source: a pipeline's own `isDefault` template, which
        executeWorkflow._resolve_pipeline_configs falls back to for exactly a require-template
        pipeline. Every shipped built-in that requires a template relies on it."""
        assert validate_trigger_required_templates(
            {}, [_step(pipeline_default_template_id="tmplPipelineDefault")]) == []

    def test_an_empty_pipeline_default_does_not_satisfy(self):
        """`_pipeline_default_template_id` returns "" for a pipeline with no default template, so the
        emptiness — not the key's presence — is what distinguishes "has one" from "has none"."""
        errors = validate_trigger_required_templates(
            {}, [_step(pipeline_default_template_id="")])
        assert errors, "the invalid configuration was not flagged"

    def test_the_supplied_ids_match_what_a_triggered_run_actually_receives(self):
        """Pins the key derivation against the runtime code it mirrors instead of restating it.

        `trigger_supplied_pipeline_ids` must name exactly the pipelineIds
        triggerMatching._default_template_params puts in the execute request, or this validation
        disagrees with the run it is predicting — in whichever direction, a wrong verdict."""
        default_template_ids = {
            "db1:pipeA": "tmplA",        # ordinary composite
            "db2:pipeB": "tmplB",        # database half differs from any workflow step
            "pipeC": "tmplC",            # no database half
            "db1:pipeD": "",             # no template chosen
        }
        runtime_params = tm._default_template_params(
            {"triggerConfig": {"defaultTemplateIds": default_template_ids}})
        assert trigger_supplied_pipeline_ids(default_template_ids) == set(runtime_params)
        assert set(runtime_params) == {"pipeA", "pipeB", "pipeC"}

    def test_only_the_unsatisfied_step_of_a_multi_step_workflow_is_reported(self):
        errors = validate_trigger_required_templates(
            {"db1:pipeA": "tmplA"},
            [_step(pipeline_id="pipeA"), _step(pipeline_id="pipeB"),
             _step(pipeline_id="pipeC", require_template=False)])
        assert errors, "the step requiring a template was not flagged"
        assert any("pipeB" in error for error in errors), errors

    def test_no_steps_no_errors(self):
        assert validate_trigger_required_templates({}, []) == []
        assert validate_trigger_required_templates(None, None) == []


def _bundle_root():
    here = os.path.dirname(__file__)
    return os.path.normpath(os.path.join(here, "..", "..", "..", "backendPipelines"))


def _bundle_workflow_files():
    """Every shipped bundle's workflow.json. A bundle sits either directly under `vamsSchema/` or in a
    per-variant subdirectory of it, so both shapes are globbed."""
    root = _bundle_root()
    found = set()
    for pattern in ("/**/vamsSchema/**/workflow.json", "/**/vamsSchema/workflow.json"):
        found.update(os.path.normpath(f) for f in glob.glob(root + pattern, recursive=True))
    return sorted(found)


def _walked_workflow_files():
    """The same set found by walking the tree, as an independent count. A bundle test in this repo once
    globbed the wrong directory and validated 0 of 29 files while passing, so the glob is cross-checked
    rather than trusted."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(_bundle_root()):
        if "workflow.json" in filenames and "vamsSchema" in dirpath.split(os.sep):
            found.append(os.path.normpath(os.path.join(dirpath, "workflow.json")))
    return sorted(found)


# The pipeline identity a bundle registers under is NOT in the bundle: no shipped pipeline.json
# declares a `pipelineId` and no shipped workflow.json declares `specifiedPipelines`, so the id comes
# entirely from the `idOverrides` the pipeline's CDK construct passes to VamsSchemaRegistration. This
# stand-in id is therefore what the sweep registers under by default, and it deliberately agrees with
# no trigger key: recovering the id from the trigger's own defaultTemplateIds composite would make the
# lookup match by construction, and the sweep could not fail on the drift it exists to catch.
SWEPT_PIPELINE_ID = "swept-bundle-pipeline"


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _bundle_trigger_cases(use_trigger_composite_id=False):
    """(path, defaultTemplateIds, workflow_pipelines) for every trigger of every shipped bundle whose
    workflow declares one — built through the production request builder `vsi.build_import_requests`,
    which is what the CDK registration custom resource drives.

    `use_trigger_composite_id` selects the deploy-time pipeline id: the stand-in id (the general case,
    where the construct's id is whatever it is), or the id hardcoded in the trigger's own composite
    (the arrangement every construct happens to produce today). Both must sweep clean — the rule may
    not depend on the two agreeing, because run time does not.

    Each step carries the three sources a headless run can take a template from, assembled the way
    workflowTriggerService._load_workflow_pipeline_steps assembles them:
      - the trigger's own defaultTemplateIds (returned separately; matched by pipelineId),
      - the workflow reference's defaultTemplateId (from the workflow create body),
      - the pipeline's own default template — the template request the importer marks `isDefault`,
        which includes the lone-template promotion build_import_requests applies for a
        requireTemplate pipeline.
    A sweep carrying fewer sources than production would flag a bundle that works."""
    cases = []
    for path in _bundle_workflow_files():
        workflow = _read_json(path)
        if not (workflow.get("triggers") or []):
            continue
        bundle_dir = os.path.dirname(path)
        pipeline = _read_json(os.path.join(bundle_dir, "pipeline.json"))
        templates = [_read_json(template_file) for template_file
                     in sorted(glob.glob(os.path.join(bundle_dir, "templates", "*.json")))]
        pipeline_id = SWEPT_PIPELINE_ID
        if use_trigger_composite_id:
            for trigger in workflow["triggers"]:
                for composite in (trigger.get("defaultTemplateIds") or {}):
                    pipeline_id = composite.split(":")[-1] or pipeline_id
        requests = vsi.build_import_requests(
            {"pipeline": pipeline, "workflow": workflow, "templates": templates},
            resource_overrides={"lambdaName": "dummy-fn"},
            id_overrides={"pipelineId": pipeline_id, "workflowId": pipeline_id})
        system_config = next(
            r["createBody"] for r in requests if r["kind"] == "pipeline").get("systemConfig") or {}
        specified = next(
            r["createBody"] for r in requests if r["kind"] == "workflow")["specifiedPipelines"]
        pipeline_default_template_id = next(
            (r["createBody"]["templateId"] for r in requests
             if r["kind"] == "template" and r["createBody"].get("isDefault")), "")
        steps = [{
            "pipelineDatabaseId": ref.get("pipelineDatabaseId", ""),
            "pipelineId": ref.get("pipelineId", ""),
            "defaultTemplateId": ref.get("defaultTemplateId", "") or "",
            "systemConfig": system_config,
            "pipelineDefaultTemplateId": pipeline_default_template_id,
        } for ref in specified]
        for request in (r for r in requests if r["kind"] == "trigger"):
            cases.append((path, request["setBody"].get("defaultTemplateIds") or {}, steps))
    return cases


def _unsatisfied_by_the_trigger(cases):
    """The swept cases where a require-template step gets NO template from the trigger map — the ones
    whose verdict depends on the pipeline's own default template rather than on the trigger."""
    selected = []
    for case in cases:
        supplied = trigger_supplied_pipeline_ids(case[1])
        if any((step.get("systemConfig") or {}).get("requireTemplate")
               and step["pipelineId"] not in supplied for step in case[2]):
            selected.append(case)
    return selected


@pytest.mark.unit
class TestShippedBundleTriggersPassRequiredTemplateValidation:
    """The deploy-time trap. Built-in registration reaches the trigger save through the same service a
    user PUT does: every backendPipelines/**/vamsSchema/workflow.json trigger becomes a setPath request
    during CDK deployment, and a rejection there fails a CloudFormation custom resource rather than
    returning a friendly 400. So a rule even slightly too strict is a failed deploy, and every shipped
    bundle is swept here."""

    def test_the_bundle_discovery_is_not_vacuous(self):
        """The sweeps below assert over a set, so an empty set would pass them. Cross-checks the glob
        against an os.walk of the same tree and pins the interesting subsets."""
        assert _bundle_workflow_files() == _walked_workflow_files(), (
            "the bundle glob and the tree walk disagree; the glob is wrong")
        assert len(_bundle_workflow_files()) >= 20
        cases = _bundle_trigger_cases()
        assert len(cases) >= 10, "no shipped bundle declares a trigger; check the glob"
        require_template_cases = [
            case for case in cases
            if any((step.get("systemConfig") or {}).get("requireTemplate") for step in case[2])]
        assert len(require_template_cases) >= 5, (
            "no swept bundle has a require-template pipeline, so the sweep exercises only the branch "
            "that returns early")
        assert len(_unsatisfied_by_the_trigger(cases)) >= 5, (
            "under the stand-in pipeline id no require-template step is left for the pipeline's own "
            "default template to satisfy, so the sweep never reaches that source")

    def test_every_swept_bundle_registers_exactly_one_pipeline(self):
        """The pipeline's default template is attributed to every step of the bundle, which is exact
        only while a bundle registers one pipeline. A multi-pipeline bundle would need its default
        resolved per pipeline, and this is what says so."""
        for path, _default_template_ids, steps in _bundle_trigger_cases():
            assert len(steps) == 1, f"{path}: {len(steps)} pipelines; per-step defaults are needed"

    def test_every_shipped_bundle_trigger_validates_clean(self):
        """The stand-in pipeline id: the trigger's hardcoded composite names a different pipelineId, so
        every require-template bundle rides on the pipeline's own default template."""
        for path, default_template_ids, steps in _bundle_trigger_cases():
            errors = validate_trigger_required_templates(default_template_ids, steps)
            assert errors == [], f"{path}: built-in trigger registration would be rejected: {errors}"

    def test_every_shipped_bundle_trigger_validates_clean_when_the_ids_agree(self):
        """The arrangement the CDK constructs produce today: `idOverrides.pipelineId` equals the
        pipelineId hardcoded in the trigger composite, so the trigger's own map satisfies the step."""
        for path, default_template_ids, steps in _bundle_trigger_cases(use_trigger_composite_id=True):
            errors = validate_trigger_required_templates(default_template_ids, steps)
            assert errors == [], f"{path}: built-in trigger registration would be rejected: {errors}"

    def test_a_swept_bundle_that_ships_no_default_template_is_reported(self):
        """The sweep asserts an EMPTY error list, so it is only evidence if a genuinely broken bundle
        would produce one. Takes a real swept case whose require-template step the trigger does not
        cover, pins that it is clean as shipped, then removes the default template the pipeline falls
        back to and asserts the same call reports it — so the removed template is the whole difference
        between the sweep passing and failing."""
        case = _unsatisfied_by_the_trigger(_bundle_trigger_cases())[0]
        path, default_template_ids, steps = case
        assert validate_trigger_required_templates(default_template_ids, steps) == [], (
            f"{path}: expected a clean baseline for the mutation")
        stripped = [{**step, "pipelineDefaultTemplateId": ""} for step in steps]
        errors = validate_trigger_required_templates(default_template_ids, stripped)
        assert errors, f"{path}: a bundle with no resolvable template swept clean"
        assert any(steps[0]["pipelineId"] in error for error in errors), errors


def _trigger_request(default_template_ids):
    """The parsed SetTriggerRequestModel fields `set_trigger` reads."""
    return type("SetTriggerRequest", (), {
        "inputFileFilters": {"allow": [], "exclude": []},
        "defaultTemplateIds": dict(default_template_ids),
        "enabled": True,
    })()


def _workflow_item(default_template_id=""):
    """A parent workflow row specifying one pipeline, as `_enforce_parent_workflow` returns it."""
    return {
        "databaseId": "db1", "workflowId": "wflow1",
        "specifiedPipelines": [{
            "pipelineDatabaseId": "GLOBAL", "pipelineId": "pipe1",
            "pipelineDatabaseId:pipelineId": "GLOBAL:pipe1",
            "jobName": "", "defaultTemplateId": default_template_id,
        }],
    }


@pytest.mark.unit
class TestTriggerSaveWiring:
    """The production call path. `set_trigger` is the only writer of a trigger row — the web PUT, the
    CLI and the deploy-time built-in registration all reach it — so these assert the rule is applied
    there and not merely available. Each patches the two lookup tables, so no test in this class issues
    a real read.

    The save also scopes the templates a trigger names to the parent workflow's pipelines, and the
    caller's identity is what that scope check authorizes against, so it is supplied here the way
    `lambda_handler` supplies it. Only its pipeline READ (`_pipeline_record`) is stubbed separately, so
    `pipelines.get_item` keeps counting the required-template check's reads alone — the property several
    tests below assert."""

    @staticmethod
    def _module():
        from backend.backend.handlers.workflows import workflowTriggerService as wts
        return wts

    def _save(self, default_template_ids, pipeline_item, template_rows,
              workflow_item=None, pipeline_read_error=None):
        wts = self._module()
        pipelines = MagicMock()
        if pipeline_read_error is not None:
            pipelines.get_item.side_effect = pipeline_read_error
        else:
            pipelines.get_item.return_value = {"Item": pipeline_item} if pipeline_item else {}
        templates = MagicMock()
        templates.query.return_value = {"Items": list(template_rows)}
        triggers = MagicMock()
        enforcer = MagicMock()
        enforcer.return_value.enforce.return_value = True
        self.scope_pipeline_read = MagicMock(return_value=pipeline_item)
        with patch.object(wts, "_pipelines_table", return_value=pipelines), \
             patch.object(wts, "_templates_table", return_value=templates), \
             patch.object(wts, "_triggers_table", return_value=triggers), \
             patch.object(wts, "_pipeline_record", self.scope_pipeline_read), \
             patch.object(wts, "CasbinEnforcer", enforcer), \
             patch.object(wts, "_load_template_tag_schema_fields", return_value=None), \
             patch.object(wts, "get_trigger", return_value=None), \
             patch.object(wts, "_same_type_triggers", return_value=[]), \
             patch.object(wts, "log_actions"):
            response = wts.set_trigger(
                "db1", "wflow1", "fileUpload", _trigger_request(default_template_ids),
                workflow_item=workflow_item if workflow_item is not None else _workflow_item(),
                claims_and_roles={"tokens": ["u"]})
        return response, pipelines, templates, triggers

    REQUIRE_TEMPLATE_PIPELINE = {"databaseId": "GLOBAL", "pipelineId": "pipe1",
                                 "systemConfig": {"requireTemplate": True}}

    def test_a_require_template_step_with_no_default_anywhere_is_rejected(self):
        """The wiring proof: no template from the trigger, the workflow reference or the pipeline's own
        default, so the save is refused instead of storing a trigger whose every run would fail."""
        response, pipelines, templates, triggers = self._save(
            {}, self.REQUIRE_TEMPLATE_PIPELINE, [{"templateId": "t1", "isDefault": False}])
        assert response["statusCode"] == 400
        errors = json.loads(response["body"])["message"]["triggerTemplateErrors"]
        assert any("pipe1" in error for error in errors)
        triggers.put_item.assert_not_called()
        pipelines.get_item.assert_called_once()
        templates.query.assert_called_once()

    def test_the_pipelines_own_default_template_lets_the_same_trigger_save(self):
        """One field apart from the case above: the pipeline has an `isDefault` template, which is what
        every shipped require-template built-in relies on."""
        response, _pipelines, templates, triggers = self._save(
            {}, self.REQUIRE_TEMPLATE_PIPELINE, [{"templateId": "t1", "isDefault": True}])
        assert response["statusCode"] == 200
        triggers.put_item.assert_called_once()
        templates.query.assert_called_once()

    def test_a_pipeline_that_requires_no_template_saves_without_a_template_lookup(self):
        """The owner's constraint: a template-less pipeline stays valid, and its default template is
        never even looked for."""
        response, _pipelines, templates, triggers = self._save(
            {}, {"databaseId": "GLOBAL", "pipelineId": "pipe1", "systemConfig": {}}, [])
        assert response["statusCode"] == 200
        triggers.put_item.assert_called_once()
        templates.query.assert_not_called()

    def test_a_trigger_naming_a_template_for_every_step_reads_no_pipeline_record(self):
        """The deploy-path property. Every shipped bundle whose pipeline requires a template names that
        template in its own trigger, so built-in registration adds no required-template read to the save
        it waits on — the one pipeline read the save makes is the scope check's, which authorizes the
        named template's pipeline and happens whatever the required-template rule concludes."""
        response, pipelines, templates, triggers = self._save(
            {"GLOBAL:pipe1": "t1"}, self.REQUIRE_TEMPLATE_PIPELINE, [])
        assert response["statusCode"] == 200
        triggers.put_item.assert_called_once()
        self.scope_pipeline_read.assert_called_once_with("GLOBAL", "pipe1")
        pipelines.get_item.assert_not_called()
        templates.query.assert_not_called()

    def test_a_trigger_key_from_another_database_is_refused_as_out_of_scope(self):
        """A key whose database half names a pipeline the workflow does not specify is refused.

        The required-template rule alone accepts this key — run time reads it by pipelineId and drops
        the database half, which `test_a_default_whose_database_half_differs_still_satisfies` pins on the
        function directly. The save refuses it anyway, one step earlier: the composite is what addresses
        the tag-schema row, so accepting a composite outside the workflow is what let an authorized PUT
        read another database's template. The refusal names neither half."""
        response, pipelines, _templates, triggers = self._save(
            {"OTHERDB:pipe1": "t1"}, self.REQUIRE_TEMPLATE_PIPELINE, [])
        assert response["statusCode"] == 400
        triggers.put_item.assert_not_called()
        message = json.loads(response["body"])["message"]
        assert "OTHERDB" not in message and "t1" not in message
        # Refused before the pipeline is even resolved, so nothing about it is read.
        self.scope_pipeline_read.assert_not_called()
        pipelines.get_item.assert_not_called()

    def test_the_workflow_reference_fallback_satisfies_the_step(self):
        response, pipelines, _templates, triggers = self._save(
            {}, self.REQUIRE_TEMPLATE_PIPELINE, [],
            workflow_item=_workflow_item(default_template_id="tmplFallback"))
        assert response["statusCode"] == 200
        triggers.put_item.assert_called_once()
        pipelines.get_item.assert_not_called()

    def test_an_unreadable_pipeline_table_does_not_reject_the_save(self):
        """Best-effort, and the reason this can never fail a CDK registration: an unknown must not turn
        into a rejection. A deployment whose trigger lambda cannot read the pipeline table loses the
        check, not the deploy."""
        response, _pipelines, _templates, triggers = self._save(
            {}, None, [], pipeline_read_error=RuntimeError("AccessDeniedException"))
        assert response["statusCode"] == 200
        triggers.put_item.assert_called_once()

    def test_a_missing_pipeline_record_does_not_reject_the_save(self):
        """A workflow referencing a pipeline that no longer exists fails at execute with its own
        message; the trigger save says nothing about it."""
        response, _pipelines, _templates, triggers = self._save({}, None, [])
        assert response["statusCode"] == 200
        triggers.put_item.assert_called_once()

    def test_a_workflow_with_no_pipelines_reads_nothing(self):
        """A trigger saved against a workflow whose snapshot lists no pipelines (the shape most handler
        tests use) must add no reads at all."""
        response, pipelines, templates, triggers = self._save(
            {}, self.REQUIRE_TEMPLATE_PIPELINE, [],
            workflow_item={"databaseId": "db1", "workflowId": "wflow1"})
        assert response["statusCode"] == 200
        triggers.put_item.assert_called_once()
        pipelines.get_item.assert_not_called()
        templates.query.assert_not_called()


def _triggers_table_with(rows):
    table = MagicMock()
    table.query.return_value = {"Items": rows}
    return table


@pytest.mark.unit
class TestTemplateNotBreakingTriggers:
    def test_no_required_missing_no_error(self):
        # Even if trigger-referenced, a schema with no required-without-default tag is fine.
        table = _triggers_table_with([
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "tmpl1"}}},
        ])
        errors = validate_template_not_breaking_triggers(
            table, "db1", "pipe1", "tmpl1",
            [{"tagKey": "q", "required": True, "default": "x"}])
        assert errors == []

    def test_referenced_and_breaking_flags(self):
        table = _triggers_table_with([
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "tmpl1"}}},
        ])
        errors = validate_template_not_breaking_triggers(
            table, "db1", "pipe1", "tmpl1",
            [{"tagKey": "q", "required": True}])
        assert errors, "the required tag with no default was not flagged"
        assert any("q" in error for error in errors), errors
        # The client-facing message names no workflow/database ids (backend Rule 11).
        assert "db1:wf1" not in errors[0]

    def test_not_referenced_no_error(self):
        table = _triggers_table_with([
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:other": "tmplX"}}},
        ])
        errors = validate_template_not_breaking_triggers(
            table, "db1", "pipe1", "tmpl1",
            [{"tagKey": "q", "required": True}])
        assert errors == []


@pytest.mark.unit
class TestPipelineTriggerTemplateWarnings:
    def test_no_warning_when_not_require_template(self):
        assert pipeline_trigger_template_warnings(
            MagicMock(), lambda *a: None, "db1", "pipe1", False) == []

    def test_warns_when_triggered_workflow_has_no_default(self):
        wf_table = MagicMock()
        wf_table.scan.return_value = {"Items": [
            {"databaseId": "db1", "workflowId": "wf1",
             "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}]},
        ]}
        # Trigger exists but picked no default template for this pipeline.
        get_trigger = lambda db, wf: {"triggerType": "fileUpload",
                                      "triggerConfig": {"defaultTemplateIds": {}}}
        warnings = pipeline_trigger_template_warnings(wf_table, get_trigger, "db1", "pipe1", True)
        assert len(warnings) == 1
        assert "db1:wf1" in warnings[0]

    def test_no_warning_when_trigger_has_default(self):
        wf_table = MagicMock()
        wf_table.scan.return_value = {"Items": [
            {"databaseId": "db1", "workflowId": "wf1",
             "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}]},
        ]}
        get_trigger = lambda db, wf: {"triggerType": "fileUpload",
                                      "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "t1"}}}
        assert pipeline_trigger_template_warnings(wf_table, get_trigger, "db1", "pipe1", True) == []

    def test_no_warning_when_no_trigger(self):
        wf_table = MagicMock()
        wf_table.scan.return_value = {"Items": [
            {"databaseId": "db1", "workflowId": "wf1",
             "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}]},
        ]}
        assert pipeline_trigger_template_warnings(
            wf_table, lambda db, wf: None, "db1", "pipe1", True) == []


class _RecordingWorkflowsTable:
    """A workflows table that records the kwargs of every scan and serves `pages` in order. Pages are
    already shaped as DynamoDB responses ({'Items': [...], 'LastEvaluatedKey': {...}})."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(dict(kwargs))
        index = len(self.calls) - 1
        return self.pages[index] if index < len(self.pages) else {"Items": []}


def _wf_row(workflow_id, pipeline_composite="db1:pipe1"):
    return {"databaseId": "db1", "workflowId": workflow_id,
            "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": pipeline_composite}]}


def _trigger_with_no_default(_db, _wf):
    return {"triggerType": "fileUpload", "triggerConfig": {"defaultTemplateIds": {}}}


@pytest.mark.unit
class TestPipelineWarningsScanIsProjected:
    """The check runs on every pipeline create/update whose systemConfig sets requireTemplate, on the
    synchronous save path, so the workflows read fetches only the attributes it uses. The function
    swallows every exception and returns [], so a projection that dropped an attribute the code later
    reads would produce no warning and no error — indistinguishable from 'correctly configured'. Every
    assertion here therefore pairs the read shape with the warning it must still produce."""

    def test_the_scan_projects_only_the_attributes_the_check_reads(self):
        table = _RecordingWorkflowsTable([{"Items": [_wf_row("wf1")]}])
        warnings = pipeline_trigger_template_warnings(
            table, _trigger_with_no_default, "db1", "pipe1", True)
        assert len(warnings) == 1, "the misconfigured workflow must still warn"
        assert table.calls, "no scan was issued"
        projection = table.calls[0].get("ProjectionExpression", "")
        for attribute in ("databaseId", "workflowId", "specifiedPipelines"):
            assert attribute in projection, f"{attribute} is not projected: {projection!r}"

    def test_the_pagination_loop_follows_last_evaluated_key(self):
        """Two pages, with the only misconfigured workflow on page TWO: a walk that stopped after the
        first page, or forgot ExclusiveStartKey, would silently report nothing. The projection must
        also survive onto the second call — dropping it there halves the saving."""
        table = _RecordingWorkflowsTable([
            {"Items": [_wf_row("wfA", pipeline_composite="db1:unrelated")],
             "LastEvaluatedKey": {"workflowId": "wfA"}},
            {"Items": [_wf_row("wfB")]},
        ])
        warnings = pipeline_trigger_template_warnings(
            table, _trigger_with_no_default, "db1", "pipe1", True)
        assert len(warnings) == 1
        assert "db1:wfB" in warnings[0]
        assert len(table.calls) == 2
        assert table.calls[1].get("ExclusiveStartKey") == {"workflowId": "wfA"}
        assert "specifiedPipelines" in table.calls[1].get("ProjectionExpression", "")

    def test_a_two_page_walk_with_nothing_misconfigured_still_reads_both_pages(self):
        """The negative control for the test above: a clean deployment returns no warnings, and the
        emptiness is not an early exit — both pages were read."""
        table = _RecordingWorkflowsTable([
            {"Items": [_wf_row("wfA", pipeline_composite="db1:unrelated")],
             "LastEvaluatedKey": {"workflowId": "wfA"}},
            {"Items": [_wf_row("wfB", pipeline_composite="db1:unrelated")]},
        ])
        assert pipeline_trigger_template_warnings(
            table, _trigger_with_no_default, "db1", "pipe1", True) == []
        assert len(table.calls) == 2


@pytest.mark.unit
class TestTriggersReferencingTemplate:
    def test_finds_reference(self):
        table = _triggers_table_with([
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "tmpl1"}}},
            {"workflowDatabaseId": "db1", "workflowId": "wf2", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "other"}}},
        ])
        hits = triggers_referencing_template(table, "db1", "pipe1", "tmpl1")
        assert hits == [("db1", "wf1", "fileUpload")]
        assert table.query.call_args.kwargs["IndexName"] == "TriggersByBaseTypeGSI"
        table.scan.assert_not_called()

    def test_finds_a_reference_from_an_additional_trigger_of_a_type(self):
        """A workflow may carry several triggers of one type, each picking its own default template.

        The rows are keyed 'fileUpload' and 'fileUpload#<id>', so this lookup partitions on the BARE
        type. Keying it on the sort key would find only the first trigger of each type, and a template
        still referenced by an additional trigger would read as unreferenced — the caller uses this to
        decide whether deleting the template breaks a trigger."""
        table = _triggers_table_with([
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload",
             "triggerBaseType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "other"}}},
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload#nightly",
             "triggerBaseType": "fileUpload", "triggerId": "nightly",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "tmpl1"}}},
        ])
        hits = triggers_referencing_template(table, "db1", "pipe1", "tmpl1")
        # The returned triggerType is the row's KEY, so the caller can name the exact trigger.
        assert hits == [("db1", "wf1", "fileUpload#nightly")]
        assert table.query.call_args.kwargs["IndexName"] == "TriggersByBaseTypeGSI"

    def test_read_error_returns_empty(self):
        table = MagicMock()
        table.query.side_effect = RuntimeError("throttled")
        assert triggers_referencing_template(table, "db1", "pipe1", "tmpl1") == []


@pytest.mark.unit
class TestTriggerLookupSignatures:
    """The trigger-reference lookup reads only the triggers table (TriggersByBaseTypeGSI). A workflow
    table parameter in either signature would read as a membership/archived filter this module does
    not apply, and both public entry points must stay callable with the triggers table alone."""

    def test_signatures_take_no_workflow_table(self):
        for fn in (triggers_referencing_template, validate_template_not_breaking_triggers):
            params = list(inspect.signature(fn).parameters)
            assert params[0] == "triggers_table"
            assert "workflows_table" not in params

    def test_no_unused_parameters(self):
        def loaded_names(code):
            """Every local/closure name the code object actually READS (parameters are locals, so a
            parameter that is never loaded emits no instruction naming it). LOAD_FAST_LOAD_FAST
            carries a tuple of two names, so each argval is flattened."""
            names = set()
            for instr in dis.get_instructions(code):
                if instr.opname.startswith(("LOAD_FAST", "LOAD_DEREF")):
                    arg = instr.argval
                    names.update(arg if isinstance(arg, tuple) else (arg,))
            for const in code.co_consts:
                if hasattr(const, "co_code"):  # nested function / comprehension
                    names |= loaded_names(const)
            return names

        for fn in (trigger_supplied_pipeline_ids, validate_trigger_default_templates,
                   validate_trigger_required_templates, triggers_referencing_template,
                   validate_template_not_breaking_triggers, pipeline_trigger_template_warnings):
            unused = set(inspect.signature(fn).parameters) - loaded_names(fn.__code__)
            assert not unused, f"{fn.__name__} has unused parameter(s): {sorted(unused)}"
