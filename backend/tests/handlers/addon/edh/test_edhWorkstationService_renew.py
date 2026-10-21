# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Renew workstation lease route tests.

The renew route resets a workstation's lease expiry to a new future instant computed from the policy's
leaseHours, clears any leaseStoppedAt timestamp, and clears a LEASE_EXPIRED code while leaving any other
code alone. Authorization is two-tier: ownership check FIRST, then the 409 refusals (disabled, no lease,
deleted state).

Once the lease is written, a renew of a desktop stored ACTIVE with no start under way re-issues its
session key: the row is claimed, a fresh key is minted, the row is settled naming it, and only then is
the key written into the session secret and the claim released. A desktop that may still be running
keeps every key it was given until the key's own expiry, so the secret never holds a key VAMS deletes
while the desktop runs: a key the row could not be settled with is deleted before any secret sees it, a
failed secret write deletes nothing, and a stop, delete or start that overtakes the re-issue leaves every
key it minted deleted or named by a live row and the secret holding a key of the desktop or the revoked
marker. A failure inside the re-issue never changes the renew's own result, every write it confirms is
re-read consistently, and no log line carries a key value. An API-key caller renews the lease only, and
the response names the re-issue's outcome whenever one was attempted. A sweep whose revoke of a stopped
desktop's key fails, or whose clear a renew overtakes, gives its claim up, so a start moments later is not
held off.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, create_autospec

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.addon.edh import edhSessionReconciler as rec
from backend.backend.handlers.addon.edh.common.edhClient import EdhClient
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    CREDENTIAL_CLAIM_SECONDS, EDH_FIELD_INSTANCE_ID, EDH_SCHEDULE_FIELD, EDH_SESSION_ID_FIELD,
    EDH_SESSION_STATE_FIELD, LAST_ERROR_LAUNCH_ERROR, LAST_ERROR_LEASE_EXPIRED, credential_claim_stamp,
    never_auto_start_schedule, session_row_live,
)
from backend.tests.handlers.addon.edh import edhAdminTestSupport as admin_support
from backend.tests.handlers.addon.edh.edhCredentialTestSupport import FakeApiKeyService, FakeSecretsManager
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import ConditionalRowTable
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    API_KEY_CLAIMS,
    BOOTSTRAP_OWN,
    SESSION_OWN,
    SESSION_OTHER,
    USER,
    USER_CLAIMS,
    install_service_fakes,
    make_event,
    other_row,
    own_row,
    response_body,
    workstation_path,
)
import backend.backend.handlers.addon.edh.edhWorkstationService as service

KEY_STAMP = "%Y-%m-%dT%H:%M:%SZ"
SECRET_NAME = f"vams/edh-session/{BOOTSTRAP_OWN}"
SECRET_ARN = f"arn:aws:secretsmanager:us-east-1:123456789012:secret:{SECRET_NAME}-AbCdEf"
INSTANCE_ID = "i-0123456789abcdef0"
NEW_KEY_VALUE = "vams_" + "n" * 64
MINTED = {"apiKeyId": "key-new", "apiKey": NEW_KEY_VALUE, "expiresAt": "2099-01-01T08:00:00Z"}

OLD_KEY_VALUE = "vams_" + "o" * 64

FAILURE_IDS = ["none", "mint refused", "stopped under the claim", "deleted under the claim", "secret write fails",
               "claim held"]

STOPPED_IN_EDH = [{EDH_SESSION_ID_FIELD: SESSION_OWN, EDH_SESSION_STATE_FIELD: "stopped",
                   EDH_FIELD_INSTANCE_ID: INSTANCE_ID}]
WRITERS = ["stop", "delete", "admin stop", "admin deactivate", "start"]


@pytest.fixture
def fakes(monkeypatch):
    return install_service_fakes(monkeypatch)


def _renew_event(session_id, rest=False):
    return make_event("POST", workstation_path(session_id, "renew"),
                      path_params={"sessionId": session_id}, rest=rest)


def _seed_row(fakes, row):
    fakes.table.query.return_value = {"Items": [row]}


def _update_names(fakes):
    """The attribute names from the last update_item call."""
    names_map = fakes.table.update_item.call_args[1]["ExpressionAttributeNames"]
    return {names_map[k]: k for k in names_map}  # reverse: attr_name -> #a0 key


def _update_attr(fakes, attr_name):
    """The value written for attr_name in the last update_item call."""
    names_map = fakes.table.update_item.call_args[1]["ExpressionAttributeNames"]
    values_map = fakes.table.update_item.call_args[1]["ExpressionAttributeValues"]
    # Find which #aN key corresponds to attr_name
    for name_key, name_val in names_map.items():
        if name_val == attr_name:
            # The value key is the same index: #a0 -> :v0
            value_key = name_key.replace("#a", ":v")
            return values_map.get(value_key)
    return None


def _set_policy(fakes, **kwargs):
    """Patch the policy reader to return the given values."""
    defaults = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24,
                "updatedAt": None, "updatedBy": None}
    defaults.update(kwargs)
    # The fakes installer already patched read_lease_policy as a lambda; replace it
    setattr(fakes.module, "read_lease_policy", lambda: defaults)


@pytest.mark.unit
class TestRenewWorkstationSuccess:
    """Successful renew cases: lease is extended, leaseStoppedAt is cleared, the expiry code is cleared."""

    def test_renew_resets_lease_and_clears_stopped_on_stopped_row(self, fakes):
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = own_row(state="STOPPED", leaseExpiresAt="2000-01-01T00:00:00+00:00",
                      leaseStoppedAt="2000-01-01T00:00:00+00:00")
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 200
        body = response_body(resp)
        assert body["sessionId"] == SESSION_OWN
        # The update cleared leaseStoppedAt and set a future leaseExpiresAt
        assert _update_attr(fakes, "leaseExpiresAt") > "2026-09"  # in the future
        assert _update_attr(fakes, "leaseStoppedAt") is None

    def test_renew_extends_lease_on_active_row(self, fakes):
        _set_policy(fakes, enabled=True, leaseHours=12)
        old_expiry = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        row = own_row(state="ACTIVE", leaseExpiresAt=old_expiry)
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 200
        # New expiry is later than old expiry
        assert _update_attr(fakes, "leaseExpiresAt") > old_expiry

    def test_renew_uses_policy_lease_hours_not_request_body(self, fakes):
        """Renew takes no lease-hours input; it uses the policy's value."""
        _set_policy(fakes, enabled=True, leaseHours=6, graceHours=24)
        row = own_row(state="ACTIVE", leaseExpiresAt="2026-09-01T00:00:00+00:00")
        _seed_row(fakes, row)

        before = datetime.now(timezone.utc)
        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})
        after = datetime.now(timezone.utc)

        assert resp["statusCode"] == 200
        # The expiry is leaseHours from the instant of the call, not graceHours and not the old expiry.
        written = datetime.fromisoformat(_update_attr(fakes, "leaseExpiresAt"))
        assert before + timedelta(hours=6) <= written <= after + timedelta(hours=6)

    def test_renew_clears_the_expiry_code(self, fakes):
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = own_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                      leaseExpiresAt="2000-01-01T00:00:00+00:00", leaseStoppedAt="2000-01-01T00:00:00+00:00",
                      lastError=LAST_ERROR_LEASE_EXPIRED)
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 200
        assert response_body(resp)["lastError"] is None
        assert "lastError" in _update_names(fakes)
        assert _update_attr(fakes, "lastError") is None

    def test_renew_leaves_other_error_codes_alone(self, fakes):
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = own_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                      leaseExpiresAt="2000-01-01T00:00:00+00:00", lastError=LAST_ERROR_LAUNCH_ERROR)
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 200
        assert response_body(resp)["lastError"] == LAST_ERROR_LAUNCH_ERROR
        assert "lastError" not in _update_names(fakes)

    def test_renew_writes_the_lease_onto_the_row_it_read(self, fakes):
        # Control for the refusal of a row deleted since the read: the same write lands on a live row.
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = own_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                      leaseExpiresAt="2000-01-01T00:00:00+00:00", leaseStoppedAt="2000-01-01T00:00:00+00:00")
        _seed_row(fakes, row)
        stored = ConditionalRowTable(**row)
        fakes.table.update_item.side_effect = stored

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 200
        assert stored.item["leaseExpiresAt"] > "2026-09"
        assert stored.item["leaseStoppedAt"] is None

    def test_renew_is_audited_as_edhWorkstationRenew(self, fakes):
        """The renew action carries the session, the new expiry and the policy hours into the audit log."""
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = own_row(state="ACTIVE", leaseExpiresAt="2026-09-01T00:00:00+00:00")
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 200
        assert [c.args[1] for c in fakes.log_actions.call_args_list] == ["edhWorkstationRenew"]
        detail = fakes.log_actions.call_args_list[0].args[2]
        assert detail["sessionId"] == SESSION_OWN
        assert detail["bootstrapId"] == row["bootstrapId"]
        assert detail["leaseHours"] == 8
        assert detail["leaseExpiresAt"] == _update_attr(fakes, "leaseExpiresAt")


