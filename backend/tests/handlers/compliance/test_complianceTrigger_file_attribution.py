# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A file event is evaluated for the asset whose stored location holds the object, in the event's
bucket (`common.fileAttribution.attribute_object`), not for the asset its first folder names.

A custom-root asset (created with `bucketExistingKey`) is reached through the record's `vamsBinding`
or the location index; the first folder of its key names no asset. Once the index is ready its miss
is final, and a location several assets claim evaluates none of them."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.tests.common.test_indexerEvents import s3_event_record
from backend.tests.handlers.compliance.test_complianceTrigger import (
    ASSET,
    ASSET_ROWS,
    AUTO_EVAL_DB,
    BUCKET_ID,
    DB,
    SCHEMA,
    _run,
    _sns_event,
)
from common import assetLocations as asset_locations
from common import fileAttribution as file_attribution
from handlers.compliance import complianceTrigger as trigger
from tests.common.fileAttributionFakes import (
    AttributionAssetTable,
    AttributionBucketsTable,
    asset_row,
    registration,
)

CUSTOM_ASSET = "custom-asset"
CUSTOM_LOCATION = "projects/alpha/"
CUSTOM_KEY = f"{CUSTOM_LOCATION}model.glb"
CUSTOM_ROW = {"databaseId": DB, "assetId": CUSTOM_ASSET, "bucketId": BUCKET_ID,
              "assetLocation": {"Key": CUSTOM_LOCATION}}
# A normal asset at `{assetId}/`, the folder its first segment names.
LOCATED_ROWS = [{**ASSET_ROWS[0], "bucketId": BUCKET_ID, "assetLocation": {"Key": f"{ASSET}/"}}]


def _message(record):
    return {"Records": [record], "ASSET_BUCKET_NAME": "asset-bucket", "ASSET_BUCKET_PREFIX": ""}


def _bound(key, database_id=DB, asset_id=CUSTOM_ASSET, bucket_id=BUCKET_ID):
    record = s3_event_record(key)
    record["vamsBinding"] = {"databaseId": database_id, "assetId": asset_id, "bucketId": bucket_id,
                             "relativePath": "/model.glb", "resolvedBy": "locationIndex"}
    return record


def _found(row, location=CUSTOM_LOCATION):
    entry = asset_locations.LocationEntry(database_id=row["databaseId"], asset_id=row["assetId"],
                                          bucket_id=row["bucketId"], location=location)
    return asset_locations.Resolution(asset_locations.RESOLVED_FOUND, entry=entry, row=row)


@pytest.mark.unit
class TestCustomRootFilesAreEvaluated:
    def test_a_bound_record_evaluates_its_custom_root_asset(self):
        mocks = _run(_sns_event(_message(_bound(CUSTOM_KEY))), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows={}, asset_item=CUSTOM_ROW)
        mocks["run_evaluation"].assert_called_once_with(DB, CUSTOM_ASSET, SCHEMA, "SYSTEM_USER")

    def test_the_location_index_evaluates_a_custom_root_asset_without_a_binding(self):
        mocks = _run(_sns_event(_message(s3_event_record(CUSTOM_KEY))), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows={},
                     index_ready=True, resolution=_found(CUSTOM_ROW))
        mocks["run_evaluation"].assert_called_once_with(DB, CUSTOM_ASSET, SCHEMA, "SYSTEM_USER")


@pytest.mark.unit
class TestTheIndexDecidesOnceItIsReady:
    def test_a_miss_in_a_ready_index_evaluates_nothing(self):
        """Before the index is ready the first folder decides; once it is ready, a miss is final."""
        mocks = _run(_sns_event(_message(s3_event_record(f"{ASSET}/model.glb"))),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA},
                     asset_rows=LOCATED_ROWS, index_ready=True)
        mocks["run_evaluation"].assert_not_called()

    def test_a_location_two_assets_claim_evaluates_neither(self):
        mocks = _run(_sns_event(_message(s3_event_record(f"{ASSET}/model.glb"))),
                     database_item=AUTO_EVAL_DB, compliance_record={"schemaName": SCHEMA},
                     asset_rows=LOCATED_ROWS, index_ready=True,
                     resolution=asset_locations.Resolution(asset_locations.RESOLVED_AMBIGUOUS))
        mocks["run_evaluation"].assert_not_called()


