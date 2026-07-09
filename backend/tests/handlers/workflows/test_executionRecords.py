# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from backend.backend.common.workflows import executionRecords as er


@pytest.mark.unit
class TestKeysAndDates:
    def test_workflow_composite_key_is_clean(self):
        assert er.workflow_composite_key("db1", "wf1") == "db1:wf1"

    def test_pipeline_composite_key_is_clean(self):
        assert er.pipeline_composite_key("pdb", "pid") == "pdb:pid"

    def test_input_file_composite_key_normalizes_leading_slash(self):
        # fileKey is normalized to exactly one leading slash
        assert er.input_file_composite_key("db", "a1", "folder/x.glb") == "db:a1:/folder/x.glb"
        assert er.input_file_composite_key("db", "a1", "/folder/x.glb") == "db:a1:/folder/x.glb"

    def test_normalize_file_key_single_leading_slash(self):
        assert er.normalize_file_key("a/b.txt") == "/a/b.txt"
        assert er.normalize_file_key("///a/b.txt") == "/a/b.txt"
        assert er.normalize_file_key("") == "/"

    def test_iso_now_format(self):
        # 2026-06-16T14:09:55Z shape: ends with Z, has T, no microseconds
        val = er.iso_now()
        assert val.endswith("Z") and "T" in val and "." not in val

    def test_iso_seconds_since_empty_is_infinite(self):
        # Empty/unparseable -> treated as "stale enough to refresh" (infinite age).
        assert er.iso_seconds_since("") == float("inf")
        assert er.iso_seconds_since("not-a-date") == float("inf")

    def test_iso_seconds_since_recent_is_small(self):
        # A timestamp of "now" should be only a few seconds old.
        assert er.iso_seconds_since(er.iso_now()) < 5

    def test_iso_seconds_since_old_exceeds_threshold(self):
        # A clearly old timestamp is well beyond the 30s sync window.
        assert er.iso_seconds_since("2000-01-01T00:00:00Z") > 30


@pytest.mark.unit
class TestPrefixDerivation:
    def test_pipeline_output_prefixes_for_first_pipeline(self):
        # Mirrors createWorkflow ASL: pipelines/{name}/{job}/output/{execId}/{type}/
        prefixes = er.pipeline_output_prefixes(
            first_pipeline_name="myPipe", first_job_name="abcde-myPipe", execution_id="EXEC1"
        )
        assert prefixes["files"] == "pipelines/myPipe/abcde-myPipe/output/EXEC1/files/"
        assert prefixes["previews"] == "pipelines/myPipe/abcde-myPipe/output/EXEC1/previews/"
        assert prefixes["metadata"] == "pipelines/myPipe/abcde-myPipe/output/EXEC1/metadata/"
        assert prefixes["results"] == "pipelines/myPipe/abcde-myPipe/output/EXEC1/results/"

    def test_aux_temp_prefix_is_execution_scoped(self):
        # Bucket-relative, execution-scoped temp working prefix: pipelines/{pipelineName}/{execId}/
        assert er.aux_pipeline_prefix("std", "EXEC1") == "pipelines/std/EXEC1/"

    def test_aux_preview_file_prefix_is_per_file(self):
        # Per-input-file aux preview prefix keyed on the FULL asset file key (location key +
        # relative path), so a custom asset base prefix is preserved: {databaseId}/{assetFileKey}/preview
        assert er.aux_preview_file_prefix("db", "xid/test/pump.e57") == "db/xid/test/pump.e57/preview"
        # Custom asset base prefix in the location key is preserved.
        assert er.aux_preview_file_prefix("db", "custom/base/xid/scan.e57") == "db/custom/base/xid/scan.e57/preview"
        # Empty file key -> {db}/preview
        assert er.aux_preview_file_prefix("db", "") == "db/preview"


