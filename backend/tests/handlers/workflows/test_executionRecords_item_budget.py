# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every execution record must be storable: within the 400 KB DynamoDB item limit and free of types
DynamoDB rejects.

These rows are written AFTER start_execution, so a record the builder produces but put_item refuses
force-stops a running workflow and answers a request that passed every validator with a 500. Each
builder therefore accounts for ALL of its variable-size fields against one budget, and flags whatever
it trims.
"""

import json
from decimal import Decimal

import pytest

from backend.backend.common.workflows import executionRecords as er


def item_bytes(record):
    """Serialized size of a record, the measure DynamoDB's item limit applies to."""
    return len(json.dumps(record, default=str).encode("utf-8"))


def has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_float(v) for v in value)
    return False


@pytest.mark.unit
class TestTemplateTagNumerics:
    """A tag value is caller-supplied, and boto3 raises TypeError on a float — so a 'number'-typed tag
    with a fractional value would fail the write of an already-running execution."""

    def test_fractional_tag_values_are_stored_as_decimals(self):
        rec = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="{}",
            input_configuration_file_s3_key="k",
            template_tags=[{"key": "scale", "value": 1.5},
                           {"key": "sizes", "value": [0.25, "a", 3]},
                           {"key": "nested", "value": {"weight": 0.5}}])
        assert not has_float(rec["templateTags"])
        assert rec["templateTags"][0]["value"] == Decimal("1.5")
        assert rec["templateTags"][1]["value"][0] == Decimal("0.25")
        assert rec["templateTags"][2]["value"]["weight"] == Decimal("0.5")
        # An integral value stays an int rather than becoming a Decimal-with-exponent surprise.
        assert rec["templateTags"][1]["value"][2] == 3

    def test_a_non_finite_tag_value_degrades_to_text(self):
        # DynamoDB stores no NaN/Infinity; keeping the tag as text preserves the snapshot instead of
        # failing the launch.
        rec = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="{}",
            input_configuration_file_s3_key="k",
            template_tags=[{"key": "ratio", "value": float("inf")}])
        assert rec["templateTags"][0]["value"] == "inf"

    def test_metadata_and_attribute_floats_are_normalized(self):
        # Asset/file metadata is equally caller-authored, and lands on its own item.
        rec = er.build_input_metadata_record(
            pipeline_execution_id="pe1", database_id="db", asset_id="a1", file_path="/f.glb",
            metadata={"scale": 1.5}, source_input_metadata_file_s3_key="im/x.json",
            attributes={"ratio": 0.25})
        assert rec["metadata"]["scale"] == Decimal("1.5")
        assert rec["attributes"]["ratio"] == Decimal("0.25")


