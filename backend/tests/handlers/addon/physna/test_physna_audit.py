# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Add-on sync handlers record one record per entity they tracked, with the sync's outcome, in the
invocation's audit context."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.addon.physna import physnaAssetSync, physnaFileSync


def _context(event, route_action=None, recorded=()):
    route_spec = SimpleNamespace(action=route_action) if route_action else None
    return SimpleNamespace(event=event, route_spec=route_spec, recorded_actions=set(recorded))


@pytest.mark.unit
class TestPhysnaSyncRecords:
    @pytest.mark.parametrize("status, outcome", [
        (physnaFileSync.SYNC_STATUS_SUCCESS, "success"), (physnaFileSync.SYNC_STATUS_FAILED, "failed")])
    def test_a_file_sync_record_carries_the_outcome(self, status, outcome):
        event = {"Records": []}
        recorder = MagicMock()
        with patch.object(physnaFileSync, "write_outbound_sync_record"), \
                patch.object(physnaFileSync, "log_action", recorder), \
                patch.object(physnaFileSync, "current_audit_context", lambda: _context(event)):
            physnaFileSync._record_file_sync("db1", "asset-01", "/a.glb", "modify", status)
        recorder.assert_called_once()
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "physnaFileSync")
        assert kwargs["outcome"] == outcome and kwargs["file_path"] == "/a.glb"
        assert kwargs["details"] == {"operation": "modify", "syncStatus": status}

    def test_an_asset_sync_record_names_the_asset(self):
        event = {"Records": []}
        recorder = MagicMock()
        with patch.object(physnaAssetSync, "write_outbound_sync_record"), \
                patch.object(physnaAssetSync, "log_action", recorder), \
                patch.object(physnaAssetSync, "current_audit_context", lambda: _context(event)):
            physnaAssetSync._record_asset_sync("db1", "asset-01", "delete", physnaAssetSync.SYNC_STATUS_SUCCESS)
        assert recorder.call_args.args == (event, "physnaAssetSync")
        assert recorder.call_args.kwargs["asset_id"] == "asset-01"

    def test_no_record_outside_an_invocation(self):
        recorder = MagicMock()
        with patch.object(physnaFileSync, "write_outbound_sync_record"), \
                patch.object(physnaFileSync, "log_action", recorder), \
                patch.object(physnaFileSync, "current_audit_context", lambda: None):
            physnaFileSync._record_file_sync("db1", "asset-01", "/a.glb", "modify", "success")
        recorder.assert_not_called()
