#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The /buckets listing must project isDefault.

The flag is stored on every row in the S3 asset buckets table and is what `common.workflows`
resolves the VAMS default asset bucket from — the single bucket that houses pipeline template
offload and execution-time run I/O. A listing that omits it leaves a client unable to tell which
bucket that is, with no error to notice: the response is well-formed, just incomplete.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.databases.databaseService import get_buckets
from backend.backend.models.databases import BucketModel


def _scan_response(rows):
    """A low-level DynamoDB scan response; get_buckets deserializes the typed shape itself."""
    def typed(value):
        if isinstance(value, bool):
            return {"BOOL": value}
        return {"S": str(value)}

    return {"Items": [{k: typed(v) for k, v in row.items()} for row in rows]}


@pytest.mark.unit
class TestBucketsListingIsDefault:
    def test_isdefault_is_projected_for_the_default_and_non_default_bucket(self):
        rows = [
            {
                "bucketId": "b9a3aba3-c092-475f-978a-d39e5d5a2657",
                "bucketName": "vams-created-asset-bucket",
                "baseAssetsPrefix": "/",
                "isDefault": True,
            },
            {
                "bucketId": "aa11bb22-c092-475f-978a-d39e5d5a2657",
                "bucketName": "imported-external-bucket",
                "baseAssetsPrefix": "/team-a/",
                "isDefault": False,
            },
        ]
        with patch("backend.backend.handlers.databases.databaseService.dbClient") as db:
            db.scan.return_value = _scan_response(rows)
            result = get_buckets({}, {})

        items = result.Items
        assert len(items) == 2, "both rows must be returned before their flags are compared"

        by_name = {i.bucketName: i for i in items}
        # Both directions asserted: a True that survives, and a False that is not silently coerced.
        assert by_name["vams-created-asset-bucket"].isDefault is True
        assert by_name["imported-external-bucket"].isDefault is False

    def test_a_row_written_before_the_flag_existed_reads_as_non_default(self):
        # Legacy row: no isDefault attribute at all. The listing must still answer, with False —
        # not omit the field, which would make a client's boolean check partial.
        rows = [{
            "bucketId": "cc33dd44-c092-475f-978a-d39e5d5a2657",
            "bucketName": "legacy-bucket",
            "baseAssetsPrefix": "/",
        }]
        with patch("backend.backend.handlers.databases.databaseService.dbClient") as db:
            db.scan.return_value = _scan_response(rows)
            result = get_buckets({}, {})

        assert len(result.Items) == 1
        assert result.Items[0].isDefault is False

    def test_isdefault_survives_json_serialization_of_the_response(self):
        # The handler returns the model; the response path serializes it. A field that exists on the
        # model but is dropped on the way out would still fail the caller.
        rows = [{
            "bucketId": "b9a3aba3-c092-475f-978a-d39e5d5a2657",
            "bucketName": "vams-created-asset-bucket",
            "baseAssetsPrefix": "/",
            "isDefault": True,
        }]
        with patch("backend.backend.handlers.databases.databaseService.dbClient") as db:
            db.scan.return_value = _scan_response(rows)
            result = get_buckets({}, {})

        payload = json.loads(json.dumps(result.dict()))
        assert payload["Items"][0]["isDefault"] is True

    def test_the_model_declares_isdefault_as_a_live_boolean_field(self):
        # Pydantic v1 silently swallows an unknown Field() kwarg, so assert on the parsed field
        # rather than trusting the declaration.
        field = BucketModel.__fields__["isDefault"]
        assert field.type_ is bool
        assert field.default is False
