#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the splatToolbox container's check that a finished run actually wrote something.

``main.py`` exits 0 whenever its upload steps are skipped, and the workflow's process-output step then
finds no files and records the execution as complete — so a job that ran for hours and produced
nothing is indistinguishable from one that worked. ``missing_output_cause`` reads the run's own output
prefix before the success callback and turns an empty one into a ``SendTaskFailure`` cause.
"""

import pytest

_PREFIX = "pipelines/splatToolbox/JOB/output/3f2c9a10/files/"


class RecordingS3:
    """An S3 client stub that records its requests and returns a real listing shape.

    Not a ``MagicMock``: a mock's ``list_objects_v2(...).get('Contents')`` returns a truthy mock, so
    every assertion below would pass whatever the code under test did.
    """

    def __init__(self, keys=(), error=None):
        self.requests = []
        self._keys = list(keys)
        self._error = error

    def list_objects_v2(self, **kwargs):
        self.requests.append(kwargs)
        if self._error is not None:
            raise self._error
        response = {"KeyCount": len(self._keys)}
        if self._keys:
            response["Contents"] = [{"Key": key} for key in self._keys]
        return response


@pytest.mark.unit
class TestOutputListingPrefix:
    def test_it_addresses_the_place_the_pair_writes_to(self, container_main):
        """Derived from the (S3_OUTPUT, UUID) pair rather than recomputed, so the prefix that is read
        cannot name a different location than the one main.py wrote to."""
        pair = container_main.resolve_output_env("run-bucket", _PREFIX, "JOB")
        bucket_name, key_prefix = container_main.output_listing_prefix(*pair)
        assert (bucket_name, key_prefix) == ("run-bucket", _PREFIX)
        assert f"{pair[0]}/{pair[1]}/" == f"s3://{bucket_name}/{key_prefix}"

    def test_a_single_segment_prefix_keeps_the_bucket_and_the_segment_apart(self, container_main):
        pair = container_main.resolve_output_env("run-bucket", "files/", "JOB")
        assert container_main.output_listing_prefix(*pair) == ("run-bucket", "files/")

    def test_an_empty_output_dir_falls_back_to_the_job_name(self, container_main):
        pair = container_main.resolve_output_env("run-bucket", "", "JOB")
        assert container_main.output_listing_prefix(*pair) == ("run-bucket", "JOB/")


@pytest.mark.unit
class TestMissingOutputCause:
    def test_an_empty_prefix_yields_a_cause_naming_the_location(self, container_main):
        client = RecordingS3(keys=())
        cause = container_main.missing_output_cause("run-bucket", _PREFIX, client)
        assert cause is not None
        assert f"s3://run-bucket/{_PREFIX}" in cause

    def test_one_object_under_the_prefix_is_enough(self, container_main):
        client = RecordingS3(keys=[f"{_PREFIX}model.ply"])
        assert container_main.missing_output_cause("run-bucket", _PREFIX, client) is None

    def test_it_asks_for_one_key_under_the_run_prefix(self, container_main):
        """The question is whether the prefix is empty, not what is in it: an output prefix holds
        thousands of files and the listing must not page through them."""
        client = RecordingS3(keys=[f"{_PREFIX}model.ply"])
        container_main.missing_output_cause("run-bucket", _PREFIX, client)
        assert client.requests == [
            {"Bucket": "run-bucket", "Prefix": _PREFIX, "MaxKeys": 1}]

    def test_a_failed_listing_does_not_fail_the_run(self, container_main):
        """A listing that raises is not evidence of an empty prefix, and discarding a finished GPU run
        on it would cost more than the check saves. The attempt is still made."""
        client = RecordingS3(error=RuntimeError("AccessDenied"))
        assert container_main.missing_output_cause("run-bucket", _PREFIX, client) is None
        assert len(client.requests) == 1
