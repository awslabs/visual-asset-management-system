# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest

# Module-level import ensures the real `backend.backend.handlers` package is
# populated in sys.modules before the root conftest's autouse fixture runs,
# preventing it from stubbing the package with a MockModule.
from backend.backend.handlers.addon.physna import physnaCommon as _pc  # noqa: F401


_VIEWER_3D_EXTENSIONS = (
    "step",
    "stp",
    "igs",
    "iges",
    "stl",
    "obj",
    "3ds",
    "asm",
    "catpart",
    "catproduct",
    "glb",
    "iam",
    "ipt",
    "jt",
    "par",
    "prt",
    "sldasm",
    "sldprt",
    "x_b",
    "x_t",
)

_DOCUMENT_IMAGE_EXTENSIONS = ("txt", "pdf", "gif", "jpeg", "jpg", "png")

# Extensions Physna does not accept at all — neither sync nor viewer.
_PHYSNA_REJECTED_EXTENSIONS = (
    "ifc",
    "ply",
    "sat",
    "3mf",
    "fbx",
    "dae",
    "dwg",
    "dxf",
    "gltf",
)


@pytest.mark.unit
class TestSyncSupportedExtension:
    """The sync gate covers everything Physna accepts: 3D/CAD + docs + images."""

    def test_3d_cad_formats_are_sync_supported(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            is_sync_supported_file,
        )

        assert is_sync_supported_file("/path/to/part.step") is True
        assert is_sync_supported_file("/path/to/part.STEP") is True
        for ext in _VIEWER_3D_EXTENSIONS:
            assert is_sync_supported_file(f"/file.{ext}") is True

    def test_documents_and_images_are_sync_supported(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            is_sync_supported_file,
        )

        for ext in _DOCUMENT_IMAGE_EXTENSIONS:
            assert is_sync_supported_file(f"/file.{ext}") is True
            assert is_sync_supported_file(f"/file.{ext.upper()}") is True

    def test_formats_physna_rejects_are_not_sync_supported(self):
        # Regression: these extensions must never be pushed to Physna. Physna's
        # upload endpoint rejects them server-side with HTTP 400 "Invalid path
        # extension". .ifc in particular triggered the original bug. Note .glb
        # IS supported but .gltf is NOT.
        from backend.backend.handlers.addon.physna.physnaCommon import (
            is_sync_supported_file,
        )

        for ext in _PHYSNA_REJECTED_EXTENSIONS:
            assert is_sync_supported_file(f"/file.{ext}") is False

    def test_file_with_no_extension_not_sync_supported(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            is_sync_supported_file,
        )

        assert is_sync_supported_file("/README") is False


@pytest.mark.unit
class TestViewerSupportedExtension:
    """The viewer gate is 3D/CAD only — docs and images are synced but not
    rendered by the embedded Physna Viewer."""

    def test_3d_cad_formats_are_viewer_supported(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            is_viewer_supported_file,
        )

        assert is_viewer_supported_file("/path/to/part.STEP") is True
        for ext in _VIEWER_3D_EXTENSIONS:
            assert is_viewer_supported_file(f"/file.{ext}") is True

    def test_documents_and_images_are_not_viewer_supported(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            is_viewer_supported_file,
        )

        for ext in _DOCUMENT_IMAGE_EXTENSIONS:
            assert is_viewer_supported_file(f"/file.{ext}") is False

    def test_rejected_formats_are_not_viewer_supported(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            is_viewer_supported_file,
        )

        for ext in _PHYSNA_REJECTED_EXTENSIONS:
            assert is_viewer_supported_file(f"/file.{ext}") is False


@pytest.mark.unit
class TestBuildPhysnaPath:
    def test_joins_with_leading_slash_stripped(self):
        from backend.backend.handlers.addon.physna.physnaCommon import build_physna_path

        result = build_physna_path("db-1", "asset-1", "/sub/part.step")
        assert result == "db-1/asset-1/sub/part.step"

    def test_joins_when_relative_path_has_no_leading_slash(self):
        from backend.backend.handlers.addon.physna.physnaCommon import build_physna_path

        result = build_physna_path("db-1", "asset-1", "sub/part.step")
        assert result == "db-1/asset-1/sub/part.step"


@pytest.mark.unit
class TestBuildPhysnaFolderAndFilename:
    def test_folder_path_and_filename(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            build_physna_folder_path,
            build_physna_filename,
        )

        assert (
            build_physna_folder_path("db-1", "asset-1", "/sub/part.step")
            == "db-1/asset-1/sub"
        )
        assert build_physna_filename("/sub/part.step") == "part.step"

    def test_file_at_asset_root_has_empty_folder_beyond_asset(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            build_physna_folder_path,
        )

        assert build_physna_folder_path("db-1", "asset-1", "/part.step") == "db-1/asset-1"


