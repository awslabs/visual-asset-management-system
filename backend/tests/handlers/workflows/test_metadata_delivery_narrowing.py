# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-step DELIVERY narrowing of the input metadata (the two-level metadataInputs contract).

The workflow's metadataInputs is INTAKE: it decides what an execution gathers into the shared
per-execution envelope. A pipeline's own metadataInputs (with its chosen template's overrides
applied) is DELIVERY: it decides what that ONE step receives. A type reaches a step only when both
levels have it on.

Delivery is enforced server-side — pipelines stay dumb and simply read the metadata location their
manifest names. A step whose gate narrows the envelope gets its own metadata.json and its manifest
points there; a step that wants everything the workflow gathered keeps reading the shared file, so
the common case stays a single object.

Both delivery points are covered, for both the step-1 (launch) and steps-2+ (interim) paths:
  - the metadata FILE the step reads
  - the template TAGS the step's config renders
"""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# executeWorkflow loads these at import (mirrors test_executeWorkflow.py).
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates")
os.environ.setdefault("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
# Additionally read at import by the interim-tracking lambda (steps 2+ delivery).
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_EXECUTION_LOG_GROUP_ARN",
                      "arn:aws:logs:us-east-1:1:log-group:vams-wf:*")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.common.workflows import executionRecords as er
from backend.backend.common.workflows import executionValidation as ev
from backend.backend.handlers.workflows import executeWorkflow as ewv2

MOD = "backend.backend.handlers.workflows.executeWorkflow"

ASSET_MD = {"ASSET_KEY": "asset-value"}
FILE_MD = {"FILE_KEY": "file-value"}
FILE_ATTRS = {"fps": "30"}
DB_MD = {"DB_KEY": "db-value"}


def _full_envelope():
    """A v2 envelope carrying all four metadata types for one asset + one database."""
    group = er.build_metadata_asset_group(
        "db1", "a1", asset_data={"assetName": "A"},
        files=[
            er.build_metadata_file_record("/", metadata=dict(ASSET_MD)),
            er.build_metadata_file_record("/clips/in.mp4", metadata=dict(FILE_MD),
                                          attributes=dict(FILE_ATTRS)),
        ])
    return er.build_grouped_metadata_envelope(
        [group], databases=[er.build_metadata_database_group("db1", metadata=dict(DB_MD))])


def _asset_level_metadata(envelope):
    record = er.get_asset_file_record(envelope, "db1", "a1", "/") or {}
    return record.get("metadata") or {}


def _file_record(envelope):
    return er.get_asset_file_record(envelope, "db1", "a1", "/clips/in.mp4") or {}


def _file_metadata(envelope):
    return _file_record(envelope).get("metadata") or {}


def _file_attributes(envelope):
    return _file_record(envelope).get("attributes") or {}


@pytest.mark.unit
class TestNarrowMetadataEnvelope:
    """The pure narrowing helper: subtractive, identity-preserving, per-type."""

    def test_all_enabled_returns_the_same_object(self):
        # Identity (not just equality) is what lets callers detect "nothing to narrow" and skip the
        # extra S3 write, so assert it directly.
        env = _full_envelope()
        gate = {"assetMetadata": True, "fileMetadata": True,
                "fileAttributes": True, "databaseMetadata": True}
        assert er.narrow_metadata_envelope(env, gate) is env

    def test_omitted_gate_keys_default_on_and_do_not_narrow(self):
        env = _full_envelope()
        assert er.narrow_metadata_envelope(env, {}) is env
        assert er.narrow_metadata_envelope(env, None) is env

    def test_database_metadata_off_drops_the_databases_key(self):
        narrowed = er.narrow_metadata_envelope(_full_envelope(), {"databaseMetadata": False})
        # Absence, not an empty list — that is what a run gathering no database metadata looks like.
        assert "databases" not in narrowed
        assert er.get_database_metadata(narrowed, "db1") == {}
        # Everything else survives.
        assert _asset_level_metadata(narrowed) == ASSET_MD
        assert _file_record(narrowed).get("metadata") == FILE_MD
        assert _file_record(narrowed).get("attributes") == FILE_ATTRS

    def test_asset_metadata_off_clears_only_the_asset_level_record(self):
        narrowed = er.narrow_metadata_envelope(_full_envelope(), {"assetMetadata": False})
        assert _asset_level_metadata(narrowed) == {}
        assert _file_record(narrowed).get("metadata") == FILE_MD
        assert _file_record(narrowed).get("attributes") == FILE_ATTRS
        assert er.get_database_metadata(narrowed, "db1") == DB_MD

    def test_file_metadata_off_clears_only_per_file_metadata(self):
        narrowed = er.narrow_metadata_envelope(_full_envelope(), {"fileMetadata": False})
        assert _file_metadata(narrowed) == {}
        assert _asset_level_metadata(narrowed) == ASSET_MD
        assert _file_record(narrowed).get("attributes") == FILE_ATTRS

    def test_file_attributes_off_removes_only_attributes(self):
        narrowed = er.narrow_metadata_envelope(_full_envelope(), {"fileAttributes": False})
        assert _file_attributes(narrowed) == {}
        assert _file_record(narrowed).get("metadata") == FILE_MD
        assert _asset_level_metadata(narrowed) == ASSET_MD

    def test_all_off_keeps_identity_and_subjects_but_no_content(self):
        narrowed = er.narrow_metadata_envelope(
            _full_envelope(),
            {"assetMetadata": False, "fileMetadata": False,
             "fileAttributes": False, "databaseMetadata": False})
        assert _asset_level_metadata(narrowed) == {}
        assert _file_metadata(narrowed) == {}
        assert _file_attributes(narrowed) == {}
        assert "databases" not in narrowed
        # A reader must still resolve the same subjects, so identity/skeleton survive.
        group = narrowed["assets"][0]
        assert group["databaseId"] == "db1" and group["assetId"] == "a1"
        assert group["assetData"] == {"assetName": "A"}
        assert [f["fileKey"] for f in group["files"]] == ["/", "/clips/in.mp4"]

    def test_does_not_mutate_the_shared_envelope(self):
        # The shared envelope is written once and narrowed per step; mutating it would corrupt every
        # later step's delivery.
        env = _full_envelope()
        er.narrow_metadata_envelope(env, {"assetMetadata": False, "databaseMetadata": False})
        assert _asset_level_metadata(env) == ASSET_MD
        assert er.get_database_metadata(env, "db1") == DB_MD


@pytest.mark.unit
class TestNarrowedViaLegacyProjection:
    """to_legacy_vams_view over a narrowed envelope — the shape template tags actually render."""

    def test_excluded_types_are_absent_from_the_rendered_view(self):
        narrowed = er.narrow_metadata_envelope(
            _full_envelope(), {"databaseMetadata": False, "fileAttributes": False})
        view = er.to_legacy_vams_view(narrowed, "db1", "a1", "/clips/in.mp4")["VAMS"]
        assert view["fileMetadata"] == FILE_MD
        assert view["assetMetadata"] == ASSET_MD
        assert view["fileAttributes"] == {}
        assert view.get("databaseMetadata", {}) == {}

    def test_inputmetadataobject_cannot_recover_an_excluded_type(self):
        # {{inputMetadataObject}} renders the WHOLE payload, so if narrowing applied only to the
        # per-scope tags a template could recover excluded data by switching tags. Narrowing the
        # payload itself is what closes that.
        narrowed = er.narrow_metadata_envelope(_full_envelope(), {"databaseMetadata": False})
        assert "databases" not in narrowed
        assert DB_MD["DB_KEY"] not in json.dumps(narrowed)


@pytest.mark.unit
class TestStepTagMetadataPayload:
    """The payload a step's template tags render must come from its DELIVERY envelope.

    Guards the wiring, not just the helper: passing the shared envelope here would leave the metadata
    FILE narrowed while the TAGS still rendered everything — the gate would become advisory.
    """

    SUBJECT = {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/clips/in.mp4"}

    def test_narrowed_envelope_hides_the_excluded_type_from_tags(self):
        narrowed = er.narrow_metadata_envelope(
            _full_envelope(), {"databaseMetadata": False, "fileAttributes": False})
        payload = ewv2._step_tag_metadata_payload(narrowed, self.SUBJECT)
        rendered = json.dumps(payload)
        assert DB_MD["DB_KEY"] not in rendered
        assert FILE_ATTRS["fps"] not in rendered
        # The types the step still wants are present, so narrowing is not blanket suppression.
        assert payload["VAMS"]["fileMetadata"] == FILE_MD
        assert payload["VAMS"]["assetMetadata"] == ASSET_MD

    def test_shared_envelope_would_expose_it(self):
        # The counterfactual that makes the assertion above meaningful: the same subject over the
        # UN-narrowed envelope does render the excluded type.
        payload = ewv2._step_tag_metadata_payload(_full_envelope(), self.SUBJECT)
        assert payload["VAMS"]["fileAttributes"] == FILE_ATTRS

    def test_asset_metadata_off_empties_the_asset_scope_tag(self):
        narrowed = er.narrow_metadata_envelope(_full_envelope(), {"assetMetadata": False})
        payload = ewv2._step_tag_metadata_payload(narrowed, self.SUBJECT)
        assert payload["VAMS"]["assetMetadata"] == {}
        assert ASSET_MD["ASSET_KEY"] not in json.dumps(payload)

    def test_missing_subject_still_yields_a_renderable_shape(self):
        payload = ewv2._step_tag_metadata_payload(_full_envelope(), {})
        assert "VAMS" in payload


@pytest.mark.unit
class TestResolveStepDelivery:
    """One step's delivery decision, shared by all three channels.

    The envelope written for a step, the location its manifest points at, and the payload its tags
    render must come from ONE decision — applying the gate to the file while missing it on the tags is
    exactly how the gate silently becomes advisory. This binds them together so a future change cannot
    narrow one channel and forget another.
    """

    SUBJECT = {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/clips/in.mp4"}
    SHARED = "s3://run-bucket/pipelines/workflowExecutionInputs/exec1/metadata.json"

    def _resolve(self, gate, index=1):
        return ewv2._resolve_step_delivery(
            _full_envelope(), gate, "exec1", "run-bucket", index, self.SHARED)

    def test_unnarrowed_step_keeps_the_shared_envelope_and_location(self):
        env = _full_envelope()
        narrowed, location, tag_payload = ewv2._resolve_step_delivery(
            env, {}, "exec1", "run-bucket", 1, self.SHARED)
        assert narrowed is env
        assert location == self.SHARED
        assert tag_payload(self.SUBJECT)["VAMS"]["fileAttributes"] == FILE_ATTRS

    def test_narrowed_step_gets_its_own_location_and_a_matching_tag_payload(self):
        narrowed, location, tag_payload = self._resolve({"databaseMetadata": False})
        assert location == f"s3://run-bucket/{er.pipeline_input_metadata_key('exec1', 1)}"
        assert location != self.SHARED
        assert "databases" not in narrowed
        # The tag payload is narrowed by the SAME decision — this is what fails if the caller passes
        # the shared envelope to the tag renderer while writing a narrowed file.
        assert DB_MD["DB_KEY"] not in json.dumps(tag_payload(self.SUBJECT))

    def test_every_channel_agrees_for_each_type(self):
        for key, probe in (("assetMetadata", ASSET_MD["ASSET_KEY"]),
                           ("fileMetadata", FILE_MD["FILE_KEY"]),
                           ("fileAttributes", FILE_ATTRS["fps"]),
                           ("databaseMetadata", DB_MD["DB_KEY"])):
            narrowed, location, tag_payload = self._resolve({key: False})
            assert location != self.SHARED, f"{key}: manifest still points at the shared file"
            assert probe not in json.dumps(narrowed), f"{key}: excluded type still in the file"
            assert probe not in json.dumps(tag_payload(self.SUBJECT)), \
                f"{key}: excluded type still reachable from template tags"

    def test_location_is_per_step_index(self):
        _, location, _ = self._resolve({"databaseMetadata": False}, index=3)
        assert location == f"s3://run-bucket/{er.pipeline_input_metadata_key('exec1', 3)}"


@pytest.mark.unit
class TestLaunchPopulatesStepMetadataKeys:
    """Closes the loop between the two ends of the steps-2+ path.

    The ASL threads the JSONPath `$.stepMetadataS3Keys[i]` and the interim lambda honors whatever key
    it is handed — but both are inert unless the LAUNCH populates the array those JSONPaths
    dereference. Asserting on the real start_execution input is what ties array position to ASL index
    to reader: gut the array and every step silently falls back to the un-narrowed shared envelope,
    which is the bug this task exists to fix.
    """

    @staticmethod
    def _pipeline(pipeline_id, metadata_inputs):
        return {
            "pipelineId": pipeline_id,
            "databaseId": "GLOBAL",
            "_jobName": pipeline_id,
            "systemConfig": {"inputFileArity": "none", "metadataInputs": metadata_inputs},
            "executionConfig": {"executionType": "Lambda", "waitForCallback": "Disabled"},
        }

    def _launch(self, pipeline_records, resolved_configs=None):
        """Drive _launch_workflow with AWS boundaries mocked, returning the parsed SFN input."""
        workflow = {
            "workflowId": "wf1",
            "databaseId": "GLOBAL",
            "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:wf1",
            "jobNames": [f"uuid-{r['pipelineId']}" for r in pipeline_records],
            "systemConfig": {"metadataInputs": {}},
        }
        with patch(f"{MOD}.s3c"), \
                patch(f"{MOD}.sfn_client") as sfn, \
                patch(f"{MOD}._persist_execution_records"):
            sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            ewv2._launch_workflow(
                workflow, pipeline_records, resolved_configs or {}, [], {},
                None, "GLOBAL", "asset1", "run-bucket", _full_envelope(),
                "Manual", "", "user@example.com", "",
                metadata_source_assets=[{"databaseId": "db1", "assetId": "a1"}],
                metadata_source_databases=["db1"])
            return json.loads(sfn.start_execution.call_args.kwargs["input"])

    def test_only_the_narrowed_step_gets_a_key(self):
        # Step 1 wants everything the workflow gathered; step 2 excludes database metadata.
        records = [self._pipeline("p1", {}),
                   self._pipeline("p2", {"databaseMetadata": False})]
        sfn_input = self._launch(records)
        keys = sfn_input["stepMetadataS3Keys"]
        execution_id = sfn_input["workflowExecutionId"]
        assert len(keys) == 2
        # Index 0 (step 1) empty -> reads the shared envelope.
        assert keys[0] == ""
        # Index 1 (step 2) -> its own narrowed file. This is the value the ASL's
        # $.stepMetadataS3Keys[1] dereferences and hands to resolve_next_metadata_location.
        assert keys[1] == er.pipeline_input_metadata_key(execution_id, 2)

    def test_no_step_narrows_leaves_every_entry_empty(self):
        records = [self._pipeline("p1", {}), self._pipeline("p2", {})]
        assert self._launch(records)["stepMetadataS3Keys"] == ["", ""]

    def test_every_step_narrowing_populates_every_entry(self):
        records = [self._pipeline("p1", {"assetMetadata": False}),
                   self._pipeline("p2", {"fileMetadata": False}),
                   self._pipeline("p3", {"databaseMetadata": False})]
        sfn_input = self._launch(records)
        execution_id = sfn_input["workflowExecutionId"]
        assert sfn_input["stepMetadataS3Keys"] == [
            er.pipeline_input_metadata_key(execution_id, i) for i in (1, 2, 3)]

    def test_array_length_always_matches_the_pipeline_count(self):
        # The ASL indexes this array positionally, so a short array would make a later step's
        # JSONPath fail to resolve at runtime rather than fall back.
        for count in (1, 2, 4):
            records = [self._pipeline(f"p{i}", {}) for i in range(1, count + 1)]
            assert len(self._launch(records)["stepMetadataS3Keys"]) == count

    def test_template_overrides_decide_the_key(self):
        # A template override is what makes a step narrow (or not), and overrides resolve only at
        # launch — so the array must be built from the EFFECTIVE config, not the stored pipeline one.
        records = [self._pipeline("p1", {"databaseMetadata": True})]
        resolved = {
            er.pipeline_composite_key("GLOBAL", "p1"): {
                "renderedConfig": "",
                "templateOverrides": {"metadataInputs": {"databaseMetadata": False}},
            }
        }
        sfn_input = self._launch(records, resolved_configs=resolved)
        execution_id = sfn_input["workflowExecutionId"]
        assert sfn_input["stepMetadataS3Keys"][0] == er.pipeline_input_metadata_key(execution_id, 1)


@pytest.mark.unit
class TestWriteExecutionInputFiles:
    """Delivery point 1 — which metadata file each step gets at launch."""

    def _write(self, gates, pipelines_count=2):
        envelope = _full_envelope()
        with patch(f"{MOD}.s3c") as s3:
            locations = ewv2._write_execution_input_files(
                "exec1", "run-bucket", pipelines_count, envelope,
                {"schemaVersion": 1}, ["cfg1", "cfg2"], step_metadata_gates=gates)
            puts = {c.kwargs["Key"]: c.kwargs["Body"] for c in s3.put_object.call_args_list}
        return locations, puts

    def test_no_gate_narrows_writes_only_the_shared_metadata_file(self):
        locations, puts = self._write({1: {}, 2: {}})
        assert locations["narrowedMetadataKeys"] == {}
        assert er.execution_input_metadata_key("exec1") in puts
        assert er.pipeline_input_metadata_key("exec1", 1) not in puts
        assert er.pipeline_input_metadata_key("exec1", 2) not in puts

    def test_narrowed_step_gets_its_own_file_and_the_other_does_not(self):
        locations, puts = self._write({1: {"databaseMetadata": False}, 2: {}})
        step1_key = er.pipeline_input_metadata_key("exec1", 1)
        assert locations["narrowedMetadataKeys"] == {1: step1_key}
        assert step1_key in puts
        assert er.pipeline_input_metadata_key("exec1", 2) not in puts
        # The step's own file really is narrowed; the shared file is untouched.
        assert "databases" not in json.loads(puts[step1_key])
        assert "databases" in json.loads(puts[er.execution_input_metadata_key("exec1")])

    def test_each_step_gets_its_own_distinct_narrowing(self):
        locations, puts = self._write(
            {1: {"databaseMetadata": False}, 2: {"fileMetadata": False}})
        k1 = er.pipeline_input_metadata_key("exec1", 1)
        k2 = er.pipeline_input_metadata_key("exec1", 2)
        assert set(locations["narrowedMetadataKeys"]) == {1, 2}
        step1, step2 = json.loads(puts[k1]), json.loads(puts[k2])
        assert "databases" not in step1
        assert _file_metadata(step1) == FILE_MD
        assert "databases" in step2
        assert _file_metadata(step2) == {}


@pytest.mark.unit
class TestInterimDeliveryFailsClosed:
    """Delivery point 2 — steps 2+, resolved mid-run by the interim lambda.

    interimPipelineTracking runs mid-execution, so a bad threaded value must degrade to today's
    behavior (the shared envelope) rather than deliver nothing: an empty payload would break a
    pipeline that needs metadata, which is worse than delivering the wider set.
    """

    SHARED = "s3://run-bucket/shared/metadata.json"

    @staticmethod
    def _resolve(body):
        # The real interim implementation, imported here so this module's import stays cheap: the
        # lambda resolves table/bucket names from SSM at import time.
        from backend.backend.handlers.workflows.sfn import interimPipelineTracking as ipt
        return ipt.resolve_next_metadata_location(body, "run-bucket")

    def _body(self, key):
        return {
            "workflowExecutionS3InputOutputBucket": "run-bucket",
            "inputMetadataS3Location": self.SHARED,
            "nextPipelineMetadataS3Key": key,
        }

    def test_threaded_key_points_at_the_steps_own_file(self):
        loc = self._resolve(self._body("in/pipeline2/metadata.json"))
        assert loc == "s3://run-bucket/in/pipeline2/metadata.json"

    def test_surrounding_whitespace_is_tolerated(self):
        loc = self._resolve(self._body("  in/pipeline2/metadata.json  "))
        assert loc == "s3://run-bucket/in/pipeline2/metadata.json"

    @pytest.mark.parametrize("bad", ["", "   ", None, 0, [], {}])
    def test_absent_or_malformed_key_falls_back_to_the_shared_envelope(self, bad):
        # Fail CLOSED: never an empty or "s3://bucket/" location, which would deliver nothing to a
        # step that needs metadata.
        assert self._resolve(self._body(bad)) == self.SHARED

    def test_missing_key_entirely_falls_back(self):
        assert self._resolve(
            {"workflowExecutionS3InputOutputBucket": "run-bucket",
             "inputMetadataS3Location": self.SHARED}) == self.SHARED


@pytest.mark.unit
class TestAggregateReportsOverrides:
    """aggregate_metadata_inputs keeps the AND (it is correct under two-level semantics) and reports
    whether template overrides were folded in."""

    def test_without_overrides_reports_false(self):
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"databaseMetadata": True}},
            [{"metadataInputs": {"databaseMetadata": True}}])
        assert agg["databaseMetadata"] is True
        assert agg["includesTemplateOverrides"] is False

    def test_overrides_can_turn_a_type_off(self):
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"databaseMetadata": True}},
            [{"metadataInputs": {"databaseMetadata": True}}],
            template_overrides=[{"metadataInputs": {"databaseMetadata": False}}])
        assert agg["databaseMetadata"] is False
        assert agg["includesTemplateOverrides"] is True

    def test_overrides_can_turn_a_type_on(self):
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"assetMetadata": True}},
            [{"metadataInputs": {"assetMetadata": False}}],
            template_overrides=[{"metadataInputs": {"assetMetadata": True}}])
        assert agg["assetMetadata"] is True

    def test_workflow_gate_still_wins_over_an_override(self):
        # Intake bounds delivery: a type the workflow never gathered cannot be delivered, so an
        # override asking for it is reported off and named in gatedOffByWorkflow.
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"databaseMetadata": False}},
            [{"metadataInputs": {"databaseMetadata": False}}],
            template_overrides=[{"metadataInputs": {"databaseMetadata": True}}])
        assert agg["databaseMetadata"] is False
        assert agg["gatedOffByWorkflow"] == ["databaseMetadata"]

    def test_shorter_overrides_list_leaves_remaining_pipelines_unchanged(self):
        agg = ev.aggregate_metadata_inputs(
            {"metadataInputs": {"fileMetadata": True}},
            [{"metadataInputs": {"fileMetadata": False}},
             {"metadataInputs": {"fileMetadata": True}}],
            template_overrides=[{}])
        assert agg["fileMetadata"] is True
