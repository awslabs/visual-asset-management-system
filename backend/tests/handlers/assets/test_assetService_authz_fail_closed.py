# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset read and mutation paths: object-level denial denies, and reaches the caller as 403.

Six Tier-2 checks are exercised here — the single-asset GET, `update_asset`, the tag-change
gate inside `update_asset`, `archive_asset`, `unarchive_asset` and `delete_asset_permanent`.
They are separate code paths that happen to look alike, so each is driven on its own; a test
of "PUT is denied" would never reach the tag gate, and a test of `archive_asset` says nothing
about `delete_asset_permanent`.

Two properties are pinned per site.

**Empty token list denies before the enforcer is consulted.** With no authenticated identity
there is nothing to evaluate, so the request is refused up front — backend/CLAUDE.md Rule 4
for a single-resource check. The assertion is that `CasbinEnforcer` was never constructed,
which is the actual property: the enforcer injected here is a stand-in whose verdict the test
chooses, so "the response was 403" can be true for the wrong reason. `update_asset`'s gate
covers the tag-change gate nested below it, which is why the tag gate no longer carries a
token-count condition of its own — with no identity the function never reaches it.

**A denial is a denial, not a 500.** Five of these checks signalled a refusal by *returning*
`authorization_error()` — a completed API response — from a business function whose caller
then read `result.dict()`. A `dict` has no `.dict()`, so the `AttributeError` fell into the
broad `except Exception` and an ordinary permission refusal was reported as an internal
server error: the wrong status to the client, an audit entry typed `internal`, and real 500s
on these endpoints masked. The functions therefore raise a denial and the request handler
translates it, so the return type is the response model and nothing else.

Each denial case is paired with the permitted case for the same site, because a handler that
refused everything would satisfy every "denied" assertion on its own.
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
    _written_attributes,
)


_DB = "db-a"
_ASSET = "asset-1"


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


# (site id, handler, method, path suffix, body, archived fixture, enforced action)
_SITES = [
    ("get_asset", "handle_get_request", "GET", "", None, False, "GET"),
    (
        "update_asset",
        "handle_put_request",
        "PUT",
        "",
        {"description": "a changed description"},
        False,
        "PUT",
    ),
    (
        "update_asset.tag_change",
        "handle_put_request",
        "PUT",
        "",
        {"tags": ["added"]},
        False,
        "GET",
    ),
    (
        "archive_asset",
        "handle_delete_request",
        "DELETE",
        "/archiveAsset",
        {"confirmArchive": True},
        False,
        "DELETE",
    ),
    (
        "unarchive_asset",
        "handle_put_request",
        "PUT",
        "/unarchiveAsset",
        {"confirmUnarchive": True},
        True,
        "PUT",
    ),
    (
        "delete_asset_permanent",
        "handle_delete_request",
        "DELETE",
        "/deleteAsset",
        {"confirmPermanentDelete": True},
        False,
        "DELETE",
    ),
]
_SITE_IDS = [site[0] for site in _SITES]


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


def _wire(archived=False, tokens=("u1",), denied_actions=()):
    """Point the loaded assetService at mocks and inject the enforcer spy."""
    m = _load_asset_service()
    spy = _EnforcerSpy(denied_actions=denied_actions)

    asset = _existing_asset(_DB)
    if archived:
        asset["status"] = "archived"

    m.get_asset_details = MagicMock(side_effect=lambda *a, **kw: dict(asset))
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


def _invoke(m, handler_name, method, path_suffix, body):
    with _tag_existence_validation_stubbed():
        return getattr(m, handler_name)(_event(method, path_suffix, body))


