# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The EDH credential keys are redacted by safeLogger's key-driven walk and by the log-text redactor.

They are declared once, in the add-on constants lane (``ADDON_LOG_SENSITIVE_KEYS`` in
``common/addonConstants.py``), which ``common/constants.py`` merges into both redactors' key lists.

The EDH root key travels as the ``X-EDH-TOKEN`` header and as the ``rootKey`` field of the secret; a
desktop row carries its DCV ``session_token``, ``authentication_token`` (returned a second time under
``broker_session_id``) and ``session_local_admin_password``; a ``connection_string`` embeds an
``authToken``; the directory user the client creates is sent with a throw-away ``password``. Redaction
matches the WHOLE lowercased key, so the header is covered in either case and a JSON-string ``body`` is
parsed before the walk. The uid, the user header and the ids stay readable --
the control pins that the walk redacts by key, not by proximity. The real modules are imported by their
package paths: tests/conftest.py installs a pass-through mock as ``customLogging.logger``.
"""

import json

import pytest

from backend.backend.common import addonConstants, logRedaction
from backend.backend.common.constants import CORE_LOG_SENSITIVE_KEYS, CORE_LOG_TEXT_SENSITIVE_KEYS
from backend.backend.customLogging.logger import (
    CONTENT_KEYS,
    REDACTED,
    SENSITIVE_KEYS,
    mask_sensitive_data,
)

EDH_CREDENTIAL_KEYS = ("X-EDH-TOKEN", "rootKey", "connection_string", "authToken",
                       "session_token", "authentication_token", "session_local_admin_password",
                       "broker_session_id", "password")
ROOT_KEY_VALUE = "edh-root-key-s3cr3t-value"
DCV_TOKEN_VALUE = "dcv-auth-t0ken-value"
SESSION_TOKEN_VALUE = "dcv-session-t0ken-value"
BROKER_SESSION_VALUE = "dcv-br0ker-sess1on-value"
LOCAL_ADMIN_PASSWORD = "l0cal-adm1n-passw0rd"
DIRECTORY_PASSWORD = "d1rect0ry-passw0rd-value"


@pytest.mark.unit
class TestEdhCredentialKeysAreRedacted:
    @pytest.mark.parametrize("key", EDH_CREDENTIAL_KEYS)
    def test_each_key_is_registered(self, key):
        assert key in SENSITIVE_KEYS

    def test_the_outbound_header_is_redacted_in_either_case(self):
        out = mask_sensitive_data(
            {"headers": {"X-EDH-TOKEN": ROOT_KEY_VALUE, "x-edh-token": ROOT_KEY_VALUE, "Accept": "application/json"}})
        assert out["headers"] == {"X-EDH-TOKEN": REDACTED, "x-edh-token": REDACTED, "Accept": "application/json"}
        assert ROOT_KEY_VALUE not in json.dumps(out)

    def test_the_secret_payload_field_is_redacted(self):
        out = mask_sensitive_data({"secret": {"rootKey": ROOT_KEY_VALUE, "description": "EDH"}})
        assert out["secret"] == {"rootKey": REDACTED, "description": "EDH"}

    def test_a_connection_string_and_its_auth_token_are_redacted_inside_a_list(self):
        session = {
            "session_uuid": "abc",
            "connection_string": f"https://dcv.example.internal/?authToken={DCV_TOKEN_VALUE}#abc",
            "authToken": DCV_TOKEN_VALUE,
        }
        out = mask_sensitive_data({"message": [session]})
        assert out["message"][0]["connection_string"] == REDACTED
        assert out["message"][0]["authToken"] == REDACTED
        assert out["message"][0]["session_uuid"] == "abc"
        assert DCV_TOKEN_VALUE not in json.dumps(out)

    def test_a_desktop_row_keeps_its_ids_and_loses_its_tokens(self):
        # The shape EDH's list routes return per session, keyed by uuid.
        row = {
            "session_uuid": "abc", "session_owner": "vfpmansl7byak6gq", "session_state": "running",
            "session_token": SESSION_TOKEN_VALUE, "authentication_token": DCV_TOKEN_VALUE,
            "session_local_admin_password": LOCAL_ADMIN_PASSWORD, "broker_session_id": BROKER_SESSION_VALUE,
        }
        token_keys = ("session_token", "authentication_token", "session_local_admin_password", "broker_session_id")
        out = mask_sensitive_data({"message": {"abc": row}})
        masked = out["message"]["abc"]
        assert {key: masked[key] for key in token_keys} == {key: REDACTED for key in token_keys}
        assert {key: masked[key] for key in ("session_uuid", "session_owner", "session_state")} == {
            "session_uuid": "abc", "session_owner": "vfpmansl7byak6gq", "session_state": "running"}
        serialized = json.dumps(out)
        assert SESSION_TOKEN_VALUE not in serialized and LOCAL_ADMIN_PASSWORD not in serialized
        assert BROKER_SESSION_VALUE not in serialized

    def test_the_directory_create_form_keeps_its_user_and_loses_its_password(self):
        # The form the client posts to create a directory user, as a diagnostic might attach it.
        form = {"user": "vfpmansl7byak6gq", "password": DIRECTORY_PASSWORD, "sudoers": "0", "shell": "/bin/bash"}
        out = mask_sensitive_data({"form": form})
        assert out["form"] == {"user": "vfpmansl7byak6gq", "password": REDACTED, "sudoers": "0", "shell": "/bin/bash"}
        assert DIRECTORY_PASSWORD not in json.dumps(out)

    def test_a_json_string_body_carrying_a_connection_string_is_redacted(self):
        body = json.dumps(
            {"sessionId": "abc", "connection_string": f"https://dcv.example.internal/?authToken={DCV_TOKEN_VALUE}"})
        out = mask_sensitive_data({"body": body})
        assert json.loads(out["body"]) == {"sessionId": "abc", "connection_string": REDACTED}

    def test_a_non_json_body_naming_a_credential_key_is_redacted_whole(self):
        # A body that is not JSON cannot be walked by key; one that names a credential key is dropped whole.
        out = mask_sensitive_data({"body": f"session_uuid=abc&authToken={DCV_TOKEN_VALUE}"})
        assert out["body"] == REDACTED
        # Control: a non-JSON body naming none of the keys is untouched.
        assert mask_sensitive_data({"body": "session_uuid=abc&is_active=true"}) == {
            "body": "session_uuid=abc&is_active=true"}

    def test_edh_identity_and_session_keys_survive(self):
        # Control: redaction is by key, so the uid, the user header, ids and statuses stay readable.
        event = {
            "headers": {"X-EDH-USER": "vfpmansl7byak6gq"},
            "edhUid": "vfpmansl7byak6gq",
            "sessionId": "abc",
            "edhRoute": "/api/dcv/virtual_desktops/list",
            "edhStatus": 200,
        }
        assert mask_sensitive_data(event) == event

    def test_the_new_keys_do_not_collide_with_content_keys(self):
        # Control: CONTENT_KEYS is the real, non-empty set (a content key is masked as content, not
        # as a credential), so the disjointness below is not true by emptiness.
        assert "configBody" in CONTENT_KEYS
        assert not set(EDH_CREDENTIAL_KEYS) & set(CONTENT_KEYS)


@pytest.mark.unit
class TestEdhCredentialKeysInReturnedLogText:
    """The free-text redactor for log lines returned to callers carries the same nine names."""

    @pytest.mark.parametrize("key", EDH_CREDENTIAL_KEYS)
    def test_each_key_is_registered_for_log_text(self, key):
        assert key in logRedaction.SENSITIVE_KEYS

    def test_a_json_fragment_with_the_header_is_redacted(self):
        text = '{"X-EDH-TOKEN": "%s", "edhRoute": "/api/dcv/virtual_desktops/list"}' % ROOT_KEY_VALUE
        out = logRedaction.redact_log_text(text)
        assert ROOT_KEY_VALUE not in out
        assert "/api/dcv/virtual_desktops/list" in out

    def test_a_form_pair_with_the_directory_password_is_redacted_and_a_prefixed_key_is_not(self):
        out = logRedaction.redact_log_text(f"user=vfpmansl7byak6gq password={DIRECTORY_PASSWORD} password_policy=strict")
        assert DIRECTORY_PASSWORD not in out
        assert "user=vfpmansl7byak6gq" in out
        # The bare key rule is word-bounded: a key that merely starts with the name is not a credential.
        assert "password_policy=strict" in out

    def test_a_key_value_pair_with_a_session_token_is_redacted(self):
        out = logRedaction.redact_log_text(f"session_token={SESSION_TOKEN_VALUE} owner=vfpmansl7byak6gq")
        assert SESSION_TOKEN_VALUE not in out
        assert "owner=vfpmansl7byak6gq" in out

    def test_the_two_registries_agree_on_the_edh_keys(self):
        assert set(EDH_CREDENTIAL_KEYS) <= set(SENSITIVE_KEYS) & set(logRedaction.SENSITIVE_KEYS)


@pytest.mark.unit
class TestEdhCredentialKeysComeFromTheAddOnLane:
    def test_the_add_on_lane_declares_exactly_the_edh_keys(self):
        assert tuple(addonConstants.ADDON_LOG_SENSITIVE_KEYS) == EDH_CREDENTIAL_KEYS

    def test_no_edh_key_is_a_core_key(self):
        # The keys reach the redactors through the lane, not through the core lists.
        folded = {key.casefold() for key in EDH_CREDENTIAL_KEYS}
        assert not folded & {key.casefold() for key in CORE_LOG_SENSITIVE_KEYS + CORE_LOG_TEXT_SENSITIVE_KEYS}
        # Control: the core lists are the real ones.
        assert "authorization" in CORE_LOG_SENSITIVE_KEYS and "TaskToken" in CORE_LOG_TEXT_SENSITIVE_KEYS

    def test_both_redactors_carry_the_lane_after_their_core_keys(self):
        assert SENSITIVE_KEYS[: len(CORE_LOG_SENSITIVE_KEYS)] == CORE_LOG_SENSITIVE_KEYS
        assert tuple(logRedaction.SENSITIVE_KEYS[: len(CORE_LOG_TEXT_SENSITIVE_KEYS)]) == CORE_LOG_TEXT_SENSITIVE_KEYS
        assert set(EDH_CREDENTIAL_KEYS) <= set(SENSITIVE_KEYS[len(CORE_LOG_SENSITIVE_KEYS):])
        assert set(EDH_CREDENTIAL_KEYS) <= set(logRedaction.SENSITIVE_KEYS[len(CORE_LOG_TEXT_SENSITIVE_KEYS):])