@pytest.mark.unit
class TestRenewWorkstation409Refusals:
    """The three 409 cases: leases disabled, no lease on the row, deleted state."""

    def test_renew_409_when_leases_disabled(self, fakes):
        _set_policy(fakes, enabled=False)
        row = own_row(state="ACTIVE", leaseExpiresAt="2100-01-01T00:00:00+00:00")
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 409
        assert "disabled" in response_body(resp)["message"].lower()
        fakes.table.update_item.assert_not_called()
        assert "edhWorkstationRenew" not in [c.args[1] for c in fakes.log_actions.call_args_list]

    def test_renew_409_when_no_lease_on_row(self, fakes):
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = own_row(state="ACTIVE")  # no leaseExpiresAt
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 409
        assert "no lease" in response_body(resp)["message"].lower()
        fakes.table.update_item.assert_not_called()

    @pytest.mark.parametrize("stored_for", [lambda row: ConditionalRowTable(**{**row, "state": "DELETED"}),
                                            lambda row: ConditionalRowTable.missing()],
                             ids=["deleted since the read", "gone since the read"])
    def test_renew_409_when_the_row_is_deleted_after_it_was_read(self, fakes, stored_for):
        # A lease delete claims the row DELETED between the renew's read and its write: the renew neither
        # writes a lease over the deletion nor recreates a row that is gone, and answers as for a row read
        # deleted.
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = own_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                      leaseExpiresAt="2000-01-01T00:00:00+00:00", leaseStoppedAt="2000-01-01T00:00:00+00:00")
        _seed_row(fakes, row)
        stored = stored_for(row)
        before = None if stored.item is None else dict(stored.item)
        fakes.table.update_item.side_effect = stored

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 409
        assert "deleted" in response_body(resp)["message"].lower()
        assert stored.item == before
        assert "edhWorkstationRenew" not in [c.args[1] for c in fakes.log_actions.call_args_list]

    def test_renew_409_when_deleted(self, fakes):
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = own_row(state="DELETED", leaseExpiresAt="2100-01-01T00:00:00+00:00")
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert resp["statusCode"] == 409
        assert "deleted" in response_body(resp)["message"].lower()
        fakes.table.update_item.assert_not_called()


@pytest.mark.unit
class TestRenewWorkstationAuthorizationGuards:
    """Authorization guards: ownership check FIRST, then the policy checks."""

    def test_renew_404_and_no_write_when_row_belongs_to_another_user(self, fakes):
        """The ownership guard: a row belonging to another user is answered 404, no update."""
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = other_row(state="ACTIVE", leaseExpiresAt="2026-09-20T00:00:00+00:00")
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OTHER), {})

        assert resp["statusCode"] == 404
        # The refusal must read exactly like a missing row, naming neither the owner nor the session.
        assert response_body(resp)["message"] == "Workstation not found"
        fakes.table.update_item.assert_not_called()

    def test_renew_403_and_no_write_when_object_tier_denies(self, monkeypatch):
        """The object tier is enforced on the row: a denial answers 403 and writes nothing."""
        fakes = install_service_fakes(monkeypatch, enforce=lambda obj, act: False)
        _set_policy(fakes, enabled=True, leaseHours=8)
        _seed_row(fakes, own_row(state="ACTIVE", leaseExpiresAt="2100-01-01T00:00:00+00:00"))

        response = fakes.module.lambda_handler(_renew_event(SESSION_OWN), {})

        assert response["statusCode"] == 403
        fakes.table.update_item.assert_not_called()
        fakes.enforcer.enforce.assert_called_once()
        assert fakes.enforcer.enforce.call_args.args[0]["object__type"] == "edhWorkstation"
        assert fakes.enforcer.enforce.call_args.args[1] == "POST"

    def test_renew_404_when_no_row_matches(self, fakes):
        _set_policy(fakes, enabled=True, leaseHours=8)
        fakes.table.query.return_value = {"Items": []}

        resp = fakes.module.lambda_handler(_renew_event("no-such-session"), {})

        assert resp["statusCode"] == 404
        fakes.table.update_item.assert_not_called()

    def test_renew_on_rest_shaped_event(self, fakes):
        """Cover the REST API (v1) event shape."""
        _set_policy(fakes, enabled=True, leaseHours=8)
        row = own_row(state="ACTIVE", leaseExpiresAt="2026-09-01T00:00:00+00:00")
        _seed_row(fakes, row)

        resp = fakes.module.lambda_handler(_renew_event(SESSION_OWN, rest=True), {})

        assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# Session key re-issue
