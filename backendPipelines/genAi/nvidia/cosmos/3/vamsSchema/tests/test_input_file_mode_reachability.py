#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests that the Cosmos 3 input-file generation modes have a launchable configuration.

image2video, video2video and transfer each consume one input file (openPipeline.INPUT_FILE_MODES,
container/__main__.py). Reaching one takes agreement at BOTH levels the execute gate checks, and the
two are authored in different files:

  - the PIPELINE's effective arity — its pipeline.json systemConfig, with the chosen template's
    `overrides.inputFileArity` layered on top (executionValidation.resolve_effective_pipeline_config);
  - the WORKFLOW's arity — the workflow.json systemConfig of the workflow the run goes through
    (executionValidation._validate_workflow_level, which applies it to the raw selection).

A template override moves only the first. The nano and super bundles ship an arity-'none' workflow
because their default template generates from text alone, and the arity vocabulary is a closed set of
none / one / multi with no value that admits both 0 and 1 files — so their built-in workflow cannot
also serve an input-file template. TestBuiltInWorkflowStillRejectsAnInputFile pins that remaining gap
against the real validator, and the counterfactual below is the evidence that no single arity closes
it: fixing it takes a second, arity-'one' built-in workflow, which is a bundle plus a CDK registration
rather than a template.

Guards FIX-049 (S4-PIPELINES-006): nano/super input-file modes (transfer, image2video,
video2video) made unreachable by the declared arity, so every such run is rejected 400.
"""

import glob
import json
import os

import pytest

from common.workflows import executionValidation as ev

_SCHEMA_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Mirrors openPipeline.INPUT_FILE_MODES / container __main__.INPUT_FILE_MODES.
_INPUT_FILE_MODES = ("image2video", "video2video", "transfer")
# The variant whose checkpoint is itself an input-file model, whatever TASK_MODE says.
_INPUT_FILE_VARIANTS = ("super-image2video",)
# container __main__.TRANSFER_CAPABLE_VARIANTS: the general-purpose omni checkpoints.
_TRANSFER_CAPABLE_VARIANTS = ("nano", "super")

_VIDEO = {"assetId": "asset1", "databaseId": "db1", "relativeFileKey": "/scene.mp4"}
_IMAGE = {"assetId": "asset1", "databaseId": "db1", "relativeFileKey": "/frame.png"}
_OUTPUT_TARGET = {"outputAssetId": "asset1", "outputDatabaseId": "db1"}


def _bundles():
    """(bundleName, pipeline dict, workflow dict, [(path, template dict), ...]) per shipped bundle."""
    found = []
    for pipeline_file in sorted(glob.glob(os.path.join(_SCHEMA_ROOT, "*", "pipeline.json"))):
        bundle_dir = os.path.dirname(pipeline_file)
        with open(pipeline_file, encoding="utf-8") as handle:
            pipeline = json.load(handle)
        with open(os.path.join(bundle_dir, "workflow.json"), encoding="utf-8") as handle:
            workflow = json.load(handle)
        templates = []
        for path in sorted(glob.glob(os.path.join(bundle_dir, "templates", "*.json"))):
            with open(path, encoding="utf-8") as handle:
                templates.append((path, json.load(handle)))
        found.append((os.path.basename(bundle_dir), pipeline, workflow, templates))
    return found


def _body(template):
    """The template's configBody parsed with its tag placeholders neutralized to a literal."""
    return json.loads((template.get("configBody") or "{}").replace("{{DISABLE_GUARDRAILS}}", "true"))


def _consumes_an_input_file(template):
    body = _body(template)
    return (body.get("TASK_MODE") in _INPUT_FILE_MODES
            or body.get("MODEL_VARIANT") in _INPUT_FILE_VARIANTS)


def _effective_arity(pipeline, template):
    effective = ev.resolve_effective_pipeline_config(
        pipeline.get("systemConfig") or {}, template.get("overrides"))
    return effective.get("inputFileArity") or "one"


def _selection_for(template):
    body = _body(template)
    if not _consumes_an_input_file(template):
        return []
    return [_IMAGE] if body.get("TASK_MODE") == "image2video" else [_VIDEO]


def _validate(workflow, pipeline, template, selected):
    effective = ev.resolve_effective_pipeline_config(
        pipeline.get("systemConfig") or {}, template.get("overrides"))
    errors, _filtered = ev.validate_execution(
        workflow.get("systemConfig") or {},
        [{"pipelineId": "p", "pipelineDatabaseId": "GLOBAL", "systemConfig": effective}],
        selected, _OUTPUT_TARGET)
    return errors


