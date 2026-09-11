# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""publish_to_orchestration_bus log fidelity: a successful publish logs success only, and a genuine
EventBridge failure is reported as a put_events failure rather than a generic publish error."""

import pytest
from unittest.mock import MagicMock, patch

from tests.handlers.indexing.test_sqsBucketSync_orchestration_publish import _load


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
            sbs.publish_to_orchestration_bus(self.RECORDS)

        m_events.put_events.assert_called_once()
        mock_logger.exception.assert_not_called()
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
