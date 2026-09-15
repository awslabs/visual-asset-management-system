# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Template body / tag-schema reads, and workflow RUN I/O writes, address the area VAMS owns.

An offloaded template body and tag schema live in the VAMS default asset bucket under a key the
template handler stores RELATIVE to the area VAMS owns in that bucket. When the default bucket is an
external bucket registered under a non-empty baseAssetsPrefix, the full key is that prefix joined to
the stored key (defaultBucket.default_bucket_key) — the same expression the template handler writes
with and the trigger service reads with. A read at the bucket root misses the object entirely, which
for the run path means a template resolved against an empty configBody.

S11-EXTERNALS3-005 / S2-BACKEND-100 extend the same rule to run I/O, which is the much larger half:
the execution input definitions and the pipeline output prefixes were written at the bucket ROOT while
the template bodies joined the prefix, so the two halves of the same handler disagreed. The write side
is asserted here against a RECORDING S3 double, since the defect was which key was written."""

import io
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

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ewv2   # noqa: E402
from backend.backend.common.workflows import executionRecords as er   # noqa: E402

MOD = "backend.backend.handlers.workflows.executeWorkflow"

ROOT_BUCKET = {"bucketId": "b-id", "bucketName": "run-bucket", "baseAssetsPrefix": ""}
PREFIXED_BUCKET = {"bucketId": "b-id", "bucketName": "run-bucket", "baseAssetsPrefix": "vamsroot/"}

CB_KEY = "pipelines/templates/db1/pipe1/tmpl1/configBody"
WF_KEY = "pipelines/templates/db1/pipe1/tmpl1/webForm.json"
TAG_KEY = "pipelines/templates/db1/pipe1/tmpl1/tagSchema.json"


def _stream(text):
    return lambda **kwargs: {"Body": io.BytesIO(text.encode("utf-8"))}


def _tag_schema_rows(fields_key):
    table = MagicMock()
    table.query.return_value = {"Items": [{
        "tagSchemaId": "id", "pipelineDatabaseId:pipelineId:templateId": "db1:pipe1:tmpl1",
        "bodyStorage": "s3", "fieldsS3Key": fields_key,
    }]}
    return table


@pytest.mark.unit
class TestOffloadedTemplateReadsUseTheDefaultBucketPrefix:
    """The run path reads the offloaded body and tag schema at the prefixed key."""

    @patch(f"{MOD}.s3c")
    @patch(f"{MOD}._default_run_bucket", return_value=PREFIXED_BUCKET)
    def test_body_read_joins_the_prefix(self, _mock_bucket, mock_s3):
        mock_s3.get_object.side_effect = _stream("stored: 1")
        row = {"templateId": "tmpl1", "bodyStorage": "s3",
               "configBodyS3Key": CB_KEY, "webFormS3Key": WF_KEY}
        resolved = ewv2._rehydrate_template_row(row, PREFIXED_BUCKET["bucketName"])
        assert resolved["configBody"] == "stored: 1"
        read = [c.kwargs["Key"] for c in mock_s3.get_object.call_args_list]
        assert read == ["vamsroot/" + CB_KEY, "vamsroot/" + WF_KEY]

    @patch(f"{MOD}._tag_schema_table")
    @patch(f"{MOD}.s3c")
    @patch(f"{MOD}._default_run_bucket", return_value=PREFIXED_BUCKET)
    def test_tag_schema_read_joins_the_prefix(self, _mock_bucket, mock_s3, mock_tag_table):
        mock_s3.get_object.side_effect = _stream('[{"tagKey": "prompt", "type": "string"}]')
        mock_tag_table.return_value = _tag_schema_rows(TAG_KEY)
        fields = ewv2._load_tag_schema_fields("db1", "pipe1", "tmpl1",
                                              PREFIXED_BUCKET["bucketName"])
        assert [f["tagKey"] for f in fields] == ["prompt"]
        assert mock_s3.get_object.call_args.kwargs["Key"] == "vamsroot/" + TAG_KEY

    @patch(f"{MOD}._tag_schema_table")
    @patch(f"{MOD}.s3c")
    @patch(f"{MOD}._default_run_bucket", return_value=ROOT_BUCKET)
    def test_root_registered_bucket_reads_the_bare_key(self, _mock_bucket, mock_s3, mock_tag_table):
        # The positive control, and the standard deployment: registered at the bucket root, the joined
        # key IS the stored key, so nothing about an existing deployment's reads changes.
        mock_s3.get_object.side_effect = _stream("[]")
        mock_tag_table.return_value = _tag_schema_rows(TAG_KEY)
        row = {"templateId": "tmpl1", "bodyStorage": "s3", "configBodyS3Key": CB_KEY,
               "webFormS3Key": WF_KEY}
        ewv2._rehydrate_template_row(row, ROOT_BUCKET["bucketName"])
        ewv2._load_tag_schema_fields("db1", "pipe1", "tmpl1", ROOT_BUCKET["bucketName"])
        read = [c.kwargs["Key"] for c in mock_s3.get_object.call_args_list]
        assert read == [CB_KEY, WF_KEY, TAG_KEY]

    @patch(f"{MOD}.s3c")
    @patch(f"{MOD}._default_run_bucket", return_value=PREFIXED_BUCKET)
    def test_inline_template_reads_nothing_and_resolves_no_key(self, mock_bucket, mock_s3):
        # An inline row must not pay a bucket lookup or an S3 read for either body.
        row = {"templateId": "tmpl1", "bodyStorage": "inline", "configBody": "x: 1",
               "webFormJson": ""}
        resolved = ewv2._rehydrate_template_row(row, PREFIXED_BUCKET["bucketName"])
        assert resolved["configBody"] == "x: 1"
        mock_s3.get_object.assert_not_called()
        mock_bucket.assert_not_called()


EXEC_ID = "EXEC1"


def _written_input_keys(run_prefix, pipelines_count=2, gates=None):
    """Drive _write_execution_input_files and return (recorded keys, returned locations).

    The double RECORDS every Bucket/Key rather than merely accepting the call: the whole defect is
    which key was written, so a stub that swallowed the put would report success either way.
    """
    with patch(f"{MOD}.s3c") as s3:
        locations = ewv2._write_execution_input_files(
            EXEC_ID, PREFIXED_BUCKET["bucketName"], pipelines_count,
            {"schemaVersion": 2, "assets": []}, {"schemaVersion": 1},
            ["cfg1", "cfg2"][:pipelines_count],
            step_metadata_gates=gates, run_prefix=run_prefix)
        recorded = [(c.kwargs["Bucket"], c.kwargs["Key"]) for c in s3.put_object.call_args_list]
    return recorded, locations


@pytest.mark.unit
class TestExecutionInputWritesUseTheDefaultBucketPrefix:
    """Delivery point: the four objects a launch writes into the per-execution input folder."""

    def test_every_object_is_written_inside_the_declared_area(self):
        recorded, _locations = _written_input_keys("vamsroot/")
        assert [key for _bucket, key in recorded] == [
            "vamsroot/pipelines/workflowExecutionInputs/EXEC1/metadata.json",
            "vamsroot/pipelines/workflowExecutionInputs/EXEC1/pipeline1/config.json",
            "vamsroot/pipelines/workflowExecutionInputs/EXEC1/pipeline2/config.json",
            "vamsroot/pipelines/workflowExecutionInputs/EXEC1/pipeline1/manifest.json",
        ]

    def test_nothing_is_written_at_the_bucket_root(self):
        """The negative half of the pair. An implementation writing to both places, or joining only
        some of the four calls, passes an existence check and fails this."""
        recorded, _locations = _written_input_keys("vamsroot/")
        assert [key for _bucket, key in recorded if not key.startswith("vamsroot/")] == []

    def test_a_narrowed_step_metadata_file_is_also_written_inside_the_area(self):
        """The fifth write, taken only when a step's metadataInputs gate subtracts something. It is a
        separate call site, so an incomplete sweep shows up here and nowhere else."""
        recorded, locations = _written_input_keys(
            "vamsroot/", gates={1: {}, 2: {"assetFileMetadata": False, "assetMetadata": False,
                                           "databaseMetadata": False, "fileAttributes": False}})
        narrowed = [key for _bucket, key in recorded if key.endswith("pipeline2/metadata.json")]
        assert narrowed == ["vamsroot/pipelines/workflowExecutionInputs/EXEC1/pipeline2/metadata.json"]
        # The RETURNED key stays relative: it travels in the SFN input, where the ASL and the interim
        # lambda join the prefix themselves.
        assert locations["narrowedMetadataKeys"][2] == (
            "pipelines/workflowExecutionInputs/EXEC1/pipeline2/metadata.json")

    def test_the_returned_locations_stay_relative_to_the_vams_area(self):
        """The returned keys are what the Step Functions input carries, so they must NOT be prefixed —
        a doubly-joined key would send the state machine to vamsroot/vamsroot/..."""
        _recorded, locations = _written_input_keys("vamsroot/")
        assert locations["metadataFileS3Key"] == (
            "pipelines/workflowExecutionInputs/EXEC1/metadata.json")
        assert locations["configKeys"] == [
            "pipelines/workflowExecutionInputs/EXEC1/pipeline1/config.json",
            "pipelines/workflowExecutionInputs/EXEC1/pipeline2/config.json",
        ]
        assert locations["firstManifestS3Key"] == (
            "pipelines/workflowExecutionInputs/EXEC1/pipeline1/manifest.json")

    def test_an_empty_prefix_writes_at_the_bucket_root_exactly_as_before(self):
        """The owner's carve-out and the must-still-work arm: a VAMS-created default bucket declares no
        prefix, and its keys are byte-identical to the returned relative ones."""
        recorded, locations = _written_input_keys("")
        assert [key for _bucket, key in recorded] == [
            "pipelines/workflowExecutionInputs/EXEC1/metadata.json",
            "pipelines/workflowExecutionInputs/EXEC1/pipeline1/config.json",
            "pipelines/workflowExecutionInputs/EXEC1/pipeline2/config.json",
            "pipelines/workflowExecutionInputs/EXEC1/pipeline1/manifest.json",
        ]
        assert locations["metadataFileS3Key"] == recorded[0][1]

    def test_a_slash_prefix_also_writes_at_the_bucket_root(self):
        """'/' must not produce a leading slash, which would put every object under an empty first
        path segment."""
        recorded, _locations = _written_input_keys("/")
        assert [key for _bucket, key in recorded if key.startswith("/")] == []
        assert recorded[0][1] == "pipelines/workflowExecutionInputs/EXEC1/metadata.json"

    def test_the_default_keeps_the_bucket_root_for_a_caller_that_supplies_no_prefix(self):
        """run_prefix is a trailing keyword with a "" default, so an existing positional call site is
        unchanged in behaviour rather than silently gaining a prefix."""
        with patch(f"{MOD}.s3c") as s3:
            ewv2._write_execution_input_files(
                EXEC_ID, PREFIXED_BUCKET["bucketName"], 1,
                {"schemaVersion": 2, "assets": []}, {"schemaVersion": 1}, ["cfg1"])
            keys = [c.kwargs["Key"] for c in s3.put_object.call_args_list]
        # Without the non-emptiness guard, a run that wrote NOTHING satisfies `all(...)` and would
        # report every key as correctly prefixed having written none.
        assert keys, "no object was written, so nothing here is about the key prefix"
        assert all(key.startswith("pipelines/") for key in keys), keys


@pytest.mark.unit
class TestStepMetadataDeliveryLocationUsesThePrefix:
    """_resolve_step_delivery mints an s3:// URI for a step whose gate narrows the envelope."""

    GATE = {"assetFileMetadata": False, "assetMetadata": False,
            "databaseMetadata": False, "fileAttributes": False}
    SHARED = "s3://run-bucket/vamsroot/pipelines/workflowExecutionInputs/EXEC1/metadata.json"

    def test_a_narrowed_step_points_at_its_own_prefixed_metadata_file(self):
        envelope = {"schemaVersion": 2, "assets": [
            {"databaseId": "db1", "assetId": "a1", "assetMetadata": {"k": "v"}, "files": []}]}
        _narrowed, location, _payload = ewv2._resolve_step_delivery(
            envelope, self.GATE, EXEC_ID, "run-bucket", 2, self.SHARED, run_prefix="vamsroot/")
        assert location == ("s3://run-bucket/vamsroot/pipelines/workflowExecutionInputs/"
                           "EXEC1/pipeline2/metadata.json")

    def test_an_unnarrowed_step_keeps_the_shared_location_untouched(self):
        envelope = {"schemaVersion": 2, "assets": []}
        _narrowed, location, _payload = ewv2._resolve_step_delivery(
            envelope, {}, EXEC_ID, "run-bucket", 2, self.SHARED, run_prefix="vamsroot/")
        assert location == self.SHARED

    def test_an_empty_prefix_mints_the_bucket_root_uri(self):
        envelope = {"schemaVersion": 2, "assets": [
            {"databaseId": "db1", "assetId": "a1", "assetMetadata": {"k": "v"}, "files": []}]}
        _narrowed, location, _payload = ewv2._resolve_step_delivery(
            envelope, self.GATE, EXEC_ID, "run-bucket", 2, self.SHARED, run_prefix="")
        assert location == ("s3://run-bucket/pipelines/workflowExecutionInputs/"
                           "EXEC1/pipeline2/metadata.json")


