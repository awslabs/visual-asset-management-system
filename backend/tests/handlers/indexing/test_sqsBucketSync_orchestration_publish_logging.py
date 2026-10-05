# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""publish_to_orchestration_bus log fidelity: a successful publish logs success only, a genuine
EventBridge failure is reported as a put_events failure rather than a generic publish error, and an
entry PutEvents refuses inside a normal response is re-sent once when its code is retryable and is
otherwise logged as an error, never as a success."""

import pytest
from unittest.mock import MagicMock, patch

from tests.handlers.indexing.test_sqsBucketSync_orchestration_publish import _load


# PutEvents answers HTTP 200 for a request whose entries it refuses: FailedEntryCount counts them and
# each refused result entry carries ErrorCode/ErrorMessage in place of an EventId.
_ACCEPTED = {"FailedEntryCount": 0, "Entries": [{"EventId": "11710aed-b79e-4468-a20b-bb3c0c3b4860"}]}


def _refused(code, message):
    return {"FailedEntryCount": 1, "Entries": [{"ErrorCode": code, "ErrorMessage": message}]}


@pytest.mark.unit
class TestOrchestrationPublishLogging:

    RECORDS = [
        {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/one.glb"}}},
        {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/two.glb"}}},
    ]

    def test_successful_publish_logs_no_exception(self):
        sbs = _load()
        mock_logger = MagicMock()
        with patch.object(sbs, "orchestration_bus_name", "bus"), \
             patch.object(sbs, "orchestration_event_source_prefix", "vams.test"), \
             patch.object(sbs, "logger", mock_logger), \
             patch.object(sbs, "events_client") as m_events:
            m_events.put_events.return_value = _ACCEPTED
            sbs.publish_to_orchestration_bus(self.RECORDS)

        m_events.put_events.assert_called_once()
        mock_logger.exception.assert_not_called()
        mock_logger.error.assert_not_called()
        info_messages = [call.args[0] for call in mock_logger.info.call_args_list]
        assert any("Published asset.file.uploaded event (2 record(s))" in m for m in info_messages), info_messages

    def test_put_events_failure_is_reported_distinctly(self):
        sbs = _load()
        mock_logger = MagicMock()
        with patch.object(sbs, "orchestration_bus_name", "bus"), \
             patch.object(sbs, "orchestration_event_source_prefix", "vams.test"), \
             patch.object(sbs, "logger", mock_logger), \
             patch.object(sbs, "events_client") as m_events:
            m_events.put_events.side_effect = RuntimeError("bus throttled")
            sbs.publish_to_orchestration_bus(self.RECORDS)

        mock_logger.exception.assert_called_once()
        message = mock_logger.exception.call_args.args[0]
        assert "put_events failed" in message
        assert "bus throttled" in message
        # The success line must not be emitted when the publish failed.
        info_messages = [call.args[0] for call in mock_logger.info.call_args_list]
        assert not any("Published asset.file.uploaded event" in m for m in info_messages), info_messages


@pytest.mark.unit
class TestOrchestrationPublishRefusedEntry:
    """A refused entry arrives in a normal PutEvents response, not as an exception, and botocore's
    retry layer does not re-send it. The publisher reads the result entry: a retryable refusal
    (ThrottlingException, InternalFailure) is re-sent once, and a refusal that remains is logged as an
    error naming the code and the objects whose fileUpload triggers do not fire."""

    RECORDS = TestOrchestrationPublishLogging.RECORDS

    def _publish(self, responses):
        sbs = _load()
        mock_logger = MagicMock()
        with patch.object(sbs, "orchestration_bus_name", "bus"), \
             patch.object(sbs, "orchestration_event_source_prefix", "vams.test"), \
             patch.object(sbs, "logger", mock_logger), \
             patch.object(sbs, "events_client") as m_events:
            m_events.put_events.side_effect = responses
            sbs.publish_to_orchestration_bus(self.RECORDS)
        return m_events, mock_logger

    def test_non_retryable_refusal_is_logged_as_error_and_not_resent(self):
        m_events, mock_logger = self._publish([
            _refused("AccessDeniedException", "not authorized to use the bus key"),
        ])

        m_events.put_events.assert_called_once()
        mock_logger.exception.assert_not_called()
        mock_logger.error.assert_called_once()
        message = mock_logger.error.call_args.args[0]
        assert "asset.file.uploaded" in message
        assert "AccessDeniedException" in message
        assert "not authorized to use the bus key" in message
        assert "a/one.glb" in message and "a/two.glb" in message
        # The success line is the only info record this function writes.
        mock_logger.info.assert_not_called()

    def test_retryable_refusal_is_resent_once_and_then_reported_published(self):
        m_events, mock_logger = self._publish([
            _refused("ThrottlingException", "Rate exceeded"),
            _ACCEPTED,
        ])

        assert m_events.put_events.call_count == 2
        first, second = m_events.put_events.call_args_list
        # The re-send carries the same entry, so the same event reaches the dispatcher.
        assert first.kwargs["Entries"] == second.kwargs["Entries"]
        mock_logger.warning.assert_called_once()
        assert "ThrottlingException" in mock_logger.warning.call_args.args[0]
        mock_logger.error.assert_not_called()
        mock_logger.exception.assert_not_called()
        mock_logger.info.assert_called_once()
        assert "Published asset.file.uploaded event (2 record(s))" in mock_logger.info.call_args.args[0]

    def test_retryable_refusal_that_persists_is_resent_only_once_and_logged_as_error(self):
        m_events, mock_logger = self._publish([
            _refused("InternalFailure", "Internal Service Failure"),
            _refused("InternalFailure", "Internal Service Failure"),
        ])

        assert m_events.put_events.call_count == 2
        mock_logger.error.assert_called_once()
        message = mock_logger.error.call_args.args[0]
        assert "InternalFailure" in message
        assert "Internal Service Failure" in message
        # The success line is the only info record this function writes.
        mock_logger.info.assert_not_called()

    def test_resend_that_raises_is_reported_as_put_events_failure(self):
        m_events, mock_logger = self._publish([
            _refused("ThrottlingException", "Rate exceeded"),
            RuntimeError("endpoint unreachable"),
        ])

        assert m_events.put_events.call_count == 2
        mock_logger.exception.assert_called_once()
        message = mock_logger.exception.call_args.args[0]
        assert "put_events failed" in message
        assert "endpoint unreachable" in message
        # The success line is the only info record this function writes.
        mock_logger.info.assert_not_called()
