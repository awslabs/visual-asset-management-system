# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Single-asset routes: authorization decides before existence and state.

Every single-asset operation used to fetch the asset, reject a missing one (404 on the GET
path, 400 elsewhere) or a wrong-state one ("already archived", "not archived"), and only then
consult Casbin. A caller refused at Tier-2 therefore read the asset inventory off the status
code: 400/404 meant "no such asset", 403 meant "it exists but is not yours", and on the archive
route 400-vs-403 additionally reported whether an existing asset was already archived. The
`assetId` was echoed back in the already-archived message as well, which backend/CLAUDE.md
Rule 11 forbids independently of the disclosure.

The order is therefore: authorize, then answer. When the asset exists the stored record is
evaluated exactly as before; when it does not, the identifiers the request supplied stand in,
so an unauthorized caller is refused instead of being told the identifiers are unused. That
last part is what makes the two cases indistinguishable, and it is why each test below asserts
the *same* status for the existing and the missing asset rather than asserting 403 once.

Every "denied" case is paired with the permitted case for the same site: a handler that refused
everything would make each denial assertion pass on its own, and would also hide the fact that a
legitimate caller must still receive the ordinary 404 / 400.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from tests.handlers.assets.test_assetService_tag_mutation_authz import (
    _tag_existence_validation_stubbed,
)
from tests.handlers.assets.test_assetService_update_tag_scope import (
    _existing_asset,
    _load_asset_service,
)


_DB = "db-a"
_ASSET = "asset-1"

# (site id, request handler, HTTP method, path suffix, body, enforced action,
#  status for a missing asset when the caller IS authorized)
_SITES = [
    ("get_asset", "handle_get_request", "GET", "", None, "GET", 404),
    (
        "update_asset",
        "handle_put_request",
        "PUT",
        "",
        {"description": "a changed description"},
        "PUT",
        400,
    ),
    (
        "archive_asset",
        "handle_delete_request",
        "DELETE",
        "/archiveAsset",
        {"confirmArchive": True},
        "DELETE",
        400,
    ),
    (
        "unarchive_asset",
        "handle_put_request",
        "PUT",
        "/unarchiveAsset",
        {"confirmUnarchive": True},
        "PUT",
        400,
    ),
    (
        "delete_asset_permanent",
        "handle_delete_request",
        "DELETE",
        "/deleteAsset",
        {"confirmPermanentDelete": True},
        "DELETE",
        400,
    ),
]
_SITE_IDS = [site[0] for site in _SITES]

# Sites whose asset must already be archived for the operation to be valid.
_ARCHIVED_FIXTURE = {"unarchive_asset"}


class _EnforcerSpy:
    """A CasbinEnforcer stand-in that records every construction and every enforce call."""

    def __init__(self, denied_actions=()):
        self.denied_actions = set(denied_actions)
        self.constructions = []
        self.calls = []

    @property
    def factory(self):
        spy = self

        class _Enforcer:
            def __init__(self, claims_and_roles):
                spy.constructions.append(claims_and_roles)

            def enforce(self, obj, action):
                spy.calls.append({"object": dict(obj), "action": action})
                return action not in spy.denied_actions

            def enforceAPI(self, event):
                return True

        return _Enforcer


def _authorized_identities(spy):
    """The (action, object__type, databaseId, assetId) tuples handed to Casbin, as a set.

    A set, deliberately: the property is that the asset the caller named was evaluated for the
    operation's action, not that it happened exactly once. A handler that authorizes the same
    asset twice, or an extra object, is strictly safer and must stay green.
    """
    return {
        (
            call["action"],
            call["object"].get("object__type"),
            call["object"].get("databaseId"),
            call["object"].get("assetId"),
        )
        for call in spy.calls
    }