@pytest.mark.unit
class TestMergeMetadata:
    """File attributes land in their own `Attribute_`-prefixed namespace
    on the Physna side so they never collide with same-named metadata
    keys. Asset-level metadata and file-level metadata still share a
    namespace (Physna treats them both as plain keys), and file-level
    wins on a conflict as the more specific source.
    """

    def test_attributes_and_metadata_with_same_key_are_kept_separately(self):
        """Without the Attribute_ prefix, a file attribute and a file /
        asset metadata key with the same name would overwrite each other
        on the Physna side. Prefixing keeps both present on the asset."""
        from backend.backend.handlers.addon.physna.physnaCommon import merge_metadata

        asset = {"color": {"value": "red", "type": "string"}}
        file_attrs = {"color": {"value": "blue", "type": "string"}}
        file_meta = {"color": {"value": "green", "type": "string"}}

        merged = merge_metadata(asset, file_meta, file_attrs)
        # file_metadata wins on the unprefixed "color" key
        assert merged["color"]["value"] == "green"
        # file attributes land under the prefixed key
        assert merged["Attribute_color"]["value"] == "blue"
        # Both keys must coexist — neither evicts the other.
        assert set(merged.keys()) == {"color", "Attribute_color"}

    def test_file_metadata_wins_over_asset_metadata_when_attributes_absent(self):
        """With file attributes out of the picture, file-level metadata
        still wins over asset-level metadata on the same (unprefixed)
        key."""
        from backend.backend.handlers.addon.physna.physnaCommon import merge_metadata

        asset = {"material": {"value": "steel", "type": "string"}}
        file_meta = {"material": {"value": "aluminum", "type": "string"}}

        merged = merge_metadata(asset, file_meta, None)
        assert merged["material"]["value"] == "aluminum"

    def test_attribute_prefix_is_idempotent(self):
        """If a caller already passes an ``Attribute_``-prefixed dict
        (e.g. during a round-trip comparison against values previously
        stored in Physna), we must not double-prefix the key."""
        from backend.backend.handlers.addon.physna.physnaCommon import merge_metadata

        file_attrs = {
            "Attribute_weight": {"value": "10", "type": "number"},
        }
        merged = merge_metadata(None, None, file_attrs)
        assert "Attribute_weight" in merged
        assert "Attribute_Attribute_weight" not in merged

    def test_union_of_distinct_keys_preserved_with_prefix(self):
        from backend.backend.handlers.addon.physna.physnaCommon import merge_metadata

        asset = {"a": {"value": "1", "type": "string"}}
        file_attrs = {"b": {"value": "2", "type": "string"}}
        file_meta = {"c": {"value": "3", "type": "string"}}

        merged = merge_metadata(asset, file_meta, file_attrs)
        assert set(merged.keys()) == {"a", "Attribute_b", "c"}


@pytest.mark.unit
class TestMapVamsTypeToPhysna:
    def test_string_passthrough(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            map_vams_type_to_physna,
        )

        assert map_vams_type_to_physna("string") == "string"

    def test_number_and_boolean_supported(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            map_vams_type_to_physna,
        )

        assert map_vams_type_to_physna("number") == "number"
        assert map_vams_type_to_physna("boolean") == "boolean"

    def test_unsupported_vams_types_fallback_to_string(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            map_vams_type_to_physna,
        )

        for vams_type in ("geopoint", "json", "xyz", "wxyz", "matrix4x4", "lla", "geojson"):
            assert map_vams_type_to_physna(vams_type) == "string", (
                f"Expected {vams_type!r} to fall back to 'string'"
            )

    def test_unknown_type_falls_back_to_string(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            map_vams_type_to_physna,
        )

        assert map_vams_type_to_physna("made-up-type") == "string"


@pytest.mark.unit
class TestPhysnaFormatMetadata:
    def test_converts_merged_to_flat_object(self):
        """Physna requires metadata as a flat ``{key: value}`` object, not a
        list. Unsupported VAMS types are coerced to a string representation
        of the value (type hint not sent — Physna infers from the value)."""
        from backend.backend.handlers.addon.physna.physnaCommon import (
            physna_format_metadata,
        )

        merged = {
            "partName": {"value": "widget-01", "type": "string"},
            "weightKg": {"value": 12, "type": "number"},
            "geoLoc": {"value": '{"lat":1,"lon":2}', "type": "geopoint"},
        }

        formatted = physna_format_metadata(merged)
        assert isinstance(formatted, dict)
        assert formatted["partName"] == "widget-01"
        # Number type passes through as-is for Physna-native number type
        assert formatted["weightKg"] == 12
        # Fallback string type coerces to string form
        assert formatted["geoLoc"] == '{"lat":1,"lon":2}'

    def test_non_string_value_with_fallback_type_is_stringified(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            physna_format_metadata,
        )

        merged = {"coord": {"value": {"x": 1, "y": 2}, "type": "xyz"}}
        formatted = physna_format_metadata(merged)
        assert isinstance(formatted["coord"], str)


