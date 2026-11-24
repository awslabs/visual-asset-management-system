# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``resolve_default_bucket`` counts flagged registrations by ``bucketId`` (#1890).

Inside VAMS a bucket registration is identified by its ``bucketId``. Two flagged rows that share a
bucket name are two registrations: the stale row an earlier release left when an external default
bucket's ``baseAssetsPrefix`` was edited, next to the current one. Counting bucket names passed them
as one bucket and the prefix rank picked one silently, so every template body and all run I/O could
go under the stale prefix. A retired row (its registration left the configuration) never counts.
"""

from unittest.mock import MagicMock

import pytest

from backend.backend.common.workflows.defaultBucket import (
    DefaultBucketAmbiguousError,
    DefaultBucketNotFoundError,
    resolve_default_bucket,
)


def _table(items):
    table = MagicMock()
    table.scan.return_value = {"Items": items}
    return table


def _row(bucket_id, prefix, **extra):
    row = {"bucketId": bucket_id, "bucketName": "customer-bucket", "baseAssetsPrefix": prefix,
           "bucketName:baseAssetsPrefix": f"customer-bucket:{prefix}", "isDefault": True}
    row.update(extra)
    return row


@pytest.mark.unit
class TestDefaultBucketRegistrationIdentity:
    def test_a_stale_prefix_row_of_the_same_bucket_is_ambiguous(self):
        """The edit scenario: 'a/' was the default, the configuration now says 'b/'. The old prefix
        rank chose 'a/' (it sorts first) and sent run I/O under the stale prefix."""
        rows = [_row("b-stale", "a/"), _row("b-current", "b/")]
        for ordered in (rows, list(reversed(rows))):
            with pytest.raises(DefaultBucketAmbiguousError):
                resolve_default_bucket(_table(ordered))

    def test_a_retired_flagged_row_does_not_count(self):
        """A row the deployment retired is left out even if its isDefault flag was not cleared."""
        table = _table([_row("b-stale", "a/", retired=True), _row("b-current", "b/")])

        resolved = resolve_default_bucket(table)

        assert resolved == {"bucketId": "b-current", "bucketName": "customer-bucket",
                            "baseAssetsPrefix": "b/"}

    def test_only_retired_flagged_rows_is_not_found(self):
        with pytest.raises(DefaultBucketNotFoundError):
            resolve_default_bucket(_table([_row("b-stale", "a/", retired=True)]))

    def test_one_flagged_registration_resolves_to_its_row(self):
        resolved = resolve_default_bucket(_table([_row("b-current", "vams/")]))

        assert resolved["bucketId"] == "b-current"
        assert resolved["baseAssetsPrefix"] == "vams/"

    def test_the_error_names_the_registrations_not_the_bucket(self):
        with pytest.raises(DefaultBucketAmbiguousError) as raised:
            resolve_default_bucket(_table([_row("b-stale", "a/"), _row("b-current", "b/")]))

        assert "registration" in str(raised.value)
