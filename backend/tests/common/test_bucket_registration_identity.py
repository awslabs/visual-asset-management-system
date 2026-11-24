# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A bucket registration is identified by its bucketId (#1889, #1891).

The deploy-time writer keeps every registration row: a row whose registration left the configuration
is retired (`retired`), and a second row an earlier release wrote for the same bucket name and prefix
names the row that answers for it (`duplicateOfBucketId`). Lookups by bucket name leave retired rows
out and treat a duplicate and its primary as one registration; an owner found by bucket name is checked
against the registration a record was stamped with (`ASSET_BUCKET_ID`). The modules are the REAL ones,
registered by tests/conftest.py, and each test reads them through `sys.modules` so a missing symbol
fails that test alone.
"""

import importlib.util
import os
import sys
import types

import pytest

from tests.common.fileAttributionFakes import (
    AttributionAssetTable,
    AttributionBucketsTable,
    asset_row,
    registration,
)

BUCKET = 'asset-bucket'


def _module(name):
    return sys.modules[name]


@pytest.fixture(autouse=True)
def _fresh():
    fa = _module('common.fileAttribution')
    al = _module('common.assetLocations')
    fa.clear_registration_cache()
    al.clear_location_cache()
    yield
    fa.clear_registration_cache()
    al.clear_location_cache()


def _attribute(monkeypatch, rows, registrations, key, *, ready, registration_bucket_id=None):
    fa = _module('common.fileAttribution')
    monkeypatch.setattr(fa, 'index_ready', lambda table_name: ready)
    kwargs = {}
    if registration_bucket_id is not None:
        kwargs['registration_bucket_id'] = registration_bucket_id
    return fa.attribute_object(BUCKET, key, asset_table=AttributionAssetTable(rows),
                               buckets_table=AttributionBucketsTable(registrations),
                               read_stamp=lambda: None, **kwargs)


@pytest.mark.unit
class TestRegistrationRows:
    def test_retired_rows_are_left_out_and_duplicates_come_last(self):
        br = _module('common.bucketRegistrations')
        rows = [
            {'bucketId': 'dup', 'duplicateOfBucketId': 'primary'},
            {'bucketId': 'old', 'retired': True},
            {'bucketId': 'primary'},
        ]
        assert [r['bucketId'] for r in br.active_registrations(rows)] == ['primary', 'dup']

    def test_a_duplicate_and_its_primary_are_one_registration(self):
        br = _module('common.bucketRegistrations')
        primary = {'bucketId': 'primary'}
        dup = {'bucketId': 'dup', 'duplicateOfBucketId': 'primary'}
        assert br.same_registration(primary, dup) and br.same_registration(dup, primary)
        assert not br.same_registration(primary, {'bucketId': 'other'})
        assert [r['bucketId'] for r in br.primary_registrations([dup, primary])] == ['primary']


@pytest.mark.unit
class TestAttributionChecksTheStampedRegistration:
    ROW = asset_row('db1', 'a1', 'reg-a', 'a1/', BUCKET)
    REGISTRATIONS = [registration('reg-a', BUCKET, '/'), registration('reg-b', BUCKET, 'b/'),
                     {**registration('reg-dup', BUCKET, '/'), 'duplicateOfBucketId': 'reg-a'}]

    def test_an_owner_in_another_registration_is_not_used(self, monkeypatch):
        result = _attribute(monkeypatch, [self.ROW], self.REGISTRATIONS, 'a1/model.glb', ready=True,
                            registration_bucket_id='reg-b')
        assert not result.attributed

    @pytest.mark.parametrize('stamped', ['reg-a', 'reg-dup', ''])
    def test_the_owner_is_used_in_its_own_registration_or_without_a_stamp(self, monkeypatch, stamped):
        result = _attribute(monkeypatch, [self.ROW], self.REGISTRATIONS, 'a1/model.glb', ready=True,
                            registration_bucket_id=stamped)
        assert result.attributed and result.asset_id == 'a1'


@pytest.mark.unit
class TestFirstFolderByRegistration:
    def test_a_retired_registration_answers_no_lookup_by_name(self, monkeypatch):
        rows = [asset_row('db1', 'a1', 'reg-old', 'a1/', BUCKET, indexed=False)]
        registrations = [{**registration('reg-old', BUCKET, '/'), 'retired': True},
                         registration('reg-new', BUCKET, '/')]
        assert not _attribute(monkeypatch, rows, registrations, 'a1/model.glb', ready=False).attributed

    def test_an_asset_of_a_duplicate_row_is_found(self, monkeypatch):
        rows = [asset_row('db1', 'a1', 'reg-dup', 'assets/a1/', BUCKET, indexed=False)]
        registrations = [registration('reg-a', BUCKET, 'assets/'),
                         {**registration('reg-dup', BUCKET, 'assets/'), 'duplicateOfBucketId': 'reg-a'}]
        result = _attribute(monkeypatch, rows, registrations, 'assets/a1/model.glb', ready=False)
        assert result.attributed and result.bucket_id == 'reg-dup'


@pytest.mark.unit
class TestIndexerEventStamp:
    def test_the_bucket_id_is_read_from_the_first_envelope_that_carries_one(self):
        ie = _module('common.indexerEvents')
        assert ie.stamped_bucket_id({}, {'ASSET_BUCKET_ID': 'reg-1'}, {'ASSET_BUCKET_ID': 'reg-2'}) == 'reg-1'
        assert ie.stamped_bucket_id({'ASSET_BUCKET_ID': ''}, None) == ''

    def test_the_stamp_is_carried_onto_a_record_that_has_none(self):
        ie = _module('common.indexerEvents')
        record = ie.carry_bucket_id_stamp({'s3': {}}, {}, {'ASSET_BUCKET_ID': 'reg-1'})
        assert record['ASSET_BUCKET_ID'] == 'reg-1'
        kept = ie.carry_bucket_id_stamp({'s3': {}, 'ASSET_BUCKET_ID': 'own'}, {'ASSET_BUCKET_ID': 'reg-1'})
        assert kept['ASSET_BUCKET_ID'] == 'own'


# ------------------------------------------------------------------ file enumeration (reindex walks)

_ENUMERATION_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "common", "indexing",
                                 "fileEnumeration.py")


@pytest.fixture
def fe():
    spec = importlib.util.spec_from_file_location("fileEnumeration_registration_identity",
                                                  os.path.abspath(_ENUMERATION_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _S3:
    def __init__(self, keys_by_bucket):
        self.keys_by_bucket = keys_by_bucket
        self.paginated = []

    def get_paginator(self, name):
        return self

    def paginate(self, **kwargs):
        self.paginated.append(kwargs["Bucket"])
        prefix = kwargs.get("Prefix", "")
        return [{"Contents": [{"Key": k} for k in sorted(self.keys_by_bucket[kwargs["Bucket"]])
                              if k.startswith(prefix)]}]


class _Scan:
    def __init__(self, rows):
        self.rows = rows

    def scan(self, **kwargs):
        return {"Items": list(self.rows)}


class _AssetIndex:
    """BucketIdGSI rows keyed (bucketId, assetId) -> databaseId."""

    name = "test-asset-table"

    def __init__(self, rows):
        self.rows = rows

    def query(self, **kwargs):
        from boto3.dynamodb.conditions import ConditionExpressionBuilder
        built = ConditionExpressionBuilder().build_expression(kwargs["KeyConditionExpression"],
                                                              is_key_condition=True)
        bucket_id, asset_id = list(built.attribute_value_placeholders.values())
        database_id = self.rows.get((bucket_id, asset_id))
        return {"Items": [{"databaseId": database_id}] if database_id else []}


@pytest.mark.unit
class TestEnumerationWalksRegistrations:
    def test_retired_rows_are_not_walked_and_a_location_is_walked_once(self, fe, monkeypatch):
        monkeypatch.setattr(fe, "index_ready", lambda name: False)
        registrations = [
            {"bucketId": "b1", "bucketName": "bucket-one", "baseAssetsPrefix": ""},
            {"bucketId": "b1d", "bucketName": "bucket-one", "baseAssetsPrefix": "", "duplicateOfBucketId": "b1"},
            {"bucketId": "b9", "bucketName": "bucket-old", "baseAssetsPrefix": "", "retired": True},
        ]
        s3 = _S3({"bucket-one": ["a1/x.glb", "a2/y.glb"], "bucket-old": ["a3/z.glb"]})
        assets = _AssetIndex({("b1", "a1"): "db1", ("b1d", "a2"): "db2", ("b9", "a3"): "db3"})

        refs, token = fe.enumerate_latest_live_files(s3_client=s3, buckets_table=_Scan(registrations),
                                                      asset_table=assets)

        assert token is None
        assert sorted((r.asset_id, r.bucket_id) for r in refs) == [("a1", "b1"), ("a2", "b1d")]
        assert s3.paginated == ["bucket-one"]

    def test_an_owner_in_another_registration_is_not_listed_for_the_walked_one(self, fe, monkeypatch):
        monkeypatch.setattr(fe, "index_ready", lambda name: True)
        registrations = [
            {"bucketId": "b1", "bucketName": "bucket-one", "baseAssetsPrefix": "p1/"},
            {"bucketId": "b2", "bucketName": "bucket-one", "baseAssetsPrefix": "p2/"},
        ]

        class _Locations:
            def owner(self, key, include_archived=True):
                asset_id = key.split("/")[1]
                entry = types.SimpleNamespace(database_id="db1", asset_id=asset_id, bucket_id="b1",
                                              archived=False, location="/".join(key.split("/")[:2]) + "/")
                return types.SimpleNamespace(status=fe.RESOLVED_FOUND, entry=entry)

        monkeypatch.setattr(fe, "load_bucket_locations", lambda table, bucket: _Locations())
        s3 = _S3({"bucket-one": ["p1/a1/x.glb", "p2/a2/y.glb"]})

        refs, _ = fe.enumerate_latest_live_files(s3_client=s3, buckets_table=_Scan(registrations),
                                                 asset_table=_AssetIndex({}))

        assert [(r.asset_id, r.base_assets_prefix) for r in refs] == [("a1", "p1/")]
