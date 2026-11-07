#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The container reports a failed stage with Step Functions SendTaskFailure, which rejects an
``error`` longer than 256 characters or a ``cause`` longer than 32,768 with a ValidationException.
A rejected report is never delivered, so the step waits out its full task timeout instead of failing.

#1203 puts PDAL's own message into the stage error, so the report is bounded here, and the full
message goes into ``cause`` so the part cut from ``error`` is still recorded."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import load_stage_module  # noqa: E402

sfn = load_stage_module("utils.aws.sfn")


@pytest.mark.unit
class TestSendTaskFailureBounds:
    def _send(self, message):
        client = MagicMock()
        with patch.object(sfn, "client", client):
            sfn.send_task_failure(message)
        return client.send_task_failure.call_args.kwargs

    def test_short_message_is_passed_whole_and_in_cause(self):
        sent = self._send("E57 file could not be read by the converter: PDAL: E57 exception")
        assert sent["error"] == "Pipeline Failure: E57 file could not be read by the converter: PDAL: E57 exception"
        assert sent["cause"].startswith("E57 file could not be read by the converter: PDAL: E57 exception")

    def test_long_message_is_cut_to_the_service_limits(self):
        """Unfixed, a 300-character message is sent whole and the service rejects it."""
        sent = self._send("x" * 300)
        assert len(sent["error"]) <= sfn.TASK_FAILURE_ERROR_MAX_CHARS
        assert sent["error"].endswith("...")
        assert "x" * 300 in sent["cause"]

        sent = self._send("y" * 40000)
        assert len(sent["cause"]) <= sfn.TASK_FAILURE_CAUSE_MAX_CHARS

    def test_empty_message_keeps_the_generic_cause(self):
        sent = self._send("")
        assert sent["error"] == "Pipeline Failure: "
        assert sent["cause"] == sfn.TASK_FAILURE_CAUSE_SUFFIX
