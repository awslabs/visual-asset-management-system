# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from backend.backend.common.workflows import executionRecords as er


@pytest.mark.unit
class TestEffectiveSystemConfigSnapshot:
    """The config snapshot records the systemConfig the STEP ran under, not just the pipeline's own.

    A template may override inputFileArity / assetScope / metadataInputs / inputFileFilters, and the
    template is chosen per execution — so the settings actually enforced are only knowable at execute
    time. Without this a finished execution cannot say what it ran with, and a reader cannot tell that a
    template raised the arity.
    """

    def test_effective_config_and_overrides_are_recorded(self):
        row = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="{}",
            input_configuration_file_s3_key="k",
            effective_system_config={"inputFileArity": "one", "requireTemplate": True},
            template_overrides={"inputFileArity": "one"})
        assert row["effectiveSystemConfig"] == {"inputFileArity": "one", "requireTemplate": True}
        assert row["templateOverrides"] == {"inputFileArity": "one"}

    def test_both_default_to_empty_maps(self):
        """An older row (or a run with no template) has no overrides; readers must not KeyError."""
        row = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="{}",
            input_configuration_file_s3_key="k")
        assert row["effectiveSystemConfig"] == {}
        assert row["templateOverrides"] == {}

    def test_the_recorded_effective_config_is_the_documented_merge(self):
        """Recompute the merge the way execute does, so the snapshot cannot drift from what was
        enforced: a template override wins per key, and untouched keys keep the pipeline's value."""
        from backend.backend.common.workflows.executionValidation import (
            resolve_effective_pipeline_config)
        pipeline_config = {"inputFileArity": "none", "requireTemplate": True,
                           "inputFileFilters": {"allow": [], "exclude": []}}
        overrides = {"inputFileArity": "one", "inputFileFilters": {"allow": ["*.mp4"],
                                                                   "exclude": []}}
        effective = resolve_effective_pipeline_config(pipeline_config, overrides)
        row = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="{}",
            input_configuration_file_s3_key="k",
            effective_system_config=effective, template_overrides=overrides)
        # The template raised the arity above the pipeline's own default...
        assert row["effectiveSystemConfig"]["inputFileArity"] == "one"
        assert row["effectiveSystemConfig"]["inputFileFilters"]["allow"] == ["*.mp4"]
        # ...while a key the template did not mention keeps the pipeline's value.
        assert row["effectiveSystemConfig"]["requireTemplate"] is True
        # And the overrides alone explain WHY the effective config differs from the pipeline's.
        assert set(row["templateOverrides"]) == {"inputFileArity", "inputFileFilters"}


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

    def test_truncate_text_budget_shares_one_limit(self):
        # Two maximal fields of one item share the budget rather than each taking the full cap.
        results = er.truncate_text_budget(["a" * 1000, "b" * 1000], total_limit=100)
        assert sum(len(t.encode("utf-8")) for t, _ in results) <= 100
        assert all(truncated for _, truncated in results)

    def test_truncate_text_budget_redistributes_unused_bytes(self):
        # A small field is kept whole and its unused share goes to the oversized field.
        (small, small_truncated), (big, big_truncated) = er.truncate_text_budget(
            ["ok", "b" * 1000], total_limit=100)
        assert small == "ok" and small_truncated is False
        assert big_truncated is True
        assert len(big.encode("utf-8")) == 98

    def test_truncate_text_budget_under_total_keeps_both(self):
        results = er.truncate_text_budget(["one", "two"], total_limit=100)
        assert results == [("one", False), ("two", False)]

    def test_input_configuration_record_two_max_fields_fit_one_item(self):
        # A large customTemplateOverride and the config rendered from it co-occur on one item, so
        # their combined size must stay inside the single-item budget.
        body = "z" * (er.MAX_TEXT_FIELD_BYTES + 10)
        rec = er.build_input_configuration_record(
            pipeline_execution_id="P1", input_configuration=body,
            input_configuration_file_s3_key="ic/x.json",
            custom_template_override_used=True, custom_template_override=body)
        total = (len(rec["inputConfiguration"].encode("utf-8"))
                 + len(rec["customTemplateOverride"].encode("utf-8")))
        assert total <= er.MAX_TEXT_FIELD_BYTES
        assert rec["inputConfigurationTruncated"] is True
        assert rec["customTemplateOverrideTruncated"] is True

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
        # A fresh pipeline row is NEW (queued) until the SFN pre-step/interim lambda flips it RUNNING.
        assert rec["executionStartDate"] == "" and rec["executionStatus"] == "NEW"
        # from_pipeline_execution_id is the PipelineExecChainGSI sort key; when there is no chain
        # parent it must be OMITTED (DynamoDB rejects an empty string for an indexed key attribute).
        assert "from_pipeline_execution_id" not in rec

    def test_build_pipeline_execution_record_omits_empty_chain_key(self):
        # Empty/None chain parent -> attribute absent (sparse GSI).
        for empty in ("", None):
            rec = er.build_pipeline_execution_record(
                pipeline_execution_id="P1", workflow_execution_id="E1",
                pipeline_database_id="pdb", pipeline_id="pid", end_state_pipeline=False,
                s3_asset_bucket="abkt", s3_aux_bucket="auxbkt",
                output_prefixes={"files": "f/", "previews": "p/", "metadata": "m/", "results": "r/"},
                input_metadata_file_prefix="", input_config_file_prefix="ic/",
                aux_temp_prefix="t/", aux_preview_prefix="",
                pipeline_execution_type="Lambda", wait_for_callback="Disabled",
                pipeline_resource_arn="arn:fn", from_pipeline_execution_id=empty,
            )
            assert "from_pipeline_execution_id" not in rec

    def test_build_pipeline_execution_record_keeps_chain_key_when_set(self):
        # A chained pipeline records its parent's id under the chain-GSI sort key.
        rec = er.build_pipeline_execution_record(
            pipeline_execution_id="P2", workflow_execution_id="E1",
            pipeline_database_id="pdb", pipeline_id="pid2", end_state_pipeline=True,
            s3_asset_bucket="abkt", s3_aux_bucket="auxbkt",
            output_prefixes={"files": "f/", "previews": "p/", "metadata": "m/", "results": "r/"},
            input_metadata_file_prefix="", input_config_file_prefix="ic/",
            aux_temp_prefix="t/", aux_preview_prefix="",
            pipeline_execution_type="SQS", wait_for_callback="Enabled",
            pipeline_resource_arn="url", from_pipeline_execution_id="P1",
        )
        assert rec["from_pipeline_execution_id"] == "P1"

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
        assert rec["versionId"] == ""  # default when not resolved

    def test_build_workflow_execution_input_record_captures_version(self):
        rec = er.build_workflow_execution_input_record(
            workflow_execution_id="E1", database_id="db", asset_id="a1",
            input_asset_file_key="x.glb", execution_start_date="2026-06-16T00:00:00Z",
            workflow_id="wf", workflow_database_id="wdb", version_id="s3-ver-123",
        )
        assert rec["versionId"] == "s3-ver-123"


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

    def test_workflow_configuration_record(self):
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", input_metadata='{"VAMS":{}}', specified_pipelines_snapshot=[{"name": "p"}],
        )
        assert rec["workflowExecutionId"] == "E1"
        assert rec["recordType"] == "configuration"
        assert rec["specifiedPipelinesSnapshot"] == [{"name": "p"}]
        # Output base-execution path extension defaults to '/' (no extra path segment).
        assert rec["outputFileBaseExecutionPathExtension"] == "/"

    def test_workflow_configuration_record_carries_path_extension(self):
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", input_metadata="",
            specified_pipelines_snapshot=[],
            output_file_base_execution_path_extension="/exec-2026/",
        )
        assert rec["outputFileBaseExecutionPathExtension"] == "/exec-2026/"

    def test_metadata_envelope_rows_reads_the_grouped_schema(self):
        # The details page reads DynamoDB rows, not the S3 envelope, so this flattening is what makes
        # input metadata visible at all. Asset-level metadata lives on the '/' record: a reader that
        # walks only per-FILE records reports "no input metadata" for an asset full of it.
        envelope = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group(
                "db1", "a1",
                files=[
                    er.build_metadata_file_record("/", metadata={"GROOT_MAX_STEPS": "60"}),
                    er.build_metadata_file_record("/x.glb", metadata={"kind": "mesh"}),
                ]),
        ])
        rows = list(er.metadata_envelope_rows(envelope))
        assert {r["filePath"] for r in rows} == {"/", "/x.glb"}
        asset_row = next(r for r in rows if r["filePath"] == "/")
        assert asset_row["metadata"] == {"GROOT_MAX_STEPS": "60"}
        assert asset_row["databaseId"] == "db1" and asset_row["assetId"] == "a1"

    def test_metadata_envelope_rows_skips_records_with_no_metadata(self):
        # The envelope always emits a '/' record per asset so the file list is uniform, even when the
        # asset carries nothing. Persisting those would fill the details response with empty rows.
        envelope = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "a1", files=[
                er.build_metadata_file_record("/", metadata=None),
                er.build_metadata_file_record("/x.glb", metadata={}),
            ]),
        ])
        assert list(er.metadata_envelope_rows(envelope)) == []

    def test_metadata_envelope_rows_spans_multiple_assets(self):
        envelope = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "a1", files=[
                er.build_metadata_file_record("/", metadata={"k": "1"})]),
            er.build_metadata_asset_group("db2", "a2", files=[
                er.build_metadata_file_record("/", metadata={"k": "2"})]),
        ])
        rows = list(er.metadata_envelope_rows(envelope))
        assert {(r["databaseId"], r["assetId"]) for r in rows} == {("db1", "a1"), ("db2", "a2")}

    def test_metadata_envelope_rows_accepts_the_legacy_flat_shape(self):
        # A caller holding either envelope version must work; misreading the grouped envelope as the
        # legacy one is what made a working capture look like a broken one.
        rows = list(er.metadata_envelope_rows({"VAMS": {"assetMetadata": {"k": "v"}}}))
        assert rows == [{"databaseId": "", "assetId": "", "filePath": "/", "metadata": {"k": "v"},
                         "attributes": {}, "scope": "asset"}]

    @pytest.mark.parametrize("payload", [None, {}, [], "text", {"assets": []}])
    def test_metadata_envelope_rows_tolerates_junk(self, payload):
        assert list(er.metadata_envelope_rows(payload)) == []

    def test_configuration_record_indexes_by_output_asset(self):
        # Partition for WorkflowExecConfigByOutputAssetGSI. Without it an asset's execution history
        # cannot include runs that only WROTE to the asset — and a results-only or arity-'none'
        # pipeline has no input rows at all, so the output target is its only association.
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", input_metadata="",
            specified_pipelines_snapshot=[], output_location_type="asset",
            output_asset_id="a1", output_database_id="db1",
            execution_start_date="2026-08-02T00:00:00Z",
        )
        assert rec["outputDatabaseId:outputAssetId"] == "db1:a1"
        assert rec["executionStartDate"] == "2026-08-02T00:00:00Z"

    def test_results_only_execution_is_absent_from_the_output_asset_index(self):
        # The index is sparse ON PURPOSE: a results-only run writes to no asset, so indexing it under
        # an empty partition would collect every such run into one hot key and surface them on an
        # unrelated asset's history.
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", input_metadata="",
            specified_pipelines_snapshot=[], output_location_type="none",
        )
        assert "outputDatabaseId:outputAssetId" not in rec

    def test_asset_output_with_unresolved_destination_is_not_indexed(self):
        # 'asset' with no ids resolved yet must not produce a partial key like "db1:" or ":a1".
        for kwargs in ({"output_asset_id": "a1"}, {"output_database_id": "db1"}, {}):
            rec = er.build_workflow_configuration_record(
                workflow_execution_id="E1", input_metadata="",
                specified_pipelines_snapshot=[], output_location_type="asset", **kwargs)
            assert "outputDatabaseId:outputAssetId" not in rec, kwargs

    def test_output_asset_partition_key_matches_the_input_gsi_shape(self):
        # The by-input-asset GSI partitions on 'databaseId:assetId'; using the same shape means a
        # caller listing one asset builds the key the same way for both directions.
        assert er.output_asset_partition_key("db1", "a1") == "db1:a1"

    def test_configuration_record_defaults_its_start_date(self):
        # An omitted start date must still be sortable rather than empty, or the row would land at the
        # bottom of every newest-first listing.
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", input_metadata="",
            specified_pipelines_snapshot=[])
        assert rec["executionStartDate"]

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

    def test_input_metadata_record_defaults_to_asset_scope(self):
        # A row written without an explicit scope is an asset row, so pre-existing rows and callers
        # that pass no scope group with asset metadata rather than falling out of both groups.
        md = er.build_input_metadata_record(
            pipeline_execution_id="P1", database_id="db", asset_id="a1", file_path="/x.glb",
            metadata={"k": "v"}, source_input_metadata_file_s3_key="im/x.json")
        assert md["scope"] == "asset"

    def test_input_metadata_record_carries_database_scope(self):
        # A database row has no asset, so its SK is '{databaseId}::/' — a non-empty composite, and the
        # table has no GSI, so the empty assetId cannot produce an index key.
        md = er.build_input_metadata_record(
            pipeline_execution_id="P1", database_id="db1", asset_id="", file_path="",
            metadata={"region": "us-east-1"}, source_input_metadata_file_s3_key="im/x.json",
            scope="database")
        assert md["scope"] == "database"
        assert md["databaseId:assetId:filePath"] == "db1::/"
        assert md["filePath"] == "/" and md["assetId"] == ""

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


