"""The asset file list, file info, version list, version get and export clients report the API's 403
and 404 for these routes as a permission refusal and a missing asset (issues #959, #1017).

The API answered both cases with 400, which the clients reported as a failed request or as invalid
export parameters. It now answers a refused asset with 403 {"message": "Not Authorized"} and a missing
asset with 404 {"message": "Asset not found"}, as `assets get` does; these tests pin that each client
method reports those two responses with their status, as the `assets get` client does.
"""

import pytest

from vamscli.utils.exceptions import APIError, PermissionDeniedError, TokenExpiredError

from tests.asset_status_wire import HANDLER_DENY, _client, _response, _Wire

DB = "db-a"
ASSET = "xasset-1"

CALLS = [
    pytest.param(lambda c: c.get_asset(DB, ASSET), id="assets-get-reference"),
    pytest.param(lambda c: c.list_asset_files(DB, ASSET), id="file-list"),
    pytest.param(lambda c: c.get_file_info(DB, ASSET, {"filePath": "/model.glb"}), id="file-info"),
    pytest.param(lambda c: c.get_asset_versions(DB, ASSET), id="asset-version-list"),
    pytest.param(lambda c: c.get_asset_version(DB, ASSET, "1"), id="asset-version-get"),
    pytest.param(lambda c: c.export_asset(DB, ASSET, {}), id="assets-export"),
]


def _not_found():
    response = _response({"message": "Asset not found"}, status=404)
    response.reason = "Not Found"
    return response


@pytest.mark.parametrize("call", CALLS)
def test_a_refused_asset_is_a_permission_refusal(monkeypatch, call):
    client = _client()
    wire = _Wire(monkeypatch, client, _response(HANDLER_DENY))

    with pytest.raises(APIError) as excinfo:
        call(client)

    # The client refreshes the token and resends on a 403 up to three times before it reports the refusal.
    assert "(403): Not Authorized" in str(excinfo.value)
    assert (len(wire.sends), wire.refreshes) == (4, 3)


@pytest.mark.parametrize("call", CALLS)
def test_a_refused_asset_with_a_failed_refresh_is_access_forbidden(monkeypatch, call):
    client = _client()
    wire = _Wire(monkeypatch, client, _response(HANDLER_DENY), refreshes_succeed=False)

    # A refresh that fails on a token that has not expired is reported as the client's own refusal
    # text, without a resend; the client methods wrap it in their own "Failed to ..." APIError.
    with pytest.raises((PermissionDeniedError, APIError)) as excinfo:
        call(client)

    assert "Access forbidden. You do not have permission to perform this action." in str(excinfo.value)
    assert (len(wire.sends), wire.refreshes) == (1, 1)


@pytest.mark.parametrize("call", CALLS)
def test_a_refused_asset_with_a_failed_refresh_on_an_expired_token_asks_to_log_in(monkeypatch, call):
    client = _client(token_expired=True)
    wire = _Wire(monkeypatch, client, _response(HANDLER_DENY), refreshes_succeed=False)

    # Some clients report it wrapped in their own "Failed to ..." APIError; either way it says to log in.
    with pytest.raises((TokenExpiredError, APIError)) as excinfo:
        call(client)

    assert "Please run 'vamscli auth login' to re-authenticate." in str(excinfo.value)
    assert (len(wire.sends), wire.refreshes) == (1, 1)


@pytest.mark.parametrize("call", CALLS)
def test_a_missing_asset_is_reported_as_not_found(monkeypatch, call):
    client = _client()
    _Wire(monkeypatch, client, _not_found())

    with pytest.raises(APIError) as excinfo:
        call(client)

    assert "Resource not found (404): Asset not found" in str(excinfo.value)
