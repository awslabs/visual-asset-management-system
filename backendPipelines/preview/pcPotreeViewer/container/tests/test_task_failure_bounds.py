#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The container reports a failed stage with Step Functions SendTaskFailure, which rejects an
``error`` longer than 256 characters or a ``cause`` longer than 32,768 with a ValidationException.
A rejected report is never delivered, so the step waits out its full task timeout instead of failing.

#1203 puts PDAL's own message into the stage error, so the report is bounded here, and when ``error`` has to be
cut the full message goes into ``cause`` so the cut part is still recorded. VAMS shows a failed step as
"<error>: <cause>", so a message that fits is not repeated in ``cause`` (#1216 D1)."""

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

    def test_short_message_is_passed_whole_and_not_repeated_in_cause(self):
        """#1216 D1: VAMS shows a failed step as "<error>: <cause>". A message that fits in `error`
        must not be copied into `cause` too, or the shown error says it twice."""
        message = ("E57 file could not be read by the converter: PDAL: readers.e57: 29 : "
                   "fileName=/tmp/input/cut.e57 header.filePhysicalLength=743424 file->length=102400")
        sent = self._send(message)
        assert sent["error"] == "Pipeline Failure: " + message
        assert sent["cause"] == sfn.TASK_FAILURE_CAUSE_SUFFIX
        shown = f"{sent['error']}: {sent['cause']}"
        assert shown.count("E57 file could not be read by the converter") == 1
        assert shown.count("Pipeline Failure:") == 1

    def test_long_message_is_cut_to_the_service_limits(self):
        """Unfixed, a 300-character message is sent whole and the service rejects it. The full
        message then goes in `cause`, as the end of it was cut from `error`."""
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
