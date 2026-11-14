"""The asset file, upload and asset version write clients report the API's 403 for a refused asset as a
permission refusal (issue #959).

The file and version write routes answered a refused asset with 400 "VAMS General Error: Not authorized
...", which the clients reported as invalid data or a failed operation. They now answer 403
{"message": "Not Authorized"}, as `assets get` does; these tests pin that each client method reports
it with its status, as the `assets get` client does.
"""

import pytest

from vamscli.utils.exceptions import APIError

from tests.asset_status_wire import HANDLER_DENY, _client, _response, _Wire

DB = "db-a"
ASSET = "xasset-1"
FILE = {"filePath": "/model.glb"}

CALLS = [
    pytest.param(lambda c: c.get_asset(DB, ASSET), id="assets-get-reference"),
    pytest.param(lambda c: c.initialize_upload(DB, ASSET, "assetFile", []), id="upload-initialize"),
    pytest.param(lambda c: c.complete_upload("upload-1", DB, ASSET, "assetFile", []), id="upload-complete"),
    pytest.param(lambda c: c.create_folder(DB, ASSET, {"relativeKey": "/folder/"}), id="file-create-folder"),
    pytest.param(
        lambda c: c.move_file(DB, ASSET, {"sourcePath": "/a.glb", "destinationPath": "/b.glb"}), id="file-move"
    ),
    pytest.param(
        lambda c: c.copy_file(DB, ASSET, {"sourcePath": "/a.glb", "destinationPath": "/b.glb"}), id="file-copy"
    ),
    pytest.param(lambda c: c.archive_file(DB, ASSET, FILE), id="file-archive"),
    pytest.param(lambda c: c.unarchive_file(DB, ASSET, FILE), id="file-unarchive"),
    pytest.param(lambda c: c.delete_file(DB, ASSET, {**FILE, "confirmPermanentDelete": True}), id="file-delete"),
    pytest.param(lambda c: c.revert_file_version(DB, ASSET, "v1", FILE), id="file-revert"),
    pytest.param(lambda c: c.set_primary_file(DB, ASSET, {**FILE, "primaryType": ""}), id="file-set-primary"),
    pytest.param(lambda c: c.delete_asset_preview(DB, ASSET), id="asset-preview-delete"),
    pytest.param(lambda c: c.delete_auxiliary_preview_files(DB, ASSET, FILE), id="auxiliary-preview-delete"),
    pytest.param(
        lambda c: c.create_asset_version(DB, ASSET, {"useLatestFiles": True, "comment": "c"}), id="version-create"
    ),
    pytest.param(lambda c: c.revert_asset_version(DB, ASSET, "1", {"comment": "c"}), id="version-revert"),
    pytest.param(lambda c: c.update_asset_version(DB, ASSET, "1", {"comment": "c"}), id="version-update"),
    pytest.param(lambda c: c.archive_asset_version(DB, ASSET, "1"), id="version-archive"),
    pytest.param(lambda c: c.unarchive_asset_version(DB, ASSET, "1"), id="version-unarchive"),
]


@pytest.mark.parametrize("call", CALLS)
def test_a_refused_asset_is_a_permission_refusal(monkeypatch, call):
    client = _client()
    wire = _Wire(monkeypatch, client, _response(HANDLER_DENY))

    with pytest.raises(APIError) as excinfo:
        call(client)

    # The client refreshes the token and resends on a 403 up to three times before it reports the refusal.
    assert "(403): Not Authorized" in str(excinfo.value)
    assert (len(wire.sends), wire.refreshes) == (4, 3)