@pytest.mark.unit
class TestDatabaseMetadataSection:
    """Database metadata is a read-only input belonging to no asset, so it is carried as its own
    top-level 'databases' list beside assets[] rather than inside an asset group — a list because a run
    over input files spanning several databases captures each of them.
    """

    @staticmethod
    def _asset_only_envelope():
        return er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "a1", asset_data={"assetName": "n"}, files=[
                er.build_metadata_file_record("/", metadata={"a": "1"})])])

    def test_no_source_envelope_is_unchanged(self):
        # Every pipeline reader gates on schemaVersion == 2 by EQUALITY, so the version must not move;
        # and with no source supplied the envelope must carry exactly the two keys it always did —
        # absent, never an empty section, so absence alone means "no source".
        env = self._asset_only_envelope()
        assert set(env) == {"schemaVersion", "assets"}
        assert env["schemaVersion"] == 2 == er.METADATA_SCHEMA_VERSION_GROUPED
        assert "databases" not in env

    @pytest.mark.parametrize("empty", [None, []])
    def test_an_empty_databases_argument_emits_no_section(self, empty):
        # An empty list would read as "sources were supplied that have no metadata"; the two cases must
        # stay distinguishable, exactly as assets[] absence does.
        assert "databases" not in er.build_grouped_metadata_envelope([], databases=empty)

    def test_database_group_shape(self):
        group = er.build_metadata_database_group("db1", {"region": "us-east-1"})
        assert group == {"databaseId": "db1", "metadata": {"region": "us-east-1"}}
        # A source with no metadata of its own still identifies the database it came from.
        assert er.build_metadata_database_group("db1") == {"databaseId": "db1", "metadata": {}}

    def test_envelope_carries_the_databases_list_beside_assets(self):
        env = er.build_grouped_metadata_envelope(
            [er.build_metadata_asset_group("db1", "a1", files=[])],
            databases=[er.build_metadata_database_group("dbsrc", {"region": "us-east-1"})])
        assert env["schemaVersion"] == 2
        assert env["databases"] == [{"databaseId": "dbsrc", "metadata": {"region": "us-east-1"}}]
        # assets[] is untouched: the list is a sibling, not an asset group.
        assert env["assets"][0]["assetId"] == "a1"
        assert all("databases" not in group for group in env["assets"])

    def test_envelope_carries_every_captured_database(self):
        env = er.build_grouped_metadata_envelope([], databases=[
            er.build_metadata_database_group("db1", {"a": "1"}),
            er.build_metadata_database_group("db2", {"b": "2"}),
            er.build_metadata_database_group("db3", {"c": "3"})])
        assert [g["databaseId"] for g in env["databases"]] == ["db1", "db2", "db3"]

    def test_legacy_view_projects_five_scopes(self):
        env = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "a1", asset_data={"assetName": "n"}, files=[
                er.build_metadata_file_record("/", metadata={"am": "1"}),
                er.build_metadata_file_record("/x.glb", metadata={"fm": "2"},
                                              attributes={"fa": "3"})])],
            databases=[er.build_metadata_database_group("db1", {"dm": "4"})])
        view = er.to_legacy_vams_view(env, "db1", "a1", "/x.glb")["VAMS"]
        assert set(view) == {"assetData", "assetMetadata", "fileMetadata", "fileAttributes",
                             "databaseMetadata"}
        assert view["assetData"] == {"assetName": "n"}
        assert view["assetMetadata"] == {"am": "1"}
        assert view["fileMetadata"] == {"fm": "2"}
        assert view["fileAttributes"] == {"fa": "3"}
        assert view["databaseMetadata"] == {"dm": "4"}

    def test_legacy_view_projects_the_database_being_projected(self):
        # The five scopes must describe ONE coherent (database, asset, file) subject: a pipeline task
        # handed db2's asset must see db2's database metadata, not db1's (or a merge of both).
        env = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "a1", files=[
                er.build_metadata_file_record("/", metadata={"am": "1"})]),
            er.build_metadata_asset_group("db2", "a2", files=[
                er.build_metadata_file_record("/", metadata={"am": "2"})])],
            databases=[er.build_metadata_database_group("db1", {"site": "plant-1"}),
                       er.build_metadata_database_group("db2", {"site": "plant-2"})])
        assert er.to_legacy_vams_view(env, "db1", "a1", "/")["VAMS"]["databaseMetadata"] == {
            "site": "plant-1"}
        assert er.to_legacy_vams_view(env, "db2", "a2", "/")["VAMS"]["databaseMetadata"] == {
            "site": "plant-2"}

    def test_legacy_view_resolves_a_lone_database_whatever_the_subject(self):
        # A named metadata-source database is not necessarily an input asset's database, and a file-less
        # run has no asset to project through at all, so its subject databaseId is empty. One captured
        # database is unambiguous, so it resolves for every subject rather than reporting {}.
        env = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("otherdb", "a2", files=[
                er.build_metadata_file_record("/", metadata={"am": "1"})])],
            databases=[er.build_metadata_database_group("dbsrc", {"dm": "4"})])
        for db, asset, fk in (("otherdb", "a2", "/"), ("otherdb", "a2", "/missing.glb"),
                              ("nosuchdb", "nosuchasset", "/"), ("", "", "/")):
            view = er.to_legacy_vams_view(env, db, asset, fk)["VAMS"]
            assert view["databaseMetadata"] == {"dm": "4"}, (db, asset, fk)

    def test_legacy_view_keeps_several_databases_attributed_to_their_own(self):
        # With more than one captured database the requested id is the only thing that can tell them
        # apart, so each subject sees its own and an unrelated subject sees nothing.
        env = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "a1", files=[
                er.build_metadata_file_record("/", metadata={"am": "1"})]),
            er.build_metadata_asset_group("db2", "a2", files=[
                er.build_metadata_file_record("/", metadata={"am": "2"})])],
            databases=[er.build_metadata_database_group("db1", {"dm": "1"}),
                       er.build_metadata_database_group("db2", {"dm": "2"})])
        assert er.to_legacy_vams_view(env, "db1", "a1", "/")["VAMS"]["databaseMetadata"] == {"dm": "1"}
        assert er.to_legacy_vams_view(env, "db2", "a2", "/")["VAMS"]["databaseMetadata"] == {"dm": "2"}
        assert er.to_legacy_vams_view(env, "db3", "a3", "/")["VAMS"]["databaseMetadata"] == {}

    def test_legacy_view_database_scope_is_empty_without_a_section(self):
        # A renderer tag reading the scope must get {} rather than KeyError when no source was supplied.
        view = er.to_legacy_vams_view(self._asset_only_envelope(), "db1", "a1", "/")["VAMS"]
        assert view["databaseMetadata"] == {}

    def test_get_database_metadata_reads_one_entry(self):
        env = er.build_grouped_metadata_envelope([], databases=[
            er.build_metadata_database_group("db1", {"a": "1"}),
            er.build_metadata_database_group("db2", {"b": "2"})])
        assert er.get_database_metadata(env, "db2") == {"b": "2"}
        assert er.get_database_metadata(env, "nosuch") == {}
        assert er.get_database_metadata({}, "db1") == {}

    def test_each_database_flattens_to_its_own_row(self):
        # The details response reads DynamoDB rows, not the S3 envelope, so without these rows the
        # captured database metadata is invisible to the execution details page and APIs — and each
        # database needs its OWN row or a multi-database run loses all but one.
        env = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "a1", files=[
                er.build_metadata_file_record("/", metadata={"am": "1"})])],
            databases=[er.build_metadata_database_group("db1", {"dm": "4"}),
                       er.build_metadata_database_group("db2", {"dm": "5"})])
        rows = list(er.metadata_envelope_rows(env))
        db_rows = [r for r in rows if r["scope"] == "database"]
        assert db_rows == [
            {"databaseId": "db1", "assetId": "", "filePath": "/", "metadata": {"dm": "4"},
             "attributes": {}, "scope": "database"},
            {"databaseId": "db2", "assetId": "", "filePath": "/", "metadata": {"dm": "5"},
             "attributes": {}, "scope": "database"}]
        # The asset rows still come through, discriminated as asset scope.
        assert [r["scope"] for r in rows if r not in db_rows] == ["asset"]

    def test_a_database_entry_with_no_metadata_yields_no_row(self):
        # Same skip rule as an asset record carrying nothing: an empty row would only pad the details
        # response. The populated siblings still produce theirs.
        env = er.build_grouped_metadata_envelope([], databases=[
            er.build_metadata_database_group("dbsrc", {}),
            er.build_metadata_database_group("dbother", {"dm": "4"})])
        rows = list(er.metadata_envelope_rows(env))
        assert [r["databaseId"] for r in rows] == ["dbother"]

    def test_database_row_builds_a_persistable_record(self):
        env = er.build_grouped_metadata_envelope(
            [], databases=[er.build_metadata_database_group("dbsrc", {"dm": "4"})])
        row = next(iter(er.metadata_envelope_rows(env)))
        rec = er.build_input_metadata_record(
            pipeline_execution_id="P1", database_id=row["databaseId"], asset_id=row["assetId"],
            file_path=row["filePath"], metadata=row["metadata"],
            source_input_metadata_file_s3_key="im/x.json", scope=row["scope"])
        assert rec["databaseId:assetId:filePath"] == "dbsrc::/"
        assert rec["scope"] == "database" and rec["metadata"] == {"dm": "4"}

    def test_each_database_row_gets_a_distinct_sort_key(self):
        # The SK is 'databaseId:assetId:filePath'; N databases must not collide onto one row.
        env = er.build_grouped_metadata_envelope([], databases=[
            er.build_metadata_database_group("db1", {"dm": "1"}),
            er.build_metadata_database_group("db2", {"dm": "2"})])
        keys = [er.build_input_metadata_record(
            pipeline_execution_id="P1", database_id=r["databaseId"], asset_id=r["assetId"],
            file_path=r["filePath"], metadata=r["metadata"],
            source_input_metadata_file_s3_key="", scope=r["scope"])["databaseId:assetId:filePath"]
            for r in er.metadata_envelope_rows(env)]
        assert keys == ["db1::/", "db2::/"]