@pytest.mark.unit
class TestApplyVamsReservedMetadata:
    """VAMS-reserved keys always overwrite same-named user metadata because
    they reflect VAMS truth, not user-entered values. None values leave the
    corresponding key out of the payload entirely."""

    def test_both_values_overwrite_user_keys(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            apply_vams_reserved_metadata,
            VAMS_RESERVED_ASSET_NAME_KEY,
            VAMS_RESERVED_FILE_VERSION_KEY,
        )

        payload = {
            "__VAMS__AssetName": "user-entered-bogus-value",
            "color": "red",
        }
        apply_vams_reserved_metadata(payload, "Real Asset Name", "v-123")
        assert payload[VAMS_RESERVED_ASSET_NAME_KEY] == "Real Asset Name"
        assert payload[VAMS_RESERVED_FILE_VERSION_KEY] == "v-123"
        assert payload["color"] == "red"

    def test_none_asset_name_leaves_key_absent(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            apply_vams_reserved_metadata,
            VAMS_RESERVED_ASSET_NAME_KEY,
            VAMS_RESERVED_FILE_VERSION_KEY,
        )

        payload = {"color": "red"}
        apply_vams_reserved_metadata(payload, None, "v-1")
        assert VAMS_RESERVED_ASSET_NAME_KEY not in payload
        assert payload[VAMS_RESERVED_FILE_VERSION_KEY] == "v-1"

    def test_none_file_version_leaves_key_absent(self):
        """Used by metadata-only update paths to preserve Physna's existing
        __VAMS__FileVersion rather than overwrite it."""
        from backend.backend.handlers.addon.physna.physnaCommon import (
            apply_vams_reserved_metadata,
            VAMS_RESERVED_ASSET_NAME_KEY,
            VAMS_RESERVED_FILE_VERSION_KEY,
        )

        payload = {}
        apply_vams_reserved_metadata(payload, "My Asset", None)
        assert payload[VAMS_RESERVED_ASSET_NAME_KEY] == "My Asset"
        assert VAMS_RESERVED_FILE_VERSION_KEY not in payload

    def test_numeric_values_are_stringified(self):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            apply_vams_reserved_metadata,
            VAMS_RESERVED_FILE_VERSION_KEY,
        )

        payload = {}
        apply_vams_reserved_metadata(payload, "name", 42)
        assert payload[VAMS_RESERVED_FILE_VERSION_KEY] == "42"


@pytest.mark.unit
class TestPhysnaClientTokenCaching:
    """PhysnaClient caches OAuth tokens in module memory and refreshes on 401."""

    def _install_fakes(self, monkeypatch, *, token_values, api_responses):
        """Install a fake urllib3 PoolManager and fake SecretsManager.

        token_values: list of (status, body_dict) tuples for successive token POSTs.
        api_responses: list of (status, body_dict) tuples for successive API calls.
        """
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        # Reset module-level cache state
        pc._reset_client_state_for_tests()

        # Fake secret retrieval
        def fake_load_secret():
            return {"clientId": "test-client", "clientSecret": "test-secret"}

        monkeypatch.setattr(pc, "_load_physna_credentials", fake_load_secret)

        # Fake HTTP layer
        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                import json as _json

                self.data = _json.dumps(body).encode("utf-8")

        token_iter = iter(token_values)
        api_iter = iter(api_responses)

        calls = {"token": 0, "api": 0}

        def fake_token_post():
            calls["token"] += 1
            status, body = next(token_iter)
            return FakeResponse(status, body)

        def fake_api_request(method, path, **kwargs):
            calls["api"] += 1
            status, body = next(api_iter)
            return FakeResponse(status, body)

        monkeypatch.setattr(pc, "_http_post_token", lambda client_id, client_secret: fake_token_post())
        monkeypatch.setattr(pc, "_http_request", lambda method, url, **kwargs: fake_api_request(method, url, **kwargs))

        return calls

    def test_first_request_fetches_token_then_calls_api(self, monkeypatch):
        from backend.backend.handlers.addon.physna.physnaCommon import PhysnaClient

        calls = self._install_fakes(
            monkeypatch,
            token_values=[(200, {"access_token": "tok-1", "expires_in": 3600})],
            api_responses=[(200, {"ok": True})],
        )
        client = PhysnaClient()
        response = client.request("GET", "/tenants/abc/assets")
        assert response.status == 200
        assert calls["token"] == 1
        assert calls["api"] == 1

    def test_second_request_reuses_cached_token(self, monkeypatch):
        from backend.backend.handlers.addon.physna.physnaCommon import PhysnaClient

        calls = self._install_fakes(
            monkeypatch,
            token_values=[(200, {"access_token": "tok-1", "expires_in": 3600})],
            api_responses=[(200, {"ok": True}), (200, {"ok": True})],
        )
        client = PhysnaClient()
        client.request("GET", "/a")
        client.request("GET", "/b")
        assert calls["token"] == 1
        assert calls["api"] == 2

    def test_401_response_invalidates_token_and_retries_once(self, monkeypatch):
        from backend.backend.handlers.addon.physna.physnaCommon import PhysnaClient

        calls = self._install_fakes(
            monkeypatch,
            token_values=[
                (200, {"access_token": "tok-1", "expires_in": 3600}),
                (200, {"access_token": "tok-2", "expires_in": 3600}),
            ],
            api_responses=[(401, {"error": "unauthorized"}), (200, {"ok": True})],
        )
        client = PhysnaClient()
        response = client.request("GET", "/a")
        assert response.status == 200
        assert calls["token"] == 2  # initial + refresh
        assert calls["api"] == 2  # first 401 + retry

    def test_second_401_is_raised(self, monkeypatch):
        from backend.backend.handlers.addon.physna.physnaCommon import (
            PhysnaClient,
            PhysnaAuthError,
        )

        self._install_fakes(
            monkeypatch,
            token_values=[
                (200, {"access_token": "tok-1", "expires_in": 3600}),
                (200, {"access_token": "tok-2", "expires_in": 3600}),
            ],
            api_responses=[(401, {"error": "unauthorized"}), (401, {"error": "still"})],
        )
        client = PhysnaClient()
        with pytest.raises(PhysnaAuthError):
            client.request("GET", "/a")


