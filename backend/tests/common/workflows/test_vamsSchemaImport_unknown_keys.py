#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A vamsSchema bundle key that no create/update body reads is reported, and the shipped bundles carry
none.

The importer cherry-picks the keys it knows, so a misspelled one is dropped and the record registers
anyway: `{"fileFilters": {...}}` on a fileUpload trigger becomes `inputFileFilters: {}`, which
apply_input_file_filters reads as allow-all — an auto-trigger in front of a GPU/Batch pipeline that
fires on every upload in scope, with `cdk deploy` green.

Two halves, and they are deliberately different strengths:

  - At registration an unknown key WARNS and the bundle still registers. That is the documented
    forward-compatibility contract (custom-pipelines.md: "Unknown fields are ignored rather than
    rejected"), so a bundle authored against a newer VAMS — or against a spelling this release does not
    know — must keep deploying. Rejecting it would brick an external self-registration.
  - The bundles this repository SHIPS are held to zero unknown keys here, where a typo fails at commit
    time instead of registering a wrong constraint on every deployment.

The same walk backs a second lint over the shipped templates' `tagSchema`: a declared tag the
template's own `configBody` never references as `{{tagKey}}` is accepted everywhere — at save, at
registration, and at launch — and is simply never substituted, so the execute form renders a field
whose value reaches no pipeline.

Guards S2-BACKEND-014, S4-PIPELINES-002, S4-PIPELINES-065.
"""

import copy
import json
import os

import pytest
from unittest.mock import patch

from backend.backend.common.workflows import vamsSchemaImport as vsi
from backend.backend.common.workflows import templateTagSchema as tts

_REPO_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
_PIPELINES_ROOT = os.path.join(_REPO_ROOT, "backendPipelines")

# Bundles that must be discovered whatever the walk finds. Every sweep below asserts over the walked
# set, so a root spelled wrong (or moved) would leave them passing without reading a bundle.
_EXPECTED_BUNDLE_KEYS = {
    "conversion/3dBasic/vamsSchema",
    "preview/3dThumbnail/vamsSchema",
    "genAi/metadata3dLabeling/vamsSchema",
    "simulation/isaacLabTraining/vamsSchema/training",
}

_TRIGGER_SHIPPING_BUNDLE = "preview/3dThumbnail/vamsSchema"


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _assemble(bundle_dir):
    """A parsed bundle in the shape importGlobalPipelineWorkflow.assemble_bundle builds from S3: the
    pipeline, the optional workflow, and the top-level templates/*.json excluding the webform
    companions."""
    bundle = {"pipeline": _read_json(os.path.join(bundle_dir, "pipeline.json"))}
    workflow_path = os.path.join(bundle_dir, "workflow.json")
    if os.path.isfile(workflow_path):
        bundle["workflow"] = _read_json(workflow_path)
    templates_dir = os.path.join(bundle_dir, "templates")
    if os.path.isdir(templates_dir):
        names = sorted(f for f in os.listdir(templates_dir)
                       if f.endswith(".json") and not f.endswith(".webform.json"))
        if names:
            bundle["templates"] = [_read_json(os.path.join(templates_dir, f)) for f in names]
    return bundle


def _discover_bundles():
    """{repo-relative-key: parsed bundle} for every vamsSchema bundle under backendPipelines."""
    bundles = {}
    for dirpath, _dirnames, filenames in os.walk(_PIPELINES_ROOT):
        if "pipeline.json" not in filenames or "vamsSchema" not in dirpath.split(os.sep):
            continue
        key = os.path.relpath(dirpath, _PIPELINES_ROOT).replace(os.sep, "/")
        bundles[key] = _assemble(dirpath)
    return bundles


_BUNDLES = _discover_bundles()


def _bundle(**over):
    """A minimal well-formed bundle: one pipeline, one template, one workflow with one trigger."""
    bundle = {
        "pipeline": {"pipelineId": "conv", "pipelineName": "Converter", "category": "conversion",
                     "executionConfig": {"executionType": "Lambda", "lambda": {}},
                     "systemConfig": {"requireTemplate": False}},
        "workflow": {"workflowId": "conv", "workflowName": "Convert WF",
                     "triggers": [{"triggerType": "fileUpload",
                                   "inputFileFilters": {"allow": ["*.glb"]}}]},
        "templates": [{"templateId": "to-obj", "templateName": "To OBJ"}],
    }
    bundle.update(over)
    return bundle


@pytest.mark.unit
class TestUnknownBundleKeys:
    def test_a_mistyped_trigger_filter_key_is_reported(self):
        """The reported case: `fileFilters` instead of `inputFileFilters`. The trigger registers with an
        empty filter map, which is allow-all, so nothing downstream can tell it from a deliberate
        match-all trigger."""
        bundle = _bundle()
        bundle["workflow"]["triggers"] = [
            {"triggerType": "fileUpload", "fileFilters": {"allow": ["*.e57"]}}]
        assert vsi.unknown_bundle_keys(bundle) == ["workflow.triggers[0].fileFilters"]

    def test_every_documented_key_is_accepted(self):
        """The positive control for the key sets: a bundle declaring the whole documented shape at every
        level reports nothing. A set missing a legitimate key would warn on every deploy instead. The
        `schemaVersion` marker is declared everywhere it is accepted, the bundle itself included — it
        states which shape the bundle was written against, so reporting it would warn at the one author
        who said so."""
        bundle = {
            "schemaVersion": 1,
            "pipeline": {"pipelineId": "p", "databaseId": "GLOBAL", "pipelineName": "P",
                         "category": "c", "description": "d", "systemConfig": {},
                         "executionConfig": {"executionType": "Lambda", "lambda": {}},
                         "enabled": True, "schemaVersion": 1},
            "workflow": {"workflowId": "w", "databaseId": "GLOBAL", "workflowName": "W",
                         "category": "c", "description": "d", "systemConfig": {},
                         "subDashboardUrl": "", "specifiedPipelines": [], "schemaVersion": 1,
                         "triggers": [{"triggerType": "fileUpload", "inputFileFilters": {},
                                       "defaultTemplateIds": {}, "enabled": True}]},
            "templates": [{"templateId": "t", "templateName": "T", "description": "d",
                           "configFormat": "json", "configBody": "{}", "webFormJson": "",
                           "allowCustomEdit": False, "inputInstructions": "how", "overrides": {},
                           "tagSchema": [], "isDefault": True, "schemaVersion": 1}],
        }
        assert vsi.unknown_bundle_keys(bundle) == []

    def test_unknown_keys_are_reported_at_every_level(self):
        bundle = _bundle(pipelines=[])
        bundle["pipeline"]["systemConfigg"] = {}
        bundle["workflow"]["subDashboardURL"] = ""
        bundle["templates"][0]["tagSchemas"] = []
        assert vsi.unknown_bundle_keys(bundle) == [
            "pipeline.systemConfigg",
            "pipelines",
            "templates[0].tagSchemas",
            "workflow.subDashboardURL",
        ]

    def test_the_index_names_the_offending_template_and_trigger(self):
        """A bundle ships several templates and may ship several triggers, so the report has to say
        which one carries the key — a bare key name would send an author through every file."""
        bundle = _bundle()
        bundle["templates"] = [
            {"templateId": "a", "templateName": "A"},
            {"templateId": "b", "templateName": "B", "webForm": "{}"},
        ]
        bundle["workflow"]["triggers"] = [
            {"triggerType": "fileUpload", "inputFileFilters": {}},
            {"triggerType": "fileUpload", "templateIds": {}},
        ]
        assert vsi.unknown_bundle_keys(bundle) == [
            "templates[1].webForm", "workflow.triggers[1].templateIds"]

    def test_the_interiors_of_the_copied_through_blocks_are_not_walked(self):
        """systemConfig / executionConfig / a template's overrides are copied into the create body
        wholesale, and the request models reject their unknown keys with a 400 that names them. Reporting
        them here as well would duplicate that check and disagree with it as those models grow."""
        bundle = _bundle()
        bundle["pipeline"]["systemConfig"] = {"madeUpSetting": True}
        bundle["pipeline"]["executionConfig"]["madeUpBlock"] = {}
        bundle["templates"][0]["overrides"] = {"madeUpOverride": True}
        assert vsi.unknown_bundle_keys(bundle) == []

    def test_a_malformed_bundle_is_tolerated_rather_than_raising(self):
        """The report runs before the structural checks, so it must never be the thing that fails: a
        bundle whose sections are the wrong type has to keep producing build_import_requests'
        VamsSchemaError, not a TypeError from the reporting."""
        assert vsi.unknown_bundle_keys(None) == []
        assert vsi.unknown_bundle_keys([]) == []
        assert vsi.unknown_bundle_keys(
            {"pipeline": "not-a-dict", "templates": {"nope": 1},
             "workflow": {"triggers": "not-a-list", "bogus": 1}}) == ["workflow.bogus"]


@pytest.mark.unit
class TestRegistrationWarnsAndStillRegisters:
    def test_a_mistyped_trigger_key_is_warned_at_registration(self):
        """The deploy-time signal. The importer runs inside the registration custom resource, so this
        warning is the only place a typo surfaces — the CR otherwise reports success."""
        bundle = _bundle()
        bundle["workflow"]["triggers"] = [
            {"triggerType": "fileUpload", "fileFilters": {"allow": ["*.e57"]}}]
        with patch.object(vsi, "logger") as logger:
            vsi.build_import_requests(bundle)
        assert logger.warning.call_count == 1
        message = logger.warning.call_args[0][0]
        assert "fileFilters" in message, message

    def test_a_clean_bundle_warns_about_nothing(self):
        with patch.object(vsi, "logger") as logger:
            vsi.build_import_requests(_bundle())
        logger.warning.assert_not_called()

    def test_an_unknown_key_still_registers_the_whole_bundle(self):
        """The constraint the report may not break: unknown keys are IGNORED, never rejected, so a
        bundle written against a newer VAMS keeps registering. Nothing about the emitted requests may
        change either — this is the same assertion that separates a report from an outage."""
        bundle = _bundle(futureSection={"whatever": True})
        bundle["pipeline"]["futureField"] = "x"
        requests = vsi.build_import_requests(bundle)
        assert [r["kind"] for r in requests] == ["pipeline", "template", "workflow", "trigger"]
        trigger = next(r for r in requests if r["kind"] == "trigger")
        assert trigger["setBody"] == {"inputFileFilters": {"allow": ["*.glb"]},
                                      "defaultTemplateIds": {}, "enabled": True}
        assert requests[0]["createBody"]["pipelineId"] == "conv"

    def test_a_structurally_invalid_bundle_still_raises(self):
        """The report runs first, so an invalid bundle must still fail the deploy for the reason it
        always did."""
        with pytest.raises(vsi.VamsSchemaError):
            vsi.build_import_requests({"bogus": 1})


@pytest.mark.unit
class TestShippedBundlesDeclareNoUnknownKey:
    """The bundle lint. Warning-only at registration is what keeps a customer's authored bundle
    deploying; the bundles VAMS ships are enforced instead, and here — a typo in one otherwise reaches
    every deployment as a registered constraint its author never wrote."""

    def test_the_walk_finds_every_expected_bundle(self):
        missing = _EXPECTED_BUNDLE_KEYS - set(_BUNDLES)
        assert not missing, f"vamsSchema bundles not found under {_PIPELINES_ROOT}: {missing}"

    def test_the_walk_covers_the_whole_pipeline_tree(self):
        # Every built-in ships a bundle, so a count in the low twenties is the floor; a walk rooted at
        # the wrong directory returns a handful and the sweep below would report success on it.
        assert len(_BUNDLES) >= 20, f"only {len(_BUNDLES)} vamsSchema bundles discovered"
        with_templates = [key for key, bundle in _BUNDLES.items() if bundle.get("templates")]
        with_triggers = [key for key, bundle in _BUNDLES.items()
                         if (bundle.get("workflow") or {}).get("triggers")]
        assert len(with_templates) >= 15, "no bundle's templates were assembled"
        assert len(with_triggers) >= 10, "no bundle's triggers were assembled"

    def test_no_shipped_bundle_declares_an_unknown_key(self):
        offenders = {key: vsi.unknown_bundle_keys(bundle) for key, bundle in _BUNDLES.items()
                     if vsi.unknown_bundle_keys(bundle)}
        assert not offenders, (
            "these shipped bundles declare keys the importer drops, so each registers a record its "
            f"author did not write: {offenders}")

    def test_the_sweep_reports_a_typo_planted_in_a_shipped_bundle(self):
        """The positive control for the sweep above, which asserts an EMPTY result and would pass on a
        report that saw nothing. Takes a real trigger-shipping bundle, renames the one key whose loss
        turns the trigger into a match-all, and asserts the sweep names it."""
        bundle = copy.deepcopy(_BUNDLES[_TRIGGER_SHIPPING_BUNDLE])
        assert vsi.unknown_bundle_keys(bundle) == [], "expected a clean baseline for the mutation"
        trigger = bundle["workflow"]["triggers"][0]
        assert "inputFileFilters" in trigger, "the bundle no longer filters its trigger"
        trigger["fileFilters"] = trigger.pop("inputFileFilters")
        assert vsi.unknown_bundle_keys(bundle) == ["workflow.triggers[0].fileFilters"]


def _declared_tags():
    """[(bundle key, templateId, tagSchema, configBody)] for every shipped template declaring tags."""
    declared = []
    for key, bundle in _BUNDLES.items():
        for template in bundle.get("templates") or []:
            schema = template.get("tagSchema")
            if schema:
                declared.append((key, template.get("templateId"), schema,
                                 template.get("configBody", "")))
    return declared


def _unreferenced_tags(schema, config_body):
    """The tagKeys the body never substitutes, so their values reach nothing."""
    return [field.get("tagKey") for field in schema
            if field.get("tagKey") and "{{" + str(field["tagKey"]) + "}}" not in config_body]


_DECLARED_TAGS = _declared_tags()


@pytest.mark.unit
class TestShippedTemplateTagSchemas:
    """A tagSchema is what puts a per-run option on the execute form, and two ways of getting one
    wrong are invisible after a green deploy: a schema `validate_tag_schema` would reject never
    reaches that validator from a bundle (registration cherry-picks the key and the template create
    is what validates it, so the failure surfaces as one line in a custom-resource log), and a tag
    declared but not referenced in the body is not an error anywhere — the field renders, the
    operator fills it in, and the substitution never happens."""

    def test_the_walk_finds_the_templates_that_declare_tags(self):
        # In-band non-vacuity: both sweeps below iterate this list, so a walk that assembled no
        # template would leave them passing while checking nothing.
        assert len(_DECLARED_TAGS) >= 15, f"only {len(_DECLARED_TAGS)} shipped templates declare tags"
        bundles_with_tags = {key for key, _id, _schema, _body in _DECLARED_TAGS}
        assert len(bundles_with_tags) >= 8, sorted(bundles_with_tags)

    def test_every_shipped_tag_schema_validates(self):
        offenders = {f"{key}:{template_id}": tts.validate_tag_schema(schema)
                     for key, template_id, schema, _body in _DECLARED_TAGS
                     if tts.validate_tag_schema(schema)}
        assert not offenders, (
            "these shipped templates declare a tag schema the template create would reject, so the "
            f"template registers without it or not at all: {offenders}")

    def test_every_declared_tag_is_referenced_in_its_own_config_body(self):
        offenders = {f"{key}:{template_id}": _unreferenced_tags(schema, body)
                     for key, template_id, schema, body in _DECLARED_TAGS
                     if _unreferenced_tags(schema, body)}
        assert not offenders, (
            "these shipped templates declare a tag their configBody never references, so the execute "
            f"form asks for a value that reaches no pipeline: {offenders}")

    def test_the_reference_sweep_reports_a_tag_removed_from_a_body(self):
        """Positive control for the sweep above, which asserts an EMPTY result: take a real shipped
        template, drop the one placeholder, and assert the matcher names the now-orphaned tag."""
        key, template_id, schema, body = _DECLARED_TAGS[0]
        tag_key = schema[0]["tagKey"]
        assert _unreferenced_tags(schema, body) == [], (
            f"expected a clean baseline for the mutation ({key}:{template_id})")
        mutated = body.replace("{{" + tag_key + "}}", "0")
        assert mutated != body, f"the placeholder was not present to remove ({tag_key})"
        assert _unreferenced_tags(schema, mutated) == [tag_key]

    def test_the_validation_sweep_reports_a_defectless_typed_tag(self):
        """Positive control for the validation sweep: an optional integer/number/boolean tag with no
        default has no blank form, so a run supplying nothing renders an unmatched {{tag}} and fails
        with no field named. That is the shape validate_tag_schema exists to catch."""
        _key, _template_id, schema, _body = _DECLARED_TAGS[0]
        assert tts.validate_tag_schema(schema) == [], "expected a clean baseline for the mutation"
        mutated = copy.deepcopy(schema)
        mutated.append({"tagKey": "PLANTED", "type": "boolean", "required": False})
        errors = tts.validate_tag_schema(mutated)
        assert errors and "PLANTED" in errors[0], errors