@pytest.mark.unit
class TestEmptyTokenListDenies:
    """Rule 4, single resource: no identity means deny before the enforcer is consulted."""

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,archived,action",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_empty_tokens_return_403_without_consulting_casbin(
        self, site, handler_name, method, path_suffix, body, archived, action
    ):
        m, spy, undo = _wire(archived=archived, tokens=())
        try:
            response = _invoke(m, handler_name, method, path_suffix, body)
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"{site}: an unauthenticated request returned {response['statusCode']} "
            f"instead of 403: {response}"
        )
        assert spy.constructions == [], (
            f"{site}: CasbinEnforcer was constructed for an empty token list; with no "
            f"identity the request must be refused before authorization is evaluated"
        )
        assert spy.calls == [], f"{site}: enforce() ran with no identity: {spy.calls}"

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,archived,action",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_empty_tokens_write_nothing(
        self, site, handler_name, method, path_suffix, body, archived, action
    ):
        m, spy, undo = _wire(archived=archived, tokens=())
        try:
            _invoke(m, handler_name, method, path_suffix, body)
        finally:
            undo()

        m.asset_table.update_item.assert_not_called()
        m.asset_table.delete_item.assert_not_called()


@pytest.mark.unit
class TestDenialSurfacesAs403NotAs500:
    """A Tier-2 refusal is a documented 403; it must not arrive as an internal error."""

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,archived,action",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_a_denied_caller_gets_403(
        self, site, handler_name, method, path_suffix, body, archived, action
    ):
        m, spy, undo = _wire(archived=archived, denied_actions=(action,))
        try:
            response = _invoke(m, handler_name, method, path_suffix, body)
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"{site}: a Tier-2 denial surfaced as {response['statusCode']}; a denial "
            f"returned as a response dict and then handed to result.dict() becomes a 500: "
            f"{response}"
        )
        assert json.loads(response["body"])["message"] == "Not Authorized"
        m.asset_table.update_item.assert_not_called()
        m.asset_table.delete_item.assert_not_called()

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,archived,action",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_the_refusal_was_decided_on_the_asset(
        self, site, handler_name, method, path_suffix, body, archived, action
    ):
        """The verdict is only meaningful if the object carries the fields that scope it."""
        m, spy, undo = _wire(archived=archived, denied_actions=(action,))
        try:
            _invoke(m, handler_name, method, path_suffix, body)
        finally:
            undo()

        assert spy.calls, f"{site}: nothing was enforced at all"
        refused = spy.calls[-1]
        assert refused["action"] == action, (
            f"{site}: the refusal came from a {refused['action']} evaluation, expected "
            f"{action}. Actions evaluated: {[call['action'] for call in spy.calls]}"
        )
        assert refused["object"]["object__type"] == "asset"
        assert refused["object"]["databaseId"] == _DB
        assert refused["object"]["assetId"] == _ASSET


@pytest.mark.unit
class TestPermittedCallerStillSucceeds:
    """Positive control: every denial assertion above is also satisfied by a broken deny-all."""

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,archived,action",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_a_permitted_caller_gets_200(
        self, site, handler_name, method, path_suffix, body, archived, action
    ):
        m, spy, undo = _wire(archived=archived)
        try:
            response = _invoke(m, handler_name, method, path_suffix, body)
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"{site}: an authorized request was refused: {response}"
        )
        assert spy.calls, f"{site}: the request succeeded with nothing enforced at all"

    def test_the_tag_change_is_written_when_permitted(self):
        """The tag gate must not block an ordinary retag for an authorized caller."""
        m, spy, undo = _wire()
        try:
            response = _invoke(
                m, "handle_put_request", "PUT", "", {"tags": ["added"]}
            )
        finally:
            undo()

        assert response["statusCode"] == 200, response
        assert _written_attributes(m.asset_table)["tags"] == ["added"]


@pytest.mark.unit
class TestTier1StillDeniesEmptyTokens:
    """The upstream control that masks the Tier-2 defect in production stays in place."""

    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    def test_lambda_handler_denies_an_empty_token_list(self, method):
        m, spy, undo = _wire(tokens=())
        try:
            with patch.object(
                m, "request_to_claims", MagicMock(return_value={"tokens": []})
            ):
                response = m.lambda_handler(
                    _event(method, "", {"description": "a changed description"}),
                    MagicMock(),
                )
        finally:
            undo()

        assert response["statusCode"] == 403
        assert spy.constructions == [], (
            f"Tier 1 constructed an enforcer for an empty token list: {spy.constructions}"
        )
        m.asset_table.update_item.assert_not_called()