@pytest.mark.unit
class TestThePersistedRowCarriesTheFullKey:
    """The Q86 half of `S2-BACKEND-100`: a STORED location must be the key the object actually has.

    The class above pins the WRITE keys and the RETURNED keys, which are deliberately different forms —
    written keys are prefixed, returned keys stay relative because the ASL and the interim lambda join
    the prefix themselves. Neither says anything about the key PERSISTED on the execution's rows, and
    that is the one a later reader dereferences.

    Nothing asserted it. Verified by grep across `backend/tests`: no assertion existed on
    `inputConfigurationFileS3Key` being prefixed, and the nearest test asserts the OPPOSITE property one
    layer earlier. So a refactor that stored the relative key would reintroduce the dead-pointer 404
    with every existence check still passing and no test failing — the object exists, the row points
    somewhere else.

    Asserted on the two functions the persist site composes rather than by driving the whole handler:
    `run_bucket_key` performs the join and `build_pipeline_input_configuration_record` stores what it is
    handed, so the pair is the contract. The final arm pins the composition at the call site itself, so
    a future edit that stops joining is caught even though both functions remain individually correct.
    """

    RELATIVE = "pipelines/workflowExecutionInputs/EXEC1/pipeline1/config.json"

    def test_run_bucket_key_joins_the_declared_area(self):
        assert er.run_bucket_key("vamsroot/", self.RELATIVE) == "vamsroot/" + self.RELATIVE

    def test_an_empty_prefix_leaves_the_key_alone(self):
        # The must-still-work arm: a VAMS-created default bucket declares no prefix.
        assert er.run_bucket_key("", self.RELATIVE) == self.RELATIVE

    def test_an_empty_key_does_not_resolve_to_the_prefix_itself(self):
        # An unset location must stay unset. Resolving it to the bare prefix would turn "no object here"
        # into a pointer at the whole VAMS area, which a listing would happily expand.
        assert er.run_bucket_key("vamsroot/", "") == ""

    def test_the_persisted_configuration_row_stores_the_prefixed_key(self):
        full = er.run_bucket_key("vamsroot/", self.RELATIVE)
        row = er.build_input_configuration_record(
            pipeline_execution_id="PEXEC1",
            input_configuration="{}",
            input_configuration_file_s3_key=full)
        assert row["inputConfigurationFileS3Key"] == "vamsroot/" + self.RELATIVE, row
        # The discriminator: the relative form is a DIFFERENT string, so this assertion cannot be
        # satisfied by a row that stored the unjoined key.
        assert row["inputConfigurationFileS3Key"] != self.RELATIVE

    def test_the_launch_path_joins_before_it_persists(self):
        """Pins the composition at the call site, which is what actually regressed.

        `run_bucket_key` and the record builder can both stay correct while the handler stops joining
        between them, and the two arms above would still pass. So this reads the source at the persist
        site and requires the joined list — not `input_locations[...]` directly — to be what feeds the
        stored key.
        """
        source = io.open(ewv2.__file__, encoding="utf-8").read()
        assert 'config_keys = [er.run_bucket_key(run_prefix, key)' in source, (
            "the launch path no longer joins the run prefix onto the config keys before persisting them; "
            "a stored key would then point at an object that does not exist at that key")
        assert 'input_config_file_prefix=cfg_key' in source, (
            "the persisted config key no longer comes from the joined config_keys list")
        assert 'metadata_file_key = er.run_bucket_key(run_prefix,' in source, (
            "the per-execution metadata file key is no longer joined before being persisted")
