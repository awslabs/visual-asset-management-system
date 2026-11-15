# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An EDH API route's record is the record of each call it makes to the EDH service; a route that made
no such call records its action itself, and never a second record when it did."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _context(event, route_action=None, recorded=()):
    route_spec = SimpleNamespace(action=route_action) if route_action else None
    return SimpleNamespace(event=event, route_spec=route_spec, recorded_actions=set(recorded))


@pytest.fixture
def edh_audit():
    from backend.backend.handlers.addon.edh.common import edhAudit
    return edhAudit


@pytest.mark.unit
class TestEdhRouteRecord:
    def test_a_route_with_no_service_call_records_its_action(self, edh_audit):
        event = {"pathParameters": {"sessionId": "s-1"}, "queryStringParameters": {"page": "2"}}
        recorder = MagicMock()
        with patch.object(edh_audit, "log_action", recorder), \
                patch.object(edh_audit, "current_audit_context", lambda: _context(event, "edhWorkstationGet")):
            edh_audit.record_route_action(event, {"statusCode": 200})
        recorder.assert_called_once_with(event, "edhWorkstationGet", entity_id="s-1",
                                         request_parameters={"page": "2"})

    def test_a_route_whose_service_call_was_recorded_adds_nothing(self, edh_audit):
        event = {"pathParameters": {}}
        recorder = MagicMock()
        with patch.object(edh_audit, "log_action", recorder), \
                patch.object(edh_audit, "current_audit_context",
                             lambda: _context(event, "edhWorkstationList", recorded={"edhWorkstationList"})):
            edh_audit.record_route_action(event, {"statusCode": 200})
        recorder.assert_not_called()

    @pytest.mark.parametrize("status", [400, 403, 500])
    def test_an_error_response_is_left_to_the_entrypoint(self, edh_audit, status):
        recorder = MagicMock()
        with patch.object(edh_audit, "log_action", recorder), \
                patch.object(edh_audit, "current_audit_context", lambda: _context({}, "edhWorkstationGet")):
            edh_audit.record_route_action({}, {"statusCode": status})
        recorder.assert_not_called()

    def test_the_decorator_records_after_the_handler(self, edh_audit):
        calls = []

        @edh_audit.records_route_action
        def handler(event, context):
            calls.append("handler")
            return {"statusCode": 200}

        with patch.object(edh_audit, "record_route_action", lambda event, response: calls.append("record")):
            assert handler({}, None) == {"statusCode": 200}
        assert calls == ["handler", "record"]
