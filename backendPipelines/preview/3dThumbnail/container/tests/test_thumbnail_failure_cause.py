#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The fatal-error failure ``cause`` keeps up to the Step Functions limit of 32,768 characters (#471).

It used to be cut to 256 characters, the limit of ``error``, so the traceback was lost.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from preview_pipeline import __main__ as entry


@pytest.mark.unit
class TestFailureCause:
    def _sent_cause(self, message, monkeypatch):
        monkeypatch.setenv("TASK_TOKEN", "token")
        client = MagicMock()
        boto3 = MagicMock(client=MagicMock(return_value=client))
        with patch.dict(sys.modules, {"boto3": boto3}):
            entry._send_sfn_failure_on_fatal(message)
        return client.send_task_failure.call_args.kwargs

    def test_a_long_traceback_is_kept_up_to_the_cause_limit(self, monkeypatch):
        sent = self._sent_cause("x" * 40_000, monkeypatch)
        assert len(sent["cause"]) == entry.SFN_CAUSE_MAX_LENGTH == 32768
        assert sent["cause"].endswith("...")
        assert len(sent["error"]) <= 256

    def test_a_message_within_the_limit_is_sent_whole(self, monkeypatch):
        message = "Fatal pipeline error: boom\n" + "frame\n" * 1000
        assert self._sent_cause(message, monkeypatch)["cause"] == message
