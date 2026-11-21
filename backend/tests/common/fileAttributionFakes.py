# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""In-memory asset and bucket registration tables for tests of `common.fileAttribution` and its callers.

`AttributionAssetTable` serves what the module and `common.assetLocations` read from the asset table:
`get_item` by primary key, `query` on `assetIdGSI` (a boto3 `Key(...).eq(...)` condition), and the
`AssetLocationGSI` lookups and `BucketIdGSI` walks on `meta.client`. `AttributionBucketsTable` serves
`query` by `bucketId` and on `bucketNameGSI`. Both page with a LastEvaluatedKey after every `page_size` items when one is given, so a
caller that reads only the first page misses rows. Every read is recorded in `reads`.
"""

import threading
from unittest.mock import MagicMock

import sys

al = sys.modules['common.assetLocations']


def _condition_value(condition):
    """(attribute, value) of a `Key(name).eq(value)` condition."""
    expression = condition.get_expression()
    key, value = expression['values']
    return key.name, value


def _page(items, kwargs, page_size):
    start = int((kwargs.get('ExclusiveStartKey') or {}).get('offset', 0))
    size = kwargs.get('Limit') or page_size or len(items) or 1
    page = items[start:start + size]
    response = {'Items': [dict(i) for i in page]}
    if start + size < len(items):
        response['LastEvaluatedKey'] = {'offset': start + size}
    return response


def asset_row(database_id, asset_id, bucket_id, location, bucket_name=None, archived=False, indexed=True):
    """An asset row with its stored location and, when `indexed`, the location index attributes."""
    row = {
        'databaseId': database_id + (al.ARCHIVED_DATABASE_SUFFIX if archived else ''),
        'assetId': asset_id,
        'bucketId': bucket_id,
        'assetLocation': {'Key': location},
    }
    if archived:
        row['status'] = 'archived'
    if indexed and bucket_name:
        row.update(al.location_attributes(bucket_name, location))
    return row


def registration(bucket_id, bucket_name, prefix='/'):
    return {'bucketId': bucket_id, 'bucketName': bucket_name, 'baseAssetsPrefix': prefix,
            'bucketName:baseAssetsPrefix': f"{bucket_name}:{prefix}"}


class AttributionAssetTable:
    def __init__(self, rows, page_size=None, name='test-asset-table'):
        self.name = name
        self.rows = list(rows)
        self.page_size = page_size
        self.reads = []
        self._lock = threading.Lock()
        self.meta = MagicMock()
        self.meta.client.query.side_effect = self._index_query

    def get_item(self, Key, **kwargs):
        with self._lock:
            self.reads.append(('get_item', Key, kwargs))
        for r in self.rows:
            if r['databaseId'] == Key['databaseId'] and r['assetId'] == Key['assetId']:
                return {'Item': dict(r)}
        return {}

    def query(self, **kwargs):
        with self._lock:
            self.reads.append(('query', kwargs.get('IndexName'), kwargs))
        assert kwargs.get('IndexName') == 'assetIdGSI', kwargs
        name, value = _condition_value(kwargs['KeyConditionExpression'])
        assert name == 'assetId'
        matches = sorted((r for r in self.rows if r['assetId'] == value), key=lambda r: r['databaseId'])
        return _page(matches, kwargs, self.page_size)

    def _index_query(self, **kwargs):
        with self._lock:
            self.reads.append(('index', kwargs.get('IndexName'), kwargs))
        if kwargs['IndexName'] == al.BUCKET_ID_INDEX_NAME:
            assert kwargs['KeyConditionExpression'] == '#b = :b', kwargs
            bucket_id = kwargs['ExpressionAttributeValues'][':b']
            matches = sorted((r for r in self.rows if r.get('bucketId') == bucket_id), key=lambda r: r['assetId'])
            return _page([dict(r) for r in matches], kwargs, self.page_size)
        assert kwargs['IndexName'] == al.LOCATION_INDEX_NAME
        values = kwargs['ExpressionAttributeValues']
        indexed = [r for r in self.rows
                   if r.get(al.LOCATION_BUCKET_ATTRIBUTE) == values[':b'] and r.get(al.LOCATION_KEY_ATTRIBUTE)]
        if 'begins_with' in kwargs['KeyConditionExpression']:
            matches = [r for r in indexed if r[al.LOCATION_KEY_ATTRIBUTE].startswith(values[':p'])]
        elif ':k' in values:
            matches = [r for r in indexed if r[al.LOCATION_KEY_ATTRIBUTE] == values[':k']]
        else:
            # The whole partition of one bucket (a bulk load)
            assert kwargs['KeyConditionExpression'] == '#b = :b', kwargs
            matches = indexed
        matches.sort(key=lambda r: (r[al.LOCATION_KEY_ATTRIBUTE], r['databaseId'], r['assetId']))
        projected = [{k: r[k] for k in ('databaseId', 'assetId', 'bucketId', al.LOCATION_BUCKET_ATTRIBUTE,
                                        al.LOCATION_KEY_ATTRIBUTE)} for r in matches]
        return _page(projected, kwargs, self.page_size)


class AttributionBucketsTable:
    def __init__(self, registrations, page_size=None):
        self.registrations = list(registrations)
        self.page_size = page_size
        self.reads = []

    def query(self, **kwargs):
        self.reads.append(kwargs)
        name, value = _condition_value(kwargs['KeyConditionExpression'])
        if kwargs.get('IndexName') == 'bucketNameGSI':
            assert name == 'bucketName'
            matches = [r for r in self.registrations if r['bucketName'] == value]
        else:
            assert kwargs.get('IndexName') is None and name == 'bucketId'
            matches = [r for r in self.registrations if r['bucketId'] == value]
        return _page(matches, kwargs, self.page_size)
