# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset file and version write routes answer an object-level denial with 403, not 400 (issue #959).

The asset file routes (delete, archive, unarchive, copy, move, revert, create folder, set primary,
delete previews) and the asset version write routes (create, revert, update, archive, unarchive)
raised a Tier 2 refusal as a plain `VAMSGeneralErrorResponse`, which their handlers answer with
`general_error` (400). They now answer 403 `{"message": "Not Authorized"}`, as the asset GET and
the asset update, archive and delete routes do.

The refusal is decided before existence: a refused caller receives the same status and body
whether or not the asset exists. A permitted caller asking for a missing asset keeps the route's
existing not-found answer (400 "Asset not found in database"); only the refusal moves. Each route
is checked for the refused caller on an existing and on a missing asset, and for the permitted
caller, who must get past the object check, because "403" alone is satisfied by a handler that
refuses everything.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.assets import assetFiles as _assetFiles
from backend.backend.handlers.assets import assetVersions as _assetVersions
from tests.handlers.assets.test_asset_reads_denied_403 import STORED_ASSET as _READ_STORED_ASSET
from tests.handlers.assets.test_asset_reads_denied_403 import (
    ASSET_ID,
    AUTHENTICATED,
    DATABASE_ID,
    VERSION_ID,
    _Enforcer,
    _event,
)

OTHER_ASSET_ID = "xasset-2"
STORED_ASSET = {**_READ_STORED_ASSET, "previewLocation": {"Key": f"{ASSET_ID}/preview.png"}}


# (route id, module, handler name, event)
ROUTES = [
    (
        "file_delete",
        _assetFiles,
        "handle_delete_file",
        _event("DELETE", "/deleteFile", body={"filePath": "/model.glb", "confirmPermanentDelete": True}),
    ),
    ("file_archive", _assetFiles, "handle_archive_file", _event("DELETE", "/archiveFile", body={"filePath": "/model.glb"})),
    ("file_unarchive", _assetFiles, "handle_unarchive_file", _event("POST", "/unarchiveFile", body={"filePath": "/model.glb"})),
    (
        "file_copy",
        _assetFiles,
        "handle_copy_file",
        _event("POST", "/copyFile", body={"sourcePath": "/model.glb", "destinationPath": "/copy.glb"}),
    ),
    (
        "file_copy_cross_asset",
        _assetFiles,
        "handle_copy_file",
        _event(
            "POST",
            "/copyFile",
            body={"sourcePath": "/model.glb", "destinationPath": "/model.glb", "destinationAssetId": OTHER_ASSET_ID},
        ),
    ),
    (
        "file_move",
        _assetFiles,
        "handle_move_file",
        _event("POST", "/moveFile", body={"sourcePath": "/model.glb", "destinationPath": "/moved.glb"}),
    ),
    (
        "file_revert",
        _assetFiles,
        "handle_revert_file_version",
        _event("POST", "/revertFileVersion/v1", body={"filePath": "/model.glb"}, extra_path_params={"versionId": "v1"}),
    ),
    ("folder_create", _assetFiles, "handle_create_folder", _event("POST", "/createFolder", body={"relativeKey": "/folder/"})),
    (
        "file_set_primary",
        _assetFiles,
        "handle_set_primary_file",
        _event("PUT", "/setPrimaryFile", body={"filePath": "/model.glb", "primaryType": ""}),
    ),
    ("asset_preview_delete", _assetFiles, "handle_delete_asset_preview", _event("DELETE", "/deleteAssetPreview")),
    (
        "auxiliary_preview_delete",
        _assetFiles,
        "handle_delete_auxiliary_preview_asset_files",
        _event("DELETE", "/deleteAuxiliaryPreviewAssetFiles", body={"filePath": "/model.glb"}),
    ),
    (
        "version_create",
        _assetVersions,
        "handle_create_version",
        _event("POST", "/createVersion", body={"useLatestFiles": True, "comment": "c"}),
    ),
    (
        "version_revert",
        _assetVersions,
        "handle_revert_version",
        _event("POST", f"/revertAssetVersion/{VERSION_ID}", body={"comment": "c"}, extra_path_params={"assetVersionId": VERSION_ID}),
    ),
    (
        "version_update",
        _assetVersions,
        "handle_update_asset_version",
        _event("PUT", f"/assetversions/{VERSION_ID}", body={"comment": "c"}, extra_path_params={"assetVersionId": VERSION_ID}),
    ),
    (
        "version_archive",
        _assetVersions,
        "handle_archive_asset_version",
        _event("POST", f"/assetversions/{VERSION_ID}/archive", body={}, extra_path_params={"assetVersionId": VERSION_ID}),
    ),
    (
        "version_unarchive",
        _assetVersions,
        "handle_unarchive_asset_version",
        _event("POST", f"/assetversions/{VERSION_ID}/unarchive", body={}, extra_path_params={"assetVersionId": VERSION_ID}),
    ),
]
ROUTE_IDS = [route[0] for route in ROUTES]


class _PastTheGate(Exception):
    """Raised right after the last object check returned, so the test needs no downstream fakes."""


