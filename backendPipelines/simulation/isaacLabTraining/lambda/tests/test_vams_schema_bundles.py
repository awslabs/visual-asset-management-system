#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the isaacLabTraining vamsSchema bundles.

Execute-time validation reads the WORKFLOW record's assetScope, so a pipeline that operates on the
whole asset only runs when its workflow bundle declares the matching scope. Training and evaluation
share one vamsExecute lambda and one state machine, discriminated solely by ``trainingConfig.mode``
from the template config body, so the evaluation pipeline must require a template.

Both pipelines are launched manually, so neither bundle declares a trigger: a trigger a bundle ships is
registered as a workflow-triggers row at deploy time, and the deployment has no configuration that
suppresses one.

The per-run parameters both templates accept are declared as tag schemas, which the execute form renders
and the launch path substitutes into the config body. Three properties of that arrangement decide
whether a run gets the values it asked for, and none of them fails loudly: a typed tag renders a JSON
literal so its placeholder must be unquoted, an optional typed tag with no default renders as an
unmatched placeholder on every default run, and a declared tag the body never references is simply never
substituted."""

import os
import json

import pytest

_SCHEMA_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "vamsSchema"))
_BUNDLE_NAMES = ("training", "evaluation")

# The tag types that render a JSON literal, so their placeholder is the whole value and carries no
# quotes. A string or enum tag renders text and sits inside the string it fills.
_JSON_LITERAL_TYPES = ("integer", "number", "boolean", "string-list")

_TEMPLATE_FILES = {
    "training": "isaaclab-training-cartpole.json",
    "evaluation": "isaaclab-evaluation-cartpole.json",
}


def _load(*parts):
    with open(os.path.join(_SCHEMA_ROOT, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def _template(bundle):
    return _load(bundle, "templates", _TEMPLATE_FILES[bundle])


def _body_with_declared_defaults(template):
    """The config body with every placeholder replaced by its tag's declared default.

    Mirrors what the launch path renders for a run that supplies no tag values: a JSON-literal type
    substitutes a JSON literal, a text type substitutes its text inside the quotes the body already
    carries, and a text type with no default substitutes the empty string.
    """
    body = template["configBody"]
    for field in template.get("tagSchema") or []:
        placeholder = "{{" + field["tagKey"] + "}}"
        if str(field.get("type", "string")).lower() in _JSON_LITERAL_TYPES:
            body = body.replace(placeholder, json.dumps(field.get("default")))
        else:
            body = body.replace(placeholder, str(field.get("default") or ""))
    return body


@pytest.mark.unit
class TestTrainingBundle:
    def test_whole_asset_pipeline_has_whole_asset_workflow_scope(self):
        pipeline_scope = _load("training", "pipeline.json")["systemConfig"]["assetScope"]
        workflow_scope = _load("training", "workflow.json")["systemConfig"]["assetScope"]
        assert pipeline_scope.get("wholeAsset") is True
        assert workflow_scope["wholeAssetAllowed"] is True

    def test_workflow_arity_and_metadata_match_the_pipeline(self):
        pipeline_config = _load("training", "pipeline.json")["systemConfig"]
        workflow_config = _load("training", "workflow.json")["systemConfig"]
        assert workflow_config["inputFileArity"] == pipeline_config["inputFileArity"]
        assert workflow_config["metadataInputs"] == pipeline_config["metadataInputs"]


@pytest.mark.unit
class TestEvaluationBundle:
    def test_evaluation_requires_a_template_for_its_mode(self):
        assert _load("evaluation", "pipeline.json")["systemConfig"]["requireTemplate"] is True

    def test_shipped_evaluation_template_selects_evaluate_mode(self):
        template = _template("evaluation")
        config_body = json.loads(_body_with_declared_defaults(template))
        assert config_body["trainingConfig"]["mode"] == "evaluate"

    def test_mode_is_a_body_literal_and_not_operator_settable(self):
        # The two pipelines share one vamsExecute lambda and one state machine, discriminated only by
        # this value, so a tag over it would let a training run be launched through the evaluation
        # pipeline. It is the one parameter that must not become a form field.
        template = _template("evaluation")
        assert '"mode":"evaluate"' in template["configBody"].replace(" ", "")
        declared = {field["tagKey"] for field in template.get("tagSchema") or []}
        assert "MODE" not in declared, sorted(declared)


@pytest.mark.unit
class TestManualExecutionOnly:
    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_neither_bundle_declares_a_trigger(self, bundle):
        assert "triggers" not in _load(bundle, "workflow.json")

    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_the_loader_reads_the_workflow_it_is_asserting_on(self, bundle):
        # Positive control: a path that resolved to nothing would raise here rather than let the
        # absence check above pass against an empty document.
        workflow = _load(bundle, "workflow.json")
        assert workflow["workflowName"].startswith("Isaac Lab")
        assert workflow["systemConfig"]

    def test_both_bundles_are_present(self):
        found = sorted(name for name in os.listdir(_SCHEMA_ROOT)
                       if os.path.isfile(os.path.join(_SCHEMA_ROOT, name, "workflow.json")))
        assert found == sorted(_BUNDLE_NAMES)


@pytest.mark.unit
class TestTemplateTagsReachTheConfigBody:
    """Both templates declare the per-run parameters as tags, and each failure mode here is silent:
    the run starts, the container parses the body, and the value the operator set is either absent,
    the wrong JSON type, or never substituted at all."""

    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_the_template_declares_tags(self, bundle):
        # In-band non-vacuity for every assertion below, each of which iterates the schema.
        schema = _template(bundle).get("tagSchema") or []
        assert len(schema) >= 5, f"{bundle} declares only {len(schema)} tags"

    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_every_declared_tag_is_referenced_in_the_body(self, bundle):
        template = _template(bundle)
        body = template["configBody"]
        unreferenced = [field["tagKey"] for field in template["tagSchema"]
                        if "{{" + field["tagKey"] + "}}" not in body]
        assert unreferenced == [], unreferenced

    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_a_json_literal_tag_is_unquoted_and_a_text_tag_is_quoted(self, bundle):
        template = _template(bundle)
        body = template["configBody"]
        misplaced = []
        for field in template["tagSchema"]:
            placeholder = "{{" + field["tagKey"] + "}}"
            quoted = f'"{placeholder}"' in body
            wants_literal = str(field.get("type", "string")).lower() in _JSON_LITERAL_TYPES
            if wants_literal == quoted:
                misplaced.append((field["tagKey"], field.get("type"), "quoted" if quoted else "bare"))
        assert misplaced == [], misplaced

    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_no_json_literal_tag_is_optional_without_a_default(self, bundle):
        # Such a tag has no blank form: a run supplying nothing renders an unmatched placeholder and
        # fails template resolution with no field named. Both bundles are launched with no tag values
        # by the IsaacSim connector, which passes no pipeline parameters at all.
        offenders = [field["tagKey"] for field in _template(bundle)["tagSchema"]
                     if str(field.get("type", "string")).lower() in _JSON_LITERAL_TYPES
                     and not field.get("required") and field.get("default") is None]
        assert offenders == [], offenders

    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_a_run_supplying_no_tag_values_renders_valid_json(self, bundle):
        rendered = _body_with_declared_defaults(_template(bundle))
        assert "{{" not in rendered, rendered
        parsed = json.loads(rendered)
        assert parsed["trainingConfig"]["task"], parsed

    def test_the_training_defaults_match_the_task_the_template_advertises(self):
        # A blank TASK with no declared default would fall through to openPipeline's DEFAULT_TASK,
        # Isaac-Cartpole-v0 — a different environment from the one the template names, and a run that
        # trains the wrong task while reporting success.
        config = json.loads(_body_with_declared_defaults(_template("training")))["trainingConfig"]
        assert config["task"] == "Isaac-Cartpole-Direct-v0"
        assert config["numEnvs"] == 4096
        assert config["maxIterations"] == 1500
        assert config["rlLibrary"] == "rsl_rl"

    def test_the_evaluation_defaults_leave_video_off_and_the_checkpoint_auto_discovered(self):
        config = json.loads(_body_with_declared_defaults(_template("evaluation")))["trainingConfig"]
        assert config["recordVideo"] is False
        assert config["numEnvs"] == 100
        assert config["numEpisodes"] == 50
        assert config["stepsPerEpisode"] == 1000
        # Blank rather than a path: merge_configs drops a blank override value, which is what leaves
        # the pipeline's own .pt discovery in place.
        assert config["checkpointPath"] == ""

    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_an_enum_tag_declares_its_values(self, bundle):
        missing = [field["tagKey"] for field in _template(bundle)["tagSchema"]
                   if str(field.get("type", "")).lower() == "enum" and not field.get("enumValues")]
        assert missing == [], missing

    @pytest.mark.parametrize("bundle", _BUNDLE_NAMES)
    def test_every_tag_carries_a_form_label(self, bundle):
        # The form renders the tagKey when no label is declared, which puts a raw config key in front
        # of the operator.
        unlabelled = [field["tagKey"] for field in _template(bundle)["tagSchema"]
                      if not field.get("label") or not field.get("description")]
        assert unlabelled == [], unlabelled
