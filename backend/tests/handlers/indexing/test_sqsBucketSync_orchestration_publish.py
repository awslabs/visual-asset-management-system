# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""WB5b producer tests: publish_to_orchestration_bus publishes a clean, flat asset.file.uploaded
detail and excludes workflow-sourced records (re-trigger loop guard)."""

import json

import pytest
from unittest.mock import MagicMock, patch

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load


@pytest.mark.unit
class TestOrchestrationPublish:
    def test_excludes_workflow_sourced_records(self):
        sbs = _load()
        records = [
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/wf-out.glb"}}},
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/user.glb"}}},
        ]
        captured = {}
        with patch.object(sbs, "orchestration_bus_name", "bus"), \
             patch.object(sbs, "orchestration_event_source_prefix", "vams.test"), \
             patch.object(sbs, "s3_client") as m_s3, \
             patch.object(sbs, "events_client") as m_events:
            m_s3.head_object.side_effect = [
                {"Metadata": {"vams-changesource": "workflowExecution"}},
                {"Metadata": {"vams-changesource": "direct"}},
            ]
            m_events.put_events.side_effect = lambda Entries: captured.update({"e": Entries})
            sbs.publish_to_orchestration_bus(records)
        assert "e" in captured, "a clean detail should have been published"
        detail = json.loads(captured["e"][0]["Detail"])
        keys = [r["s3"]["object"]["key"] for r in detail["Records"]]
        assert keys == ["a/user.glb"]  # workflow-sourced record excluded
        assert captured["e"][0]["DetailType"] == "asset.file.uploaded"

    def test_all_workflow_sourced_publishes_nothing(self):
        sbs = _load()
        records = [{"s3": {"bucket": {"name": "b"}, "object": {"key": "a/wf-out.glb"}}}]
        with patch.object(sbs, "orchestration_bus_name", "bus"), \
             patch.object(sbs, "orchestration_event_source_prefix", "vams.test"), \
             patch.object(sbs, "s3_client") as m_s3, \
             patch.object(sbs, "events_client") as m_events:
            m_s3.head_object.return_value = {"Metadata": {"vams-changesource": "workflowExecution"}}
            sbs.publish_to_orchestration_bus(records)
        m_events.put_events.assert_not_called()

    def test_no_publish_when_bus_unconfigured(self):
        sbs = _load()
        records = [{"s3": {"bucket": {"name": "b"}, "object": {"key": "a/user.glb"}}}]
        with patch.object(sbs, "orchestration_bus_name", ""), \
             patch.object(sbs, "events_client") as m_events:
            sbs.publish_to_orchestration_bus(records)
        m_events.put_events.assert_not_called()
