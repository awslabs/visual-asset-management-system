# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync identifies a registration by its bucketId (#1889, #1891).

- `get_bucket_id` reads every page of the bucketNameGSI answer, leaves a retired row out (its
  registration left the configuration) and prefers the row a duplicate names, so the registration that
  answers for a bucket name and prefix is the same on every call.
- `get_or_create_database_for_bucket` caches and matches by bucketId: two registrations that share a
  bucket name and prefix never share an auto database through the cache.
- Every message bucket sync forwards carries the bucketId of the registration it serves
  (`ASSET_BUCKET_ID`), beside the name and prefix it carried before.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.indexing.test_sqsBucketSync_deleted_database import _load


class _Cache:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


def _condition_values(condition):
    """{attribute: value} of a `Key(a).eq(x) & Key(b).eq(y)` condition."""
    expression = condition.get_expression()
    if expression['operator'] == 'AND':
        result = {}
        for part in expression['values']:
            result.update(_condition_values(part))
        return result
    key, value = expression['values']
    return {key.name: value}


class _BucketsTable:
    """The bucketNameGSI answer for (bucketName, baseAssetsPrefix), served one row per page."""

    def __init__(self, rows):
        self.rows = rows
        self.queried = []

    def query(self, **kwargs):
        values = _condition_values(kwargs['KeyConditionExpression'])
        self.queried.append(values)
        matches = [r for r in self.rows if r['bucketName'] == values['bucketName']
                   and r['baseAssetsPrefix'] == values['baseAssetsPrefix']]
        start = int((kwargs.get('ExclusiveStartKey') or {}).get('offset', 0))
        response = {'Items': matches[start:start + 1]}
        if start + 1 < len(matches):
            response['LastEvaluatedKey'] = {'offset': start + 1}
        return response


def _with_table(monkeypatch, rows):
    m = _load()
    table = _BucketsTable(rows)
    resource = MagicMock()
    resource.Table.return_value = table
    monkeypatch.setattr(m, 'dynamodb', resource)
    monkeypatch.setattr(m, 's3_buckets_cache', _Cache())
    return m, table


def _set(monkeypatch, m, **attributes):
    for name, value in attributes.items():
        monkeypatch.setattr(m, name, value)


def _row(bucket_id, prefix='assets/', **extra):
    return {'bucketId': bucket_id, 'bucketName': 'customer-bucket', 'baseAssetsPrefix': prefix, **extra}


@pytest.mark.unit
class TestGetBucketId:
    def test_a_retired_row_answers_no_lookup(self, monkeypatch):
        m, _ = _with_table(monkeypatch, [_row('reg-old', retired=True), _row('reg-new')])
        assert m.get_bucket_id('customer-bucket', 'assets/') == 'reg-new'

    def test_the_row_a_duplicate_names_answers(self, monkeypatch):
        m, _ = _with_table(monkeypatch, [_row('reg-dup', duplicateOfBucketId='reg-a'), _row('reg-a')])
        assert m.get_bucket_id('customer-bucket', 'assets/') == 'reg-a'

    def test_a_prefix_without_its_slash_is_the_stored_registration(self, monkeypatch):
        m, table = _with_table(monkeypatch, [_row('reg-a')])
        assert m.get_bucket_id('customer-bucket', 'assets') == 'reg-a'
        assert table.queried[0]['baseAssetsPrefix'] == 'assets/'

    def test_the_root_prefix_is_queried_as_stored(self, monkeypatch):
        m, table = _with_table(monkeypatch, [_row('reg-root', prefix='/')])
        assert m.get_bucket_id('customer-bucket', '/') == 'reg-root'


@pytest.mark.unit
class TestAutoDatabaseByRegistration:
    def test_two_registrations_with_one_name_and_prefix_do_not_share_the_cached_database(self, monkeypatch):
        m = _load()
        _set(monkeypatch, m, database_cache=_Cache(),
             lookup_databases=lambda bucket_id: [{'databaseId': f'db-{bucket_id}', 'defaultBucketId': bucket_id}],
             get_bucket_info_from_bucket_id=lambda bucket_id: {'bucketName': 'customer-bucket',
                                                               'baseAssetsPrefix': 'assets/'})

        first = m.get_or_create_database_for_bucket('reg-1', 'customer-bucket', 'assets/')
        second = m.get_or_create_database_for_bucket('reg-2', 'customer-bucket', 'assets/')

        assert (first, second) == ('db-reg-1', 'db-reg-2')


@pytest.mark.unit
class TestForwardedMessagesCarryTheRegistration:
    def test_the_file_indexer_message_carries_the_bucket_id(self, monkeypatch):
        m = _load()
        sns = MagicMock()
        sns.publish.return_value = {'MessageId': 'm1'}
        _set(monkeypatch, m, asset_bucket_name='customer-bucket', asset_bucket_prefix='assets/',
             get_bucket_id=MagicMock(return_value='reg-1'), file_indexer_sns_topic_arn='arn:test:topic', sns_client=sns)

        assert m.publish_to_file_indexer_sns({'Records': [{'s3': {}}]}) is True

        message = json.loads(m.sns_client.publish.call_args.kwargs['Message'])
        assert message['ASSET_BUCKET_ID'] == 'reg-1'
        assert (message['ASSET_BUCKET_NAME'], message['ASSET_BUCKET_PREFIX']) == ('customer-bucket', 'assets/')

    def test_a_registration_that_cannot_be_read_still_forwards_the_message(self, monkeypatch):
        m = _load()
        sns = MagicMock()
        sns.publish.return_value = {'MessageId': 'm1'}
        _set(monkeypatch, m, asset_bucket_name='customer-bucket', asset_bucket_prefix='/',
             get_bucket_id=MagicMock(return_value=None), file_indexer_sns_topic_arn='arn:test:topic', sns_client=sns)

        assert m.publish_to_file_indexer_sns({'Records': [{'s3': {}}]}) is True

        message = json.loads(m.sns_client.publish.call_args.kwargs['Message'])
        assert 'ASSET_BUCKET_ID' not in message
