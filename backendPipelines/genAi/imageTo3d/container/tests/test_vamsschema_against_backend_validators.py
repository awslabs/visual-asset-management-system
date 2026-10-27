#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Validation of the imageTo3d schema bundles against the REAL backend save-time validators.

Registration imports the bundle through the same models an API caller's create request goes through
(``CreatePipelineRequestModel``, ``CreateTemplateRequestModel``, ``CreateWorkflowRequestModel``,
``TemplateTagFieldModel`` and the shared ``validate_tag_schema``), so a bundle that would be refused
with a 400 deploys as AWS resources with nothing registered behind them. Instantiating the real
models rather than restating their bounds here is the point: a bound this file never names still
catches a violation.

The models are exercised in a SUBPROCESS. This suite puts the container directory first on
``sys.path`` and the container ships its own ``models/`` package, which would shadow the backend's
``models.pipelines`` in-process. The subprocess gets the backend path alone.
"""

import json
import os
import subprocess  # nosec B404 - runs this repo's own interpreter on a fixture script
import sys

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPELINE_DIR = os.path.dirname(_CONTAINER_DIR)
_SCHEMA_DIR = os.path.join(_PIPELINE_DIR, "vamsSchema")
_REFINE_SCHEMA_DIR = os.path.join(_PIPELINE_DIR, "vamsSchema", "refine")
_TEMPLATE_DIR = os.path.join(_SCHEMA_DIR, "templates")
_BACKEND_DIR = os.path.abspath(
    os.path.join(_PIPELINE_DIR, "..", "..", "..", "backend", "backend"))

# The ids CDK registration assigns the built-in, and the execution target it injects at deploy time.
_PIPELINE_ID = "genai-image-to-3d"
_REFINE_PIPELINE_ID = "genai-image-to-3d-refine"
_LAMBDA_RESOURCE_ID = "vams-imageTo3d-vamsExecute"

# Tag types whose placeholder renders a JSON value of that type and therefore takes no quotes.
_TYPED_TAG_TYPES = ("integer", "number", "boolean", "string-list")

# Run in the subprocess: instantiate every real validator over the bundle and report per-file
# verdicts as JSON on stdout. Kept as source text so the container suite needs no backend import.
# ``quoting`` selects the config body validated: 'authored' takes the bundle's own, 'requoted' wraps
# every typed placeholder back in quotes to exercise the counter-case.
_VALIDATOR_SCRIPT = '''
import json, os, sys

backend_dir, schema_dir, pipeline_id, lambda_resource_id, quoting = sys.argv[1:6]
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
sys.path.insert(0, backend_dir)

from models.pipelines import (
    CreatePipelineRequestModel, CreateTemplateRequestModel, TemplateTagFieldModel)
from models.workflows import CreateWorkflowRequestModel
from common.workflows.templateTagSchema import validate_tag_schema

TYPED = ("integer", "number", "boolean", "string-list")
template_dir = os.path.join(schema_dir, "templates")
result = {"pipeline": None, "workflow": None, "templates": {}, "tagSchema": {}}


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _requoted(template):
    body = template["configBody"]
    for field in template["tagSchema"]:
        if field["type"] in TYPED:
            tag = "{{" + field["tagKey"] + "}}"
            body = body.replace(tag, chr(34) + tag + chr(34))
    return dict(template, configBody=body)


pipeline = _load(os.path.join(schema_dir, "pipeline.json"))
try:
    CreatePipelineRequestModel(
        databaseId="GLOBAL", pipelineId=pipeline_id,
        pipelineName=pipeline["pipelineName"], category=pipeline.get("category", ""),
        description=pipeline.get("description", ""),
        executionConfig={**pipeline["executionConfig"],
                         "lambda": {"resourceId": lambda_resource_id}},
        systemConfig=pipeline.get("systemConfig") or {})
except Exception as e:
    result["pipeline"] = str(e)

workflow = _load(os.path.join(schema_dir, "workflow.json"))
try:
    CreateWorkflowRequestModel(
        databaseId="GLOBAL", workflowId=pipeline_id,
        workflowName=workflow["workflowName"], category=workflow.get("category", ""),
        description=workflow.get("description", ""),
        systemConfig=workflow.get("systemConfig") or {},
        specifiedPipelines=[{"pipelineDatabaseId": "GLOBAL", "pipelineId": pipeline_id}])
except Exception as e:
    result["workflow"] = str(e)

for name in sorted(os.listdir(template_dir)):
    template = _load(os.path.join(template_dir, name))
    errors = []
    try:
        CreateTemplateRequestModel(
            **(_requoted(template) if quoting == "requoted" else template))
    except Exception as e:
        errors.append(str(e))
    for index, field in enumerate(template.get("tagSchema") or []):
        try:
            TemplateTagFieldModel(**field)
        except Exception as e:
            errors.append("tagSchema[%d] %s: %s" % (index, field.get("tagKey"), e))
    result["templates"][name] = errors
    result["tagSchema"][name] = validate_tag_schema(template.get("tagSchema"))

sys.stdout.write("<<<RESULT>>>" + json.dumps(result))
'''

_RESULT_MARKER = "<<<RESULT>>>"


def _run_validators(quoting, schema_dir=_SCHEMA_DIR, pipeline_id=_PIPELINE_ID):
    """Per-file verdicts from the real backend validators, or a skip when they cannot be loaded."""
    if not os.path.isdir(_BACKEND_DIR):
        pytest.skip(f"backend models are not present at {_BACKEND_DIR}")
    completed = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [sys.executable, "-c", _VALIDATOR_SCRIPT, _BACKEND_DIR, schema_dir, pipeline_id,
         _LAMBDA_RESOURCE_ID, quoting],
        capture_output=True, text=True, timeout=300,
    )  # nosemgrep: dangerous-subprocess-use-audit
    if _RESULT_MARKER not in completed.stdout:
        pytest.skip(
            "the backend validators could not be loaded "
            f"(exit {completed.returncode}): {completed.stderr[-500:]}")
    return json.loads(completed.stdout.split(_RESULT_MARKER, 1)[1])


@pytest.fixture(scope="module")
def backend_verdicts():
    """Verdicts over the bundle as authored."""
    return _run_validators("authored")


@pytest.fixture(scope="module")
def requoted_verdicts():
    """Verdicts over the same bundle with every typed placeholder quoted."""
    return _run_validators("requoted")


@pytest.fixture(scope="module")
def refine_verdicts():
    """Verdicts over the refine bundle, which the reconstruct-then-refine workflow's second step registers."""
    return _run_validators("authored", _REFINE_SCHEMA_DIR, _REFINE_PIPELINE_ID)


