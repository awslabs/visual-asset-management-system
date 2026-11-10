# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workflow callers of the copy cancel path wait out the deploy window's AccessDenied and
throttles (#1123 L1): the error handler for up to the 2-minute deploy bound, the abort route only
briefly because it answers an API call. The wait itself is pinned in
tests/common/test_largeObjectCopy_throttle.py."""

from unittest.mock import patch

import pytest

from backend.tests.handlers.workflows import test_system_pipeline_hooks as hooks

loc = hooks.le.loc


@pytest.mark.unit
class TestCancelCallersPassTheirBound:
    def test_the_error_handler_passes_its_bound(self):
        heh = hooks.heh
        with patch.object(heh.loc, "cancel_execution_copy_jobs", return_value=[]) as cancel, \
                patch.object(heh, "get_table_name", return_value="t"), \
                patch.object(heh, "dynamodb"):
            heh._cancel_output_copies("E1")
        assert cancel.call_args.kwargs["max_wait_seconds"] == loc.ENGINE_CALL_DEPLOY_WAIT_SECONDS

    def test_the_abort_route_passes_its_short_bound(self):
        le = hooks.le
        with patch.object(le.loc, "cancel_execution_copy_jobs", return_value=[]) as cancel, \
                patch.object(le, "get_table_name", return_value="t"), \
                patch.object(le, "dynamodb"), patch.object(le, "_copy_s3_client"):
            le._cancel_output_copies("E1")
        assert cancel.call_args.kwargs["max_wait_seconds"] == loc.API_CALLER_WAIT_SECONDS