@pytest.mark.unit
class TestListPhysnaAssetsUnder:
    """list_physna_assets_under matches Physna v3 response shape.

    Per the Physna OpenAPI spec, GET /tenants/{tenantId}/assets returns:
        {"assets": [...], "pageData": {"currentPage", "lastPage", ...}}
    Pagination is 1-based via `page` + `perPage` (not nextPageToken).
    Returned assets include every state (indexing, finished, failed, ...).
    """

    def test_yields_assets_from_single_page_with_client_side_prefix_filter(
        self, monkeypatch
    ):
        import json as _json
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.data = _json.dumps(body).encode("utf-8")

        def fake_request(method, path, **kwargs):
            return FakeResponse(
                200,
                {
                    "assets": [
                        {
                            "id": "a-1",
                            "path": "db-1/asset-1/file1.step",
                            "state": "indexing",
                        },
                        {
                            "id": "a-2",
                            "path": "db-1/asset-1/sub/file2.step",
                            "state": "finished",
                        },
                        # Should be filtered out (same folder, different asset)
                        {
                            "id": "a-3",
                            "path": "db-1/other-asset/x.step",
                            "state": "finished",
                        },
                    ],
                    "pageData": {"currentPage": 1, "lastPage": 1},
                },
            )

        class FakeClient:
            def request(self, method, path, **kwargs):
                return fake_request(method, path, **kwargs)

        assets = list(
            pc.list_physna_assets_under(FakeClient(), "tenant-1", "db-1/asset-1")
        )
        # Includes the indexing asset AND filters out other-asset
        assert len(assets) == 2
        assert {a["id"] for a in assets} == {"a-1", "a-2"}

    def test_paginates_across_multiple_pages(self, monkeypatch):
        import json as _json
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.data = _json.dumps(body).encode("utf-8")

        pages = [
            {
                "assets": [{"id": "a-1", "path": "db-1/asset-1/f1.step"}],
                "pageData": {"currentPage": 1, "lastPage": 2},
            },
            {
                "assets": [{"id": "a-2", "path": "db-1/asset-1/f2.step"}],
                "pageData": {"currentPage": 2, "lastPage": 2},
            },
        ]
        call_count = {"n": 0}

        class FakeClient:
            def request(self, method, path, **kwargs):
                body = pages[call_count["n"]]
                call_count["n"] += 1
                return FakeResponse(200, body)

        assets = list(
            pc.list_physna_assets_under(FakeClient(), "tenant-1", "db-1/asset-1")
        )
        assert call_count["n"] == 2
        assert [a["id"] for a in assets] == ["a-1", "a-2"]


