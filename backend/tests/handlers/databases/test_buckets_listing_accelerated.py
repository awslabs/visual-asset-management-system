#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The /buckets listing projects isAccelerated (S3 Transfer Acceleration, #1653).

The value is read from the row the listing already scans; a row without the attribute reads as
false, and no other field of the response changes.
"""

import json
from unittest.mock import patch

import pytest

from backend.backend.handlers.databases.databaseService import get_buckets


def _scan_response(rows):
    def typed(value):
        if isinstance(value, bool):
            return {"BOOL": value}
        return {"S": str(value)}

    return {"Items": [{k: typed(v) for k, v in row.items()} for row in rows]}


def _row(name, **extra):
    row = {
        "bucketId": "b9a3aba3-c092-475f-978a-d39e5d5a2657",
        "bucketName": name,
        "baseAssetsPrefix": "/",
        "isDefault": False,
    }
    row.update(extra)
    return row


def _list(rows):
    with patch("backend.backend.handlers.databases.databaseService.dbClient") as db:
        db.scan.return_value = _scan_response(rows)
        return get_buckets({}, {})


@pytest.mark.unit
class TestBucketsListingIsAccelerated:
    def test_true_and_false_rows_are_projected(self):
        result = _list([
            _row("fast-bucket", isAccelerated=True),
            _row("plain-bucket", isAccelerated=False),
        ])
        by_name = {i.bucketName: i for i in result.Items}
        assert by_name["fast-bucket"].isAccelerated is True
        assert by_name["plain-bucket"].isAccelerated is False

    def test_row_without_the_attribute_reads_as_false(self):
        result = _list([_row("legacy-bucket")])
        assert result.Items[0].isAccelerated is False

    def test_non_boolean_value_reads_as_false(self):
        result = _list([_row("odd-bucket", isAccelerated="true")])
        assert result.Items[0].isAccelerated is False

    def test_field_survives_serialization_and_other_fields_are_unchanged(self):
        row = _row("fast-bucket", isAccelerated=True, bucketRegion="us-west-2",
                   bucketAccountId="123456789012", isDefault=True)
        payload = json.loads(json.dumps(_list([row]).dict()))
        assert payload["Items"][0] == {
            "bucketId": "b9a3aba3-c092-475f-978a-d39e5d5a2657",
            "bucketName": "fast-bucket",
            "baseAssetsPrefix": "/",
            "isDefault": True,
            "bucketRegion": "us-west-2",
            "bucketAccountId": "123456789012",
            "isAccelerated": True,
        }