# ---------------------------------------------------------------------------
def _in(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(KEY_STAMP)


def _keyed_row(**overrides):
    """A running, leased desktop whose session key is live and whose secret is bound to its instance."""
    row = own_row(state="ACTIVE", leaseExpiresAt=_in(2), apiKeyId="key-old", keyExpiresAt=_in(3),
                  secretName=SECRET_NAME, secretArn=SECRET_ARN, instanceId=INSTANCE_ID,
                  secretBoundInstanceId=INSTANCE_ID,
                  updatedAt=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    row.update(overrides)
    return row


def _write_kind(kwargs):
    """What one row write is: the renew's claim, its settle, its release, the lease, or another write."""
    names = kwargs["ExpressionAttributeNames"]
    sets, _, removes = kwargs["UpdateExpression"].partition(" REMOVE ")
    set_names = {names[term.split(" = ")[0]] for term in sets.removeprefix("SET ").split(", ")}
    removed = {names.get(term, term) for term in filter(None, removes.split(", "))}
    if "credentialClaimedAt" in set_names:
        return "claim"
    if "apiKeyId" in set_names:
        return "settle"
    if "credentialClaimedAt" in removed:
        return "release"
    if "leaseExpiresAt" in set_names:
        return "lease"
    return "row"


def _table_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "UpdateItem")


def _lagging(stored, first):
    """The faked table's get_item: a consistent read is answered from ``stored``, and any other with
    ``first``, the row as it was stored before any write, as a read that is not consistent may be."""

    def get_item(**kwargs):
        if kwargs.get("ConsistentRead"):
            return stored.read(**kwargs)
        return {"Item": {**kwargs["Key"], **first}}

    return get_item


def _log_lines(recorder):
    return [repr(call) for call in recorder.mock_calls]


class _Reissue:
    """The bootstrap module's re-issue steps replaced, and the session row stored as DynamoDB would store
    it. ``order`` is every step and row write in turn. A write of a kind in ``landed`` is applied and then
    answered ConditionalCheckFailedException, as the SDK's resend of a write whose first answer was lost is
    refused by its own condition; one of a kind in ``failing`` raises a throttling error and writes
    nothing. A read that does not ask for ConsistentRead is answered with the row as it was first stored,
    as one that is not consistent may be."""

    def __init__(self, monkeypatch, row, *, claims=None, mint=("provisioned", MINTED), write=None,
                 write_error=None, delete_error=None, bind_error=None, neutralise_error=None, during_mint=None,
                 during_write=None, context=None, enabled=True, read=None, landed=(), failing=()):
        self.fakes = install_service_fakes(monkeypatch, claims=claims)
        _set_policy(self.fakes, enabled=True, leaseHours=8)
        _seed_row(self.fakes, row)
        self.stored = ConditionalRowTable(**row)
        self.read = _lagging(self.stored, dict(row))
        self.order = []
        self.mint_calls = []
        self.written = []
        self.deleted = []
        self.bound = []
        self.neutralised = []
        self.mint, self.write, self.write_error = mint, write, write_error
        self.delete_error, self.bind_error, self.neutralise_error = delete_error, bind_error, neutralise_error
        self.during_mint, self.during_write = during_mint, during_write

        def update_item(**kwargs):
            kind = _write_kind(kwargs)
            self.order.append(kind)
            if kind in failing:
                raise _table_error("ProvisionedThroughputExceededException")
            answer = self.stored(**kwargs)
            if kind in landed:
                raise _table_error("ConditionalCheckFailedException")
            return answer

        self.fakes.table.update_item.side_effect = update_item
        self.fakes.table.get_item.side_effect = read if read is not None else self.read
        monkeypatch.setattr(bootstrap, "session_bootstrap_enabled", enabled)
        monkeypatch.setattr(bootstrap, "mint_reissue_key", self._mint)
        monkeypatch.setattr(bootstrap, "write_reissued_secret", self._write)
        monkeypatch.setattr(bootstrap, "delete_session_key", self._delete)
        monkeypatch.setattr(bootstrap, "bind_session_secret", self._bind)
        monkeypatch.setattr(bootstrap, "neutralise_session_secret", self._neutralise)
        self.context = context if context is not None else MagicMock()

    def _mint(self, event, row, mfa_enabled, *, deadline=None):
        self.order.append("mint")
        self.mint_calls.append({"row": dict(row), "mfaEnabled": mfa_enabled, "deadline": deadline})
        if self.during_mint is not None:
            self.during_mint(self)
        outcome, minted = self.mint
        return outcome, (dict(minted) if minted else None)

    def _write(self, row, minted):
        self.order.append("write")
        self.written.append(minted["apiKeyId"])
        if self.during_write is not None:
            self.during_write(self)
        if self.write_error is not None:
            raise self.write_error
        return dict(self.write if self.write is not None else {"secretArn": SECRET_ARN, "secretName": SECRET_NAME})

    def _delete(self, event, api_key_id, *, bootstrap_id, user_id, actor, operation=bootstrap.AUDIT_OPERATION_REVOKE):
        self.order.append("delete")
        self.deleted.append({"apiKeyId": api_key_id, "bootstrapId": bootstrap_id, "userId": user_id, "actor": actor})
        if self.delete_error is not None:
            raise self.delete_error

    def _bind(self, row, instance_id):
        self.order.append("bind")
        self.bound.append(instance_id)
        if self.bind_error is not None:
            raise self.bind_error
        return {"secretBoundInstanceId": instance_id}

    def _neutralise(self, bootstrap_id, *, delete_secret):
        self.order.append("neutralise")
        self.neutralised.append((bootstrap_id, delete_secret))
        if self.neutralise_error is not None:
            raise self.neutralise_error

    def renew(self):
        return self.fakes.module.lambda_handler(_renew_event(SESSION_OWN), self.context)

    def renew_audit(self):
        return [c.args[2] for c in self.fakes.log_actions.call_args_list if c.args[1] == "edhWorkstationRenew"]


@pytest.mark.unit
class TestRenewReissuesTheSessionKey:
    def test_a_running_desktop_is_re_keyed(self, monkeypatch):
        row = _keyed_row()
        run = _Reissue(monkeypatch, row)

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "provisioned"
        stored = run.stored.item
        assert (stored["apiKeyId"], stored["keyExpiresAt"]) == ("key-new", MINTED["expiresAt"])
        assert stored["leaseExpiresAt"] > row["leaseExpiresAt"]
        # The claim is gone once the secret holds the key, and nothing was deleted or bound again.
        assert "credentialClaimedAt" not in stored
        assert run.written == ["key-new"] and run.deleted == [] and run.bound == []

    def test_the_row_names_the_new_key_before_any_secret_holds_it(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row())

        run.renew()

        assert run.order == ["lease", "claim", "mint", "settle", "write", "release"]

    def test_the_mint_is_handed_the_row_as_the_claim_left_it_with_the_callers_mfa_state(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row(), claims={**USER_CLAIMS, "mfaEnabled": True})

        run.renew()

        (call,) = run.mint_calls
        assert call["row"]["credentialClaimedAt"]
        assert call["row"]["apiKeyId"] == "key-old"
        assert call["mfaEnabled"] is True

    def test_the_mint_gets_the_lambdas_deadline_less_the_renew_reserve(self, monkeypatch):
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 25_000
        run = _Reissue(monkeypatch, _keyed_row(), context=context)

        run.renew()

        (call,) = run.mint_calls
        import time
        expected = time.monotonic() + 25.0 - service.MINT_RESERVE_SECONDS_RENEW
        assert expected - 5.0 < call["deadline"] <= expected
        assert service.MINT_RESERVE_SECONDS_RENEW == 3.0

    def test_the_response_and_the_audit_name_the_outcome_and_carry_no_credential(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row())

        response = run.renew()

        body = response_body(response)
        (audit,) = run.renew_audit()
        assert audit["sessionBootstrap"] == "provisioned"
        for internal in ("apiKeyId", "keyExpiresAt", "credentialClaimedAt", "secretName", "secretArn"):
            assert internal not in body and internal not in audit
        assert NEW_KEY_VALUE not in response["body"] and NEW_KEY_VALUE not in json.dumps(audit)

    @pytest.mark.parametrize("expiry", [(datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(KEY_STAMP),
                                        "not-a-date", ""], ids=["expired", "unreadable", "blank"])
    def test_a_row_whose_key_is_past_or_without_a_readable_expiry_is_re_keyed(self, monkeypatch, expiry):
        run = _Reissue(monkeypatch, _keyed_row(keyExpiresAt=expiry))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "provisioned"
        assert run.stored.item["apiKeyId"] == "key-new"

    def test_a_row_whose_key_the_sweep_revoked_is_re_keyed(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row(apiKeyId="", keyExpiresAt=""))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "provisioned"
        assert run.stored.item["apiKeyId"] == "key-new"

    def test_a_row_without_a_container_has_the_new_one_bound_to_its_instance_once_the_claim_is_released(
            self, monkeypatch):
        row = _keyed_row(secretName="", secretArn="", secretBoundInstanceId="")
        created = {"secretArn": SECRET_ARN, "secretName": SECRET_NAME, "secretBoundInstanceId": ""}
        run = _Reissue(monkeypatch, row, write=created)

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "provisioned"
        assert run.order[-3:] == ["release", "bind", "row"]
        assert run.bound == [INSTANCE_ID]
        assert (run.stored.item["secretName"], run.stored.item["secretBoundInstanceId"]) == (SECRET_NAME, INSTANCE_ID)

    @pytest.mark.parametrize("bind_error, failing", [(bootstrap.SessionBootstrapError("down"), ()), (None, ("row",))],
                             ids=["binding refused", "row write fails"])
    def test_a_binding_that_fails_leaves_the_re_issue_unavailable(self, monkeypatch, bind_error, failing):
        # The reconciler binds the container on a later sweep.
        row = _keyed_row(secretName="", secretArn="", secretBoundInstanceId="")
        created = {"secretArn": SECRET_ARN, "secretName": SECRET_NAME, "secretBoundInstanceId": ""}
        run = _Reissue(monkeypatch, row, write=created, bind_error=bind_error, failing=failing)

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.stored.item["secretBoundInstanceId"] == ""
        (audit,) = run.renew_audit()
        assert audit["sessionBootstrap"] == "unavailable"


@pytest.mark.unit
class TestARenewThatReissuesNothing:
    def test_an_api_key_caller_renews_the_lease_and_mints_nothing(self, monkeypatch):
        # A desktop's own session key can keep its lease alive but never mint or extend a key.
        row = _keyed_row()
        run = _Reissue(monkeypatch, row, claims=API_KEY_CLAIMS)

        response = run.renew()

        assert response["statusCode"] == 200
        assert "sessionBootstrap" not in response_body(response)
        assert run.stored.item["leaseExpiresAt"] > row["leaseExpiresAt"]
        assert run.mint_calls == [] and run.order == ["lease"]
        assert "credentialClaimedAt" not in run.stored.item
        (audit,) = run.renew_audit()
        assert "sessionBootstrap" not in audit

    @pytest.mark.parametrize("state, edh_state", [("STOPPED", "stopped"), ("CREATING", "pending"),
                                                  ("ORPHANED", "error")])
    def test_a_desktop_not_stored_running_renews_the_lease_only(self, monkeypatch, state, edh_state):
        row = _keyed_row(state=state, edhState=edh_state, connectionAvailable=False)
        run = _Reissue(monkeypatch, row)

        response = run.renew()

        assert response["statusCode"] == 200
        assert "sessionBootstrap" not in response_body(response)
        assert run.mint_calls == [] and run.order == ["lease"]

    def test_a_desktop_with_a_start_under_way_renews_the_lease_only(self, monkeypatch):
        row = _keyed_row(startRequestedAt=datetime.now(timezone.utc).isoformat(timespec="microseconds"))
        run = _Reissue(monkeypatch, row)

        response = run.renew()

        assert "sessionBootstrap" not in response_body(response)
        assert run.mint_calls == [] and run.order == ["lease"]

    def test_with_session_bootstrap_off_the_outcome_is_disabled_and_nothing_is_claimed(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row(), enabled=False)

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "disabled"
        assert run.mint_calls == [] and run.order == ["lease"]
        (audit,) = run.renew_audit()
        assert audit["sessionBootstrap"] == "disabled"

    def test_a_windows_desktop_is_disabled_and_nothing_is_claimed(self, monkeypatch):
        """A Windows image carries no consumer for a session key, so its renew re-issues none."""
        run = _Reissue(monkeypatch, _keyed_row(osFamily="windows"))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "disabled"
        assert run.mint_calls == [] and run.order == ["lease"]
        assert "credentialClaimedAt" not in run.stored.item
        (audit,) = run.renew_audit()
        assert audit["sessionBootstrap"] == "disabled"


@pytest.mark.unit
class TestReissueOutcomes:
    def test_a_row_another_renew_or_the_sweep_holds_is_unavailable_and_the_lease_still_renewed(self, monkeypatch):
        held = credential_claim_stamp(datetime.now(timezone.utc) - timedelta(seconds=30))
        row = _keyed_row(credentialClaimedAt=held)
        run = _Reissue(monkeypatch, row)

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.mint_calls == []
        assert run.stored.item["credentialClaimedAt"] == held
        assert run.stored.item["leaseExpiresAt"] > row["leaseExpiresAt"]
        assert run.stored.item["apiKeyId"] == "key-old"

    def test_a_claim_older_than_the_window_is_taken_over(self, monkeypatch):
        stale = credential_claim_stamp(datetime.now(timezone.utc) - timedelta(seconds=CREDENTIAL_CLAIM_SECONDS + 5))
        run = _Reissue(monkeypatch, _keyed_row(credentialClaimedAt=stale))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "provisioned"
        assert "credentialClaimedAt" not in run.stored.item

    def test_a_claim_dated_more_than_a_window_ahead_is_taken_over(self, monkeypatch):
        # Only a hand edit or a corrupt value dates a claim that far ahead; it holds nothing off.
        ahead = credential_claim_stamp(datetime.now(timezone.utc) + timedelta(seconds=CREDENTIAL_CLAIM_SECONDS + 60))
        run = _Reissue(monkeypatch, _keyed_row(credentialClaimedAt=ahead))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "provisioned"
        assert "credentialClaimedAt" not in run.stored.item

    def test_a_start_that_claimed_the_row_after_the_read_holds_the_re_issue_off(self, monkeypatch):
        row = _keyed_row()
        started = {**row, "startRequestedAt": datetime.now(timezone.utc).isoformat(timespec="microseconds")}
        reads = iter([{"Item": dict(row)}])
        run = _Reissue(monkeypatch, started,
                       read=lambda **kwargs: next(reads, None) or run.read(**kwargs))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.mint_calls == []

    def test_a_user_without_roles_is_skipped_and_the_claim_released(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row(), mint=("skipped", None))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "skipped"
        assert run.order == ["lease", "claim", "mint", "release"]
        assert "credentialClaimedAt" not in run.stored.item
        assert run.stored.item["apiKeyId"] == "key-old"

    def test_a_mint_that_fails_is_unavailable_writes_no_secret_and_deletes_nothing(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row(), mint=("unavailable", None))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.written == [] and run.deleted == []
        assert "credentialClaimedAt" not in run.stored.item
        assert run.stored.item["apiKeyId"] == "key-old"

    @pytest.mark.parametrize("change", [{"state": "STOPPED"}, {"state": "DELETED"}, {"apiKeyId": "key-start"},
                                        {"credentialClaimedAt": "2099-01-01T00:00:00.000000+00:00"}],
                             ids=["stopped", "deleted", "another key", "another claim"])
    def test_a_row_that_changed_under_the_claim_takes_the_new_key_back_before_any_secret_sees_it(
            self, monkeypatch, change):
        run = _Reissue(monkeypatch, _keyed_row(), during_mint=lambda r: r.stored.item.update(change))

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.written == []
        assert run.deleted == [{"apiKeyId": "key-new", "bootstrapId": BOOTSTRAP_OWN, "userId": USER, "actor": USER}]
        # The key the row names stays as it was, and so does the secret.
        assert run.stored.item["apiKeyId"] == change.get("apiKeyId", "key-old")

    def test_a_new_key_that_cannot_be_taken_back_leaves_the_renew_unavailable(self, monkeypatch):
        # The key no row names goes with the orphan sweep.
        run = _Reissue(monkeypatch, _keyed_row(), during_mint=lambda r: r.stored.item.update(state="STOPPED"),
                       delete_error=bootstrap.SessionBootstrapError("down"))

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.written == []

    def test_a_secret_write_that_fails_deletes_nothing(self, monkeypatch):
        # The row names the new key and the secret still holds the one it replaced; the running desktop
        # keeps both until each one's own expiry.
        run = _Reissue(monkeypatch, _keyed_row(), write_error=bootstrap.SessionBootstrapError("down"))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.deleted == [] and run.neutralised == []
        stored = run.stored.item
        assert stored["apiKeyId"] == "key-new"
        assert "credentialClaimedAt" not in stored
        assert run.order[-2:] == ["write", "release"]

    def test_an_unexpected_secret_write_error_is_unavailable_too(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row(), write_error=RuntimeError("boom"))

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.deleted == []

    @pytest.mark.parametrize("change", [{"credentialClaimedAt": "2099-01-01T00:00:00.000000+00:00"},
                                        {"state": "CREATING", "edhState": "pending"}],
                             ids=["another claim", "launching"])
    def test_a_release_overtaken_on_a_live_row_leaves_the_new_key_with_the_desktop(self, monkeypatch, change):
        # The desktop may be running on the key the secret now holds, which stays valid until its own expiry.
        run = _Reissue(monkeypatch, _keyed_row(), during_write=lambda r: r.stored.item.update(change))

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.deleted == [] and run.neutralised == []
        assert run.stored.item.get("credentialClaimedAt") == change.get("credentialClaimedAt")
        (audit,) = run.renew_audit()
        assert audit["sessionBootstrap"] == "unavailable"

    def test_a_release_a_stop_overtook_takes_the_new_key_back_and_revokes_the_secret(self, monkeypatch):
        # The stop took back what the row named before the settle; the key the secret was then given goes
        # too, and the value is overwritten with the revoked marker.
        run = _Reissue(monkeypatch, _keyed_row(), during_write=lambda r: r.stored.item.update(
            state="STOPPED", edhState="stopped", apiKeyId="", keyExpiresAt=""))

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.deleted == [{"apiKeyId": "key-new", "bootstrapId": BOOTSTRAP_OWN, "userId": USER, "actor": USER}]
        assert run.neutralised == [(BOOTSTRAP_OWN, False)]
        assert "credentialClaimedAt" not in run.stored.item
        (audit,) = run.renew_audit()
        assert audit["sessionBootstrap"] == "unavailable"

    def test_a_release_a_delete_overtook_removes_the_container_the_write_created(self, monkeypatch):
        row = _keyed_row(secretName="", secretArn="", secretBoundInstanceId="")
        created = {"secretArn": SECRET_ARN, "secretName": SECRET_NAME, "secretBoundInstanceId": ""}
        run = _Reissue(monkeypatch, row, write=created, during_write=lambda r: r.stored.item.update(
            state="DELETED", apiKeyId="", keyExpiresAt=""))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert [removed["apiKeyId"] for removed in run.deleted] == ["key-new"]
        assert run.neutralised == [(BOOTSTRAP_OWN, True)]
        assert run.bound == []

    def test_a_container_the_write_created_that_the_row_now_names_is_kept_with_its_value_revoked(self, monkeypatch):
        row = _keyed_row(secretName="", secretArn="", secretBoundInstanceId="")
        created = {"secretArn": SECRET_ARN, "secretName": SECRET_NAME, "secretBoundInstanceId": ""}
        run = _Reissue(monkeypatch, row, write=created, during_write=lambda r: r.stored.item.update(
            state="STOPPED", apiKeyId="", keyExpiresAt="", secretName=SECRET_NAME))

        run.renew()

        assert run.neutralised == [(BOOTSTRAP_OWN, False)]

    def test_a_take_back_that_fails_is_left_to_the_sweep_and_the_renew_still_answers(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row(), delete_error=bootstrap.SessionBootstrapError("down"),
                       neutralise_error=bootstrap.SessionBootstrapError("down"),
                       during_write=lambda r: r.stored.item.update(state="STOPPED", apiKeyId="", keyExpiresAt=""))

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert "credentialClaimedAt" not in run.stored.item

    @pytest.mark.parametrize("kind", ["claim", "settle", "release"])
    def test_a_write_that_landed_though_its_resend_was_refused_counts_as_landed(self, monkeypatch, kind):
        run = _Reissue(monkeypatch, _keyed_row(), landed=(kind,))

        response = run.renew()

        assert response_body(response)["sessionBootstrap"] == "provisioned"
        assert run.stored.item["apiKeyId"] == "key-new"
        assert "credentialClaimedAt" not in run.stored.item
        assert run.written == ["key-new"] and run.deleted == [] and run.neutralised == []

    @pytest.mark.parametrize("kind", ["claim", "settle", "release"])
    def test_a_table_error_inside_the_re_issue_leaves_the_lease_renewed_and_the_renew_audited(
            self, monkeypatch, kind):
        row = _keyed_row()
        run = _Reissue(monkeypatch, row, failing=(kind,))

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.stored.item["leaseExpiresAt"] > row["leaseExpiresAt"]
        (audit,) = run.renew_audit()
        assert audit["sessionBootstrap"] == "unavailable"

    def test_a_claim_that_errors_mints_nothing(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row(), failing=("claim",))

        run.renew()

        assert run.mint_calls == []
        assert run.stored.item["apiKeyId"] == "key-old"

    def test_a_settle_that_errors_takes_the_new_key_back_and_releases_the_claim(self, monkeypatch):
        run = _Reissue(monkeypatch, _keyed_row(), failing=("settle",))

        run.renew()

        assert [removed["apiKeyId"] for removed in run.deleted] == ["key-new"]
        assert run.written == []
        assert run.stored.item["apiKeyId"] == "key-old"
        assert "credentialClaimedAt" not in run.stored.item

    def test_a_release_that_errors_leaves_the_new_key_with_the_desktop(self, monkeypatch):
        # The row names the key the secret holds; the claim is left to age out of its window.
        run = _Reissue(monkeypatch, _keyed_row(), failing=("release",))

        run.renew()

        assert run.written == ["key-new"] and run.deleted == [] and run.neutralised == []
        assert run.stored.item["apiKeyId"] == "key-new"
        assert run.stored.item["credentialClaimedAt"]

    def test_a_mint_that_raises_releases_the_claim(self, monkeypatch):
        def fail(_run):
            raise RuntimeError("boom")

        run = _Reissue(monkeypatch, _keyed_row(), during_mint=fail)

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.written == [] and run.deleted == []
        assert "credentialClaimedAt" not in run.stored.item

    def test_a_re_issue_that_raises_leaves_the_renew_answered_and_audited(self, monkeypatch):
        row = _keyed_row()
        run = _Reissue(monkeypatch, row)
        monkeypatch.setattr(service, "_reissue_session_key", MagicMock(side_effect=RuntimeError("boom")))

        response = run.renew()

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "unavailable"
        assert run.stored.item["leaseExpiresAt"] > row["leaseExpiresAt"]
        (audit,) = run.renew_audit()
        assert audit["sessionBootstrap"] == "unavailable"

    def test_of_two_renews_at_once_one_re_issues_and_the_other_is_unavailable(self, monkeypatch):
        answers = {}

        def second_renew(run):
            answers["second"] = response_body(run.renew())
            answers["mints during the first"] = len(run.mint_calls)

        run = _Reissue(monkeypatch, _keyed_row(), during_mint=second_renew)

        answers["first"] = response_body(run.renew())

        assert answers["first"]["sessionBootstrap"] == "provisioned"
        assert answers["second"]["sessionBootstrap"] == "unavailable"
        assert answers["mints during the first"] == 1
        assert run.written == ["key-new"]

    def test_a_sweep_that_reaches_the_row_while_the_renew_holds_it_revokes_nothing(self, monkeypatch):
        # The key the renew replaces has expired, which on its own the sweep revokes; the sweep reaches the
        # row past its write grace but inside the claim's window.
        seen = {}

        def sweep(run):
            revoke = MagicMock(return_value=dict(bootstrap.CLEARED_KEY_ATTRIBUTES))
            monkeypatch.setattr(bootstrap, "revoke_session", revoke)
            monkeypatch.setattr(bootstrap, "api_key_service_function_name", "vams-apiKeyService")
            monkeypatch.setattr(rec, "session_table", MagicMock(
                get_item=MagicMock(side_effect=run.stored.read), update_item=MagicMock(side_effect=run.stored)))
            later = datetime.now(timezone.utc) + timedelta(seconds=rec.RECENT_WRITE_GRACE_SECONDS + 30)
            summary = {"examined": 0, "updated": 0, "revoked": 0, "scheduled": 0, "deferred": 0, "skipped": 0,
                       "failed": 0, "orphanKeysDeleted": 0}
            rec._reconcile({"bootstrapId": BOOTSTRAP_OWN}, rec._index_desktops([]), later, summary)
            seen["revoked"], seen["summary"] = revoke.call_args_list, summary

        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(KEY_STAMP)
        run = _Reissue(monkeypatch, _keyed_row(keyExpiresAt=expired, scheduleAppliedAt="2026-09-28T00:00:00+00:00"),
                       during_mint=sweep)

        response = run.renew()

        assert seen["revoked"] == []
        assert seen["summary"]["deferred"] == 1 and seen["summary"]["revoked"] == 0
        # The sweep recorded the desktop EDH no longer lists; the renew's settle then finds the row changed.
        assert response_body(response)["sessionBootstrap"] == "unavailable"


# ---------------------------------------------------------------------------
# The same sequences against the real bootstrap module, apiKeyService and Secrets Manager faked
# ---------------------------------------------------------------------------
class _Credentials:
    """The real bootstrap module wired to faked apiKeyService and Secrets Manager, the session row stored
    as DynamoDB stores it -- a read that does not ask for ConsistentRead answered with the row as it was
    first stored -- and a bootstrap clock that starts at the current minute and moves a minute on every
    reading, so each re-issued key's name is its own."""

    def __init__(self, monkeypatch, row=None, *, claims=None):
        self.monkeypatch = monkeypatch
        self.keys = FakeApiKeyService()
        self.secrets = FakeSecretsManager()
        row = row if row is not None else _keyed_row()
        if row.get("apiKeyId"):
            self.keys.seed(row["apiKeyId"], f"temporary-session-edh-{BOOTSTRAP_OWN}", user_id=USER,
                           expires_at=row["keyExpiresAt"], created_at="2000-01-01T00:00:00Z", key_value=OLD_KEY_VALUE)
            self.secrets.seed(SECRET_NAME, json.dumps({"apiKey": OLD_KEY_VALUE, "userId": USER,
                                                       "baseUrl": "https://vams.example.com",
                                                       "expiresAt": row["keyExpiresAt"]}))
        self.fakes = install_service_fakes(monkeypatch, claims=claims)
        _set_policy(self.fakes, enabled=True, leaseHours=8)
        _seed_row(self.fakes, row)
        self.stored = ConditionalRowTable(**row)
        self.read = _lagging(self.stored, dict(row))
        self.fakes.table.update_item.side_effect = self.stored
        self.fakes.table.get_item.side_effect = self.read
        self.fakes.table.query.side_effect = lambda **kwargs: {"Items": [dict(self.stored.item)]}
        self.first_tick = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        self.ticks = iter(self.first_tick + timedelta(minutes=n) for n in range(100))
        for name, value in (("session_bootstrap_enabled", True), ("lambda_client", self.keys),
                            ("secretsmanager_client", self.secrets), ("log_auth_changes", MagicMock()),
                            ("vams_base_url", "https://vams.example.com"), ("_resolved_base_url", None),
                            ("_budget_deadline", None), ("_now", lambda: next(self.ticks))):
            monkeypatch.setattr(bootstrap, name, value)

    def call(self, action, method="POST"):
        path_params = {"sessionId": SESSION_OWN}
        return self.fakes.module.lambda_handler(
            make_event(method, workstation_path(SESSION_OWN, action), path_params=path_params), MagicMock())

    def secret_key_ids_ever(self):
        return {self.keys.key_id_of(value) for value in self.secrets.key_values_written(SECRET_NAME)}

    def secret_key_id_now(self):
        return self.keys.key_id_of(self.secrets.key_value_held(SECRET_NAME))

    def sweep(self, at, edh_state="running"):
        """One reconciler sweep at ``at`` over the stored row, EDH's cluster listing -- which carries no
        connection string -- recording the desktop ``edh_state`` on its instance under the schedule that
        never starts it; the sweep's summary."""
        desktop = {EDH_SESSION_ID_FIELD: SESSION_OWN, EDH_SESSION_STATE_FIELD: edh_state,
                   EDH_FIELD_INSTANCE_ID: INSTANCE_ID, EDH_SCHEDULE_FIELD: json.dumps(never_auto_start_schedule())}
        client = create_autospec(EdhClient, instance=True)
        client.list_all_desktops.return_value = [desktop]
        table = MagicMock()
        table.scan.side_effect = lambda **kwargs: {"Items": [dict(self.stored.item)]}
        table.get_item.side_effect = self.read
        table.update_item.side_effect = self.stored
        policy = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24, "updatedAt": None,
                  "updatedBy": None}
        with pytest.MonkeyPatch.context() as patched:
            for name, value in (("EdhClient", MagicMock(return_value=client)), ("session_table", table),
                                ("read_lease_policy", lambda: policy), ("_now", lambda: at)):
                patched.setattr(rec, name, value)
            return rec.lambda_handler({}, MagicMock())

    def between_the_settle_and_the_secret_write(self, send):
        """A renew during which ``send(row)`` runs once, after the renew has named its key on the row and
        before it writes the secret, ``row`` being the row as it stood before that settle; the renew's
        response."""
        before = {}
        self.keys.after_create = lambda _key_id: before.setdefault("row", dict(self.stored.item))
        create = self.secrets.create_secret

        def create_secret(**kwargs):
            if "row" in before and "sent" not in before:
                before["sent"] = True
                send(before["row"])
            return create(**kwargs)

        self.secrets.create_secret = create_secret
        return self.call("renew")

    def assert_every_minted_key_accounted_for(self):
        """Every key minted is deleted, or named by the row while the row is live and still on record; the
        secret, where it exists, holds the revoked marker or a key of this desktop still on record."""
        item = self.stored.item
        assert self.keys.created, "no key was minted"
        for key_id in self.keys.created:
            assert key_id in self.keys.deleted or (
                key_id == item.get("apiKeyId") and session_row_live(item) and key_id in self.keys.records), key_id
        if SECRET_NAME in self.secrets.values:
            held = self.secret_key_id_now()
            assert json.loads(self.secrets.values[SECRET_NAME]) == {"revoked": True} or (
                held in self.keys.records
                and bootstrap.bootstrap_id_from_key_name(self.keys.records[held]["apiKeyName"]) == BOOTSTRAP_OWN)


def _refuse_mint(run):
    run.keys.refuse_create = (500, {"message": "Internal Server Error"})


def _stop_during_mint(run):
    run.keys.after_create = lambda _key_id: run.stored.item.update(state="STOPPED")


def _delete_during_mint(run):
    run.keys.after_create = lambda _key_id: run.stored.item.update(state="DELETED")


def _fail_secret_writes(run):
    run.secrets.fail_writes = "InternalServiceError"


def _hold_the_claim(run):
    run.stored.item["credentialClaimedAt"] = credential_claim_stamp(datetime.now(timezone.utc))


FAILURES = [(None, "provisioned"), (_refuse_mint, "unavailable"), (_stop_during_mint, "unavailable"),
            (_delete_during_mint, "unavailable"), (_fail_secret_writes, "unavailable"),
            (_hold_the_claim, "unavailable")]


@pytest.mark.unit
class TestTheSecretNeverHoldsAKeyVamsDeletes:
    """Every key value the session secret is ever given is a key the row names and that nothing deleted:
    the desktop's refresher signs in with whatever the secret holds, and a sign-in with a deleted key wipes
    the desktop's working profile."""

    @pytest.mark.parametrize("failure, outcome", FAILURES, ids=FAILURE_IDS)
    def test_no_key_the_secret_was_given_is_deleted_on_any_path(self, monkeypatch, failure, outcome):
        run = _Credentials(monkeypatch)
        if failure is not None:
            failure(run)

        response = run.call("renew")

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == outcome
        given = run.secret_key_ids_ever()
        assert given, "the secret was never given a key"
        assert given.isdisjoint(run.keys.deleted)
        # Whatever the secret holds now is still a key record of this desktop, which the desktop keeps
        # until the key's own expiry while it runs.
        held = run.secret_key_id_now()
        assert held in run.keys.records
        assert bootstrap.bootstrap_id_from_key_name(run.keys.records[held]["apiKeyName"]) == BOOTSTRAP_OWN

    @pytest.mark.parametrize("failure", [_stop_during_mint, _delete_during_mint], ids=["stopped", "deleted"])
    def test_a_key_the_row_could_not_be_settled_with_is_deleted(self, monkeypatch, failure):
        run = _Credentials(monkeypatch)
        failure(run)

        run.call("renew")

        assert run.keys.created, "no key was minted"
        assert set(run.keys.created) <= set(run.keys.deleted)
        assert "key-old" not in run.keys.deleted

    def test_a_provisioned_re_issue_puts_the_new_key_in_the_secret_and_keeps_the_old_one_valid(self, monkeypatch):
        run = _Credentials(monkeypatch)

        run.call("renew")

        (new_key,) = run.keys.created
        assert run.secret_key_id_now() == new_key
        assert run.keys.records[new_key]["apiKeyName"] == (
            f"temporary-session-edh-{BOOTSTRAP_OWN}-r{run.first_tick.strftime('%Y%m%d%H%M%S')}")
        assert "key-old" in run.keys.records
        assert run.stored.item["apiKeyId"] == new_key
        # The secret's binding to the desktop's instance is left as it was.
        assert run.secrets.policies == {}

    def test_a_stop_after_a_re_issue_signs_out_both_keys(self, monkeypatch):
        run = _Credentials(monkeypatch)
        run.call("renew")
        (new_key,) = run.keys.created

        response = run.call("stop")

        assert response["statusCode"] == 200
        assert {new_key, "key-old"} <= set(run.keys.deleted)
        assert json.loads(run.secrets.values[SECRET_NAME]) == {"revoked": True}
        assert run.stored.item["apiKeyId"] == ""

    def test_a_delete_after_a_re_issue_signs_out_both_keys_and_removes_the_container(self, monkeypatch):
        run = _Credentials(monkeypatch)
        run.call("renew")
        (new_key,) = run.keys.created

        response = run.call(None, method="DELETE")

        assert response["statusCode"] == 200
        assert {new_key, "key-old"} <= set(run.keys.deleted)
        assert SECRET_NAME not in run.secrets.values

    def test_a_row_without_a_container_gets_one_bound_to_its_instance(self, monkeypatch):
        row = _keyed_row(apiKeyId="", keyExpiresAt="", secretName="", secretArn="", secretBoundInstanceId="")
        run = _Credentials(monkeypatch, row)

        response = run.call("renew")

        assert response_body(response)["sessionBootstrap"] == "provisioned"
        stored = run.stored.item
        assert (stored["secretName"], stored["secretBoundInstanceId"]) == (SECRET_NAME, INSTANCE_ID)
        principals = run.secrets.policies[SECRET_NAME]["Statement"][0]["Principal"]["AWS"]
        assert principals and all(p.endswith(f"/{INSTANCE_ID}") for p in principals)

    def test_an_api_key_caller_mints_nothing_and_the_secret_is_left_alone(self, monkeypatch):
        run = _Credentials(monkeypatch, claims=API_KEY_CLAIMS)

        response = run.call("renew")

        assert response["statusCode"] == 200
        assert run.keys.created == [] and run.keys.deleted == []
        assert run.secret_key_id_now() == "key-old"

    def test_a_stopped_desktop_mints_nothing(self, monkeypatch):
        run = _Credentials(monkeypatch, _keyed_row(state="STOPPED", edhState="stopped", connectionAvailable=False))

        response = run.call("renew")

        assert "sessionBootstrap" not in response_body(response)
        assert run.keys.created == []


@pytest.mark.unit
class TestEveryKeyTheDesktopWasGivenOutlivesTheSweep:
    """A running desktop signs in with whatever its secret holds, which after a failed secret write is a key
    the row no longer names; the sweep keeps every unexpired key of a live desktop until its own expiry."""

    @pytest.mark.parametrize("renews", [2, 3])
    def test_every_earlier_key_stays_valid_through_a_sweep_until_its_own_expiry(self, monkeypatch, renews):
        run = _Credentials(monkeypatch)
        for _ in range(renews):
            run.call("renew")
        minted = list(run.keys.created)
        assert len(minted) == renews and run.stored.item["apiKeyId"] == minted[-1]

        run.sweep(datetime.now(timezone.utc) + timedelta(minutes=16))

        assert run.keys.deleted == []
        assert {"key-old", *minted} <= set(run.keys.records)

        # The launch key's own expiry passes three hours on; the next sweep deletes it and keeps the others.
        run.sweep(datetime.now(timezone.utc) + timedelta(hours=3, minutes=1))

        assert run.keys.deleted == ["key-old"]
        assert set(minted) <= set(run.keys.records)

    def test_the_key_the_secret_holds_after_failed_writes_outlives_the_sweep(self, monkeypatch):
        run = _Credentials(monkeypatch)
        run.secrets.fail_writes = "InternalServiceError"
        outcomes = [response_body(run.call("renew"))["sessionBootstrap"] for _ in range(2)]
        run.secrets.fail_writes = None
        held = run.secret_key_id_now()
        assert outcomes == ["unavailable", "unavailable"]
        assert held == "key-old" and run.stored.item["apiKeyId"] != held

        run.sweep(datetime.now(timezone.utc) + timedelta(minutes=16))

        assert held in run.keys.records
        assert run.keys.deleted == []


def _first_read(table, stale, read):
    """Answer the table's next row read with ``stale`` when one is given, and every read after it with
    ``read``."""
    pending = [stale] if stale is not None else []

    def get_item(**kwargs):
        if pending:
            return {"Item": {**kwargs["Key"], **pending.pop()}}
        return read(**kwargs)

    table.get_item.side_effect = get_item


def _once_within(inside, answer):
    """An EDH call that runs ``inside`` the first time it is made, and answers ``answer``."""
    pending = [inside] if inside is not None else []

    def call(*args):
        if pending:
            pending.pop()()
        return answer

    return call


def _send(monkeypatch, run, writer, *, inside=None, stale=None):
    """Send ``writer``'s request for the caller's desktop against the stored row: ``inside`` runs within the
    request's EDH call, after its row read and before any of its writes, and ``stale`` is what that row read
    returns. The request's response."""
    if writer in ("admin stop", "admin deactivate"):
        action = writer.split(" ")[1]
        wrapper = "stop_desktop" if action == "stop" else "delete_desktop"
        client = admin_support.FakeEdhClient(
            list_all_desktops=[admin_support.edh_desktop(SESSION_OWN, edh_uid_for(USER), "running")],
            **{wrapper: _once_within(inside, {})})
        fakes = admin_support.install_admin_fakes(monkeypatch, edh=client)
        fakes.table.query.side_effect = lambda **kwargs: {"Items": [dict(run.stored.item)]}
        fakes.table.update_item.side_effect = run.stored
        _first_read(fakes.table, stale, run.read)
        return fakes.module.lambda_handler(admin_support.make_event(
            "POST", admin_support.instance_path(SESSION_OWN, action), path_params={"sessionId": SESSION_OWN}), {})
    wrapper, answer, action, method = {
        "stop": ("stop_desktop", {}, "stop", "POST"),
        "delete": ("delete_desktop", {}, None, "DELETE"),
        "start": ("get_desktop_states", STOPPED_IN_EDH, "start", "POST"),
    }[writer]
    setattr(run.fakes.edh, wrapper, _once_within(inside, answer))
    _first_read(run.fakes.table, stale, run.read)
    try:
        return run.call(action, method=method)
    finally:
        run.fakes.table.get_item.side_effect = run.read


@pytest.mark.unit
class TestARenewRacingARequestThatTakesTheKeyBack:
    """A stop, a delete, an administrator's stop or deactivate, and a start, each raced against a renew's
    re-issue: the request reads the row before the renew settles its key, and its writes land either after
    the whole renew or between the settle and the secret write. Whatever the order, every key the renew
    minted ends deleted or named by a live row, and the secret holds a key of the desktop or the revoked
    marker."""

    @pytest.mark.parametrize("writer", WRITERS)
    def test_writes_after_the_whole_renew(self, monkeypatch, writer):
        run = _Credentials(monkeypatch)
        seen = {}

        response = _send(monkeypatch, run, writer,
                         inside=lambda: seen.setdefault("renew", response_body(run.call("renew"))))

        assert response["statusCode"] == 200
        assert seen["renew"]["sessionBootstrap"] == "provisioned"
        run.assert_every_minted_key_accounted_for()

    @pytest.mark.parametrize("writer", WRITERS)
    def test_writes_between_the_settle_and_the_secret_write(self, monkeypatch, writer):
        run = _Credentials(monkeypatch)
        seen = {}

        renew = run.between_the_settle_and_the_secret_write(
            lambda row: seen.setdefault("response", _send(monkeypatch, run, writer, stale=row)))

        assert "response" in seen, "the request never ran"
        assert response_body(renew)["sessionBootstrap"] == "unavailable"
        run.assert_every_minted_key_accounted_for()

    @pytest.mark.parametrize("writer", WRITERS)
    def test_the_key_the_row_holds_once_the_request_has_written_is_taken_back_without_the_listing(
            self, monkeypatch, writer):
        # The request read the row before the renew named its key. With the session-key listing refused, the
        # revoke still takes back the key the row holds once the request's own write has landed.
        run = _Credentials(monkeypatch)
        seen = {}

        def renew_then_refuse_the_listing():
            seen["renew"] = response_body(run.call("renew"))
            run.keys.refuse_listing = (500, {"message": "Internal Server Error"})

        response = _send(monkeypatch, run, writer, inside=renew_then_refuse_the_listing)

        assert response["statusCode"] == 200
        assert seen["renew"]["sessionBootstrap"] == "provisioned"
        reissued = run.keys.created[0]
        assert reissued in run.keys.deleted

    def test_a_stop_after_the_settle_signs_out_every_key_and_leaves_the_secret_revoked(self, monkeypatch):
        run = _Credentials(monkeypatch)

        run.between_the_settle_and_the_secret_write(lambda row: _send(monkeypatch, run, "stop", stale=row))

        (minted,) = run.keys.created
        assert {minted, "key-old"} <= set(run.keys.deleted)
        assert json.loads(run.secrets.values[SECRET_NAME]) == {"revoked": True}

    def test_a_delete_after_the_settle_leaves_no_container(self, monkeypatch):
        run = _Credentials(monkeypatch)

        run.between_the_settle_and_the_secret_write(lambda row: _send(monkeypatch, run, "delete", stale=row))

        assert SECRET_NAME not in run.secrets.values
        assert "key-old" in run.keys.deleted

    def test_a_start_after_the_whole_renew_replaces_every_key_with_its_own(self, monkeypatch):
        run = _Credentials(monkeypatch)

        _send(monkeypatch, run, "start", inside=lambda: run.call("renew"))

        reissued, started = run.keys.created
        assert {reissued, "key-old"} <= set(run.keys.deleted)
        assert run.stored.item["apiKeyId"] == started and run.secret_key_id_now() == started


def _renew(_monkeypatch, run):
    return run.call("renew")


def _renew_losing_the_settle(_monkeypatch, run):
    _stop_during_mint(run)
    return run.call("renew")


def _renew_failing_the_secret_write(_monkeypatch, run):
    _fail_secret_writes(run)
    return run.call("renew")


def _renew_a_stop_takes_back(monkeypatch, run):
    return run.between_the_settle_and_the_secret_write(lambda row: _send(monkeypatch, run, "stop", stale=row))


@pytest.mark.unit
class TestNoLogLineCarriesAKeyValue:
    """The service holds the fresh key's value from the mint until the secret write, and the bootstrap
    module reads the replaced one; neither logs a key value on any path of the re-issue."""

    @pytest.mark.parametrize("renew, outcome", [
        (_renew, "provisioned"), (_renew_losing_the_settle, "unavailable"),
        (_renew_failing_the_secret_write, "unavailable"), (_renew_a_stop_takes_back, "unavailable"),
    ], ids=["provisioned", "the settle is lost", "the secret write fails", "a stop takes the key back"])
    def test_no_log_line_carries_a_key_value(self, monkeypatch, renew, outcome):
        run = _Credentials(monkeypatch)
        recorder = MagicMock()
        monkeypatch.setattr(service, "logger", recorder)
        monkeypatch.setattr(bootstrap, "logger", recorder)

        response = renew(monkeypatch, run)

        assert response_body(response)["sessionBootstrap"] == outcome
        assert run.keys.created, "no key was minted"
        assert not any("vams_" in line for line in _log_lines(recorder))
        recorder.info("control vams_control")
        assert any("vams_" in line for line in _log_lines(recorder))


@pytest.mark.unit
class TestAStartWaitsOutACredentialClaim:
    """A start and a re-issue exclude each other: a start finds a row under a fresh credential claim taken,
    as it finds one under another start."""

    @staticmethod
    def _stopped(monkeypatch, seconds_ago):
        """A stopped desktop whose row a renew or the sweep claimed ``seconds_ago``."""
        claim = credential_claim_stamp(datetime.now(timezone.utc) - timedelta(seconds=seconds_ago))
        run = _Credentials(monkeypatch, _keyed_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                                                   credentialClaimedAt=claim))
        run.fakes.edh.results["get_desktop_states"] = STOPPED_IN_EDH
        return run

    def test_a_start_is_refused_while_a_credential_claim_is_fresh(self, monkeypatch):
        run = self._stopped(monkeypatch, 30)

        response = run.call("start")

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "A start of this workstation is already in progress"
        assert run.keys.created == [] and run.keys.deleted == []

    def test_a_start_takes_over_a_claim_past_its_window(self, monkeypatch):
        run = self._stopped(monkeypatch, CREDENTIAL_CLAIM_SECONDS + 5)

        response = run.call("start")

        assert response["statusCode"] == 200
        assert run.keys.created, "the start minted no key"

    def test_a_start_takes_over_a_claim_dated_more_than_a_window_ahead(self, monkeypatch):
        run = self._stopped(monkeypatch, -(CREDENTIAL_CLAIM_SECONDS + 60))

        response = run.call("start")

        assert response["statusCode"] == 200
        assert run.keys.created, "the start minted no key"


