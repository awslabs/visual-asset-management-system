# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The edhProxy audit labels: each wrapper's (verb, route) matches the sanitized EDH capture.

The routes are the client's own constants, so only the verb can drift from the wrapper body; both
are checked against the ``request`` half of the capture the client wrapper is itself tested against.
A route the client joins to the endpoint origin (one starting at ``/api/``) is compared to the
capture's ``absolutePath``. Only the wrappers this Lambda calls are labelled here; the launch's EDH
calls (the directory probe, the create, the read-back) are the launch worker's and are labelled there.
"""

import json
import os

import pytest

from backend.tests.handlers.addon.edh.edhTestSupport import FIXTURES_DIR
import backend.backend.handlers.addon.edh.edhWorkstationService as service

# wrapper name -> the capture of the request that wrapper sends
CAPTURES = {
    "get_desktop_states": "virtual_desktops_session_state.json",
    "get_desktop_connection": "virtual_desktops_list_active.json",
    "stop_desktop": "virtual_desktops_stop.json",
    "start_desktop": "virtual_desktops_start.json",
    "delete_desktop": "virtual_desktops_delete.json",
    "list_software_stacks": "software_stacks_dcv_list.json",
    "list_profiles": "profiles_dcv_list.json",
}


def _request_of(fixture_name):
    with open(os.path.join(FIXTURES_DIR, fixture_name), encoding="utf-8") as handle:
        return json.load(handle)["request"]


def _assert_labels_match(labels, request):
    verb, route = labels
    assert verb == request["method"]
    assert route in (request["path"], request.get("absolutePath"))


@pytest.mark.unit
class TestAuditRoutes:
    def test_every_audited_wrapper_has_a_capture(self):
        """Control: the map and the capture table name the same wrappers, so no entry goes unchecked."""
        assert set(service.EDH_AUDIT_ROUTES) == set(CAPTURES)

    @pytest.mark.parametrize("wrapper", sorted(CAPTURES))
    def test_the_audit_labels_match_the_captured_request(self, wrapper):
        _assert_labels_match(service.EDH_AUDIT_ROUTES[wrapper], _request_of(CAPTURES[wrapper]))
