# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Launch path of edhWorkstationService with session bootstrap: the credential is minted after the
CREATING row is written and before the launch is handed to the worker, so the secret exists before the
desktop's first boot; the worker payload never carries it; the credential's attributes land on the row
and never in the response; the response names the bootstrap outcome; a hand-over that fails takes the
credential back with the row; a repeated bootstrapId and an API-key principal mint nothing."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    API_KEY_CLAIMS,
    BOOTSTRAP_OWN,
    USER,
    WORKSTATIONS_PATH,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
)

BODY = {
    "bootstrapId": BOOTSTRAP_OWN,
    "imageId": "42",
    "instanceType": "g5.xlarge",
    "name": "Design desk",
    "rootSizeGb": 100,
    "osFamily": "linux",
}
SECRET_NAME = f"vams/edh-session/{BOOTSTRAP_OWN}"
CREDENTIAL = {
    "apiKeyId": "key-1",
    "secretArn": f"arn:aws:secretsmanager:us-east-1:123456789012:secret:{SECRET_NAME}-AbCdEf",
    "secretName": SECRET_NAME,
    "keyExpiresAt": "2026-09-08T20:00:00Z",
}
CREDENTIAL_KEYS = ("apiKeyId", "secretArn", "secretName", "keyExpiresAt", "apiKey")
CLEARED_ALL = {**bootstrap.CLEARED_KEY_ATTRIBUTES, **bootstrap.CLEARED_SECRET_ATTRIBUTES}
INVOKE_ERROR = ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "no function"}}, "Invoke")
DUPLICATE = ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem")


def _launch(monkeypatch, *, outcome="provisioned", credential=CREDENTIAL, claims=None,
            invoke_error=None, revoke_error=None, existing=None, rest=False):
    """Drive one launch with every collaborator recording its turn on ``order``."""
    order = []
    revoked_rows = []
    fakes = install_service_fakes(monkeypatch, claims=claims)
    monkeypatch.setattr(service, "allowed_instance_types", ())
    monkeypatch.setattr(bootstrap, "session_bootstrap_enabled", outcome != "disabled")

    def _put(**kwargs):
        order.append("put")
        if existing is not None:
            raise DUPLICATE
        return {}

    def _invoke(**kwargs):
        order.append("invoke")
        if invoke_error is not None:
            raise invoke_error
        return {"StatusCode": 202}

    def _session(event, row, mfa_enabled, *, deadline=None):
        order.append("bootstrap")
        return outcome, dict(credential)

    def _revoke(event, row, *, delete_secret, actor, every_key):
        order.append("revoke")
        # The handler clears the row in place once the revoke returns, and a MagicMock records the
        # reference it was given; keep the row as it was handed over.
        revoked_rows.append(dict(row))
        if revoke_error is not None:
            raise revoke_error
        return dict(CLEARED_ALL)

    fakes.revoked_rows = revoked_rows
    fakes.table.put_item.side_effect = _put
    if existing is not None:
        fakes.table.get_item.return_value = {"Item": existing}
    fakes.lambda_client.invoke.side_effect = _invoke
    session_fn = MagicMock(side_effect=_session)
    revoke_fn = MagicMock(side_effect=_revoke)
    monkeypatch.setattr(bootstrap, "bootstrap_session", session_fn)
    monkeypatch.setattr(bootstrap, "revoke_session", revoke_fn)
    response = service.lambda_handler(make_event("POST", WORKSTATIONS_PATH, body=BODY, rest=rest), MagicMock())
    return response, fakes, session_fn, revoke_fn, order


def _updates_naming(fakes, attribute):
    return [c.kwargs for c in fakes.table.update_item.call_args_list
            if attribute in c.kwargs["ExpressionAttributeNames"].values()]


def _worker_payload(fakes):
    return json.loads(fakes.lambda_client.invoke.call_args.kwargs["Payload"])


