# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``resolve_default_bucket`` pages the isDefault scan on key PRESENCE.

The scan behind the default-bucket lookup filters on ``isDefault = True``. A FilterExpression is
applied AFTER DynamoDB reads its 1 MB page, so the flagged row can sit on any page of a table with
many registered buckets -- reading one page reports "no default bucket is flagged" for a deployment
that has one, which fails every template-body write and all run I/O (the resolver is on the
``pipelineTemplateService``, ``executeWorkflow`` and ``workflowTriggerService`` paths).

The loop must therefore page to exhaustion, and it must do so on the key's PRESENCE: the value form
spins forever against an under-stubbed reader instead of failing. See ``tests/pagingStub``.
"""

from unittest.mock import MagicMock

import pytest

from backend.backend.common.workflows.defaultBucket import (
    DefaultBucketAmbiguousError,
    DefaultBucketNotFoundError,
    resolve_default_bucket,
)
from backend.tests.pagingStub import BareMockReader, Pager

DEFAULT_ROW = {"bucketId": "b-default", "bucketName": "vams-assets", "baseAssetsPrefix": ""}


@pytest.mark.unit
class TestResolveDefaultBucketPaging:
    def test_a_default_flagged_on_a_later_page_is_found(self):
        """The property the paging exists for: the flagged row is not on the first page."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"bucketId": "b-page-1"}},
            {"Items": [], "LastEvaluatedKey": {"bucketId": "b-page-2"}},
            {"Items": [DEFAULT_ROW]},
            name="resolve_default_bucket scan",
        )
        table = MagicMock()
        table.scan.side_effect = pager

        resolved = resolve_default_bucket(table)

        assert resolved["bucketName"] == "vams-assets"
        # Each page's cursor was sent back as the next read's ExclusiveStartKey.
        pager.assert_paged_to_exhaustion()

    def test_the_filter_is_carried_on_every_read(self):
        """The continuation reads must keep filtering, not fall back to reading the whole table."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"bucketId": "b-page-1"}},
            {"Items": [DEFAULT_ROW]},
            name="resolve_default_bucket scan",
        )
        table = MagicMock()
        table.scan.side_effect = pager

        resolve_default_bucket(table)

        assert all("FilterExpression" in call for call in pager.calls), pager.calls

    def test_a_single_page_result_resolves(self):
        """Positive control for the termination test below: the resolver does not always raise."""
        table = MagicMock()
        table.scan.return_value = {"Items": [DEFAULT_ROW]}

        assert resolve_default_bucket(table)["bucketName"] == "vams-assets"

    def test_terminates_against_an_under_stubbed_reader(self):
        """A bare Mock page is what an under-specified fixture hands the loop.

        The loop must END (with whatever it read, which here is nothing) rather than spin. The
        reader raises after a capped number of reads, so the value form fails with a message
        instead of hanging the run.
        """
        table = MagicMock()
        table.scan.side_effect = BareMockReader(name="resolve_default_bucket scan")

        with pytest.raises(DefaultBucketNotFoundError):
            resolve_default_bucket(table)

    def test_ambiguity_is_still_detected_across_pages(self):
        """Paging must not lose the second flagged bucket, which is what makes it unresolvable."""
        pager = Pager(
            {"Items": [DEFAULT_ROW], "LastEvaluatedKey": {"bucketId": "b-default"}},
            {"Items": [{"bucketId": "b-stale", "bucketName": "stale-bucket",
                        "baseAssetsPrefix": ""}]},
            name="resolve_default_bucket scan",
        )
        table = MagicMock()
        table.scan.side_effect = pager

        with pytest.raises(DefaultBucketAmbiguousError):
            resolve_default_bucket(table)
