# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""WB5b producer tests: publish_to_orchestration_bus publishes a clean, flat asset.file.uploaded
detail and excludes workflow-sourced records (re-trigger loop guard)."""

import json

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load


@pytest.mark.unit
class TestOrchestrationPublish:
    """The producer publishes EVERY S3 record, workflow-written ones included.

    The re-trigger decision is deliberately NOT made here: it depends on the candidate workflow's
    allowWorkflowTriggerChaining and on whether the file came from that same workflow, neither of which
    is known at publish time. Dropping records here would make a per-workflow opt-in unreachable, so
    the dispatcher decides per workflow (see common/workflows/triggerMatching.chaining_allows_trigger)
    using the provenance metadata it already reads."""

    def test_publishes_workflow_sourced_records_for_the_dispatcher_to_judge(self):
        sbs = _load()
        records = [
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/wf-out.glb"}}},
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/user.glb"}}},
        ]
        captured = {}
        with patch.object(sbs, "orchestration_bus_name", "bus"),              patch.object(sbs, "orchestration_event_source_prefix", "vams.test"),              patch.object(sbs, "events_client") as m_events:
            m_events.put_events.side_effect = lambda Entries: captured.update({"e": Entries})
            sbs.publish_to_orchestration_bus(records)
        assert "e" in captured, "a clean detail should have been published"
        detail = json.loads(captured["e"][0]["Detail"])
        keys = [r["s3"]["object"]["key"] for r in detail["Records"]]
        # BOTH records travel; the workflow-sourced one is judged downstream, not dropped here.
        assert keys == ["a/wf-out.glb", "a/user.glb"]
        assert captured["e"][0]["DetailType"] == "asset.file.uploaded"

    def test_publish_does_not_head_objects(self):
        """Publishing no longer inspects provenance, so it must not pay for a head_object per record —
        the dispatcher already heads the object to resolve the asset and reads the metadata there."""
        sbs = _load()
        records = [{"s3": {"bucket": {"name": "b"}, "object": {"key": "a/wf-out.glb"}}}]
        with patch.object(sbs, "orchestration_bus_name", "bus"),              patch.object(sbs, "orchestration_event_source_prefix", "vams.test"),              patch.object(sbs, "s3_client") as m_s3,              patch.object(sbs, "events_client"):
            sbs.publish_to_orchestration_bus(records)
        m_s3.head_object.assert_not_called()

    def test_records_without_an_s3_block_are_skipped(self):
        sbs = _load()
        records = [{"not_s3": {}}]
        with patch.object(sbs, "orchestration_bus_name", "bus"), \
             patch.object(sbs, "orchestration_event_source_prefix", "vams.test"), \
             patch.object(sbs, "events_client") as m_events:
            sbs.publish_to_orchestration_bus(records)
        m_events.put_events.assert_not_called()

    def test_literal_plus_key_falls_back_to_raw_key(self):
        """A key with a literal '+' 404s on the decoded form; the raw form resolves it."""
        sbs = _load()
        records = [{"s3": {"bucket": {"name": "b"}, "object": {"key": "a/PART+1.CATPart"}}}]
        captured = {}
        with patch.object(sbs, "orchestration_bus_name", "bus"), \
             patch.object(sbs, "orchestration_event_source_prefix", "vams.test"), \
             patch.object(sbs, "s3_client") as m_s3, \
             patch.object(sbs, "events_client") as m_events:
            def _head(Bucket, Key):
                if Key == "a/PART 1.CATPart":
                    raise ClientError({"Error": {"Code": "404", "Message": "nope"}}, "HeadObject")
                return {"Metadata": {"vams-changesource": "upload"}}
            m_s3.head_object.side_effect = _head
            m_events.put_events.side_effect = lambda Entries: captured.update({"e": Entries})
            sbs.publish_to_orchestration_bus(records)
        assert "e" in captured
        detail = json.loads(captured["e"][0]["Detail"])
        assert [r["s3"]["object"]["key"] for r in detail["Records"]] == ["a/PART+1.CATPart"]

    def test_no_publish_when_bus_unconfigured(self):
        sbs = _load()
        records = [{"s3": {"bucket": {"name": "b"}, "object": {"key": "a/user.glb"}}}]
        with patch.object(sbs, "orchestration_bus_name", ""), \
             patch.object(sbs, "events_client") as m_events:
            sbs.publish_to_orchestration_bus(records)
        m_events.put_events.assert_not_called()
