# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceTrigger, file events: an asset id is unique within a database only, so a file event
evaluates the asset whose bucket and location hold the event's object, read from every page of the
asset id index, and never another database's asset with the same id."""

import pytest

from backend.tests.handlers.compliance._harness import ASSET, DB, SCHEMA
from backend.tests.handlers.compliance.test_complianceTrigger import (
    AUTO_EVAL_DB, _run, _sns_event)
from backend.tests.pagingStub import Pager

OTHER_DB = "db0"  # sorts before DB, so the asset id index lists its row first
BUCKETS = {"bucket-0": "vams-bucket-0", "bucket-1": "vams-bucket-1"}


def _located_row(database_id, bucket_id, key=f"{ASSET}/"):
    return {"databaseId": database_id, "assetId": ASSET, "bucketId": bucket_id,
            "assetLocation": {"Key": key}}


def _file_event_in(bucket_name, key=f"{ASSET}/model.glb", prefix=""):
    return {"s3": {"bucket": {"name": bucket_name}, "object": {"key": key}},
            "ASSET_BUCKET_NAME": bucket_name, "ASSET_BUCKET_PREFIX": prefix}


def _run_located(message, asset_rows=None, asset_query_effect=None):
    database_items = {DB: AUTO_EVAL_DB, OTHER_DB: dict(AUTO_EVAL_DB, databaseId=OTHER_DB)}
    registrations = [{"bucketId": bucket_id, "bucketName": name, "baseAssetsPrefix": "/"}
                     for bucket_id, name in BUCKETS.items()]
    return _run(_sns_event(message), compliance_record={"schemaName": SCHEMA},
                asset_rows=asset_rows, asset_query_effect=asset_query_effect,
                database_effect=lambda Key: {"Item": database_items.get(Key["databaseId"])},
                registrations=registrations)


def _evaluated(mocks):
    return {call.args[:2] for call in mocks["run_evaluation"].call_args_list}


@pytest.mark.unit
class TestAFileEventIsMatchedToTheDatabaseThatHoldsTheObject:

    def test_the_database_whose_bucket_holds_the_object_is_evaluated(self):
        mocks = _run_located(
            _file_event_in("vams-bucket-1"),
            asset_rows=[_located_row(OTHER_DB, "bucket-0"), _located_row(DB, "bucket-1")])
        assert (DB, ASSET) in _evaluated(mocks)
        assert (OTHER_DB, ASSET) not in _evaluated(mocks)

    def test_a_row_on_a_later_page_of_the_asset_id_index_is_found(self):
        pager = Pager({"Items": [_located_row(OTHER_DB, "bucket-0")],
                       "LastEvaluatedKey": {"assetId": ASSET, "databaseId": OTHER_DB}},
                      {"Items": [_located_row(DB, "bucket-1")]}, name="assetIdGSI")
        mocks = _run_located(_file_event_in("vams-bucket-1"), asset_query_effect=pager)
        pager.assert_paged_to_exhaustion()
        assert (DB, ASSET) in _evaluated(mocks)
        assert (OTHER_DB, ASSET) not in _evaluated(mocks)

    def test_an_object_no_row_holds_evaluates_nothing(self):
        mocks = _run_located(_file_event_in("vams-bucket-1"),
                             asset_rows=[_located_row(OTHER_DB, "bucket-0")])
        mocks["run_evaluation"].assert_not_called()

    def test_an_object_two_rows_claim_evaluates_nothing(self):
        mocks = _run_located(
            _file_event_in("vams-bucket-1"),
            asset_rows=[_located_row(OTHER_DB, "bucket-1"), _located_row(DB, "bucket-1")])
        mocks["run_evaluation"].assert_not_called()

    def test_a_shared_bucket_is_told_apart_by_the_asset_location(self):
        mocks = _run_located(
            _file_event_in("vams-bucket-1", key=f"b/{ASSET}/model.glb", prefix="b"),
            asset_rows=[_located_row(OTHER_DB, "bucket-1", key=f"a/{ASSET}/"),
                        _located_row(DB, "bucket-1", key=f"b/{ASSET}/")])
        assert (DB, ASSET) in _evaluated(mocks)
        assert (OTHER_DB, ASSET) not in _evaluated(mocks)