@pytest.mark.unit
class TestBootstrapBetweenTheRowAndTheHandOver:
    def test_the_row_is_written_then_the_credential_minted_then_the_worker_invoked(self, monkeypatch):
        response, _fakes, session_fn, _revoke_fn, order = _launch(monkeypatch)
        assert response["statusCode"] == 202
        assert order == ["put", "bootstrap", "invoke"]
        event_arg, row_arg, mfa_arg = session_fn.call_args.args
        assert event_arg["requestContext"]["http"]["path"] == WORKSTATIONS_PATH
        assert row_arg["bootstrapId"] == BOOTSTRAP_OWN
        assert row_arg["userId"] == USER
        assert row_arg["state"] == service.STATE_CREATING
        assert mfa_arg is False

    def test_the_credential_is_written_to_the_row_and_only_the_outcome_is_returned(self, monkeypatch):
        response, fakes, _session_fn, _revoke_fn, _order = _launch(monkeypatch)
        writes = _updates_naming(fakes, "apiKeyId")
        assert len(writes) == 1
        written = dict(zip(writes[0]["ExpressionAttributeNames"].values(),
                           writes[0]["ExpressionAttributeValues"].values()))
        assert {k: written[k] for k in CREDENTIAL} == CREDENTIAL
        assert writes[0]["Key"] == {"bootstrapId": BOOTSTRAP_OWN}
        body = response_body(response)
        assert body[service.RESPONSE_FIELD_SESSION_BOOTSTRAP] == "provisioned"
        assert body["state"] == service.STATE_CREATING
        assert body["sessionId"] is None
        for key in CREDENTIAL_KEYS:
            assert key not in body

    def test_the_worker_payload_carries_no_credential(self, monkeypatch):
        _response, fakes, _session_fn, _revoke_fn, _order = _launch(monkeypatch)
        payload = _worker_payload(fakes)
        assert set(payload) == set(service.WORKER_PAYLOAD_KEYS)
        assert not set(payload) & set(CREDENTIAL_KEYS)
        assert fakes.lambda_client.invoke.call_args.kwargs["InvocationType"] == "Event"

    def test_a_skipped_bootstrap_still_launches_and_reports_skipped(self, monkeypatch):
        response, fakes, _session_fn, _revoke_fn, order = _launch(monkeypatch, outcome="skipped", credential={})
        assert response["statusCode"] == 202
        assert order == ["put", "bootstrap", "invoke"]
        assert response_body(response)[service.RESPONSE_FIELD_SESSION_BOOTSTRAP] == "skipped"
        assert _updates_naming(fakes, "apiKeyId") == []

    def test_an_unavailable_bootstrap_still_launches_and_reports_unavailable(self, monkeypatch):
        response, fakes, _session_fn, _revoke_fn, order = _launch(monkeypatch, outcome="unavailable", credential={})
        assert response["statusCode"] == 202
        assert "invoke" in order
        assert response_body(response)[service.RESPONSE_FIELD_SESSION_BOOTSTRAP] == "unavailable"
        assert _updates_naming(fakes, "apiKeyId") == []

    def test_a_disabled_bootstrap_reports_disabled(self, monkeypatch):
        response, _fakes, _session_fn, _revoke_fn, order = _launch(monkeypatch, outcome="disabled", credential={})
        assert response["statusCode"] == 202
        assert "invoke" in order
        assert response_body(response)[service.RESPONSE_FIELD_SESSION_BOOTSTRAP] == "disabled"


