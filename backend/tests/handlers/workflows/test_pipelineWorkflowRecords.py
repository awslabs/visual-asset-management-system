# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Phase-2 pipeline/workflow V2 pure record builders and the hybrid
template body-storage + default-bucket helpers. These modules have no AWS/env dependencies
(the S3/DynamoDB helpers take injected clients/tables), so they test in isolation."""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.common.workflows import pipelineRecords as pr
from backend.backend.common.workflows import workflowRecords as wr
from backend.backend.common.workflows import templateBodyStorage as tbs
from backend.backend.common.workflows import defaultBucket as db
from backend.backend.common.workflows import executionRecords as er


# ============================ pipelineRecords ============================

@pytest.mark.unit
class TestPipelineRecords:
    def test_pipeline_record_keys_and_defaults(self):
        rec = pr.build_pipeline_record(
            database_id="db1", pipeline_id="p1", pipeline_name="My Pipe",
            category="conversion", description="desc",
            execution_config=pr.build_pipeline_execution_config(execution_type="Lambda"),
            system_config=pr.build_pipeline_system_config(),
        )
        assert rec["databaseId"] == "db1"          # PK
        assert rec["pipelineId"] == "p1"            # SK
        assert rec["databaseId:category"] == "db1:conversion"  # GSI PK
        assert rec["enabled"] is True and rec["archived"] is False
        assert rec["schemaVersion"] == pr.PIPELINE_SCHEMA_VERSION
        # timestamps auto-filled ISO-8601 Z
        assert rec["dateCreated"].endswith("Z") and rec["dateModified"].endswith("Z")

    def test_pipeline_system_config_defaults_single_asset(self):
        sc = pr.build_pipeline_system_config()
        assert sc["inputFileArity"] == "one"
        assert sc["assetScope"]["singleAssetOnly"] is True
        assert sc["metadataInputs"] == {
            "assetMetadata": True, "fileMetadata": True, "fileAttributes": True,
            "databaseMetadata": True}
        assert sc["requireTemplate"] is False
        assert sc["inputFileFilters"] == {"allow": [], "exclude": []}
        # allowNoTemplate is fully removed — only requireTemplate remains
        assert "allowNoTemplate" not in sc

    def test_execution_config_lambda_keyword_key(self):
        ec = pr.build_pipeline_execution_config(
            execution_type="Lambda", lambda_config={"resourceId": "fn"})
        assert ec["executionType"] == "Lambda"
        assert ec["lambda"] == {"resourceId": "fn"}

    def test_template_record_inline_body(self):
        rec = pr.build_template_record(
            pipeline_database_id="db1", pipeline_id="p1", template_id="t1",
            template_name="tmpl", description="d", config_format="yaml",
            input_instructions="Provide a prompt.", config_body="key: {{tag}}",
        )
        assert rec["pipelineDatabaseId:pipelineId"] == "db1:p1"  # PK
        assert rec["templateId"] == "t1"                          # SK
        assert rec["configFormat"] == "yaml"
        assert rec["bodyStorage"] == pr.BODY_STORAGE_INLINE
        assert rec["inputInstructions"] == "Provide a prompt."   # moved from pipeline
        assert rec["overrides"] == {}

    def test_template_overrides_carried(self):
        rec = pr.build_template_record(
            pipeline_database_id="db1", pipeline_id="p1", template_id="t1",
            template_name="tmpl", description="d",
            overrides={"inputFileArity": "multi"},
        )
        assert rec["overrides"] == {"inputFileArity": "multi"}

    def test_tag_schema_record_inline_json_string(self):
        # Mirrors MetadataSchemaStorageTableV2: fields stored inline as a JSON string.
        fields = [{"tagKey": "prompt", "type": "string", "required": True}]
        rec = pr.build_tag_schema_record(
            pipeline_database_id="db1", pipeline_id="p1", template_id="t1", fields=fields,
        )
        assert rec["pipelineDatabaseId:pipelineId:templateId"] == "db1:p1:t1"  # SK / GSI PK
        assert isinstance(rec["fields"], str)
        assert json.loads(rec["fields"]) == fields
        assert len(rec["tagSchemaId"]) == 32  # UUID hex

    def test_tag_schema_record_s3_offload_empties_fields(self):
        rec = pr.build_tag_schema_record(
            pipeline_database_id="db1", pipeline_id="p1", template_id="t1",
            fields=[{"tagKey": "x"}], body_storage=pr.BODY_STORAGE_S3,
            fields_s3_key="pipelines/.../tagSchema.json",
        )
        assert rec["fields"] == ""
        assert rec["fieldsS3Key"] == "pipelines/.../tagSchema.json"


# ============================ workflowRecords ============================

@pytest.mark.unit
class TestWorkflowRecords:
    def test_workflow_record_keys(self):
        refs = [wr.build_specified_pipeline_ref("db1", "p1", "job-p1")]
        rec = wr.build_workflow_record(
            database_id="db1", workflow_id="wf1", workflow_name="W", category="cat",
            description="d", specified_pipelines=refs,
            system_config=wr.build_workflow_system_config(),
        )
        assert rec["databaseId"] == "db1"           # PK
        assert rec["workflowId"] == "wf1"           # SK
        assert rec["databaseId:category"] == "db1:cat"
        assert rec["specifiedPipelines"][0]["pipelineDatabaseId:pipelineId"] == "db1:p1"
        assert rec["schemaVersion"] == wr.WORKFLOW_SCHEMA_VERSION

    def test_specified_pipeline_ref_stores_both_ids(self):
        ref = wr.build_specified_pipeline_ref("pdb", "pid", "job")
        assert ref["pipelineDatabaseId"] == "pdb"
        assert ref["pipelineId"] == "pid"
        assert ref["pipelineDatabaseId:pipelineId"] == "pdb:pid"

    def test_specified_pipeline_ref_model_round_trips_persisted_fields(self):
        # The model documents the stored ref shape, so parsing a built ref must not drop a field the
        # builder persists (extra='ignore' would silently discard it). The composite key is derived.
        from backend.backend.models.workflows import SpecifiedPipelineRef
        built = wr.build_specified_pipeline_ref("pdb", "pid", "job", "tmpl1")
        parsed = SpecifiedPipelineRef(**built).dict()
        assert set(built) - set(parsed) == {"pipelineDatabaseId:pipelineId"}
        assert parsed["defaultTemplateId"] == "tmpl1"

    def test_trigger_record_keys_and_config(self):
        cfg = wr.build_file_upload_trigger_config(
            input_file_filters={"allow": ["*.glb"], "exclude": []},
            default_template_ids={"db1:p1": "t1"},
        )
        rec = wr.build_trigger_record("db1", "wf1", "fileUpload", cfg)
        assert rec["workflowDatabaseId:workflowId"] == "db1:wf1"  # PK
        assert rec["triggerType"] == "fileUpload"                 # SK
        assert rec["triggerConfig"]["defaultTemplateIds"] == {"db1:p1": "t1"}
        assert rec["triggerConfig"]["inputFileFilters"]["allow"] == ["*.glb"]



# ============================ templateBodyStorage ============================

@pytest.mark.unit
class TestTemplateBodyStorage:
    def test_small_body_stays_inline(self):
        assert tbs.should_offload("small", "form") is False
        plan = tbs.plan_body_storage("small", "form")
        assert plan["bodyStorage"] == tbs.BODY_STORAGE_INLINE
        assert plan["offload"] is False

    def test_large_body_offloads(self):
        big = "x" * (tbs.INLINE_THRESHOLD_BYTES + 1)
        assert tbs.should_offload(big, "") is True
        plan = tbs.plan_body_storage(big, "")
        assert plan["bodyStorage"] == tbs.BODY_STORAGE_S3 and plan["offload"] is True

    def test_combined_size_counts_both_fields(self):
        # combined size drives the decision, not either field alone
        half = "y" * (tbs.INLINE_THRESHOLD_BYTES // 2 + 10)
        assert tbs.should_offload(half, half) is True

    def test_cap_enforced(self):
        over = "z" * (tbs.ABSOLUTE_CAP_BYTES + 1)
        with pytest.raises(tbs.TemplateBodyTooLargeError):
            tbs.assert_within_cap(over, "")

    def test_cap_ok_returns_size(self):
        assert tbs.assert_within_cap("abc", "de") == 5

    def test_content_hash_stable_and_distinct(self):
        assert tbs.content_hash("a") == tbs.content_hash("a")
        assert tbs.content_hash("a") != tbs.content_hash("b")
        assert tbs.content_hash("") == tbs.content_hash(None)

    def test_deterministic_keys(self):
        assert tbs.config_body_s3_key("db", "p", "t") == "pipelines/templates/db/p/t/configBody"
        assert tbs.web_form_s3_key("db", "p", "t") == "pipelines/templates/db/p/t/webForm.json"
        assert tbs.tag_schema_s3_key("db", "p", "t") == "pipelines/templates/db/p/t/tagSchema.json"

    def test_rehydrate_inline_row(self):
        row = {"bodyStorage": "inline", "configBody": "cb", "webFormJson": "wf"}
        out = tbs.rehydrate_template_bodies(MagicMock(), "bucket", row)
        assert out == {"configBody": "cb", "webFormJson": "wf"}

    def test_rehydrate_s3_row_reads_from_bucket(self):
        s3 = MagicMock()
        s3.get_object.side_effect = lambda Bucket, Key: {
            "Body": MagicMock(read=lambda k=Key: (b"CB" if k.endswith("configBody") else b"WF"))
        }
        row = {
            "bodyStorage": "s3",
            "configBodyS3Key": "pipelines/templates/db/p/t/configBody",
            "webFormS3Key": "pipelines/templates/db/p/t/webForm.json",
        }
        out = tbs.rehydrate_template_bodies(s3, "bucket", row)
        assert out == {"configBody": "CB", "webFormJson": "WF"}

    def test_write_and_read_roundtrip_via_injected_client(self):
        store = {}
        s3 = MagicMock()
        s3.put_object.side_effect = lambda Bucket, Key, Body: store.__setitem__(Key, Body)
        s3.get_object.side_effect = lambda Bucket, Key: {
            "Body": MagicMock(read=lambda k=Key: store[k])}
        tbs.write_body_to_s3(s3, "b", "k1", "hello")
        assert tbs.read_body_from_s3(s3, "b", "k1") == "hello"


# ============================ defaultBucket resolver ============================

@pytest.mark.unit
class TestDefaultBucketResolver:
    def _table_with(self, items):
        table = MagicMock()
        table.scan.return_value = {"Items": items}
        return table

    def test_resolves_default_row(self):
        table = self._table_with([
            {"bucketId": "b1", "bucketName": "vams-bucket", "baseAssetsPrefix": "/",
             "isDefault": True},
        ])
        out = db.resolve_default_bucket(table)
        assert out == {"bucketId": "b1", "bucketName": "vams-bucket", "baseAssetsPrefix": "/"}

    def test_prefers_root_prefix_row(self):
        table = self._table_with([
            {"bucketId": "b1", "bucketName": "vams-bucket", "baseAssetsPrefix": "sub/",
             "isDefault": True},
            {"bucketId": "b1", "bucketName": "vams-bucket", "baseAssetsPrefix": "/",
             "isDefault": True},
        ])
        out = db.resolve_default_bucket(table)
        assert out["baseAssetsPrefix"] == "/"

    def test_raises_when_no_default(self):
        table = self._table_with([])
        with pytest.raises(db.DefaultBucketNotFoundError):
            db.resolve_default_bucket(table)

    def test_multiple_default_buckets_raise_rather_than_picking_one(self):
        """A second isDefault row is a bucket that left the configuration and no longer carries VAMS's
        grants, so picking either name would send template bodies and run I/O somewhere that may reject
        the write. The ambiguity surfaces instead, in both row orders."""
        rows = [
            {"bucketId": "b2", "bucketName": "zzz-current-default", "baseAssetsPrefix": "/",
             "isDefault": True},
            {"bucketId": "b1", "bucketName": "aaa-removed-bucket", "baseAssetsPrefix": "/",
             "isDefault": True},
        ]
        for ordered in (rows, list(reversed(rows))):
            with pytest.raises(db.DefaultBucketAmbiguousError):
                db.resolve_default_bucket(self._table_with(ordered))
        # Callers that already treat an unresolvable default bucket as fatal need no new arm.
        assert issubclass(db.DefaultBucketAmbiguousError, db.DefaultBucketNotFoundError)

    def test_default_bucket_key_joins_the_registered_prefix(self):
        """A key builder produces the key relative to the area VAMS owns. An external bucket
        registered under a prefix scopes VAMS to that prefix, so a bucket-root-relative write lands
        outside it (and 403s under the normal cross-account bucket policy)."""
        prefixed = {"bucketId": "b1", "bucketName": "customer", "baseAssetsPrefix": "vams/"}
        assert db.default_bucket_key(
            prefixed, "pipelines/templates/db/p/t/configBody") == "vams/pipelines/templates/db/p/t/configBody"
        # A leading slash on the key does not produce a double slash.
        assert db.default_bucket_key(prefixed, "/pipelines/x") == "vams/pipelines/x"
        # A root-prefix bucket is unchanged.
        for root in ("/", "", None):
            assert db.default_bucket_key(
                {"baseAssetsPrefix": root}, "pipelines/x") == "pipelines/x"

    def test_paginates_scan(self):
        table = MagicMock()
        table.scan.side_effect = [
            {"Items": [], "LastEvaluatedKey": {"bucketId": "x"}},
            {"Items": [{"bucketId": "b2", "bucketName": "n", "baseAssetsPrefix": "/",
                        "isDefault": True}]},
        ]
        out = db.resolve_default_bucket(table)
        assert out["bucketId"] == "b2"
        assert table.scan.call_count == 2


# ============================ executionRecords config snapshot ============================

@pytest.mark.unit
class TestConfigSnapshotExtension:
    def test_config_record_snapshots_template_metadata(self):
        rec = er.build_input_configuration_record(
            pipeline_execution_id="pe1",
            input_configuration="rendered body",
            input_configuration_file_s3_key="pipelines/.../config.json",
            template_id="t1", template_schema_version="1", tag_schema_version="1",
            template_tags=[{"key": "prompt", "value": "hi"}],
            custom_template_override_used=True,
        )
        assert rec["templateId"] == "t1"
        assert rec["templateTags"] == [{"key": "prompt", "value": "hi"}]
        assert rec["customTemplateOverrideUsed"] is True
        assert rec["inputConfiguration"] == "rendered body"

    def test_config_record_backward_compatible_defaults(self):
        # Old call sites (no snapshot args) still work with empty snapshot fields.
        rec = er.build_input_configuration_record("pe1", "body", "key")
        assert rec["templateId"] == ""
        assert rec["templateTags"] == []
        assert rec["customTemplateOverrideUsed"] is False
