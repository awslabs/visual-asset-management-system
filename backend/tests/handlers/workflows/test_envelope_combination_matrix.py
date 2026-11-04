# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workflow-execution input envelope over every combination of the settings that shape it.

Six metadataInputs keys (the four metadata types and the assetFileMetadata / metadataSchema opt-in
sections) are set at three levels: the workflow, the pipeline, and a chosen template's `overrides`
(which replaces the pipeline's map). Each step receives a section only when the workflow and its own
effective gate both have it; with neither opt-in key on at both levels a step receives exactly the bytes
the execute path wrote before the keys existed. These are the durable guards for that contract:

  - byte identity against digests of the previous code, over every map of the four types at the
    workflow and at the step, for the opt-in keys off, absent, or on at one level only;
  - the two-level gate, exhaustively over the six keys, against an independent model of what a step
    receives (`_expected`);
  - which map is in force for a step (pipeline vs template overrides) and what the run gathers, over
    every opt-in combination of a one- and a two-step workflow;
  - whole launches through the handler, pairwise over the workflow, pipeline and template maps, the
    selection, inputFileArity and the execution types: the envelope each step reads, the files the run
    lists, the recorded rows and the Step Functions input;
  - expanded records never counting as inputs (arity, filters, the per-execution input limit);
  - for every file record the envelopes carry, files without an extension included, the schema
    section's pick rule giving the fields the metadata service validates that file against;
  - every execution type reading its envelope through the manifest;
  - every built-in pipeline registration requesting neither opt-in key, so its input is unchanged.

The counts each test asserts are the number of cases it ran."""

import copy
import glob
import itertools
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.tests import envelopeMatrixFixtures as fx
from backend.tests.workflowDeploymentStub import with_current_deployment
from backend.backend.common import metadataSchemaValidation as msv
from backend.backend.common.workflows import executionValidation as ev
from backend.backend.common.workflows import workflowAsl
from backend.backend.common.workflows import workflowAslBuilder as asl_builder
from backend.backend.models import pipelines as pipeline_models

ew = fx.ew
er = fx.er
MOD = fx.MOD
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden",
                      "envelope_matrix_without_opt_in_keys.json")
REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))


def _golden():
    with open(GOLDEN, encoding="utf-8") as handle:
        return json.load(handle)["selections"]


#######################
# The model of what a step receives
#######################

def _expected(full, intake, step):
    """What a step receives, derived from the envelope gathered with everything on (`full`) by the
    contract alone: `intake` is the gate the run gathered under, `step` the step's own gate, both as
    the six booleans. A type the run did not gather reads as an empty map; one the step's gate removes
    reads as null (asset and file metadata) or is absent (attributes, databases, schemas); expanded
    records exist only when the run expanded (assetFileMetadata with file metadata or attributes) and
    the step asks for them."""
    expanded = intake["assetFileMetadata"] and (intake["fileMetadata"] or intake["fileAttributes"])
    take_expanded = expanded and step["assetFileMetadata"]
    envelope = {"schemaVersion": full["schemaVersion"], "assets": []}
    for group in full["assets"]:
        out = {k: v for k, v in group.items() if k not in ("files", "fileExpansion")}
        files = []
        for record in group["files"]:
            if record.get("expanded") and not take_expanded:
                continue
            kind = "assetMetadata" if record["fileKey"] == "/" else "fileMetadata"
            result = {"fileKey": record["fileKey"]}
            if not step[kind]:
                result["metadata"] = None
            else:
                result["metadata"] = record["metadata"] if intake[kind] else {}
            if kind == "fileMetadata" and intake["fileAttributes"] and step["fileAttributes"]:
                result["attributes"] = record.get("attributes", {})
            if record.get("expanded"):
                result["expanded"] = True
            files.append(result)
        out["files"] = files
        if "fileExpansion" in group and take_expanded:
            out["fileExpansion"] = group["fileExpansion"]
        envelope["assets"].append(out)
    if "databases" in full and intake["databaseMetadata"] and step["databaseMetadata"]:
        envelope["databases"] = full["databases"]
    if "metadataSchemas" in full and intake["metadataSchema"] and step["metadataSchema"]:
        envelope["metadataSchemas"] = full["metadataSchemas"]
    return envelope


_ALL_ON = {k: True for k in fx.SIX_KEYS}
_FULL = {}


def _full(selection):
    if selection not in _FULL:
        _FULL[selection] = fx.build_envelope(_ALL_ON, selection)
    return _FULL[selection]


def _step_record(metadata_inputs, pipeline_id="p1"):
    return {"databaseId": "db1", "pipelineId": pipeline_id,
            "systemConfig": {"metadataInputs": dict(metadata_inputs)}}


#######################
# Byte identity with the opt-in keys off
#######################

# (workflow opt-in map, step opt-in map) with neither key on at both levels.
_OFF_VARIANTS = {
    "both-absent": ({}, {}),
    "both-false": ({"assetFileMetadata": False, "metadataSchema": False},
                   {"assetFileMetadata": False, "metadataSchema": False}),
    "workflow-on-no-step-asks": ({"assetFileMetadata": True, "metadataSchema": True}, {}),
    "step-asks-workflow-off": ({}, {"assetFileMetadata": True, "metadataSchema": True}),
    "step-asks-workflow-false": ({"assetFileMetadata": False, "metadataSchema": False},
                                 {"assetFileMetadata": True, "metadataSchema": True}),
    "mixed-false-and-absent": ({"assetFileMetadata": False}, {"metadataSchema": False}),
    "each-on-at-one-level": ({"metadataSchema": True, "assetFileMetadata": False},
                             {"assetFileMetadata": True, "metadataSchema": False}),
}


@pytest.mark.unit
class TestByteIdentityWithTheOptInKeysOff:
    """For every map of the four metadata types at the workflow (81) and at the step (81), each type
    on, off or omitted, the shared envelope and every delivered envelope are the bytes the previous
    code wrote, whenever no opt-in key is on at both levels."""

    @pytest.mark.parametrize("selection", sorted(fx.SELECTIONS))
    @pytest.mark.parametrize("variant", sorted(_OFF_VARIANTS))
    def test_every_four_type_combination_matches_the_previous_code(self, selection, variant):
        golden = _golden()[selection]
        workflow_opt_in, step_opt_in = _OFF_VARIANTS[variant]
        four = fx.combos(fx.FOUR_KEYS)
        cases = 0
        for workflow_four in four:
            wid = fx.combo_id(workflow_four, fx.FOUR_KEYS)
            workflow_map = {**workflow_four, **workflow_opt_in}
            step_maps = [{**step_four, **step_opt_in} for step_four in four]
            s3 = MagicMock()
            s3.list_objects_v2.side_effect = fx.list_objects
            intake = ew._intake_metadata_inputs(
                workflow_map, [_step_record(m, f"p{i}") for i, m in enumerate(step_maps)], {})
            # Some step asks for an opt-in key only in the variants whose workflow has it off, so the
            # run gathers no opt-in section in any of them.
            envelope = fx.build_envelope(intake, selection, s3=s3)
            assert fx.digest(fx.envelope_bytes(envelope)) == golden["shared"][wid], (variant, wid)
            assert fx.row_digest([fx.envelope_bytes(fx.delivered(envelope, m)) for m in step_maps]) \
                == golden["delivered"][wid], (variant, wid)
            s3.list_objects_v2.assert_not_called()
            cases += len(step_maps)
        assert cases == 81 * 81

    def test_the_golden_covers_every_selection_and_map(self):
        golden = _golden()
        assert sorted(golden) == sorted(fx.SELECTIONS)
        for record in golden.values():
            assert len(record["shared"]) == len(record["delivered"]) == 81
            assert len(record["pairs"]) == 256


#######################
# The two-level gate, exhaustively
#######################

@pytest.mark.unit
class TestTwoLevelGateProduct:
    """Every workflow map of the six keys (each on, off or omitted) against every boolean step map, and
    every boolean workflow map against every step map with omitted keys: the step receives exactly what
    _expected says."""

    SELECTION = "mixed"  # a file, a whole asset and a folder in two databases, plus a source asset

    @classmethod
    def _run(cls, workflow_maps, step_maps):
        full = _full(cls.SELECTION)
        steps = [(m, fx.normalized(m), [_step_record(m)]) for m in step_maps]
        cases = 0
        for workflow_map in workflow_maps:
            workflow_gate = fx.normalized(workflow_map)
            for step_map, step_gate, records in steps:
                intake = ew._intake_metadata_inputs(workflow_map, records, {})
                expected_intake = {k: workflow_gate[k] and (k not in fx.OPT_IN_KEYS or step_gate[k])
                                   for k in fx.SIX_KEYS}
                assert fx.normalized(intake) == expected_intake, (workflow_map, step_map)
                built_key = json.dumps(intake, sort_keys=True)
                if built_key not in _BUILT:
                    _BUILT[built_key] = fx.build_envelope(intake, cls.SELECTION)
                received = fx.delivered(_BUILT[built_key], step_map)
                assert received == _expected(full, expected_intake, step_gate), (workflow_map, step_map)
                cases += 1
        return cases

    def test_every_workflow_map_against_every_boolean_step_map(self):
        assert self._run(fx.combos(fx.SIX_KEYS), fx.combos(fx.SIX_KEYS, (True, False))) == 729 * 64

    def test_every_boolean_workflow_map_against_every_step_map(self):
        assert self._run(fx.combos(fx.SIX_KEYS, (True, False)), fx.combos(fx.SIX_KEYS)) == 64 * 729


_BUILT = {}


#######################
# Which map is in force, and what the run gathers
#######################

def _effective(pipeline_map, template_map):
    """The step's map: a template's metadataInputs replaces the pipeline's whole map."""
    return pipeline_map if template_map is None else template_map


@pytest.mark.unit
class TestTemplateOverridesAndWorkflowGate:

    _OPT = fx.combos(fx.OPT_IN_KEYS)
    _TEMPLATES = [None] + fx.combos(fx.OPT_IN_KEYS)

    @staticmethod
    def _resolved(steps):
        return {er.pipeline_composite_key("db1", f"p{i}"): {"templateOverrides": (
                    {} if template is None else {"metadataInputs": template})}
                for i, (_pipeline, template) in enumerate(steps)}

    def test_every_opt_in_combination_of_a_one_and_a_two_step_workflow(self):
        cases = 0
        for workflow_map in self._OPT:
            for steps in itertools.chain(
                    ([s] for s in itertools.product(self._OPT, self._TEMPLATES)),
                    itertools.product(itertools.product(self._OPT, self._TEMPLATES), repeat=2)):
                steps = list(steps)
                records = [_step_record(pipeline, f"p{i}") for i, (pipeline, _t) in enumerate(steps)]
                resolved = self._resolved(steps)
                intake = ew._intake_metadata_inputs(workflow_map, records, resolved)
                effective = [_effective(p, t) for p, t in steps]
                for key in fx.OPT_IN_KEYS:
                    asks = any(fx.normalized(m)[key] for m in effective)
                    assert fx.normalized(intake)[key] == (fx.normalized(workflow_map)[key] and asks)
                for i, record in enumerate(records):
                    overrides = resolved[er.pipeline_composite_key("db1", f"p{i}")]["templateOverrides"]
                    step_map = ev.resolve_effective_pipeline_config(
                        record["systemConfig"], overrides)["metadataInputs"]
                    assert step_map == effective[i]
                cases += 1
        assert cases == 9 * (90 + 90 * 90)

    @pytest.mark.parametrize("pipeline_map", [{}, {k: False for k in fx.SIX_KEYS},
                                              {k: True for k in fx.SIX_KEYS}],
                             ids=["absent", "all-false", "all-true"])
    def test_a_template_map_replaces_the_pipeline_map_whatever_it_omits(self, pipeline_map):
        cases = 0
        for template_map in fx.combos(fx.SIX_KEYS):
            effective = ev.resolve_effective_pipeline_config(
                {"metadataInputs": pipeline_map}, {"metadataInputs": template_map})
            # An omitted key reads at its default, not at the pipeline's value.
            assert fx.normalized(effective["metadataInputs"]) == fx.normalized(template_map)
            cases += 1
        assert cases == 729

    @pytest.mark.parametrize("template", [None, {}, {"inputFileArity": "multi"}],
                             ids=["no-template", "empty-overrides", "overrides-without-metadata"])
    def test_without_a_template_map_the_pipeline_map_is_in_force(self, template):
        cases = 0
        for pipeline_map in fx.combos(fx.SIX_KEYS):
            effective = ev.resolve_effective_pipeline_config({"metadataInputs": pipeline_map}, template)
            assert effective["metadataInputs"] == pipeline_map
            cases += 1
        assert cases == 729


#######################
# Whole launches, pairwise
#######################

def allpairs(parameters):
    """A deterministic greedy all-pairs set over {name: [values]}: every pair of values of every two
    parameters appears in at least one case."""
    names = list(parameters)
    uncovered = {(a, va, b, vb) for ia, a in enumerate(names) for b in names[ia + 1:]
                 for va in range(len(parameters[a])) for vb in range(len(parameters[b]))}
    cases = []
    while uncovered:
        a, va, b, vb = min(uncovered)
        case = {a: va, b: vb}
        for name in names:
            if name in case:
                continue
            best, best_gain = 0, -1
            for value in range(len(parameters[name])):
                gain = sum(1 for other, ov in case.items()
                           if ((other, ov, name, value) if names.index(other) < names.index(name)
                               else (name, value, other, ov)) in uncovered)
                if gain > best_gain:
                    best, best_gain = value, gain
            case[name] = best
        for ia, x in enumerate(names):
            for y in names[ia + 1:]:
                uncovered.discard((x, case[x], y, case[y]))
        cases.append({name: parameters[name][case[name]] for name in names})
    return cases


_EXECUTION_CONFIGS = {
    "lambda-sync": {"executionType": "Lambda", "lambda": {"resourceId": "fn"},
                    "waitForCallback": "Disabled"},
    "lambda-async": {"executionType": "Lambda", "lambda": {"resourceId": "fn"},
                     "waitForCallback": "Enabled"},
    "sqs": {"executionType": "SQS",
            "sqs": {"queueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/vams-q"},
            "waitForCallback": "Disabled"},
    "sqs-callback": {"executionType": "SQS",
                     "sqs": {"queueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/vams-q"},
                     "waitForCallback": "Enabled"},
    "eventbridge-default-bus": {"executionType": "EventBridge", "eventBridge": {},
                                "waitForCallback": "Disabled"},
    "eventbridge-default-bus-callback": {"executionType": "EventBridge", "eventBridge": {},
                                         "waitForCallback": "Enabled"},
    "eventbridge-named-bus": {
        "executionType": "EventBridge",
        "eventBridge": {"busArn": "arn:aws:events:us-east-1:123456789012:event-bus/vams-bus"},
        "waitForCallback": "Disabled"},
    "eventbridge-named-bus-callback": {
        "executionType": "EventBridge",
        "eventBridge": {"busArn": "arn:aws:events:us-east-1:123456789012:event-bus/vams-bus"},
        "waitForCallback": "Enabled"},
    "deadlinecloud": {"executionType": "DeadlineCloud",
                      "deadlineCloud": {"farmId": "farm-0123456789abcdef0123456789abcdef",
                                        "queueId": "queue-0123456789abcdef0123456789abcdef",
                                        "template": "specificationVersion: jobtemplate-2023-09\n"
                                                    "name: vams-step\nsteps: []\n"},
                      "waitForCallback": "Enabled"},
}
_SCOPE = {"crossAssetAllowed": True, "singleAssetOnly": False, "wholeAssetAllowed": True,
          "folderAllowed": True}


def _pipeline(pipeline_id, metadata_inputs, arity, execution, allow=None):
    return {
        "databaseId": "db1", "pipelineId": pipeline_id, "pipelineName": pipeline_id,
        "enabled": True, "archived": False,
        "executionConfig": copy.deepcopy(_EXECUTION_CONFIGS[execution]),
        "systemConfig": {"inputFileArity": arity, "requireTemplate": False,
                         "allowCustomTemplateOverride": False, "assetScope": dict(_SCOPE),
                         "inputFileFilters": {"allow": allow or [], "exclude": []},
                         "metadataInputs": dict(metadata_inputs)},
    }


def _workflow(metadata_inputs, pipelines):
    workflow = {
        "databaseId": "db1", "workflowId": "wf1", "workflowName": "WF", "enabled": True,
        "archived": False, "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:vams-wf1",
        "specifiedPipelines": [{"pipelineDatabaseId": "db1", "pipelineId": p["pipelineId"],
                                "jobName": p["pipelineId"]} for p in pipelines],
        "jobNames": [f"uuid-{p['pipelineId']}" for p in pipelines],
        "systemConfig": {
            "inputFileArity": "multi", "assetScope": dict(_SCOPE),
            "metadataInputs": dict(metadata_inputs),
            "inputFileFilters": {"allow": [], "exclude": []},
            "concurrencyRestriction": "none",
            "outputTarget": {"locationType": "asset", "allowOverride": True},
        },
    }
    return with_current_deployment(workflow, pipelines)


class _Launch:
    """One handler run with every boundary stubbed, and what it wrote."""

    def __init__(self, workflow, pipelines, inputs, templates=None, listings=None):
        self.workflow = workflow
        self.pipelines = pipelines
        self.inputs = inputs
        self.templates = templates or {}
        self.listings = listings
        self.tables = {}
        self.s3 = MagicMock()
        self.s3.list_objects_v2.side_effect = self._list
        self.puts = {}
        self.s3.put_object.side_effect = lambda **kw: self.puts.__setitem__(kw["Key"], kw["Body"])
        self.sfn = MagicMock()
        self.sfn.start_execution.return_value = {"executionArn": "arn:exec"}

    def _list(self, **kwargs):
        if self.listings is None:
            return fx.list_objects(**kwargs)
        prefix = kwargs["Prefix"]
        keys = sorted(k for k in self.listings if k.startswith(prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def _resolve(self, real):
        def _call(pipeline_records, params, bucket):
            errors, resolved = real(pipeline_records, params, bucket)
            for record in pipeline_records:
                template = self.templates.get(record["pipelineId"])
                composite = er.pipeline_composite_key(record["databaseId"], record["pipelineId"])
                if template is not None and composite in resolved:
                    resolved[composite]["templateId"] = f"t-{record['pipelineId']}"
                    resolved[composite]["templateOverrides"] = {"metadataInputs": dict(template)}
            return errors, resolved
        return _call

    def run(self):
        by_id = {p["pipelineId"]: p for p in self.pipelines}
        assets = {(i["databaseId"], i["assetId"]) for i in self.inputs}
        body = {"inputFiles": [{k: i[k] for k in ("databaseId", "assetId", "relativeFileKey")}
                               for i in self.inputs]}
        if len(assets) > 1:
            first = self.inputs[0]
            body["outputAssetId"], body["outputDatabaseId"] = first["assetId"], first["databaseId"]
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/workflows/db1/wf1/execute"},
                               "authorizer": {}},
            "pathParameters": {"workflowDatabaseId": "db1", "workflowId": "wf1"},
            "queryStringParameters": {}, "headers": {"authorization": "Bearer t"},
            "body": json.dumps(body),
        }
        casbin = MagicMock()
        casbin.enforce.return_value = True
        casbin.enforceAPI.return_value = True
        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: self.tables.setdefault(name, MagicMock())
        database_table = self.tables.setdefault(ew.database_table_name, MagicMock())
        database_table.get_item.side_effect = lambda Key, **_k: {"Item": {
            "restrictMetadataOutsideSchemas": Key["databaseId"] in fx.RESTRICTING_DATABASES}}
        real_resolve = ew._resolve_pipeline_configs
        with patch(f"{MOD}._get_workflow", return_value=self.workflow), \
             patch(f"{MOD}._get_pipeline", side_effect=lambda db, pid: copy.deepcopy(by_id[pid])), \
             patch(f"{MOD}._get_asset", side_effect=lambda db, a: fx.asset_record(db, a)), \
             patch(f"{MOD}._default_run_bucket",
                   return_value={"bucketName": "run-bucket", "baseAssetsPrefix": ""}), \
             patch(f"{MOD}._asset_bucket_details",
                   return_value={"bucketName": "asset-bucket", "baseAssetsPrefix": ""}), \
             patch(f"{MOD}._input_exists_in_s3", return_value=(True, "v-resolved")), \
             patch(f"{MOD}._resolve_pipeline_configs", side_effect=self._resolve(real_resolve)), \
             patch(f"{MOD}.CasbinEnforcer", return_value=casbin), \
             patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
             patch(f"{MOD}._fetch_metadata",
                   side_effect=lambda d, a, _q, _e: fx.READS.get(("asset", d, a), [])), \
             patch(f"{MOD}._fetch_file_metadata",
                   side_effect=lambda d, a, r, kind, _e: fx.READS.get((kind, d, a, r), [])), \
             patch(f"{MOD}._fetch_database_metadata",
                   side_effect=lambda d, _e: fx.READS.get(("database", d), [])), \
             patch(f"{MOD}.load_enabled_schemas",
                   side_effect=lambda d, t, _c, _t: copy.deepcopy(fx.SCHEMAS.get((d, t), []))), \
             patch(f"{MOD}.DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED", True), \
             patch(f"{MOD}.s3c", self.s3), patch(f"{MOD}.sfn_client", self.sfn), \
             patch(f"{MOD}.dynamodb", dynamodb):
            self.response = ew.lambda_handler(event, MagicMock())
        self.status = self.response["statusCode"]
        self.body = json.loads(self.response["body"])
        return self

    # What the run wrote.
    def sfn_input(self):
        return json.loads(self.sfn.start_execution.call_args.kwargs["input"])

    def execution_id(self):
        return self.sfn_input()["workflowExecutionId"]

    def shared_envelope(self):
        return json.loads(self.puts[er.execution_input_metadata_key(self.execution_id())])

    def step_envelope(self, index):
        key = er.pipeline_input_metadata_key(self.execution_id(), index)
        return json.loads(self.puts[key]) if key in self.puts else self.shared_envelope()

    def step_has_own_file(self, index):
        return er.pipeline_input_metadata_key(self.execution_id(), index) in self.puts

    def first_manifest(self):
        return json.loads(self.puts[er.pipeline_input_manifest_key(self.execution_id(), 1)])

    def input_rows(self):
        return [c.kwargs["Item"] for c in self.tables["t-wf-inputs"].put_item.call_args_list]

    def metadata_rows_by_step(self):
        pexec = [c.kwargs["Item"] for c in self.tables["t-pexec"].put_item.call_args_list]
        order = {r["pipelineExecutionId"]: i + 1 for i, r in enumerate(pexec)}
        writer = self.tables["t-pin-md"].batch_writer.return_value.__enter__.return_value
        rows = {i + 1: [] for i in range(len(pexec))}
        for call in writer.put_item.call_args_list:
            item = call.kwargs["Item"]
            rows[order[item["pipelineExecutionId"]]].append(item)
        return rows


def _selection_rejected(selection, steps):
    """The launch outcome the selection rules give, independent of the opt-in keys: a single-file step
    handed more than one selected entry is refused (the first step's own count; a later step's when the
    selection alone already gives it more than one)."""
    entries = {(i["databaseId"], i["assetId"], er.normalize_file_key(i["relativeFileKey"]))
               for i in selection}
    return any(step["arity"] == "one" and len(entries) > 1 for step in steps)


def _check_launch(case, steps, workflow_map):
    """Run one case and hold it to the model."""
    selection = fx.LAUNCH_SELECTIONS[case["selection"]]
    pipelines = [_pipeline(f"p{i + 1}", s["pipeline"], s["arity"], s["execution"])
                 for i, s in enumerate(steps)]
    templates = {f"p{i + 1}": s["template"] for i, s in enumerate(steps) if s["template"] is not None}
    launch = _Launch(_workflow(workflow_map, pipelines), pipelines, selection, templates).run()

    if _selection_rejected(selection, steps):
        assert launch.status == 400, launch.body
        assert "accepts a single input file" in json.dumps(launch.body)
        # Refused before anything was gathered: no listing, no run.
        launch.s3.list_objects_v2.assert_not_called()
        launch.sfn.start_execution.assert_not_called()
        return "rejected"
    assert launch.status == 200, launch.body

    effective = [fx.normalized(s["pipeline"] if s["template"] is None else s["template"]) for s in steps]
    workflow_gate = fx.normalized(workflow_map)
    intake = {k: workflow_gate[k] and (k not in fx.OPT_IN_KEYS or any(e[k] for e in effective))
              for k in fx.SIX_KEYS}
    full = _launch_full(case["selection"])

    # The shared file is everything the run gathered; each step reads its own file only when its gate
    # subtracts something, and otherwise the shared one.
    shared = _expected(full, intake, _ALL_ON)
    assert launch.shared_envelope() == shared
    keys = launch.sfn_input()["stepMetadataS3Keys"]
    shared_expands = any("fileExpansion" in g for g in shared["assets"])
    for index, gate in enumerate(effective, start=1):
        received = launch.step_envelope(index)
        assert received == _expected(full, intake, gate), (case, index)
        # A step gets a file of its own exactly when its gate turns a metadata type off or removes a
        # section the shared file carries; every other step reads the one shared file.
        narrows = (not all(gate[k] for k in fx.FOUR_KEYS)
                   or (not gate["assetFileMetadata"] and shared_expands)
                   or (not gate["metadataSchema"] and "metadataSchemas" in shared))
        assert launch.step_has_own_file(index) == narrows, (case, index)
        assert bool(keys[index - 1]) == narrows, (case, index)

    # A listing happens only for an expansion the run performs, and only of the selections it expands.
    expands = intake["assetFileMetadata"] and (intake["fileMetadata"] or intake["fileAttributes"])
    expandable = [i for i in selection if i["relativeFileKey"].endswith("/")]
    assert launch.s3.list_objects_v2.called == bool(expands and expandable), case

    # The files the run lists are the selection: never an expanded file.
    selected = sorted({(i["databaseId"], i["assetId"], er.normalize_file_key(i["relativeFileKey"]))
                       for i in selection})
    # Each input row names its file by the key under the asset's root (assets/<assetId>/).
    assert sorted((r["databaseId"], r["assetId"], r["inputAssetFileKey"])
                  for r in launch.input_rows()) == sorted(
        (d, a, er.normalize_file_key(f"assets/{a}/" + k.lstrip("/"))) for d, a, k in selected)
    manifest_paths = sorted((e["databaseId"], e["assetId"], e["relativePath"])
                            for e in launch.first_manifest()["inputFiles"])
    assert manifest_paths == ([] if steps[0]["arity"] == "none" else selected)

    # Recorded rows: expanded rows only for a step that received them, under a selection it received.
    for index, rows in launch.metadata_rows_by_step().items():
        expanded_rows = [r for r in rows if r.get("expanded")]
        receives = steps[index - 1]["arity"] != "none"
        if not (expands and effective[index - 1]["assetFileMetadata"] and receives):
            assert not expanded_rows, (case, index)
        for row in expanded_rows:
            covering = [i for i in selection if i["databaseId"] == row["databaseId"]
                        and i["assetId"] == row["assetId"]
                        and row["filePath"].startswith(er.normalize_file_key(i["relativeFileKey"]))
                        and i["relativeFileKey"].endswith("/")]
            assert covering, (case, row)
            assert (row["databaseId"], row["assetId"], row["filePath"]) not in selected
    return "launched"


_LAUNCH_FULL = {}


def _launch_full(selection):
    """The envelope a launch gathers with everything on, for the model's reference."""
    if selection not in _LAUNCH_FULL:
        inputs = fx.LAUNCH_SELECTIONS[selection]
        databases = list(dict.fromkeys(i["databaseId"] for i in inputs))
        fx.SELECTIONS[f"launch:{selection}"] = (inputs, [], databases)
        try:
            _LAUNCH_FULL[selection] = fx.build_envelope(_ALL_ON, f"launch:{selection}")
        finally:
            fx.SELECTIONS.pop(f"launch:{selection}")
    return _LAUNCH_FULL[selection]


def _family(step_count, templates):
    """The pairwise parameters of one family: the workflow's six keys, each step's six keys, its
    template (none, or a map of six keys), arity and execution type, and the selection."""
    tri = list(fx.TRI_STATE)
    parameters = {"selection": sorted(fx.LAUNCH_SELECTIONS)}
    parameters.update({f"workflow.{k}": tri for k in fx.SIX_KEYS})
    for step in range(1, step_count + 1):
        parameters.update({f"step{step}.pipeline.{k}": tri for k in fx.SIX_KEYS})
        if templates == "mixed":
            parameters[f"step{step}.template"] = [False, True]
        if templates != "none":
            parameters.update({f"step{step}.template.{k}": tri for k in fx.SIX_KEYS})
        parameters[f"step{step}.arity"] = ["one", "multi", "none"]
        parameters[f"step{step}.execution"] = sorted(_EXECUTION_CONFIGS)
    return allpairs(parameters)


def _steps(case, step_count, templates):
    steps = []
    for step in range(1, step_count + 1):
        has_template = (templates == "every-step"
                        or (templates == "mixed" and case[f"step{step}.template"]))
        steps.append({
            "pipeline": fx.gate_map({k: case[f"step{step}.pipeline.{k}"] for k in fx.SIX_KEYS}),
            "template": (fx.gate_map({k: case[f"step{step}.template.{k}"] for k in fx.SIX_KEYS})
                         if has_template else None),
            "arity": case[f"step{step}.arity"],
            "execution": case[f"step{step}.execution"],
        })
    return steps


@pytest.mark.unit
class TestWholeLaunchesPairwise:
    """Each family is the all-pairs set over its parameters, run through the whole handler."""

    def test_every_execution_config_used_is_one_the_model_accepts(self):
        for name, config in _EXECUTION_CONFIGS.items():
            pipeline_models._validate_execution_config(config)
        # The combination the model refuses is the one left out: Deadline Cloud without the callback.
        with pytest.raises(ValueError, match="waitForCallback must be Enabled"):
            pipeline_models._validate_execution_config(
                {**_EXECUTION_CONFIGS["deadlinecloud"], "waitForCallback": "Disabled"})

    @pytest.mark.parametrize("step_count", [1, 2])
    @pytest.mark.parametrize("templates", ["none", "every-step", "mixed"])
    def test_the_family_holds_to_the_model(self, step_count, templates):
        cases = _family(step_count, templates)
        outcomes = {"launched": 0, "rejected": 0}
        for case in cases:
            workflow_map = fx.gate_map({k: case[f"workflow.{k}"] for k in fx.SIX_KEYS})
            outcomes[_check_launch(case, _steps(case, step_count, templates), workflow_map)] += 1
        # Both outcomes occur, so neither half of the model went untested.
        assert outcomes["launched"] and outcomes["rejected"], outcomes
        assert sum(outcomes.values()) == len(cases)
        _PAIRWISE_COUNTS[(step_count, templates)] = (len(cases), outcomes["launched"])


_PAIRWISE_COUNTS = {}


#######################
# Expanded records are never inputs
#######################

@pytest.mark.unit
class TestExpandedRecordsAreNeverInputs:

    @staticmethod
    def _many(count, root="assets/a2/"):
        return [f"{root}f{i:04d}.glb" for i in range(count)]

    def test_a_single_file_step_takes_a_whole_asset_of_many_files(self):
        gate = {"assetFileMetadata": True}
        pipelines = [_pipeline("p1", gate, "one", "lambda-sync")]
        launch = _Launch(_workflow(gate, pipelines), pipelines, fx.LAUNCH_SELECTIONS["wholeAsset"],
                         listings=self._many(ew.MAX_ASSET_FILE_EXPANSION_PER_SELECTION + 10)).run()
        assert launch.status == 200, launch.body
        group = launch.step_envelope(1)["assets"][0]
        assert group["fileExpansion"]["expandedFileCount"] == ew.MAX_ASSET_FILE_EXPANSION_PER_SELECTION
        assert group["fileExpansion"]["omittedFileCount"] == 10
        assert [e["relativePath"] for e in launch.first_manifest()["inputFiles"]] == ["/"]
        assert [r["inputAssetFileKey"] for r in launch.input_rows()] == ["/assets/a2/"]
        assert any("db1:a2:/ (10 files left out)" in w for w in launch.body["message"]["warnings"])

    def test_the_input_limit_counts_selected_files_only(self):
        gate = {"assetFileMetadata": True, "fileAttributes": False}
        explicit = [fx._input("db1", "a1", f"/x{i:04d}.glb")
                    for i in range(ew.MAX_INPUT_FILES_PER_EXECUTION - 1)]
        selection = explicit + [fx._input("db1", "a2", "/")]
        pipelines = [_pipeline("p1", gate, "multi", "lambda-sync")]
        launch = _Launch(_workflow(gate, pipelines), pipelines, selection,
                         listings=self._many(5)).run()
        # A full selection launches; the expansion takes only what the limit leaves (one record).
        assert launch.status == 200, launch.body
        assert len(launch.input_rows()) == ew.MAX_INPUT_FILES_PER_EXECUTION
        assert len(launch.first_manifest()["inputFiles"]) == ew.MAX_INPUT_FILES_PER_EXECUTION
        expanded = [r for g in launch.step_envelope(1)["assets"] for r in g["files"] if r.get("expanded")]
        assert len(expanded) == 1
        assert any("db1:a2:/ (4 files left out)" in w for w in launch.body["message"]["warnings"])

    def test_a_steps_filters_neither_take_expanded_files_as_inputs_nor_drop_their_records(self):
        gate = {"assetFileMetadata": True}
        selection = [fx._input("db1", "a2", "/")]
        pipelines = [_pipeline("p1", gate, "multi", "lambda-sync", allow=["*.usd"]),
                     _pipeline("p2", gate, "one", "lambda-sync", allow=["*.glb"])]
        launch = _Launch(_workflow(gate, pipelines), pipelines, selection).run()
        # The single-file second step is not overfilled by the three expanded files.
        assert launch.status == 200, launch.body
        # The whole-asset selection is the first step's one input: the .usd under it is context, not
        # a file its filters admitted as an input.
        assert [e["relativePath"] for e in launch.first_manifest()["inputFiles"]] == ["/"]
        # Both steps are handed every expanded record whatever their filters (delivery follows the
        # gate), and the recorded rows say so for each.
        for index in (1, 2):
            files = {r["fileKey"] for r in launch.step_envelope(index)["assets"][0]["files"]
                     if r.get("expanded")}
            assert files == {"/README", "/a.b/LICENSE", "/b.glb", "/sub/c.usd", "/z.txt"}
        rows = launch.metadata_rows_by_step()
        for index in (1, 2):
            assert {r["filePath"] for r in rows[index] if r.get("expanded")} == {
                "/README", "/b.glb", "/sub/c.usd"}


#######################
# The schema section, file by file
#######################

_FILE_SCHEMA_TYPES = (("fileMetadata", "fileMetadata"), ("fileAttributes", "fileAttribute"))
_FULL_ENVELOPES = ([("built", s) for s in sorted(fx.SELECTIONS)]
                   + [("launch", s) for s in sorted(fx.LAUNCH_SELECTIONS)])


def _full_envelope(kind, selection):
    return _launch_full(selection) if kind == "launch" else _full(selection)


def _picked_fields(entries, file_key):
    """The pipeline developer guide's rule over one entity type's schema entries for one file: per
    field, the entry whose fileKeyTypeRestriction lists the file's extension, else the one with an
    empty list, else none (the field does not apply to that file)."""
    extension = msv.extract_file_extension(file_key)
    picked = {}
    for name in dict.fromkeys(e["metadataFieldKeyName"] for e in entries):
        mine = [e for e in entries if e["metadataFieldKeyName"] == name]
        listed = [e for e in mine if extension in e["fileKeyTypeRestriction"]]
        every = [e for e in mine if not e["fileKeyTypeRestriction"]]
        assert len(listed) <= 1 and len(every) <= 1, (name, file_key)
        if listed or every:
            picked[name] = (listed or every)[0]
    return picked


@pytest.mark.unit
class TestSchemaSectionAgreesWithTheMetadataService:
    """For every file record a full envelope carries (selected and expanded, with an extension and
    without one), the schema fields the pick rule gives over that file's database entry are the fields
    the metadata service validates that file against (get_aggregated_schemas over the same schemas)."""

    @staticmethod
    def _check(envelope):
        entries = {e["databaseId"]: e for e in envelope["metadataSchemas"]}
        checked = []
        with patch.object(msv, "load_enabled_schemas", side_effect=lambda d, t, *_a: copy.deepcopy(
                fx.SCHEMAS.get((d, t), []))), \
             patch.object(msv, "_get_from_cache", return_value=None), \
             patch.object(msv, "_set_in_cache"):
            for group in envelope["assets"]:
                for record in group["files"]:
                    if record["fileKey"] == "/":
                        continue
                    for envelope_key, entity_type in _FILE_SCHEMA_TYPES:
                        applied = msv.get_aggregated_schemas(
                            [group["databaseId"], "GLOBAL"], entity_type, record["fileKey"], None, "t")
                        picked = _picked_fields(entries[group["databaseId"]][envelope_key],
                                                record["fileKey"])
                        where = (group["databaseId"], record["fileKey"], envelope_key)
                        assert sorted(picked) == sorted(applied), where
                        for name, field in picked.items():
                            for key in ("metadataFieldValueType", "required",
                                        "metadataSchemaMultiFieldConflict"):
                                assert field[key] == applied[name][key], (where, name, key)
                    checked.append((group["databaseId"], record["fileKey"]))
        return checked

    @pytest.mark.parametrize("kind,selection", _FULL_ENVELOPES,
                             ids=[f"{k}-{s}" for k, s in _FULL_ENVELOPES])
    def test_each_file_picks_what_the_service_applies(self, kind, selection):
        assert self._check(_full_envelope(kind, selection))

    def test_the_files_checked_include_ones_without_an_extension(self):
        checked = {f for kind, selection in _FULL_ENVELOPES
                   for f in self._check(_full_envelope(kind, selection))}
        # Each sits in a database with an extension-restricted schema (db3 also restricts metadata
        # outside its schemas), which is where the service's treatment of such a file shows.
        assert {("db1", "/README"), ("db1", "/a.b/LICENSE"), ("db3", "/LICENSE")} <= checked
        assert all(msv.extract_file_extension(k) is None
                   for k in ("/README", "/a.b/LICENSE", "/LICENSE"))


#######################
# Every execution type reads its envelope through the manifest
#######################

def _asl_pipeline(name, execution):
    """The flat pipeline dict the deployment builds the state machine from, for a pipeline record."""
    return workflowAsl.to_asl_pipeline_dict(_pipeline(name, {}, "multi", execution), job_name=name)


@pytest.mark.unit
class TestEveryExecutionTypeReadsTheManifest:
    """The envelope a step reads is named only in its manifest (and, for a later step, by the per-run
    stepMetadataS3Keys entry the interim step writes into that manifest), so what a step receives does
    not depend on how it is invoked."""

    @pytest.mark.parametrize("first, second", list(itertools.product(sorted(_EXECUTION_CONFIGS), repeat=2)))
    def test_no_task_payload_names_a_metadata_file(self, first, second):
        definition, _names = asl_builder.generate_workflow_asl(
            [_asl_pipeline("p1", first), _asl_pipeline("p2", second)], "db1", "wf1",
            "process-output", "interim", "error-handler")
        states = definition["States"]
        tasks = [s for name, s in states.items() if name.startswith("step")]
        interims = [s for name, s in states.items() if name.startswith("interim-")]
        assert len(tasks) == 2 and len(interims) == 1
        for task in tasks:
            text = json.dumps(task)
            assert "manifest.json" in text
            assert "metadata.json" not in text and "stepMetadataS3Keys" not in text
        interim = json.dumps(interims[0])
        assert '"nextPipelineMetadataS3Key.$": "$.stepMetadataS3Keys[1]"' in interim


#######################
# Built-in pipelines
#######################

def _bundle_dirs():
    root = os.path.join(REPO, "backendPipelines")
    found = {os.path.dirname(p) for p in glob.glob(os.path.join(root, "**", "vamsSchema", "**",
                                                                 "pipeline.json"), recursive=True)}
    found |= {os.path.dirname(p) for p in glob.glob(os.path.join(root, "**", "vamsSchema",
                                                                 "pipeline.json"), recursive=True)}
    return sorted(d for d in found if "node_modules" not in d)


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _bundle(directory):
    pipeline = _load(os.path.join(directory, "pipeline.json"))
    workflow_path = os.path.join(directory, "workflow.json")
    workflow = _load(workflow_path) if os.path.exists(workflow_path) else None
    templates = {}
    for path in sorted(glob.glob(os.path.join(directory, "templates", "*.json"))):
        if not path.endswith(".webform.json"):
            templates[os.path.basename(path)] = _load(path)
    return pipeline, workflow, templates


def _metadata_inputs(record):
    return ((record or {}).get("systemConfig") or {}).get("metadataInputs") or {}


@pytest.mark.unit
class TestBuiltInPipelinesAreUnchanged:
    """Every pipeline the deployment registers (each vamsSchema bundle under backendPipelines/, all
    registered as system records in GLOBAL) asks for neither opt-in key in its pipeline, its templates'
    overrides or its workflow, and receives for a file, a folder and a whole-asset run the bytes the
    previous code delivered for its gates."""

    BUNDLES = _bundle_dirs()

    def test_the_discovery_finds_the_shipped_bundles(self):
        # 29 registrations ship today; fewer means the glob broke, not that bundles were removed.
        assert len(self.BUNDLES) >= 29, len(self.BUNDLES)

    @pytest.mark.parametrize("directory", BUNDLES, ids=lambda d: os.path.relpath(d, REPO))
    def test_the_bundle_requests_no_opt_in_section_and_receives_what_it_did(self, directory):
        pipeline, workflow, templates = _bundle(directory)
        maps = {"pipeline": _metadata_inputs(pipeline), "workflow": _metadata_inputs(workflow)}
        for name, template in templates.items():
            maps[f"template {name}"] = ((template.get("overrides") or {}).get("metadataInputs") or {})
        for where, metadata_inputs in maps.items():
            for key in fx.OPT_IN_KEYS:
                assert er.metadata_input_enabled(metadata_inputs, key) is False, (directory, where, key)

        golden = _golden()
        # A registration with no workflow, or one whose workflow omits the map, runs under the
        # workflow defaults the importer fills: every type on.
        workflow_map = maps["workflow"]
        step_maps = [maps["pipeline"]] + [
            m for where, m in maps.items() if where.startswith("template ") and m]
        cases = 0
        for selection in ("file", "folder", "wholeAsset"):
            intake = ew._intake_metadata_inputs(
                workflow_map, [_step_record(m, f"p{i}") for i, m in enumerate(step_maps)], {})
            s3 = MagicMock()
            envelope = fx.build_envelope(intake, selection, s3=s3)
            s3.list_objects_v2.assert_not_called()
            workflow_id = fx.combo_id({k: fx.normalized(workflow_map)[k] for k in fx.FOUR_KEYS},
                                      fx.FOUR_KEYS)
            for step_map in step_maps:
                step_id = fx.combo_id({k: fx.normalized(step_map)[k] for k in fx.FOUR_KEYS},
                                      fx.FOUR_KEYS)
                received = fx.envelope_bytes(fx.delivered(envelope, step_map))
                assert fx.digest(received) == golden[selection]["pairs"][f"{workflow_id}|{step_id}"], (
                    directory, selection, step_map)
                cases += 1
        assert cases == 3 * len(step_maps)