def _event(method, path_suffix, body):
    event = {
        "requestContext": {
            "http": {
                "method": method,
                "path": f"/database/{_DB}/assets/{_ASSET}{path_suffix}",
            }
        },
        "pathParameters": {"databaseId": _DB, "assetId": _ASSET},
        "queryStringParameters": None,
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _wire(asset, tokens=("u1",), denied_actions=()):
    """Point the loaded assetService at mocks; `asset` is the stored record or None."""
    m = _load_asset_service()
    spy = _EnforcerSpy(denied_actions=denied_actions)

    m.get_asset_details = MagicMock(
        side_effect=lambda *a, **kw: dict(asset) if asset else None
    )
    m.get_asset_bucket_details = MagicMock(return_value={"bucketName": "bucket-1"})
    m.enhance_asset_with_version_info = MagicMock(side_effect=lambda a: dict(a))
    m.asset_table = MagicMock()
    m.write_asset_history_record = MagicMock()
    m.send_subscription_email = MagicMock()

    saved_claims = m.claims_and_roles
    m.claims_and_roles = {"tokens": list(tokens)}
    enforcer_patch = patch.object(m, "CasbinEnforcer", spy.factory)
    enforcer_patch.start()

    def _undo():
        m.claims_and_roles = saved_claims
        enforcer_patch.stop()

    return m, spy, _undo


def _asset_for(site, archived=None):
    asset = _existing_asset(_DB)
    if archived if archived is not None else site in _ARCHIVED_FIXTURE:
        asset["status"] = "archived"
    return asset


def _invoke(m, handler_name, method, path_suffix, body):
    with _tag_existence_validation_stubbed():
        return getattr(m, handler_name)(_event(method, path_suffix, body))


def _call(site, handler_name, method, path_suffix, body, asset, denied_actions=()):
    m, spy, undo = _wire(asset, denied_actions=denied_actions)
    try:
        response = _invoke(m, handler_name, method, path_suffix, body)
    finally:
        undo()
    return response, spy, m


@pytest.mark.unit
class TestAMissingAssetIsNotDisclosedToADeniedCaller:
    """403 for a missing asset and 403 for an existing one, so neither can be told apart."""

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,action,missing_status",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_denied_caller_gets_the_same_status_whether_the_asset_exists(
        self, site, handler_name, method, path_suffix, body, action, missing_status
    ):
        existing, _, _ = _call(
            site, handler_name, method, path_suffix, body,
            _asset_for(site), denied_actions=(action,),
        )
        missing, _, _ = _call(
            site, handler_name, method, path_suffix, body,
            None, denied_actions=(action,),
        )

        assert existing["statusCode"] == missing["statusCode"] == 403, (
            f"{site}: a refused caller can tell a missing asset ({missing['statusCode']}) "
            f"from one it may not reach ({existing['statusCode']}), which reports whether the "
            f"asset exists"
        )
        assert json.loads(missing["body"])["message"] == "Not Authorized"

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,action,missing_status",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_the_missing_asset_is_authorized_on_the_identifiers_the_caller_supplied(
        self, site, handler_name, method, path_suffix, body, action, missing_status
    ):
        """A probe object with no scoping fields would deny (or allow) everyone alike."""
        _, spy, _ = _call(
            site, handler_name, method, path_suffix, body, None,
            denied_actions=(action,),
        )

        assert (action, "asset", _DB, _ASSET) in _authorized_identities(spy), (
            f"{site}: the absent asset was not evaluated on the requested identifiers, so the "
            f"ABAC rule that scopes access cannot match. Evaluated: "
            f"{sorted(_authorized_identities(spy))}"
        )

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,action,missing_status",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_an_authorized_caller_still_learns_the_asset_is_missing(
        self, site, handler_name, method, path_suffix, body, action, missing_status
    ):
        """Positive control: "always 403" would satisfy the assertions above on its own."""
        response, spy, m = _call(site, handler_name, method, path_suffix, body, None)

        assert response["statusCode"] == missing_status, (
            f"{site}: an authorized caller was refused instead of being told the asset does "
            f"not exist: {response}"
        )
        assert spy.calls, f"{site}: the response was produced with nothing authorized"
        m.asset_table.put_item.assert_not_called()
        m.asset_table.delete_item.assert_not_called()

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,action,missing_status",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_an_authorized_caller_still_reaches_an_existing_asset(
        self, site, handler_name, method, path_suffix, body, action, missing_status
    ):
        """Second positive control: the reorder must not break the ordinary success path."""
        response, spy, m = _call(
            site, handler_name, method, path_suffix, body, _asset_for(site)
        )

        assert response["statusCode"] == 200, (
            f"{site}: an authorized request against an existing asset was refused: {response}"
        )
        assert (action, "asset", _DB, _ASSET) in _authorized_identities(spy), (
            f"{site}: the stored asset was not the object authorized: "
            f"{sorted(_authorized_identities(spy))}"
        )