def _fail_the_secret_overwrite(run):
    run.secrets.fail_writes = "InternalServiceError"


def _renew_the_lease(run):
    assert run.call("renew")["statusCode"] == 200


@pytest.mark.unit
class TestASweepWhoseRevokeDoesNotFinishGivesItsClaimUp:
    """EDH stopped the running desktop on its idle timer, and a sweep ten seconds ago claimed its row --
    the start slot and the credential claim -- to revoke its key. A revoke that fails, or a clear that a
    renew's lease write overtakes, gives that claim up while the credential stays recorded for the next
    sweep, so a start is not held off for the claim's window."""

    @pytest.mark.parametrize("during_the_revoke, counted", [
        (_fail_the_secret_overwrite, "failed"), (_renew_the_lease, "deferred"),
    ], ids=["the revoke fails", "a renew lands during the revoke"])
    def test_the_claim_goes_and_a_start_moments_later_is_provisioned(self, monkeypatch, during_the_revoke, counted):
        run = _Credentials(monkeypatch)
        invoke, seen = run.keys.invoke, []

        def invoke_once_within(**kwargs):
            # The revoke's first call to apiKeyService deletes the key the row names.
            if not seen:
                seen.append(kwargs)
                during_the_revoke(run)
            return invoke(**kwargs)

        run.keys.invoke = invoke_once_within
        summary = run.sweep(datetime.now(timezone.utc) - timedelta(seconds=10), edh_state="stopped")
        run.keys.invoke, run.secrets.fail_writes = invoke, None

        assert seen, "the sweep revoked nothing"
        assert summary[counted] == 1
        stored = run.stored.item
        assert "credentialClaimedAt" not in stored and "startRequestedAt" not in stored
        assert (stored["state"], stored["apiKeyId"]) == ("STOPPED", "key-old")

        run.fakes.edh.results["get_desktop_states"] = STOPPED_IN_EDH
        response = run.call("start")

        assert response["statusCode"] == 200
        assert response_body(response)["sessionBootstrap"] == "provisioned"