@pytest.mark.unit
class TestTheHandOverDecidesTheCredentialsFate:
    def test_a_failed_hand_over_takes_the_credential_back_and_releases_the_row(self, monkeypatch):
        response, fakes, _session_fn, revoke_fn, order = _launch(monkeypatch, invoke_error=INVOKE_ERROR)
        assert response["statusCode"] == 500
        assert order == ["put", "bootstrap", "invoke", "revoke"]
        assert fakes.revoked_rows[-1]["apiKeyId"] == "key-1"
        assert revoke_fn.call_args.kwargs == {"delete_secret": True, "actor": USER, "every_key": True}
        assert fakes.table.delete_item.call_args.kwargs == {"Key": {"bootstrapId": BOOTSTRAP_OWN}}

    @pytest.mark.parametrize("error", [bootstrap.SessionBootstrapError("down"), RuntimeError("boom")],
                             ids=["bootstrap", "unexpected"])
    def test_a_failed_hand_over_whose_revoke_fails_still_releases_the_row(self, monkeypatch, error):
        response, fakes, _session_fn, _revoke_fn, order = _launch(
            monkeypatch, invoke_error=INVOKE_ERROR, revoke_error=error)
        assert response["statusCode"] == 500
        assert order == ["put", "bootstrap", "invoke", "revoke"]
        assert fakes.table.delete_item.call_args.kwargs == {"Key": {"bootstrapId": BOOTSTRAP_OWN}}

    def test_a_failed_hand_over_without_a_credential_revokes_nothing(self, monkeypatch):
        response, fakes, _session_fn, revoke_fn, order = _launch(
            monkeypatch, outcome="skipped", credential={}, invoke_error=INVOKE_ERROR)
        assert response["statusCode"] == 500
        revoke_fn.assert_not_called()
        assert order == ["put", "bootstrap", "invoke"]
        assert fakes.table.delete_item.call_args.kwargs == {"Key": {"bootstrapId": BOOTSTRAP_OWN}}

    def test_a_repeated_bootstrap_id_mints_nothing_and_carries_no_outcome(self, monkeypatch):
        response, fakes, session_fn, revoke_fn, order = _launch(
            monkeypatch, existing=own_row(state="CREATING", edhState=None, connectionAvailable=False))
        assert response["statusCode"] == 200
        assert order == ["put"]
        session_fn.assert_not_called()
        revoke_fn.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()
        assert service.RESPONSE_FIELD_SESSION_BOOTSTRAP not in response_body(response)

    def test_an_api_key_principal_is_refused_before_any_mint(self, monkeypatch):
        response, fakes, session_fn, _revoke_fn, order = _launch(monkeypatch, claims=API_KEY_CLAIMS)
        assert response["statusCode"] == 403
        assert order == []
        session_fn.assert_not_called()
        fakes.table.put_item.assert_not_called()

    def test_a_rest_shaped_launch_with_null_path_parameters_bootstraps(self, monkeypatch):
        response, _fakes, session_fn, _revoke_fn, order = _launch(monkeypatch, rest=True)
        assert response["statusCode"] == 202
        assert order == ["put", "bootstrap", "invoke"]
        assert session_fn.call_args is not None


@pytest.mark.unit
class TestTheMintDeadline:
    """The mint may spend what the Lambda has left beyond the launch hand-over and the response."""

    def _launch_with(self, monkeypatch, context):
        fakes = install_service_fakes(monkeypatch)
        monkeypatch.setattr(service, "allowed_instance_types", ())
        monkeypatch.setattr(bootstrap, "session_bootstrap_enabled", True)
        session_fn = MagicMock(return_value=("skipped", {}))
        monkeypatch.setattr(bootstrap, "bootstrap_session", session_fn)
        monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: 1000.0))
        response = service.lambda_handler(make_event("POST", WORKSTATIONS_PATH, body=BODY), context)
        assert response["statusCode"] == 202
        return session_fn, fakes

    def test_a_launch_hands_the_mint_the_lambdas_deadline_less_the_create_reserve(self, monkeypatch):
        session_fn, _fakes = self._launch_with(monkeypatch, SimpleNamespace(get_remaining_time_in_millis=lambda: 20000))
        assert session_fn.call_args.kwargs == {"deadline": 1000.0 + 20.0 - service.MINT_RESERVE_SECONDS_CREATE}

    def test_a_runtime_that_reports_no_remaining_time_hands_the_mint_its_own_budget(self, monkeypatch):
        session_fn, _fakes = self._launch_with(monkeypatch, {})
        assert session_fn.call_args.kwargs == {"deadline": None}

    def test_a_runtime_whose_remaining_time_is_not_a_number_hands_the_mint_its_own_budget(self, monkeypatch):
        session_fn, _fakes = self._launch_with(monkeypatch, MagicMock())
        assert session_fn.call_args.kwargs == {"deadline": None}