@pytest.mark.unit
class TestAnUnreadableRegistrationIsRetried:
    def test_a_throttled_registration_read_fails_the_queue_record(self):
        throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Query")
        mocks = _run(_sns_event(_message(_bound(CUSTOM_KEY))), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows={}, asset_item=CUSTOM_ROW,
                     buckets_effect=throttled)
        mocks["run_evaluation"].assert_not_called()
        assert mocks["response"] == {"batchItemFailures": [{"itemIdentifier": "q-1"}]}


# An archived asset nested in a live one (D10), read through the real location index lookups.
NEST_BUCKET = "asset-bucket"
NEST_OUTER = asset_row(DB, "outer", BUCKET_ID, "outer/", NEST_BUCKET)
NEST_ARCHIVED = asset_row("db-other", "annex", BUCKET_ID, "outer/annex/", NEST_BUCKET, archived=True)


@pytest.mark.unit
class TestArchivedNestedAsset:
    """A file of an archived asset nested in a live one has no live owner: the deepest stored location that
    holds it is the archived asset's, so no compliance evaluation of the containing asset starts for it."""

    @pytest.fixture
    def target(self, monkeypatch):
        def _target(key, ready=True):
            file_attribution.clear_registration_cache()
            asset_locations.clear_location_cache()
            monkeypatch.setattr(file_attribution, "index_ready", lambda name: ready)
            monkeypatch.setattr(trigger, "asset_table", AttributionAssetTable([NEST_OUTER, NEST_ARCHIVED]))
            monkeypatch.setattr(trigger.store, "s3_asset_buckets_table",
                                AttributionBucketsTable([registration(BUCKET_ID, NEST_BUCKET, "/")]))
            targets = MagicMock(name="evaluation_target", return_value={"schemaName": SCHEMA})
            monkeypatch.setattr(trigger.TriggerBatch, "evaluation_target", targets)
            resolved = trigger._file_event_target(s3_event_record(key), NEST_BUCKET, key, batch=trigger.TriggerBatch())
            file_attribution.clear_registration_cache()
            return resolved, targets
        return _target

    @pytest.mark.parametrize("ready", [True, False])
    def test_an_unbound_file_event_in_the_archived_nested_folder_evaluates_nothing(self, target, ready):
        resolved, targets = target("outer/annex/model.glb", ready=ready)
        assert resolved is None
        targets.assert_not_called()

    def test_control_the_containing_assets_own_file_is_evaluated_for_it(self, target):
        resolved, targets = target("outer/model.glb")
        assert resolved is not None and resolved[:2] == (DB, "outer")
        targets.assert_called_once_with(DB, "outer")

    def test_the_queue_record_succeeds_with_no_evaluation(self):
        # The location index names the archived nested asset: the record is consumed, not retried.
        archived = asset_locations.Resolution(
            asset_locations.RESOLVED_FOUND,
            entry=asset_locations.LocationEntry(database_id="db-other#deleted", asset_id="annex",
                                                bucket_id=BUCKET_ID, location="outer/annex/"),
            row={**NEST_ARCHIVED})
        mocks = _run(_sns_event(_message(s3_event_record("outer/annex/model.glb"))), database_item=AUTO_EVAL_DB,
                     compliance_record={"schemaName": SCHEMA}, asset_rows={}, index_ready=True,
                     resolution=archived)
        mocks["run_evaluation"].assert_not_called()
        assert not (mocks["response"] or {}).get("batchItemFailures")