@pytest.fixture
def route(request, monkeypatch):
    route_id, module, handler_name, event = request.param
    _Enforcer.object_verdict = True
    _Enforcer.object_calls = []
    monkeypatch.setattr(module, "CasbinEnforcer", _Enforcer)
    monkeypatch.setattr(module, "request_to_claims", lambda event: AUTHENTICATED)

    table = MagicMock()
    monkeypatch.setattr(module, "asset_table", table)

    # Stop at the first step after authorization: the S3 location lookup (file routes) or the
    # version existence check (version routes).
    gate_results = []

    def past_the_gate(*args, **kwargs):
        gate_results.append(args)
        raise _PastTheGate()

    monkeypatch.setattr(module, "get_asset_s3_location", past_the_gate)
    if module is _assetFiles:
        monkeypatch.setattr(module, "get_default_bucket_details", past_the_gate)
    else:
        monkeypatch.setattr(module, "validate_asset_version_exists", past_the_gate)

    def call(stored_asset):
        """Answer the request with `stored_asset` as every stored record (None: no record)."""
        def get_item(Key):
            if not stored_asset:
                return {}
            return {"Item": {**stored_asset, "assetId": Key["assetId"], "databaseId": Key["databaseId"]}}

        table.get_item.side_effect = get_item
        return getattr(module, handler_name)(json.loads(json.dumps(event)), MagicMock())

    return route_id, call, gate_results


@pytest.mark.unit
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS, indirect=True)
class TestAssetWriteDenialIs403:
    def test_authorized_caller_passes_the_object_check(self, route):
        """Positive control: the permitted caller reaches the work after the check."""
        _, call, gate_results = route

        call(STORED_ASSET)

        assert len(gate_results) == 1
        assert ("asset", DATABASE_ID, ASSET_ID) in {c[:3] for c in _Enforcer.object_calls}

    def test_refused_existing_asset_answers_403(self, route):
        _, call, gate_results = route
        _Enforcer.object_verdict = False

        response = call(STORED_ASSET)

        assert response["statusCode"] == 403
        assert json.loads(response["body"]) == {"message": "Not Authorized"}
        assert gate_results == []

    def test_refused_missing_asset_answers_exactly_as_a_refused_existing_one(self, route):
        _, call, gate_results = route
        _Enforcer.object_verdict = False

        existing = call(STORED_ASSET)
        missing = call(None)

        assert missing["statusCode"] == existing["statusCode"] == 403
        assert missing["body"] == existing["body"]
        assert gate_results == []
        # The missing asset was decided through the identifiers the request supplied
        assert ("asset", DATABASE_ID, ASSET_ID) in {c[:3] for c in _Enforcer.object_calls}

    def test_missing_asset_keeps_its_not_found_answer_for_a_permitted_caller(self, route):
        _, call, gate_results = route

        response = call(None)

        assert response["statusCode"] == 400
        assert "Asset not found in database" in json.loads(response["body"])["message"]
        assert gate_results == []


@pytest.mark.unit
class TestCrossAssetCopyRefusals:
    """A cross-asset copy is refused with 403 when either side refuses, whichever side it is."""

    @pytest.fixture
    def copy(self, monkeypatch):
        verdicts = {}

        class Enforcer(_Enforcer):
            def enforce(self, obj, act):
                return verdicts.get((obj.get("assetId"), act), True)

        monkeypatch.setattr(_assetFiles, "CasbinEnforcer", Enforcer)
        monkeypatch.setattr(_assetFiles, "request_to_claims", lambda event: AUTHENTICATED)
        table = MagicMock()
        table.get_item.side_effect = lambda Key: {"Item": {**STORED_ASSET, "assetId": Key["assetId"]}}
        monkeypatch.setattr(_assetFiles, "asset_table", table)
        monkeypatch.setattr(_assetFiles, "get_asset_s3_location", MagicMock(side_effect=_PastTheGate()))
        event = _event(
            "POST",
            "/copyFile",
            body={"sourcePath": "/model.glb", "destinationPath": "/model.glb", "destinationAssetId": OTHER_ASSET_ID},
        )

        def call(**refused):
            verdicts.clear()
            verdicts.update({key: False for key in refused.get("refuse", [])})
            return _assetFiles.handle_copy_file(json.loads(json.dumps(event)), MagicMock())

        return call

    @pytest.mark.parametrize(
        "refuse",
        [[(ASSET_ID, "GET")], [(OTHER_ASSET_ID, "POST")]],
        ids=["source_read_refused", "destination_write_refused"],
    )
    def test_either_side_refused_answers_403(self, copy, refuse):
        response = copy(refuse=refuse)

        assert response["statusCode"] == 403
        assert json.loads(response["body"]) == {"message": "Not Authorized"}

    def test_cross_asset_permission_check_raises_the_refusal_type(self):
        VAMSAuthorizationDeniedResponse = _assetFiles.VAMSAuthorizationDeniedResponse

        class Refuse(_Enforcer):
            def enforce(self, obj, act):
                return act == "GET"

        original = _assetFiles.CasbinEnforcer
        _assetFiles.CasbinEnforcer = Refuse
        try:
            with pytest.raises(VAMSAuthorizationDeniedResponse) as raised:
                _assetFiles.validate_cross_asset_permissions(STORED_ASSET, STORED_ASSET, AUTHENTICATED)
        finally:
            _assetFiles.CasbinEnforcer = original
        # A route that does not map the refusal still answers 400 with the same message
        assert "Not authorized to write to destination asset" in str(raised.value)
