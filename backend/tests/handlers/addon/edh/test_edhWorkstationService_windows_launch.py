# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Windows rows on the launch and start routes, and the root disk floor a launch is held to.

A Windows desktop has no consumer for a session credential, so its launch and its start answer
``disabled`` and mint nothing, while the start still clears the keys a bootstrap may hold from before.
A launch below the root size EDH registered for the image is refused before anything is written; a size
the catalog does not state as a positive whole number holds nothing back.
"""

from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    BOOTSTRAP_OWN,
    CATALOG_STACK,
    SESSION_OWN,
    WORKSTATIONS_PATH,
    FakeEdhClient,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
    workstation_path,
)
import backend.backend.handlers.addon.edh.edhWorkstationService as service

bootstrap = service.bootstrap

WINDOWS_STACK = {
    **CATALOG_STACK,
    service.EDH_FIELD_STACK_NAME: "Windows Server 2022",
    service.EDH_FIELD_STACK_BASE_OS: "windows2022",
    service.EDH_FIELD_STACK_OS_FAMILY: "windows",
    service.EDH_FIELD_STACK_ROOT_SIZE: 60,
}
MINTED = {"apiKeyId": "key-1", "apiKey": "vams_" + "k" * 64, "expiresAt": "2026-09-08T20:00:00Z"}


def _with_root_size(value):
    """The catalog stack of imageId 42 with ``value`` as its registered root size."""
    return {**CATALOG_STACK, service.EDH_FIELD_STACK_ROOT_SIZE: value}


def _body(**overrides):
    body = {"bootstrapId": BOOTSTRAP_OWN, "imageId": "42", "instanceType": "g5.xlarge", "name": "Design desk",
            "rootSizeGb": 100, "osFamily": "linux"}
    body.update(overrides)
    return body


def _launch(monkeypatch, *, stacks=None, enforce=None):
    edh = FakeEdhClient(list_software_stacks=stacks) if stacks is not None else None
    fakes = install_service_fakes(monkeypatch, edh=edh, enforce=enforce)
    monkeypatch.setattr(service, "allowed_instance_types", ("g5.xlarge", "g5.2xlarge"))
    return fakes


def _post(fakes, body):
    return fakes.module.lambda_handler(make_event("POST", WORKSTATIONS_PATH, body=body), {})


def _bootstrap_on(monkeypatch):
    """Session bootstrap on, with the mint and the secret write replaced; returns the mint."""
    monkeypatch.setattr(bootstrap, "session_bootstrap_enabled", True)
    monkeypatch.setattr(bootstrap, "resolve_vams_base_url", lambda: "https://vams.example.com")
    monkeypatch.setattr(bootstrap, "_budget_deadline", None)
    mint = MagicMock(return_value=dict(MINTED))
    monkeypatch.setattr(bootstrap, "mint_session_key", mint)
    monkeypatch.setattr(bootstrap, "create_session_secret", MagicMock(return_value="secret-arn-" + BOOTSTRAP_OWN))
    return mint


@pytest.mark.unit
class TestWindowsLaunch:
    def test_a_windows_launch_answers_disabled_and_mints_nothing(self, monkeypatch):
        fakes = _launch(monkeypatch, stacks=[WINDOWS_STACK])
        mint = _bootstrap_on(monkeypatch)

        response = _post(fakes, _body(osFamily="windows", rootSizeGb=60))

        assert response["statusCode"] == 202
        assert response_body(response)["sessionBootstrap"] == "disabled"
        mint.assert_not_called()
        assert fakes.table.put_item.call_args.kwargs["Item"]["osFamily"] == "windows"
        fakes.lambda_client.invoke.assert_called_once()

    def test_a_linux_launch_still_provisions(self, monkeypatch):
        """Control: the same doubles provision a Linux launch."""
        fakes = _launch(monkeypatch)
        mint = _bootstrap_on(monkeypatch)

        response = _post(fakes, _body())

        assert response["statusCode"] == 202
        assert response_body(response)["sessionBootstrap"] == "provisioned"
        mint.assert_called_once()


@pytest.mark.unit
class TestRootDiskFloor:
    def test_a_launch_below_the_images_root_size_is_refused_before_anything_is_written(self, monkeypatch):
        fakes = _launch(monkeypatch)

        response = _post(fakes, _body(rootSizeGb=99))

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Root disk size must be at least 100 GB for this image"
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()

    def test_a_launch_at_the_images_root_size_is_accepted(self, monkeypatch):
        fakes = _launch(monkeypatch)

        assert _post(fakes, _body(rootSizeGb=100))["statusCode"] == 202

    @pytest.mark.parametrize("registered", ["120", 120.0, " 120 "])
    def test_a_root_size_stated_as_text_or_a_whole_float_is_a_floor(self, monkeypatch, registered):
        fakes = _launch(monkeypatch, stacks=[_with_root_size(registered)])

        response = _post(fakes, _body(rootSizeGb=119))

        assert response["statusCode"] == 400
        assert "120 GB" in response_body(response)["message"]

    @pytest.mark.parametrize("registered", [True, 0, None, -5, "abc", 120.5, "6e1", "", "12.0"])
    def test_a_root_size_that_is_not_a_positive_whole_number_holds_nothing_back(self, monkeypatch, registered):
        fakes = _launch(monkeypatch, stacks=[_with_root_size(registered)])

        assert service._stack_root_size(_with_root_size(registered)) is None
        assert _post(fakes, _body(rootSizeGb=10))["statusCode"] == 202

    def test_a_stack_without_a_root_size_holds_nothing_back(self, monkeypatch):
        stack = dict(CATALOG_STACK)
        stack.pop(service.EDH_FIELD_STACK_ROOT_SIZE)
        fakes = _launch(monkeypatch, stacks=[stack])

        assert _post(fakes, _body(rootSizeGb=10))["statusCode"] == 202

    def test_a_caller_tier2_denies_learns_nothing_of_the_floor(self, monkeypatch):
        fakes = _launch(monkeypatch, enforce=lambda obj, act: False)

        response = _post(fakes, _body(rootSizeGb=99))

        assert response["statusCode"] == 403
        fakes.table.put_item.assert_not_called()


@pytest.mark.unit
class TestWindowsStart:
    def test_a_windows_start_mints_nothing_and_hands_the_start_over(self, monkeypatch):
        """The start answers disabled and mints nothing; the launch worker, once EDH takes the start, clears the
        keys a bootstrap left before (test_edhLaunchWorker_start.py)."""
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[
            {service.EDH_FIELD_SESSION_UUID: SESSION_OWN, service.EDH_FIELD_SESSION_STATE: "stopped"}]))
        row = own_row(state="STOPPED", edhState="stopped", connectionAvailable=False, osFamily="windows")
        fakes.table.query.return_value = {"Items": [row]}
        mint = _bootstrap_on(monkeypatch)
        others = MagicMock()
        monkeypatch.setattr(bootstrap, "delete_other_session_keys", others)
        event = make_event("POST", workstation_path(SESSION_OWN, "start"), path_params={"sessionId": SESSION_OWN})

        response = service.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 202
        assert response_body(response)["sessionBootstrap"] == "disabled"
        mint.assert_not_called()
        others.assert_not_called()
        assert fakes.edh.calls_to("start_desktop") == []
        fakes.lambda_client.invoke.assert_called_once()