@pytest.mark.unit
class TestTruncateAndBuilders:
    def test_truncate_text_under_limit(self):
        text, truncated = er.truncate_text("hello", limit=100)
        assert text == "hello" and truncated is False

    def test_truncate_text_over_limit(self):
        big = "x" * 50
        text, truncated = er.truncate_text(big, limit=10)
        assert len(text.encode("utf-8")) <= 10 and truncated is True

    def test_truncate_text_multibyte_safe(self):
        # 'é' is 2 bytes in UTF-8; truncating at an odd byte limit must not emit invalid UTF-8
        text, truncated = er.truncate_text("é" * 50, limit=15)
        assert truncated is True
        assert len(text.encode("utf-8")) <= 15
        text.encode("utf-8")  # must not raise / must be valid UTF-8

    def test_build_workflow_execution_record_shape(self):
        rec = er.build_workflow_execution_record(
            execution_id="E1", workflow_database_id="db", workflow_id="wf",
            workflow_arn="arn:sm", workflow_execution_arn="arn:ex",
            execution_start_date="2026-06-16T00:00:00Z", execution_status="NEW",
            triggered_by_user_id="user@x", trigger_type="Manual",
            execution_log_group_arn="arn:lg",
        )
        assert rec["workflowExecutionId"] == "E1"
        assert rec["workflowDatabaseId:workflowId"] == "db:wf"
        assert rec["workflow_execution_arn"] == "arn:ex"
        assert rec["executionStartDate"] == "2026-06-16T00:00:00Z"
        assert rec["executionStopDate"] == ""
        assert rec["triggeredByUserId"] == "user@x"
        assert rec["triggerType"] == "Manual"
        # New v2.6 sync/error fields default empty at launch.
        assert rec["lastSfnSyncCheckDate"] == ""
        assert rec["executionError"] == "" and rec["executionLog"] == ""
        # asset/database coupling fields must NOT be present
        assert "databaseId:assetId" not in rec and "inputAssetFileKey" not in rec

    def test_build_workflow_execution_record_accepts_sync_check_date(self):
        rec = er.build_workflow_execution_record(
            execution_id="E1", workflow_database_id="db", workflow_id="wf",
            workflow_arn="arn:sm", workflow_execution_arn="arn:ex",
            execution_start_date="2026-06-16T00:00:00Z", execution_status="NEW",
            triggered_by_user_id="user@x", trigger_type="Manual",
            execution_log_group_arn="arn:lg",
            last_sfn_sync_check_date="2026-06-16T00:01:00Z",
        )
        assert rec["lastSfnSyncCheckDate"] == "2026-06-16T00:01:00Z"

    def test_build_pipeline_execution_record_end_state_and_sts_fields(self):
        rec = er.build_pipeline_execution_record(
            pipeline_execution_id="P1", workflow_execution_id="E1",
            pipeline_database_id="pdb", pipeline_id="pid", end_state_pipeline=True,
            s3_asset_bucket="abkt", s3_aux_bucket="auxbkt",
            output_prefixes={"files": "f/", "previews": "p/", "metadata": "m/", "results": "r/"},
            input_metadata_file_prefix="im/", input_config_file_prefix="ic/",
            aux_temp_prefix="t/", aux_preview_prefix="pv/",
            pipeline_execution_type="Lambda", wait_for_callback="Disabled",
            pipeline_resource_arn="arn:fn", from_pipeline_execution_id="",
        )
        assert rec["pipelineExecutionId"] == "P1"
        assert rec["workflowExecutionId"] == "E1"
        assert rec["pipelineDatabaseId:pipelineId"] == "pdb:pid"
        assert rec["endStatePipeline"] == "true"   # stored as string for GSI key
        assert rec["S3AssetPipelineBucketOutputFilesPrefix"] == "f/"
        assert rec["credentialVendingState"] == "notVended"
        assert rec["vendedRoleArn"] == "" and rec["s3ReadOnlyScopes"] == [] and rec["s3ReadWriteScopes"] == []
        assert rec["executionStartDate"] == "" and rec["executionStatus"] == ""

    def test_build_input_file_record(self):
        rec = er.build_pipeline_input_file_record(
            pipeline_execution_id="P1", workflow_execution_id="E1",
            database_id="db", asset_id="a1", input_asset_file_key="folder/x.glb",
        )
        assert rec["pipelineExecutionId"] == "P1"
        assert rec["databaseId:assetId:inputAssetFileKey"] == "db:a1:/folder/x.glb"
        assert rec["databaseId:assetId"] == "db:a1"
        assert rec["inputAssetFileKey"] == "/folder/x.glb"
        assert rec["workflowExecutionId"] == "E1"

    def test_build_workflow_execution_input_record_has_asset_gsi_keys(self):
        rec = er.build_workflow_execution_input_record(
            workflow_execution_id="E1", database_id="db", asset_id="a1",
            input_asset_file_key="x.glb", execution_start_date="2026-06-16T00:00:00Z",
            workflow_id="wf", workflow_database_id="wdb",
        )
        assert rec["workflowExecutionId"] == "E1"
        assert rec["databaseId:assetId:inputAssetFileKey"] == "db:a1:/x.glb"
        assert rec["databaseId:assetId"] == "db:a1"          # GSI PK
        assert rec["executionStartDate"] == "2026-06-16T00:00:00Z"  # GSI SK