@pytest.mark.unit
class TestInputConfigurationItemBudget:
    """The config-snapshot row's tag list and two config maps are variable-size fields on the same item
    as its two text bodies, so all five share the one item budget."""

    def test_many_long_tags_keep_the_item_storable(self):
        # 50 tags at 8 KB each is within the per-request bounds and alone exceeds 400 KB.
        tags = [{"key": f"prompt{i}", "value": "x" * 8192} for i in range(50)]
        rec = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="{" + "a" * 300 * 1024 + "}",
            input_configuration_file_s3_key="k", template_tags=tags,
            custom_template_override_used=True, custom_template_override="y" * 200 * 1024)
        assert item_bytes(rec) <= er.MAX_ITEM_BYTES

    def test_the_legal_maximum_tag_value_still_fits(self):
        # One tag at the documented per-value maximum alongside a config body at the text budget.
        rec = er.build_input_configuration_record(
            pipeline_execution_id="pe1",
            input_configuration="{" + "a" * er.MAX_TEXT_FIELD_BYTES + "}",
            input_configuration_file_s3_key="k",
            template_tags=[{"key": "prompt", "value": "x" * 65536}])
        assert item_bytes(rec) <= er.MAX_ITEM_BYTES

    def test_oversized_config_blocks_are_bounded_too(self):
        big = {f"k{i}": "v" * 10000 for i in range(200)}
        rec = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="a" * 400 * 1024,
            input_configuration_file_s3_key="k",
            effective_system_config=big, template_overrides=big,
            custom_template_override_used=True, custom_template_override="y" * 400 * 1024)
        assert item_bytes(rec) <= er.MAX_ITEM_BYTES

    def test_anything_trimmed_is_flagged(self):
        tags = [{"key": f"prompt{i}", "value": "x" * 65536} for i in range(250)]
        rec = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="a" * 400 * 1024,
            input_configuration_file_s3_key="k", template_tags=tags,
            effective_system_config={f"k{i}": "v" * 10000 for i in range(200)})
        assert rec["templateTagsTruncated"] is True
        assert rec["effectiveSystemConfigTruncated"] is True
        assert rec["inputConfigurationTruncated"] is True
        assert len(rec["templateTags"]) < len(tags)

    def test_an_ordinary_snapshot_is_flagged_whole(self):
        rec = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration='{"a":1}',
            input_configuration_file_s3_key="k",
            template_tags=[{"key": "prompt", "value": "hello"}],
            effective_system_config={"inputFileArity": "one"},
            template_overrides={"inputFileArity": "one"})
        assert rec["templateTags"] == [{"key": "prompt", "value": "hello"}]
        assert rec["templateTagsTruncated"] is False
        assert rec["effectiveSystemConfigTruncated"] is False
        assert rec["templateOverridesTruncated"] is False


@pytest.mark.unit
class TestInputMetadataItemBudget:
    """metadata and attributes are bounded per ENTITY by the metadata service, and both land on one
    row — so the row bounds the pair, not each map."""

    def test_two_full_maps_fit_one_item(self):
        # Each map is within the metadata service's own per-entity ceiling; together they were not.
        metadata = {f"k{i:04d}": "v" * 400 for i in range(500)}
        attributes = {f"a{i:04d}": "v" * 400 for i in range(500)}
        rec = er.build_input_metadata_record(
            pipeline_execution_id="pe1", database_id="db", asset_id="a1", file_path="/f.glb",
            metadata=metadata, source_input_metadata_file_s3_key="im/x.json",
            attributes=attributes)
        assert item_bytes(rec) <= er.MAX_ITEM_BYTES
        assert rec["metadataTruncated"] is True
        assert rec["attributesTruncated"] is True

    def test_dropped_entries_are_the_same_set_every_time(self):
        # Deterministic (sorted-key) dropping means a re-run of the same selection records the same
        # rows, so the details view is stable rather than arbitrary per launch.
        metadata = {f"k{i:04d}": "v" * 800 for i in range(1000)}
        first = er.build_input_metadata_record(
            pipeline_execution_id="pe1", database_id="db", asset_id="a1", file_path="/f.glb",
            metadata=dict(metadata), source_input_metadata_file_s3_key="im/x.json")
        second = er.build_input_metadata_record(
            pipeline_execution_id="pe1", database_id="db", asset_id="a1", file_path="/f.glb",
            metadata=dict(reversed(list(metadata.items()))),
            source_input_metadata_file_s3_key="im/x.json")
        assert first["metadata"] == second["metadata"]
        assert sorted(first["metadata"]) == sorted(metadata)[:len(first["metadata"])]

    def test_an_ordinary_row_is_untouched_and_flagged_whole(self):
        rec = er.build_input_metadata_record(
            pipeline_execution_id="pe1", database_id="db", asset_id="a1", file_path="/f.glb",
            metadata={"k": "v"}, source_input_metadata_file_s3_key="im/x.json",
            attributes={"a": "b"})
        assert rec["metadata"] == {"k": "v"} and rec["attributes"] == {"a": "b"}
        assert rec["metadataTruncated"] is False and rec["attributesTruncated"] is False