@pytest.mark.unit
class TestArchiveStateIsNotDisclosedToADeniedCaller:
    """The archive routes rejected a wrong-state asset before authorizing it."""

    def test_a_denied_caller_cannot_tell_an_archived_asset_from_an_active_one(self):
        statuses = {}
        for label, asset in (
            ("active", _asset_for("archive_asset", archived=False)),
            ("already archived", _asset_for("archive_asset", archived=True)),
            ("missing", None),
        ):
            response, _, _ = _call(
                "archive_asset", "handle_delete_request", "DELETE", "/archiveAsset",
                {"confirmArchive": True}, asset, denied_actions=("DELETE",),
            )
            statuses[label] = response["statusCode"]

        assert set(statuses.values()) == {403}, (
            f"the archive route reports an asset's archive state to a caller it refuses: "
            f"{statuses}"
        )

    def test_a_denied_caller_cannot_tell_an_unarchivable_asset_from_an_archived_one(self):
        statuses = {}
        for label, asset in (
            ("archived", _asset_for("unarchive_asset", archived=True)),
            ("not archived", _asset_for("unarchive_asset", archived=False)),
            ("missing", None),
        ):
            response, _, _ = _call(
                "unarchive_asset", "handle_put_request", "PUT", "/unarchiveAsset",
                {"confirmUnarchive": True}, asset, denied_actions=("PUT",),
            )
            statuses[label] = response["statusCode"]

        assert set(statuses.values()) == {403}, (
            f"the unarchive route reports an asset's archive state to a caller it refuses: "
            f"{statuses}"
        )

    def test_an_authorized_caller_is_still_told_the_asset_is_already_archived(self):
        """Positive control, and Rule 11: the reason is stated without echoing the request."""
        response, spy, m = _call(
            "archive_asset", "handle_delete_request", "DELETE", "/archiveAsset",
            {"confirmArchive": True}, _asset_for("archive_asset", archived=True),
        )

        assert response["statusCode"] == 400, response
        message = json.loads(response["body"])["message"]
        assert "already archived" in message, message
        assert _ASSET not in message, (
            f"the already-archived message echoes the caller-supplied assetId: {message}"
        )
        assert _DB not in message, (
            f"the already-archived message echoes the caller-supplied databaseId: {message}"
        )
        m.asset_table.put_item.assert_not_called()

    def test_an_authorized_caller_is_still_told_the_asset_is_not_archived(self):
        response, spy, m = _call(
            "unarchive_asset", "handle_put_request", "PUT", "/unarchiveAsset",
            {"confirmUnarchive": True}, _asset_for("unarchive_asset", archived=False),
        )

        assert response["statusCode"] == 400, response
        message = json.loads(response["body"])["message"]
        assert "not archived" in message, message
        assert _ASSET not in message and _DB not in message, (
            f"the not-archived message echoes request input: {message}"
        )
        m.asset_table.put_item.assert_not_called()


@pytest.mark.unit
class TestEmptyTokenListDeniesBeforeTheFetchIsUsed:
    """Rule 4: no identity is nothing to evaluate, at every one of these sites."""

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,action,missing_status",
        _SITES,
        ids=_SITE_IDS,
    )
    @pytest.mark.parametrize("asset_present", [True, False], ids=["exists", "missing"])
    def test_empty_tokens_return_403_without_consulting_casbin(
        self, site, handler_name, method, path_suffix, body, action, missing_status,
        asset_present,
    ):
        asset = _asset_for(site) if asset_present else None
        m, spy, undo = _wire(asset, tokens=())
        try:
            response = _invoke(m, handler_name, method, path_suffix, body)
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"{site}: an unauthenticated request returned {response['statusCode']}: {response}"
        )
        assert spy.constructions == [], (
            f"{site}: CasbinEnforcer was constructed for an empty token list"
        )
        assert spy.calls == [], f"{site}: enforce() ran with no identity: {spy.calls}"