@pytest.mark.unit
class TestOutputBuilders:
    def test_output_file_record(self):
        rec = er.build_output_file_record(
            pipeline_execution_id="P1", file_type="file", relative_file_path="sub/x.glb",
            s3_bucket="b", s3_key="pipelines/.../x.glb", file_size=123,
            content_type="model/gltf-binary", s3_version_id="v1",
        )
        assert rec["pipelineExecutionId"] == "P1"
        assert rec["fileType:relativeFilePath"] == "file:sub/x.glb"
        assert rec["fileType"] == "file" and rec["relativeFilePath"] == "sub/x.glb"
        assert rec["fileSize"] == 123 and rec["s3VersionId"] == "v1"

    def test_output_metadata_record(self):
        rec = er.build_output_metadata_record(
            pipeline_execution_id="P1", target_file_path="/x.glb", metadata_key="color",
            metadata_value="red", source_metadata_file_relative_path="x.glb.metadata.json",
        )
        assert rec["targetFilePath:metadataKey"] == "/x.glb:color"
        assert rec["metadataValue"] == "red"

    def test_output_result_record_truncates(self):
        rec = er.build_output_result_record(
            pipeline_execution_id="P1", relative_file_path="out.csv",
            results_content="y" * (er.MAX_TEXT_FIELD_BYTES + 5), s3_key="k",
        )
        assert rec["relativeFilePath"] == "out.csv"
        assert rec["resultsContentTruncated"] is True

    def test_log_record_truncates_both_fields(self):
        rec = er.build_log_record(
            pipeline_execution_id="P1", log_type="summary",
            result_log="ok", error_log="z" * (er.MAX_LOG_FIELD_BYTES + 5),
            log_group_arn="arn:lg", log_stream_name="stream",
        )
        assert rec["logType"] == "summary"
        assert rec["resultLog"] == "ok"
        assert rec["errorLogTruncated"] is True

    def test_input_metadata_and_config_records(self):
        md = er.build_input_metadata_record(
            pipeline_execution_id="P1", database_id="db", asset_id="a1",
            file_path="/", metadata={"k": "v"}, source_input_metadata_file_s3_key="im/x.json",
        )
        assert md["databaseId:assetId:filePath"] == "db:a1:/"
        assert md["metadata"] == {"k": "v"}

        cfg = er.build_input_configuration_record(
            pipeline_execution_id="P1", input_configuration='{"a":1}',
            input_configuration_file_s3_key="ic/x.json",
        )
        assert cfg["pipelineExecutionId"] == "P1"
        assert cfg["recordType"] == "configuration"
        assert cfg["inputConfigurationTruncated"] is False
        assert cfg["inputPortMappings"] == {}

    def test_workflow_configuration_record(self):
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", workflow_configuration='{"s":1}',
            input_metadata='{"VAMS":{}}', specified_pipelines_snapshot=[{"name": "p"}],
        )
        assert rec["workflowExecutionId"] == "E1"
        assert rec["recordType"] == "configuration"
        assert rec["specifiedPipelinesSnapshot"] == [{"name": "p"}]
        # Output base-execution path extension defaults to '/' (no extra path segment).
        assert rec["outputFileBaseExecutionPathExtension"] == "/"

    def test_workflow_configuration_record_carries_path_extension(self):
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", workflow_configuration="", input_metadata="",
            specified_pipelines_snapshot=[],
            output_file_base_execution_path_extension="/exec-2026/",
        )
        assert rec["outputFileBaseExecutionPathExtension"] == "/exec-2026/"

    def test_manifest_output_target_defaults_and_extension(self):
        # Default: location 'asset', empty ids, '/' extension (no extra path).
        default = er.build_manifest_output_target()
        assert default == {"locationType": "asset", "assetId": "", "databaseId": "",
                           "fileBaseExecutionPathExtension": "/"}
        # Populated: identity + a sub-folder extension.
        populated = er.build_manifest_output_target(
            location_type="asset", asset_id="a1", database_id="db",
            file_base_execution_path_extension="/exec-2026/")
        assert populated["assetId"] == "a1" and populated["databaseId"] == "db"
        assert populated["fileBaseExecutionPathExtension"] == "/exec-2026/"

    def test_manifest_envelope_includes_output_target(self):
        env = er.build_manifest_envelope(
            input_files=[], input_metadata_s3_location="", outputs={},
            aux_bucket="", aux_temp_prefix="", aux_preview_pipeline_suffix="",
            output_target=er.build_manifest_output_target(asset_id="a1", database_id="db"))
        assert env["outputTarget"]["assetId"] == "a1"
        assert env["outputTarget"]["fileBaseExecutionPathExtension"] == "/"

    def test_manifest_envelope_locations_are_relative(self):
        # outputs = bucket + relative prefixes; auxBucket = name only; no top-level auxPreviewPrefix.
        env = er.build_manifest_envelope(
            input_files=[], input_metadata_s3_location="",
            outputs=er.build_manifest_outputs(bucket="abkt", files="f/", previews="p/",
                                              metadata="m/", results="r/"),
            aux_bucket="auxbkt", aux_temp_prefix="pipelines/pipe/EXEC1/",
            aux_preview_pipeline_suffix="")
        assert env["outputs"]["bucket"] == "abkt" and env["outputs"]["files"] == "f/"
        assert env["auxBucket"] == "auxbkt"
        assert env["auxTempPrefix"] == "pipelines/pipe/EXEC1/"
        assert env["auxPreviewPipelineSuffix"] == ""
        assert "auxPreviewPrefix" not in env and "auxBucketS3Root" not in env
