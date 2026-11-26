# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync reads its registration's settings from the registration row (#1978).

- The fallback database for a direct write is the row's `defaultSyncDatabaseId`; the function's
  DEFAULT_DATABASE_ID is used only for a row that does not carry the field yet.
- A function deployed for one registration (ASSET_BUCKET_NAME set) reads it by its configured bucket and
  prefix, as before. A function that serves any registration finds it by bucket name and the longest
  prefix that holds the key (bucketNameGSI, cached).
"""

import hashlib
from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.indexing.test_sqsBucketSync_deleted_database import _load


class _Cache:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ttl=60):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def _condition_values(condition):
    expression = condition.get_expression()
    if expression['operator'] == 'AND':
        result = {}
        for part in expression['values']:
            result.update(_condition_values(part))
        return result
    key, value = expression['values']
    return {key.name: value}


class _BucketsTable:
    """bucketNameGSI by bucket name, one row per page."""

    def __init__(self, rows):
        self.rows = rows
        self.queried = []

    def query(self, **kwargs):
        values = _condition_values(kwargs['KeyConditionExpression'])
        self.queried.append(values)
        matches = [r for r in self.rows if r['bucketName'] == values['bucketName']
                   and ('baseAssetsPrefix' not in values or r['baseAssetsPrefix'] == values['baseAssetsPrefix'])]
        start = int((kwargs.get('ExclusiveStartKey') or {}).get('offset', 0))
        response = {'Items': matches[start:start + 1]}
        if start + 1 < len(matches):
            response['LastEvaluatedKey'] = {'offset': start + 1}
        return response


def _row(bucket_id, prefix, **extra):
    return {'bucketId': bucket_id, 'bucketName': 'customer-bucket', 'baseAssetsPrefix': prefix, **extra}


def _auto_id(stem, bucket='customer-bucket', prefix='assets/'):
    digest = hashlib.md5(f"{bucket}:{prefix.rstrip('/')}".encode()).hexdigest()[:8]  # nosec B324 - test of the id format
    return f"{stem}-{digest}"


@pytest.fixture
def m(monkeypatch):
    module = _load()
    monkeypatch.setattr(module, 's3_buckets_cache', _Cache())
    monkeypatch.setattr(module, 'database_cache', _Cache())
    monkeypatch.setattr(module, 'database_id', 'env-db')
    return module


@pytest.mark.unit
class TestFallbackDatabaseFromTheRow:
    def test_the_rows_value_wins_over_the_environment(self, m, monkeypatch):
        monkeypatch.setattr(m, 'get_bucket_info_from_bucket_id',
                            lambda bucket_id: _row(bucket_id, 'assets/', defaultSyncDatabaseId='row-db'))
        assert m.default_sync_database_id('reg-1') == 'row-db'

    @pytest.mark.parametrize('row', [
        _row('reg-1', 'assets/'),
        _row('reg-1', 'assets/', defaultSyncDatabaseId=''),
        None,
    ])
    def test_the_environment_answers_for_a_row_without_the_field(self, m, monkeypatch, row):
        monkeypatch.setattr(m, 'get_bucket_info_from_bucket_id', lambda bucket_id: row)
        assert m.default_sync_database_id('reg-1') == 'env-db'

    def test_neither_gives_none(self, m, monkeypatch):
        monkeypatch.setattr(m, 'get_bucket_info_from_bucket_id', lambda bucket_id: _row(bucket_id, '/'))
        monkeypatch.setattr(m, 'database_id', None)
        assert m.default_sync_database_id('reg-1') is None

    def test_the_configured_default_is_read_from_the_row(self, m, monkeypatch):
        # Two databases on the registration: the one the row names as its fallback is used.
        monkeypatch.setattr(m, 'get_bucket_info_from_bucket_id',
                            lambda bucket_id: _row(bucket_id, 'assets/', defaultSyncDatabaseId='row-db'))
        monkeypatch.setattr(m, 'lookup_databases', lambda bucket_id: [
            {'databaseId': 'row-db', 'defaultBucketId': bucket_id},
            {'databaseId': 'other', 'defaultBucketId': bucket_id}])
        assert m.get_or_create_database_for_bucket('reg-1', 'customer-bucket', 'assets/') == 'row-db'

    def _auto_create(self, m, monkeypatch, row):
        monkeypatch.setattr(m, 'get_bucket_info_from_bucket_id', lambda bucket_id: row)
        monkeypatch.setattr(m, 'lookup_databases', lambda bucket_id: [])
        resource = MagicMock()
        resource.Table.return_value.get_item.return_value = {}
        monkeypatch.setattr(m, 'dynamodb', resource)
        monkeypatch.setattr(m, 'lookup_archived_database', lambda database_id: None)
        created = MagicMock(side_effect=lambda bucket_id, database_id: database_id)
        monkeypatch.setattr(m, 'create_new_database', created)
        return m.get_or_create_database_for_bucket('reg-1', 'customer-bucket', 'assets/'), created

    def test_the_auto_generated_database_is_named_from_the_rows_fallback(self, m, monkeypatch):
        result, created = self._auto_create(m, monkeypatch,
                                            _row('reg-1', 'assets/', defaultSyncDatabaseId='row-db'))
        assert result == _auto_id('row-db')
        created.assert_called_once_with('reg-1', _auto_id('row-db'))

    def test_a_row_without_the_field_keeps_the_environment_name(self, m, monkeypatch):
        result, _ = self._auto_create(m, monkeypatch, _row('reg-1', 'assets/'))
        assert result == _auto_id('env-db')

    def test_no_fallback_creates_nothing(self, m, monkeypatch):
        monkeypatch.setattr(m, 'database_id', None)
        result, created = self._auto_create(m, monkeypatch, _row('reg-1', 'assets/'))
        assert result is None
        created.assert_not_called()


def _record(key):
    return {'eventSource': 'aws:s3', 'eventName': 'ObjectCreated:Put',
            's3': {'bucket': {'name': 'customer-bucket'}, 'object': {'key': key}}}


@pytest.mark.unit
class TestRegistrationOfAnEvent:
    def _wire(self, m, monkeypatch, rows, bucket_name, prefix):
        table = _BucketsTable(rows)
        resource = MagicMock()
        resource.Table.return_value = table
        monkeypatch.setattr(m, 'dynamodb', resource)
        monkeypatch.setattr(m, 'asset_bucket_name', bucket_name)
        monkeypatch.setattr(m, 'asset_bucket_prefix', prefix)
        # Stop right after the registration is known, recording the prefix the key is parsed under.
        extract = MagicMock(return_value=None)
        monkeypatch.setattr(m, 'extract_asset_id_from_key', extract)
        return table, extract

    def test_a_function_for_any_registration_takes_the_longest_prefix(self, m, monkeypatch):
        rows = [_row('root', '/'), _row('team', 'team1/'), _row('sub', 'team1/sub/'),
                _row('old', 'team1/sub/x/', retired=True)]
        table, extract = self._wire(m, monkeypatch, rows, None, None)
        get_bucket_id = MagicMock()
        monkeypatch.setattr(m, 'get_bucket_id', get_bucket_id)

        ok, forward, _ = m.process_s3_record(_record('team1/sub/x/f.glb'))

        assert (ok, forward) == (False, True)
        extract.assert_called_once_with('team1/sub/x/f.glb', 'team1/sub/')
        get_bucket_id.assert_not_called()
        # One read of the bucket's rows by name, paged to the end (the fake serves a row per page).
        assert table.queried == [{'bucketName': 'customer-bucket'}] * len(rows)
        assert m.s3_buckets_cache.get('sub:versioning') is False

    def test_registrations_of_a_bucket_are_read_once_per_cache_window(self, m, monkeypatch):
        table, _ = self._wire(m, monkeypatch, [_row('root', '/')], None, None)
        m.process_s3_record(_record('a/f.glb'))
        m.process_s3_record(_record('b/f.glb'))
        assert table.queried == [{'bucketName': 'customer-bucket'}]

    def test_a_key_no_active_registration_holds_is_skipped_and_forwarded(self, m, monkeypatch):
        _, extract = self._wire(m, monkeypatch, [_row('team', 'team1/'), _row('old', 'team2/', retired=True)],
                                None, None)
        ok, forward, message = m.process_s3_record(_record('team2/a/f.glb'))
        assert (ok, forward) == (False, True)
        assert 'No bucket ID found' in message
        extract.assert_not_called()

    def test_a_function_for_one_registration_reads_it_by_its_configured_prefix(self, m, monkeypatch):
        table, extract = self._wire(m, monkeypatch, [_row('team', 'team1/')], 'customer-bucket', 'team1/')
        get_bucket_id = MagicMock(return_value='team')
        monkeypatch.setattr(m, 'get_bucket_id', get_bucket_id)

        m.process_s3_record(_record('team1/a/f.glb'))

        get_bucket_id.assert_called_once_with('customer-bucket', 'team1/')
        extract.assert_called_once_with('team1/a/f.glb', 'team1/')
        assert table.queried == []
