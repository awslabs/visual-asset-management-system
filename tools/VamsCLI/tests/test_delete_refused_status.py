"""The permanent asset delete and the file delete clients report the API's 400 as the methods' own refusal
(issues #1768, #1782), through the real request path.

A permanent delete that Amazon S3 refuses part of answers 400 with the storage message. The clients map a 400 to
InvalidAssetDataError, which the commands show as "Delete Refused" with the hint. These tests send the 400 through
`_make_request` (only the HTTP session is stubbed), so a client that lets `_make_request` turn the 400 into a generic
APIError first fails here.
"""

import pytest

from vamscli.utils.exceptions import APIError, AssetDeletionError, InvalidAssetDataError

from tests.test_api_403_classification import _client, _response, _Wire

DB = "db-a"
ASSET = "xasset-1"
STORAGE_REFUSED = {"message": "VAMS General Error: Error permanently deleting asset: the asset's storage could "
                              "not be fully deleted"}

CALLS = [
    pytest.param(lambda c: c.delete_asset_permanent(DB, ASSET, reason="r", confirm=True), id="asset-delete"),
    pytest.param(
        lambda c: c.delete_file(DB, ASSET, {"filePath": "/model.glb", "confirmPermanentDelete": True}),
        id="file-delete"),
]


@pytest.mark.parametrize("call", CALLS)
def test_a_refused_delete_is_the_methods_own_refusal(monkeypatch, call):
    client = _client()
    wire = _Wire(monkeypatch, client, _response(STORAGE_REFUSED, status=400))

    with pytest.raises(InvalidAssetDataError) as excinfo:
        call(client)

    assert not isinstance(excinfo.value, AssetDeletionError)
    assert str(excinfo.value) == f"Invalid delete operation: {STORAGE_REFUSED['message']}"
    assert (len(wire.sends), wire.refreshes) == (1, 0)


def test_a_missing_confirmation_is_still_a_deletion_error(monkeypatch):
    client = _client()
    _Wire(monkeypatch, client, _response({"message": "Permanent deletion requires confirmation"}, status=400))

    with pytest.raises(AssetDeletionError):
        client.delete_asset_permanent(DB, ASSET, confirm=False)


@pytest.mark.parametrize("call", CALLS)
def test_a_server_error_is_still_an_api_error(monkeypatch, call):
    client = _client()
    _Wire(monkeypatch, client, _response({"message": "Internal Server Error"}, status=500))

    with pytest.raises(APIError) as excinfo:
        call(client)

    assert not isinstance(excinfo.value, InvalidAssetDataError)