@pytest.mark.unit
class TestInputFileModesAreConfigured:
    def test_bundle_discovery_is_not_vacuous(self):
        bundles = _bundles()
        assert len(bundles) >= 4, f"bundle discovery broke: {_SCHEMA_ROOT}"
        assert sum(len(t) for _n, _p, _w, t in bundles) >= 9, "template discovery broke"
        assert [t for _n, _p, _w, ts in bundles for _path, t in ts if _consumes_an_input_file(t)], (
            "no input-file-mode template found; every assertion below would be vacuous")

    def test_every_input_file_mode_template_raises_the_pipeline_arity_to_one(self):
        offenders = []
        for _name, pipeline, _workflow, templates in _bundles():
            for path, template in templates:
                if not _consumes_an_input_file(template):
                    continue
                arity = _effective_arity(pipeline, template)
                if arity != "one":
                    offenders.append(
                        f"{os.path.basename(path)}: effective inputFileArity is {arity!r}; the run "
                        f"would carry no input file")
        assert not offenders, offenders

    def test_no_text_mode_template_asks_for_an_input_file(self):
        # The mirror image: a text-mode template that raised arity would demand a file the execute form
        # has nothing to do with, and openPipeline would reject the run for want of a prompt.
        offenders = []
        for _name, pipeline, _workflow, templates in _bundles():
            for path, template in templates:
                if _consumes_an_input_file(template):
                    continue
                arity = _effective_arity(pipeline, template)
                if arity != "none":
                    offenders.append(f"{os.path.basename(path)}: effective arity is {arity!r}")
        assert not offenders, offenders

    def test_every_transfer_capable_model_ships_a_transfer_template(self):
        # Control-signal transfer is the mode with the most dead plumbing behind it (build_control_blocks
        # and the control download in container/__main__.py, the video2video model_mode mapping in
        # inference.py). It runs only on the general-purpose omni checkpoints.
        transfer_variants = set()
        for _name, _pipeline, _workflow, templates in _bundles():
            for _path, template in templates:
                body = _body(template)
                if body.get("TASK_MODE") == "transfer":
                    transfer_variants.add(body.get("MODEL_VARIANT"))
        assert set(_TRANSFER_CAPABLE_VARIANTS) <= transfer_variants, (
            f"no shipped transfer template for {set(_TRANSFER_CAPABLE_VARIANTS) - transfer_variants}; "
            f"the transfer plumbing is unreachable for those variants")

    def test_every_input_file_mode_template_filters_to_a_type_the_pipeline_accepts(self):
        # openPipeline rejects anything outside .mp4/.mov/.jpg/.jpeg/.png/.webp, so a template whose
        # allow list admits more than that lets a run be launched and then fail at the lambda.
        allowed = {".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"}
        offenders = []
        for _name, _pipeline, _workflow, templates in _bundles():
            for path, template in templates:
                if not _consumes_an_input_file(template):
                    continue
                patterns = (((template.get("overrides") or {}).get("inputFileFilters") or {})
                            .get("allow") or [])
                if not patterns:
                    offenders.append(f"{os.path.basename(path)}: no allow list")
                for pattern in patterns:
                    if not pattern.startswith("*.") or pattern[1:].lower() not in allowed:
                        offenders.append(f"{os.path.basename(path)}: {pattern!r} is not accepted "
                                         f"by openPipeline")
        assert not offenders, offenders


@pytest.mark.unit
class TestBuiltInWorkflowStillRejectsAnInputFile:
    """Pins the workflow-level half of the gate, which the bundles cannot close on their own.

    When a second arity-'one' built-in workflow is registered for the nano and super pipelines, the
    first test here starts FAILING — that is the intended signal, and the message says what to do.
    """

    def _input_mode_cases(self, bundle_names):
        cases = []
        for name, pipeline, workflow, templates in _bundles():
            if name not in bundle_names:
                continue
            for path, template in templates:
                if _consumes_an_input_file(template):
                    cases.append((name, os.path.basename(path), pipeline, workflow, template))
        return cases

    def test_the_arity_one_bundle_accepts_its_input_file_mode(self):
        # Positive control. Without it the rejection below is equally consistent with the validator
        # rejecting every call.
        cases = self._input_mode_cases({"super-image2video"})
        assert cases, "the super-image2video bundle no longer ships an input-file template"
        for name, template_name, pipeline, workflow, template in cases:
            errors = _validate(workflow, pipeline, template, _selection_for(template))
            assert errors == [], f"{name}/{template_name}: {errors}"

    def test_the_arity_none_built_in_workflow_rejects_the_input_file_modes(self):
        cases = self._input_mode_cases({"nano", "super"})
        assert cases, "no nano/super input-file template found; the pin would be vacuous"
        for name, template_name, pipeline, workflow, template in cases:
            assert (workflow.get("systemConfig") or {}).get("inputFileArity") == "none", (
                f"{name}/workflow.json arity changed; re-derive this pin")
            errors = _validate(workflow, pipeline, template, _selection_for(template))
            assert any("Workflow expects no input files" in e for e in errors), (
                f"{name}/{template_name} is now accepted through the built-in workflow. If an "
                f"arity-'one' built-in workflow was added for this pipeline, delete this test; if the "
                f"workflow-level arity gate changed, re-derive the pin. errors={errors}")

    def test_no_single_workflow_arity_serves_both_mode_classes(self):
        """Why the built-in workflow cannot be widened instead: arity is a closed none/one/multi set,
        and each value rejects one of the two classes."""
        name, pipeline, workflow, templates = next(
            b for b in _bundles() if b[0] == "nano")
        text_mode = next(t for _p, t in templates if not _consumes_an_input_file(t))
        input_mode = next(t for _p, t in templates
                          if _body(t).get("TASK_MODE") == "transfer")
        for arity in ("none", "one", "multi"):
            system_config = dict(workflow.get("systemConfig") or {})
            system_config["inputFileArity"] = arity
            widened = {"systemConfig": system_config}
            text_errors = _validate(widened, pipeline, text_mode, [])
            input_errors = _validate(widened, pipeline, input_mode, _selection_for(input_mode))
            assert bool(text_errors) != bool(input_errors), (
                f"workflow arity {arity!r} accepts both a no-file text run and a one-file input run "
                f"({name}); the arity vocabulary changed and the second-workflow conclusion no longer "
                f"holds")
