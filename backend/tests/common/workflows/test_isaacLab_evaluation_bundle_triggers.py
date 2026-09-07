#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Isaac Lab evaluation bundle registers no fileUpload trigger.

Both Isaac Lab pipelines are launched manually. ``build_import_requests`` emits one trigger request per
trigger a bundle SHIPS, so the only thing that keeps a trigger out of the workflow-triggers table is the
bundle not declaring one — there is no deploy-time enable/disable that suppresses a declared trigger to
nothing, and no reconciliation that removes a trigger a bundle stopped shipping.

The requests are built from the bundles on disk, the same files the CDK uploads to the artefacts bucket
and the import lambda reads, so a re-added trigger fails here rather than at deploy time.

Guards FIX-068 (S3-CONTRACTS-022): a bundle that ships a fileUpload trigger with no deploy-time
enable path, unlike every other trigger-shipping bundle.
"""

import json
import os

import pytest

from backend.backend.common.workflows import vamsSchemaImport as vsi

_REPO_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
_PIPELINES_ROOT = os.path.join(_REPO_ROOT, "backendPipelines")

# Bundles that must be discovered whatever the walk finds. A bundle root spelled wrong (or moved) makes
# the walk return an empty or partial set, at which point every "no trigger is emitted" assertion below
# passes without reading anything.
_EXPECTED_BUNDLE_KEYS = {
    "simulation/isaacLabTraining/vamsSchema/evaluation",
    "simulation/isaacLabTraining/vamsSchema/training",
    "preview/3dThumbnail/vamsSchema",
    "conversion/3dBasic/vamsSchema",
}

_EVALUATION_BUNDLE = "simulation/isaacLabTraining/vamsSchema/evaluation"
_TRIGGER_SHIPPING_BUNDLE = "preview/3dThumbnail/vamsSchema"

# A built-in's ids come from the registration construct's idOverrides rather than the schema files, so
# the builder needs them supplied the way the CDK supplies them. The Isaac Lab pair use their real ids
# (isaacLabTraining-construct.ts); every other bundle only needs an id that resolves.
_ID_OVERRIDES = {
    "simulation/isaacLabTraining/vamsSchema/evaluation": {
        "pipelineId": "isaaclab-evaluation", "workflowId": "isaaclab-evaluation"},
    "simulation/isaacLabTraining/vamsSchema/training": {
        "pipelineId": "isaaclab-training", "workflowId": "isaaclab-training"},
}
_FALLBACK_IDS = {"pipelineId": "test-pipeline", "workflowId": "test-workflow"}


def _requests_for(key):
    """The importer's request list for one bundle, built the way the registration CR builds it."""
    return vsi.build_import_requests(
        _BUNDLES[key], id_overrides=_ID_OVERRIDES.get(key, _FALLBACK_IDS))


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


def _kinds(requests):
    return [request.get("kind") for request in requests]


def _trigger_requests(requests):
    return [r for r in requests if r.get("target") == vsi.TARGET_TRIGGER_SERVICE]


@pytest.mark.unit
class TestBundleDiscovery:
    def test_the_walk_finds_every_expected_bundle(self):
        missing = _EXPECTED_BUNDLE_KEYS - set(_BUNDLES)
        assert not missing, f"vamsSchema bundles not found under {_PIPELINES_ROOT}: {missing}"

    def test_the_walk_covers_the_whole_pipeline_tree(self):
        # Every built-in ships a bundle, so a count in the low twenties is the floor. A walk rooted at
        # the wrong directory returns a handful and would otherwise report success.
        assert len(_BUNDLES) >= 20, f"only {len(_BUNDLES)} vamsSchema bundles discovered"


@pytest.mark.unit
class TestIsaacLabEvaluationRegistration:
    def test_no_trigger_request_is_emitted(self):
        assert _trigger_requests(_requests_for(_EVALUATION_BUNDLE)) == []

    def test_the_pipeline_template_and_workflow_are_still_registered(self):
        # An over-broad edit that dropped the workflow (or the templates) would satisfy the assertion
        # above while leaving the pipeline unlaunchable.
        requests = _requests_for(_EVALUATION_BUNDLE)
        kinds = _kinds(requests)
        assert kinds.count("pipeline") == 1
        assert kinds.count("workflow") == 1
        assert kinds.count("template") == 1
        workflow = next(r for r in requests if r.get("kind") == "workflow")
        assert workflow["updatePath"] == "/database/GLOBAL/workflows/isaaclab-evaluation"

    def test_a_deploy_time_trigger_enable_has_nothing_to_act_on(self):
        # The trigger-enable override forces a SHIPPED trigger's enabled flag; it cannot conjure one.
        # So no autoRegisterAutoTriggerOnFileUpload wiring could re-introduce the trigger by itself.
        requests = vsi.build_import_requests(
            _BUNDLES[_EVALUATION_BUNDLE], id_overrides=_ID_OVERRIDES[_EVALUATION_BUNDLE],
            trigger_enabled_override=True)
        assert _trigger_requests(requests) == []

    def test_the_training_bundle_registers_no_trigger_either(self):
        requests = _requests_for("simulation/isaacLabTraining/vamsSchema/training")
        assert _trigger_requests(requests) == []
        assert "workflow" in _kinds(requests)


@pytest.mark.unit
class TestTriggerRequestsAreStillEmittedWhereShipped:
    def test_a_bundle_that_ships_a_trigger_produces_a_trigger_request(self):
        # Positive control for every assertion above: the builder does emit trigger requests, and the
        # target constant they are matched on is the one it uses.
        requests = _requests_for(_TRIGGER_SHIPPING_BUNDLE)
        assert len(_trigger_requests(requests)) == 1

    def test_declared_and_emitted_triggers_agree_across_every_bundle(self):
        declared_counts = {
            key: len((bundle.get("workflow") or {}).get("triggers") or [])
            for key, bundle in _BUNDLES.items()
        }
        emitted_counts = {
            key: len(_trigger_requests(_requests_for(key))) for key in _BUNDLES
        }
        assert emitted_counts == declared_counts
        assert declared_counts[_EVALUATION_BUNDLE] == 0
        # Both halves must be populated, or the equality above is satisfied by an all-zero mapping.
        assert sum(1 for count in declared_counts.values() if count > 0) >= 10
        assert sum(1 for count in declared_counts.values() if count == 0) >= 10