@pytest.mark.unit
class TestWorkflowConfigurationItemBudget:
    """The step snapshot and the two metadata-source lists share the row's budget with the
    inputMetadata body, and the source lists have first claim because the read paths gate on them."""

    def test_many_sources_over_a_full_envelope_fit_one_item(self):
        sources = [{"databaseId": "d" * 20, "assetId": "a" * 20} for _ in range(300)]
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", input_metadata="m" * 400 * 1024, specified_pipelines_snapshot=[{"name": "p"}] * 20,
            metadata_source_assets=sources)
        assert item_bytes(rec) <= er.MAX_ITEM_BYTES
        # The sources are the run's authorization subjects, so they survive whole and the envelope
        # body (which is also written to S3 in full) is what gives way.
        assert rec["metadataSourceAssetsTruncated"] is False
        assert len(rec["metadataSourceAssets"]) == 300
        assert rec["inputMetadataTruncated"] is True

    def test_the_request_maximum_source_selection_fits(self):
        sources = [{"databaseId": "d" * 20, "assetId": "a" * 20} for _ in range(1000)]
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", input_metadata="m" * 400 * 1024,
            specified_pipelines_snapshot=[{"name": "p", "pipelineId": "x" * 63}] * 100,
            metadata_source_assets=sources)
        assert item_bytes(rec) <= er.MAX_ITEM_BYTES
        assert rec["metadataSourceAssetsTruncated"] is False
        assert rec["specifiedPipelinesSnapshotTruncated"] is False

    def test_an_unbounded_snapshot_and_source_list_are_bounded_and_flagged(self):
        sources = [{"databaseId": "d" * 256, "assetId": "a" * 256} for _ in range(1000)]
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", input_metadata="m" * 400 * 1024,
            specified_pipelines_snapshot=[{"name": "p" * 1000}] * 100,
            metadata_source_assets=sources,
            metadata_source_databases=["db" * 100] * 500)
        assert item_bytes(rec) <= er.MAX_ITEM_BYTES
        assert rec["metadataSourceAssetsTruncated"] is True
        assert rec["specifiedPipelinesSnapshotTruncated"] is True

    def test_an_ordinary_row_keeps_its_collections_whole(self):
        rec = er.build_workflow_configuration_record(
            workflow_execution_id="E1", input_metadata='{"VAMS":{}}', specified_pipelines_snapshot=[{"name": "p"}],
            metadata_source_assets=[{"databaseId": "db1", "assetId": "a1"}],
            metadata_source_databases=["db1", ""])
        assert rec["specifiedPipelinesSnapshot"] == [{"name": "p"}]
        assert rec["metadataSourceAssets"] == [{"databaseId": "db1", "assetId": "a1"}]
        assert rec["metadataSourceDatabases"] == ["db1"]
        assert rec["specifiedPipelinesSnapshotTruncated"] is False
        assert rec["metadataSourceAssetsTruncated"] is False
        assert rec["metadataSourceDatabasesTruncated"] is False


@pytest.mark.unit
class TestBudgetReserve:
    """The text budget must leave room for the keys and fixed attributes that ride on the same item."""

    def test_the_text_budget_leaves_a_reserve_under_the_item_limit(self):
        assert er.MAX_TEXT_FIELD_BYTES < er.MAX_ITEM_BYTES
        assert er.MAX_LOG_FIELD_BYTES < er.MAX_ITEM_BYTES

    def test_two_max_length_text_bodies_plus_the_reserve_fit(self):
        rec = er.build_input_configuration_record(
            pipeline_execution_id="pe1", input_configuration="a" * er.MAX_TEXT_FIELD_BYTES,
            input_configuration_file_s3_key="ic/x.json",
            custom_template_override_used=True,
            custom_template_override="b" * er.MAX_TEXT_FIELD_BYTES)
        assert item_bytes(rec) <= er.MAX_ITEM_BYTES

    def test_a_full_log_record_fits(self):
        rec = er.build_log_record(
            pipeline_execution_id="pe1", log_type="summary",
            result_log="r" * er.MAX_LOG_FIELD_BYTES, error_log="e" * er.MAX_LOG_FIELD_BYTES,
            log_group_arn="arn:lg", log_stream_name="stream")
        assert item_bytes(rec) <= er.MAX_ITEM_BYTES