def _template_names():
    return sorted(name for name in os.listdir(_TEMPLATE_DIR) if name.endswith(".json"))


def _template(name):
    with open(os.path.join(_TEMPLATE_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def _schema_file(name):
    with open(os.path.join(_SCHEMA_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
class TestTheBundlePassesTheRealValidators:
    def test_the_pipeline_record_is_accepted(self, backend_verdicts):
        assert backend_verdicts["pipeline"] is None, backend_verdicts["pipeline"]

    def test_the_workflow_record_is_accepted(self, backend_verdicts):
        assert backend_verdicts["workflow"] is None, backend_verdicts["workflow"]

    @pytest.mark.parametrize("name", _template_names())
    def test_the_template_request_is_accepted(self, backend_verdicts, name):
        """Covers the configBody type-driven quoting gate, the inputInstructions length bound, and
        every per-tag-field bound in one instantiation."""
        assert backend_verdicts["templates"][name] == [], backend_verdicts["templates"][name]

    @pytest.mark.parametrize("name", _template_names())
    def test_the_declared_tag_schema_is_accepted(self, backend_verdicts, name):
        assert backend_verdicts["tagSchema"][name] == [], backend_verdicts["tagSchema"][name]

    @pytest.mark.parametrize("name", _template_names())
    def test_quoting_a_typed_placeholder_is_what_the_gate_refuses(self, requoted_verdicts, name):
        """The counter-case, so a passing verdict above is evidence the gate is live rather than
        evidence it is absent: re-quoting the typed placeholders must be refused."""
        typed = [field for field in _template(name)["tagSchema"]
                 if field["type"] in _TYPED_TAG_TYPES]
        assert typed, name
        errors = " ".join(requoted_verdicts["templates"][name])
        assert errors, f"{name}: the quoted form was accepted"
        assert "configBody" in errors, errors


@pytest.mark.unit
class TestTheRefineBundlePassesTheRealValidators:
    def test_the_pipeline_record_is_accepted(self, refine_verdicts):
        assert refine_verdicts["pipeline"] is None, refine_verdicts["pipeline"]

    def test_the_workflow_record_is_accepted(self, refine_verdicts):
        assert refine_verdicts["workflow"] is None, refine_verdicts["workflow"]

    @pytest.mark.parametrize("name", sorted(os.listdir(os.path.join(_REFINE_SCHEMA_DIR, "templates"))))
    def test_every_template_and_its_tag_schema_is_accepted(self, refine_verdicts, name):
        assert refine_verdicts["templates"][name] == [], refine_verdicts["templates"][name]
        assert refine_verdicts["tagSchema"][name] == [], refine_verdicts["tagSchema"][name]


@pytest.mark.unit
class TestTheBundleDeclaresItsIntent:
    """Checks the bundle's own declarations rather than what a validator would accept: registration
    fills an omitted systemConfig key with a documented default, so an omission is silent."""

    _METADATA_INPUT_KEYS = ("assetMetadata", "fileMetadata", "fileAttributes", "databaseMetadata")

    @pytest.mark.parametrize("name", ("pipeline.json", "workflow.json"))
    def test_every_metadata_input_key_is_declared_explicitly(self, name):
        """A default that changes later would otherwise change what an existing bundle consumes."""
        metadata_inputs = _schema_file(name)["systemConfig"]["metadataInputs"]
        assert set(metadata_inputs) == set(self._METADATA_INPUT_KEYS)
        assert all(isinstance(value, bool) for value in metadata_inputs.values())

    @pytest.mark.parametrize("name", ("pipeline.json", "workflow.json"))
    def test_the_pipeline_and_workflow_agree_on_what_they_consume(self, name):
        """A workflow gate narrower than the pipeline's starves the step of a scope it reads."""
        assert (_schema_file("workflow.json")["systemConfig"]["metadataInputs"]
                == _schema_file("pipeline.json")["systemConfig"]["metadataInputs"])

    def test_the_workflow_admits_every_file_type_some_template_can_take(self):
        """A filter only ever narrows down the chain workflow -> pipeline -> template, and the workflow's
        is applied first. The pipeline's list was widened to meshes for the Blender agent's refine mode
        while the workflow's still said images, and the first refine run was refused at the door --
        "One or more input files fail the workflow input-file filters" -- with the pipeline and template
        both willing. The upload TRIGGER keeps its own narrower list deliberately: a mesh landing in an
        asset must not start a reconstruction of it."""
        pipeline_allow = set(_schema_file("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"])
        workflow_allow = set(_schema_file("workflow.json")["systemConfig"]["inputFileFilters"]["allow"])
        assert pipeline_allow <= workflow_allow, f"workflow refuses {pipeline_allow - workflow_allow}"
        for name in _template_names():
            overrides = _template(name).get("overrides") or {}
            template_allow = set((overrides.get("inputFileFilters") or {}).get("allow") or pipeline_allow)
            assert template_allow <= pipeline_allow, (
                f"{name} admits {template_allow - pipeline_allow}, which the pipeline refuses")

    def test_no_metadata_scope_is_consumed(self):
        """Run options come only from the template's configuration body.

        The declaration is what makes the workflow gather metadata and pass its location to the
        container. Leaving a scope declared while nothing reads it costs a fetch on every run and, worse,
        implies an input channel that does not exist -- the model key picks the container image in the
        Lambda and the adapter inside the container, so a second source has to agree in both places."""
        for name in ("pipeline.json", "workflow.json"):
            scopes = _schema_file(name)["systemConfig"]["metadataInputs"]
            assert not any(scopes.values()), f"{name} still declares {scopes}"

    def test_the_system_config_boolean_gates_are_real_booleans(self):
        """A truthy string such as "false" is stored and read back as True, inverting the gate."""
        system_config = _schema_file("pipeline.json")["systemConfig"]
        for key in ("requireTemplate", "allowCustomTemplateOverride"):
            assert isinstance(system_config[key], bool), key

    @pytest.mark.parametrize("name", ("pipeline.json", "workflow.json"))
    def test_no_name_or_category_carries_a_control_character(self, name):
        """Both are ABAC constraint fields matched as ^value$, and both reach single-line logs."""
        record = _schema_file(name)
        fields = [record.get("pipelineName"), record.get("workflowName"), record.get("category")]
        for value in [f for f in fields if f]:
            assert not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in value), value