@pytest.mark.unit
class TestLookupPhysnaAssetIdByTextSearch:
    """``lookup_physna_asset_id`` must resolve path → UUID via the
    ``POST /assets/text-search`` endpoint so that indexing assets (which the
    plain list endpoint omits) are still findable. This is what lets the
    viewer show a proper "still indexing" page instead of "not synced yet"
    immediately after upload."""

    @staticmethod
    def _fake_client_with_response(captured, status=200, body=None):
        import json as _json

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.data = _json.dumps(body or {}).encode("utf-8")

        class FakeClient:
            def request(self, method, path, **kwargs):
                captured.append({"method": method, "path": path, "kwargs": kwargs})
                return FakeResponse(status, body or {})

        return FakeClient()

    def test_sends_post_text_search_with_folder_and_filename(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc
        import json as _json

        pc._reset_client_state_for_tests()
        captured = []
        client = self._fake_client_with_response(
            captured,
            body={
                "matches": [
                    {
                        "asset": {
                            "id": "uuid-1",
                            "path": "db-1/asset-1/sub/file.step",
                            "state": "indexing",
                        }
                    }
                ],
                "pageData": {"currentPage": 1, "lastPage": 1},
            },
        )

        result = pc.lookup_physna_asset_id(
            client, "tenant-1", "db-1/asset-1/sub/file.step"
        )
        assert result == "uuid-1"
        # Exactly one call, to the text-search endpoint.
        assert len(captured) == 1
        call = captured[0]
        assert call["method"] == "POST"
        assert call["path"] == "/tenants/tenant-1/assets/text-search"
        body = _json.loads(call["kwargs"]["body"].decode("utf-8"))
        assert body["searchQuery"] == "file.step"
        # Physna's filterData reports folders with a trailing slash — we
        # match that shape on the request side.
        assert body["filters"]["folders"] == ["db-1/asset-1/sub/"]
        # Every filter field from the spec must be present even when empty.
        for key in ("labels", "folderIds", "extensions"):
            assert key in body["filters"]
        assert body["filters"]["metadata"] == {}

    def test_returns_indexing_asset_uuid(self):
        """The whole point of switching to text-search: indexing-state
        assets must be findable by path."""
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        captured = []
        client = self._fake_client_with_response(
            captured,
            body={
                "matches": [
                    {
                        "asset": {
                            "id": "uuid-indexing",
                            "path": "db/asset/file.stp",
                            "state": "indexing",
                        }
                    }
                ]
            },
        )
        assert (
            pc.lookup_physna_asset_id(client, "tenant", "db/asset/file.stp")
            == "uuid-indexing"
        )

    def test_empty_matches_returns_none(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        client = self._fake_client_with_response([], body={"matches": []})
        assert (
            pc.lookup_physna_asset_id(client, "tenant", "db/asset/missing.step")
            is None
        )

    def test_ignores_near_match_requires_exact_path(self):
        """text-search is substring-ish on ``searchQuery``; if Physna returns
        an asset whose ``path`` doesn't exactly equal ``full_path``, we
        must skip it rather than return the wrong UUID."""
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        client = self._fake_client_with_response(
            [],
            body={
                "matches": [
                    {
                        "asset": {
                            "id": "other-uuid",
                            # Same filename, different folder — must be skipped.
                            "path": "db/other-asset/file.step",
                        }
                    }
                ]
            },
        )
        assert (
            pc.lookup_physna_asset_id(client, "tenant", "db/asset-1/file.step")
            is None
        )

    def test_picks_exact_match_from_multiple_matches(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        client = self._fake_client_with_response(
            [],
            body={
                "matches": [
                    {"asset": {"id": "wrong-1", "path": "x/y/file.step"}},
                    {"asset": {"id": "right", "path": "db/asset/file.step"}},
                    {"asset": {"id": "wrong-2", "path": "db/asset/other.step"}},
                ]
            },
        )
        assert (
            pc.lookup_physna_asset_id(client, "tenant", "db/asset/file.step")
            == "right"
        )

    def test_path_without_slash_sends_empty_folder_filter(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc
        import json as _json

        pc._reset_client_state_for_tests()
        captured = []
        client = self._fake_client_with_response(
            captured,
            body={"matches": [{"asset": {"id": "u", "path": "file.step"}}]},
        )
        pc.lookup_physna_asset_id(client, "tenant", "file.step")
        body = _json.loads(captured[0]["kwargs"]["body"].decode("utf-8"))
        assert body["searchQuery"] == "file.step"
        assert body["filters"]["folders"] == []

    def test_http_404_returns_none(self):
        """A 404 from Physna is a soft miss, not an error."""
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        captured = []
        client = self._fake_client_with_response(captured, status=404, body={})
        assert pc.lookup_physna_asset_id(client, "t", "db/a/f.step") is None

    def test_http_500_raises(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        captured = []
        client = self._fake_client_with_response(captured, status=500, body={})
        with pytest.raises(pc.PhysnaApiError):
            pc.lookup_physna_asset_id(client, "t", "db/a/f.step")

    @staticmethod
    def _fake_client_with_pages(pages, captured):
        """FakeClient that returns a scripted sequence of response bodies,
        one per call. Bounds violations blow up loudly so a test that
        paginates farther than expected fails immediately."""
        import json as _json

        class FakeResponse:
            def __init__(self, body):
                self.status = 200
                self.data = _json.dumps(body).encode("utf-8")

        calls = {"n": 0}

        class FakeClient:
            def request(self, method, path, **kwargs):
                captured.append(
                    {"method": method, "path": path, "kwargs": kwargs}
                )
                if calls["n"] >= len(pages):
                    raise AssertionError(
                        f"FakeClient: unexpected request #{calls['n'] + 1} "
                        f"(only {len(pages)} pages scripted)"
                    )
                body = pages[calls["n"]]
                calls["n"] += 1
                return FakeResponse(body)

        return FakeClient()

    def test_paginates_until_exact_match_found(self):
        """When Physna returns a full page of near-matches before the exact
        one, lookup must walk every page. Real-world scenario: a common
        filename like ``part.step`` appears in dozens of folders tenant-
        wide, and the exact one we want may sit on page 2+."""
        import backend.backend.handlers.addon.physna.physnaCommon as pc
        import json as _json

        pc._reset_client_state_for_tests()
        target_path = "db/asset-1/part.step"
        # Page 1: only near-matches (same filename, different folders). Page
        # 2: contains the exact path at the tail.
        page1 = {
            "matches": [
                {"asset": {"id": "x1", "path": "other/a/part.step"}},
                {"asset": {"id": "x2", "path": "other/b/part.step"}},
            ],
            "pageData": {"currentPage": 1, "lastPage": 2, "total": 3},
        }
        page2 = {
            "matches": [
                {"asset": {"id": "wanted", "path": target_path}},
            ],
            "pageData": {"currentPage": 2, "lastPage": 2, "total": 3},
        }
        captured = []
        client = self._fake_client_with_pages([page1, page2], captured)

        assert (
            pc.lookup_physna_asset_id(client, "tenant", target_path)
            == "wanted"
        )
        assert len(captured) == 2, "lookup should have paginated to page 2"
        # Page number advanced in the POST body.
        body1 = _json.loads(captured[0]["kwargs"]["body"].decode("utf-8"))
        body2 = _json.loads(captured[1]["kwargs"]["body"].decode("utf-8"))
        assert body1["page"] == 1
        assert body2["page"] == 2

    def test_stops_paginating_once_match_found(self):
        """If the exact match is on page 1, the function must not issue a
        second request even when ``lastPage`` says more pages exist."""
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        page1 = {
            "matches": [
                {"asset": {"id": "right", "path": "db/asset/f.step"}},
                {"asset": {"id": "x", "path": "db/other/f.step"}},
            ],
            "pageData": {"currentPage": 1, "lastPage": 5},
        }
        captured = []
        # Only script one page; the test will fail loudly if a second
        # request fires.
        client = self._fake_client_with_pages([page1], captured)
        assert (
            pc.lookup_physna_asset_id(client, "t", "db/asset/f.step")
            == "right"
        )
        assert len(captured) == 1

    def test_returns_none_after_exhausting_all_pages_without_match(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        page1 = {
            "matches": [{"asset": {"id": "x", "path": "other/a/f.step"}}],
            "pageData": {"currentPage": 1, "lastPage": 2},
        }
        page2 = {
            "matches": [{"asset": {"id": "y", "path": "other/b/f.step"}}],
            "pageData": {"currentPage": 2, "lastPage": 2},
        }
        captured = []
        client = self._fake_client_with_pages([page1, page2], captured)
        assert (
            pc.lookup_physna_asset_id(client, "t", "db/asset/f.step") is None
        )
        assert len(captured) == 2

    def test_handles_sample_response_shape_from_docs(self):
        """Verifies the exact response envelope Physna returned in a real
        text-search call (the 3-match sample shared by the user, which
        includes folders, metadata, state=indexing, filterData, and
        pageData keys). The exact-match row is the first entry and sits
        under a nested folder path."""
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        sample = {
            "matches": [
                {
                    "asset": {
                        "id": "63ac12ef-9e93-42ca-a6c7-c93790b5b68b",
                        "path": "building/x880e9206-1821-4307-b8fe-be500792f501/471T5003-1 ---.CATPart",
                        "state": "indexing",
                    }
                },
                {
                    "asset": {
                        "id": "8946c51a-3e54-4885-a1db-c47966bf98ba",
                        "path": "Chipper/a7-2spz100-1610_01.par",
                        "state": "finished",
                    }
                },
                {
                    "asset": {
                        "id": "ed7c22bd-3e81-42fc-8732-16fa24dea98f",
                        "path": "Brushcutter/a20-200-01_default_as machined_.par",
                        "state": "finished",
                    }
                },
            ],
            "filterData": {"extensions": [], "labels": [], "folders": []},
            "pageData": {
                "total": 3,
                "perPage": 50,
                "currentPage": 1,
                "lastPage": 1,
            },
        }
        captured = []
        client = self._fake_client_with_pages([sample], captured)
        target = "building/x880e9206-1821-4307-b8fe-be500792f501/471T5003-1 ---.CATPart"
        assert (
            pc.lookup_physna_asset_id(client, "tenant", target)
            == "63ac12ef-9e93-42ca-a6c7-c93790b5b68b"
        )


@pytest.mark.unit
class TestEnsureMetadataFieldsRegistered:
    """Physna requires every metadata key to exist as a tenant metadataField
    before values can be set. ``ensure_metadata_fields_registered`` bridges
    that by listing existing fields and POSTing any missing ones as type
    ``text`` before the caller's metadata PATCH."""

    def _fake_client(self, existing_names, record):
        import json as _json

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.data = _json.dumps(body).encode("utf-8")

        class FakeClient:
            def request(self, method, path, **kwargs):
                record.append((method, path, kwargs.get("body")))
                if method == "GET" and "metadata-fields" in path:
                    return FakeResponse(
                        200,
                        {
                            "metadataFields": [
                                {"id": f"id-{n}", "name": n, "type": "text"}
                                for n in existing_names
                            ],
                            "pageData": {"currentPage": 1, "lastPage": 1},
                        },
                    )
                if method == "POST" and "metadata-fields" in path:
                    body = _json.loads(kwargs.get("body").decode("utf-8"))
                    return FakeResponse(
                        200,
                        {
                            "metadataField": {
                                "id": "new-id",
                                "name": body["name"],
                                "type": "text",
                            }
                        },
                    )
                return FakeResponse(500, {"error": "unexpected call"})

        return FakeClient()

    def test_registers_only_missing_fields(self, monkeypatch):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_metadata_field_cache_for_tests()
        record = []
        client = self._fake_client({"Material", "Cost"}, record)

        pc.ensure_metadata_fields_registered(
            client, "tenant-1", ["Material", "NewField", "Cost", "OtherNew"]
        )

        # One GET for list; POSTs only for the two missing fields
        methods = [r[0] for r in record]
        paths = [r[1] for r in record]
        assert methods.count("GET") == 1
        posts = [r for r in record if r[0] == "POST"]
        assert len(posts) == 2
        posted_names = set()
        for _method, _path, body in posts:
            import json as _json

            posted_names.add(_json.loads(body.decode("utf-8"))["name"])
        assert posted_names == {"NewField", "OtherNew"}

    def test_caches_known_fields_across_calls(self, monkeypatch):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_metadata_field_cache_for_tests()
        record = []
        client = self._fake_client({"Material"}, record)

        pc.ensure_metadata_fields_registered(client, "tenant-1", ["Material"])
        pc.ensure_metadata_fields_registered(client, "tenant-1", ["Material"])

        # Second call should reuse cache — still only one GET total
        gets = [r for r in record if r[0] == "GET"]
        assert len(gets) == 1
        posts = [r for r in record if r[0] == "POST"]
        assert len(posts) == 0

    def test_409_on_create_is_treated_as_success(self, monkeypatch):
        import json as _json
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_metadata_field_cache_for_tests()

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.data = _json.dumps(body).encode("utf-8")

        class FakeClient:
            def request(self, method, path, **kwargs):
                if method == "GET":
                    return FakeResponse(
                        200,
                        {
                            "metadataFields": [],
                            "pageData": {"currentPage": 1, "lastPage": 1},
                        },
                    )
                # Simulate race: field already exists
                return FakeResponse(409, {"message": "exists"})

        pc.ensure_metadata_fields_registered(
            FakeClient(), "tenant-1", ["ShouldExistNow"]
        )
        assert (
            "ShouldExistNow"
            in pc._known_metadata_fields_by_tenant["tenant-1"]
        )


@pytest.mark.unit
class TestDeleteFolderIfEmpty:
    def test_no_delete_when_folder_has_assets(self, monkeypatch):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        calls = {"delete": 0}

        class FakeClient:
            def request(self, method, path, **kwargs):
                import json

                class R:
                    status = 200
                    data = json.dumps(
                        {
                            "assets": [
                                {"id": "a-1", "path": "db-1/asset-1/file1.step"}
                            ],
                            "pageData": {"currentPage": 1, "lastPage": 1},
                        }
                    ).encode("utf-8")

                if method == "DELETE":
                    calls["delete"] += 1
                return R()

        result = pc.delete_folder_if_empty(FakeClient(), "tenant-1", "db-1/asset-1")
        assert result is False
        assert calls["delete"] == 0

    def test_delete_invoked_logic_runs_when_empty(self, monkeypatch):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        # Capture the "delete attempted" side effect the stub exposes for tests
        events = []

        class FakeClient:
            def request(self, method, path, **kwargs):
                import json

                class R:
                    status = 200
                    data = json.dumps(
                        {
                            "assets": [],
                            "pageData": {"currentPage": 1, "lastPage": 1},
                        }
                    ).encode("utf-8")

                return R()

        # Patch the stub-callback hook
        monkeypatch.setattr(
            pc, "_folder_delete_stub_callback", lambda client, tenant_id, folder: events.append(folder)
        )
        result = pc.delete_folder_if_empty(FakeClient(), "tenant-1", "db-1/asset-1/sub")
        assert result is True
        assert events == ["db-1/asset-1/sub"]


@pytest.mark.unit
class TestGetFileMetadata:
    def test_returns_metadata_and_attributes_separately(self, monkeypatch):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        class FakeTable:
            def __init__(self, items):
                self._items = items

            def query(self, **kwargs):
                return {"Items": self._items}

        meta_items = [
            {
                "databaseId:assetId:filePath": "db-1:asset-1:/sub/part.step",
                "metadataKey": "color",
                "metadataValue": "red",
                "metadataValueType": "string",
            },
            {
                "databaseId:assetId:filePath": "db-1:asset-1:/sub/part.step",
                "metadataKey": "REINDEX_METADATA_RECORD",
                "metadataValue": "skip-me",
                "metadataValueType": "string",
            },
        ]
        attr_items = [
            {
                "databaseId:assetId:filePath": "db-1:asset-1:/sub/part.step",
                "attributeKey": "material",
                "attributeValue": "steel",
                "attributeValueType": "string",
            }
        ]

        monkeypatch.setattr(pc, "asset_file_metadata_table", FakeTable(meta_items))
        monkeypatch.setattr(pc, "file_attribute_table", FakeTable(attr_items))

        metadata, attrs = pc.get_file_metadata("db-1", "asset-1", "/sub/part.step")
        assert "color" in metadata
        assert metadata["color"]["value"] == "red"
        assert "REINDEX_METADATA_RECORD" not in metadata  # system record skipped
        assert "material" in attrs
        assert attrs["material"]["value"] == "steel"


@pytest.mark.unit
class TestGetAssetMetadata:
    def test_returns_asset_level_metadata_map(self, monkeypatch):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        class FakeTable:
            def query(self, **kwargs):
                return {
                    "Items": [
                        {
                            "databaseId:assetId:filePath": "db-1:asset-1:/",
                            "metadataKey": "partFamily",
                            "metadataValue": "widgets",
                            "metadataValueType": "string",
                        }
                    ]
                }

        monkeypatch.setattr(pc, "asset_file_metadata_table", FakeTable())
        metadata = pc.get_asset_metadata("db-1", "asset-1")
        assert metadata["partFamily"]["value"] == "widgets"


@pytest.mark.unit
class TestGetPhysnaAssetAndDeleteFields:
    """Direct asset GET and metadata-field DELETE helpers match the Physna
    v3 shapes verified from live traffic."""

    def test_get_physna_asset_returns_inner_asset_dict(self):
        import json as _json
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.data = _json.dumps(body).encode("utf-8")

        class FakeClient:
            def request(self, method, path, **kwargs):
                assert method == "GET"
                assert "/assets/uuid-1" in path
                return FakeResponse(
                    200,
                    {
                        "asset": {
                            "id": "uuid-1",
                            "path": "db-1/asset-1/f.step",
                            "state": "indexing",
                            "metadata": {"a": "1", "b": "2"},
                        }
                    },
                )

        asset = pc.get_physna_asset(FakeClient(), "tenant-1", "uuid-1")
        assert asset["state"] == "indexing"
        assert asset["metadata"] == {"a": "1", "b": "2"}

    def test_get_physna_asset_returns_none_on_404(self):
        import json as _json
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.data = _json.dumps(body).encode("utf-8")

        class FakeClient:
            def request(self, method, path, **kwargs):
                return FakeResponse(404, {"message": "not found"})

        assert pc.get_physna_asset(FakeClient(), "tenant-1", "uuid-1") is None

    def test_delete_physna_metadata_fields_sends_metadataFieldNames_body(self):
        import json as _json
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        seen = {}

        class FakeResponse:
            status = 204
            data = b""

        class FakeClient:
            def request(self, method, path, **kwargs):
                seen["method"] = method
                seen["path"] = path
                seen["body"] = kwargs.get("body")
                return FakeResponse()

        pc.delete_physna_metadata_fields(
            FakeClient(), "tenant-1", "uuid-1", ["old1", "old2"]
        )
        assert seen["method"] == "DELETE"
        assert seen["path"] == "/tenants/tenant-1/assets/uuid-1/metadata"
        body = _json.loads(seen["body"].decode("utf-8"))
        assert body == {"metadataFieldNames": ["old1", "old2"]}

    def test_delete_physna_metadata_fields_no_op_on_empty(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        calls = []

        class FakeClient:
            def request(self, method, path, **kwargs):
                calls.append((method, path))

                class R:
                    status = 204
                    data = b""

                return R()

        pc.delete_physna_metadata_fields(FakeClient(), "tenant-1", "uuid-1", [])
        assert calls == []


@pytest.mark.unit
class TestGetDatabaseIdForAssetId:
    """Reverse-lookup via assetIdGSI with bucket disambiguation. Mirrors
    ``fileIndexer.lookup_database_id_for_permanent_delete`` so the Physna
    sync resolves a deleted S3 object's databaseId the same way the
    OpenSearch indexer does — including the "refuse to guess when
    ambiguous" safety behavior."""

    def _mock_assets(self, monkeypatch, assets):
        """Wire ``asset_storage_table.query`` to return a scripted list."""
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        class _Table:
            def query(self, IndexName, KeyConditionExpression, **kwargs):
                return {"Items": list(assets)}

        monkeypatch.setattr(pc, "asset_storage_table", _Table())
        return pc

    def _mock_bucket_details(self, monkeypatch, by_id):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        monkeypatch.setattr(
            pc, "get_bucket_details", lambda bid: by_id.get(bid)
        )

    def test_single_match_returns_database_id(self, monkeypatch):
        pc = self._mock_assets(
            monkeypatch,
            [{"assetId": "a-1", "databaseId": "db-X", "bucketId": "b-1"}],
        )
        assert (
            pc.get_database_id_for_asset_id("a-1", bucket_name="any", base_assets_prefix="any")
            == "db-X"
        )

    def test_empty_result_returns_none(self, monkeypatch):
        pc = self._mock_assets(monkeypatch, [])
        assert pc.get_database_id_for_asset_id("missing") is None

    def test_multiple_matches_without_bucket_context_returns_none(
        self, monkeypatch
    ):
        """Without bucket context we cannot safely pick — refuse."""
        pc = self._mock_assets(
            monkeypatch,
            [
                {"assetId": "a-1", "databaseId": "db-A", "bucketId": "b-A"},
                {"assetId": "a-1", "databaseId": "db-B", "bucketId": "b-B"},
            ],
        )
        assert pc.get_database_id_for_asset_id("a-1") is None

    def test_multiple_matches_filtered_by_bucket_returns_unique_db(
        self, monkeypatch
    ):
        """When assetId is non-unique across databases, the bucket+prefix
        must pick the right database."""
        pc = self._mock_assets(
            monkeypatch,
            [
                {"assetId": "a-1", "databaseId": "db-A", "bucketId": "b-A"},
                {"assetId": "a-1", "databaseId": "db-B", "bucketId": "b-B"},
            ],
        )
        self._mock_bucket_details(
            monkeypatch,
            {
                "b-A": {
                    "bucketId": "b-A",
                    "bucketName": "bucket-A",
                    "baseAssetsPrefix": "assets/",
                },
                "b-B": {
                    "bucketId": "b-B",
                    "bucketName": "bucket-B",
                    "baseAssetsPrefix": "assets/",
                },
            },
        )
        assert (
            pc.get_database_id_for_asset_id(
                "a-1",
                bucket_name="bucket-B",
                base_assets_prefix="assets/",
            )
            == "db-B"
        )

    def test_multiple_matches_same_bucket_different_prefix(self, monkeypatch):
        """Two assets share both assetId and bucket name but differ in
        base prefix — the prefix is what disambiguates."""
        pc = self._mock_assets(
            monkeypatch,
            [
                {"assetId": "a-1", "databaseId": "db-A", "bucketId": "b-A"},
                {"assetId": "a-1", "databaseId": "db-B", "bucketId": "b-B"},
            ],
        )
        self._mock_bucket_details(
            monkeypatch,
            {
                "b-A": {
                    "bucketId": "b-A",
                    "bucketName": "shared",
                    "baseAssetsPrefix": "db-A/",
                },
                "b-B": {
                    "bucketId": "b-B",
                    "bucketName": "shared",
                    "baseAssetsPrefix": "db-B/",
                },
            },
        )
        assert (
            pc.get_database_id_for_asset_id(
                "a-1", bucket_name="shared", base_assets_prefix="db-A/"
            )
            == "db-A"
        )

    def test_multiple_matches_no_bucket_filter_match_returns_none(
        self, monkeypatch
    ):
        """Bucket filter provided but no asset's bucket matches — refuse."""
        pc = self._mock_assets(
            monkeypatch,
            [
                {"assetId": "a-1", "databaseId": "db-A", "bucketId": "b-A"},
                {"assetId": "a-1", "databaseId": "db-B", "bucketId": "b-B"},
            ],
        )
        self._mock_bucket_details(
            monkeypatch,
            {
                "b-A": {
                    "bucketId": "b-A",
                    "bucketName": "bucket-A",
                    "baseAssetsPrefix": "assets/",
                },
                "b-B": {
                    "bucketId": "b-B",
                    "bucketName": "bucket-B",
                    "baseAssetsPrefix": "assets/",
                },
            },
        )
        assert (
            pc.get_database_id_for_asset_id(
                "a-1",
                bucket_name="unrelated-bucket",
                base_assets_prefix="assets/",
            )
            is None
        )
