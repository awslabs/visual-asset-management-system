"""The asset file list, file info, version list, version get and export clients report the API's 403
and 404 for these routes as a permission refusal and a missing asset (issues #959, #1017).

The API answered both cases with 400, which the clients reported as a failed request or as invalid
export parameters. It now answers a refused asset with 403 {"message": "Not Authorized"} and a missing
asset with 404 {"message": "Asset not found"}, as `assets get` does; these tests pin that each client
method reports those two responses with their status, as the `assets get` client does.
"""

import pytest

from vamscli.utils.exceptions import APIError

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

    # On 2.6.7 the client refreshes the token and resends on a 403 before it reports the refusal
    # (development reports a handler refusal at once, #539), so only the reported status is pinned here.
    assert "(403): Not Authorized" in str(excinfo.value)
    assert wire.sends


@pytest.mark.parametrize("call", CALLS)
def test_a_missing_asset_is_reported_as_not_found(monkeypatch, call):
    client = _client()
    _Wire(monkeypatch, client, _not_found())

    with pytest.raises(APIError) as excinfo:
        call(client)

    assert "Resource not found (404): Asset not found" in str(excinfo.value)
