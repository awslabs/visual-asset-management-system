# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input bounds on the metadata request models.

Each bound is covered from both sides: an over-limit request is rejected, and a
legitimate at-or-near-limit request still parses. Metadata values carry unicode,
large GeoJSON and JSON blobs, so the accept cases assert those stay valid.
"""

import json

import pytest
from pydantic import ValidationError


@pytest.mark.unit
class TestMetadataItemValueBounds:
    def test_rejects_value_over_max_length(self):
        from models.metadata import MetadataItemModel, MAX_METADATA_VALUE_LENGTH
        with pytest.raises(ValidationError):
            MetadataItemModel(
                metadataKey="notes",
                metadataValue="x" * (MAX_METADATA_VALUE_LENGTH + 1),
                metadataValueType="string",
            )

    def test_accepts_value_at_max_length(self):
        from models.metadata import MetadataItemModel, MAX_METADATA_VALUE_LENGTH
        model = MetadataItemModel(
            metadataKey="notes",
            metadataValue="x" * MAX_METADATA_VALUE_LENGTH,
            metadataValueType="string",
        )
        assert len(model.metadataValue) == MAX_METADATA_VALUE_LENGTH

    def test_accepts_unicode_value(self):
        """Unicode metadata values are legitimate and must not be rejected."""
        from models.metadata import MetadataItemModel
        value = "Grüße 建物スキャン \U0001F6F0 — naïve façade"
        model = MetadataItemModel(
            metadataKey="description",
            metadataValue=value,
            metadataValueType="string",
        )
        assert model.metadataValue == value

    def test_accepts_large_geojson_value(self):
        """A realistic multi-vertex polygon stays well inside the value ceiling."""
        from models.metadata import MetadataItemModel
        ring = [[i * 0.001, i * 0.001] for i in range(500)]
        ring.append(ring[0])
        value = json.dumps({"type": "Polygon", "coordinates": [ring]})
        model = MetadataItemModel(
            metadataKey="footprint",
            metadataValue=value,
            metadataValueType="geojson",
        )
        assert json.loads(model.metadataValue)["type"] == "Polygon"


def _nested_collection_json(levels):
    """A GeometryCollection chain `levels` nodes deep, built as text.

    json.dumps recurses once per level and would run out of stack building the deepest
    fixtures here, so the string is assembled directly.
    """
    return (
        '{"type":"GeometryCollection","geometries":[' * (levels - 1)
        + '{"type":"Point","coordinates":[1.0,2.0]}'
        + "]}" * (levels - 1)
    )


@pytest.mark.unit
class TestGeoJsonNestingBound:
    """A GeoJSON value nested past MAX_GEOJSON_NESTING_DEPTH is refused, not walked.

    _validate_geometry recurses once per GeometryCollection level, so an unbounded value
    exhausted the interpreter stack: the request 500'd instead of being rejected. The same
    value is reachable through the metadata write API, metadata-schema defaults, asset-link
    metadata and the search geoJson filter, which all run this one validator.
    """

    def _model(self, value):
        from models.metadata import MetadataItemModel
        return MetadataItemModel(
            metadataKey="footprint", metadataValue=value, metadataValueType="geojson"
        )

    def test_accepts_nesting_at_the_bound(self):
        from models.metadata import MAX_GEOJSON_NESTING_DEPTH
        value = _nested_collection_json(MAX_GEOJSON_NESTING_DEPTH)
        assert json.loads(value)["type"] == "GeometryCollection"
        assert self._model(value).metadataValue == value

    def test_rejects_nesting_one_level_past_the_bound(self):
        from models.metadata import MAX_GEOJSON_NESTING_DEPTH
        with pytest.raises(ValidationError) as exc:
            self._model(_nested_collection_json(MAX_GEOJSON_NESTING_DEPTH + 1))
        assert str(MAX_GEOJSON_NESTING_DEPTH) in str(exc.value)

    def test_rejects_nesting_deep_enough_to_exhaust_the_stack(self):
        """1200 levels parse cleanly, so the walk is what used to raise RecursionError."""
        from models.metadata import MAX_METADATA_VALUE_LENGTH
        value = _nested_collection_json(1200)
        assert len(value) < MAX_METADATA_VALUE_LENGTH
        assert json.loads(value)["type"] == "GeometryCollection"
        with pytest.raises(ValidationError):
            self._model(value)

    def test_rejects_value_too_deep_for_the_parser(self):
        """Past the parser's own limit json.loads raises RecursionError, not a decode error."""
        from models.metadata import MAX_METADATA_VALUE_LENGTH
        value = _nested_collection_json(4000)
        assert len(value) < MAX_METADATA_VALUE_LENGTH
        # Positive control on the fixture: the parse failure is a RecursionError, so a guard
        # catching only json.JSONDecodeError does not see it.
        with pytest.raises(RecursionError):
            json.loads(value)
        with pytest.raises(ValidationError):
            self._model(value)

    def test_geopoint_and_json_types_also_refuse_an_unparseable_value(self):
        from models.metadata import MetadataItemModel
        value = _nested_collection_json(4000)
        for value_type in ("geopoint", "json", "matrix4x4", "xyz", "wxyz", "lla"):
            with pytest.raises(ValidationError):
                MetadataItemModel(
                    metadataKey="k", metadataValue=value, metadataValueType=value_type
                )


def _items(count):
    return [
        {"metadataKey": f"key{i}", "metadataValue": f"v{i}", "metadataValueType": "string"}
        for i in range(count)
    ]


@pytest.mark.unit
class TestMetadataListBounds:
    CREATE_MODELS = [
        "CreateAssetMetadataRequestModel",
        "CreateDatabaseMetadataRequestModel",
        "CreateAssetLinkMetadataRequestModel",
    ]
    UPDATE_MODELS = [
        "UpdateAssetMetadataRequestModel",
        "UpdateDatabaseMetadataRequestModel",
        "UpdateAssetLinkMetadataRequestModel",
    ]
    DELETE_MODELS = [
        "DeleteAssetMetadataRequestModel",
        "DeleteDatabaseMetadataRequestModel",
        "DeleteAssetLinkMetadataRequestModel",
    ]

    @pytest.mark.parametrize("model_name", CREATE_MODELS + UPDATE_MODELS)
    def test_rejects_metadata_list_over_max_items(self, model_name):
        import models.metadata as m
        model_cls = getattr(m, model_name)
        with pytest.raises(ValidationError):
            model_cls(metadata=_items(m.MAX_METADATA_ITEMS_PER_REQUEST + 1))

    @pytest.mark.parametrize("model_name", CREATE_MODELS + UPDATE_MODELS)
    def test_accepts_metadata_list_at_max_items(self, model_name):
        import models.metadata as m
        model_cls = getattr(m, model_name)
        model = model_cls(metadata=_items(m.MAX_METADATA_ITEMS_PER_REQUEST))
        assert len(model.metadata) == m.MAX_METADATA_ITEMS_PER_REQUEST

    @pytest.mark.parametrize("model_name", DELETE_MODELS)
    def test_rejects_metadata_keys_over_max_items(self, model_name):
        import models.metadata as m
        model_cls = getattr(m, model_name)
        keys = [f"key{i}" for i in range(m.MAX_METADATA_KEYS_PER_REQUEST + 1)]
        with pytest.raises(ValidationError):
            model_cls(metadataKeys=keys)

    @pytest.mark.parametrize("model_name", DELETE_MODELS)
    def test_accepts_metadata_keys_at_max_items(self, model_name):
        import models.metadata as m
        model_cls = getattr(m, model_name)
        keys = [f"key{i}" for i in range(m.MAX_METADATA_KEYS_PER_REQUEST)]
        model = model_cls(metadataKeys=keys)
        assert len(model.metadataKeys) == m.MAX_METADATA_KEYS_PER_REQUEST


@pytest.mark.unit
class TestFileMetadataListBounds:
    """File metadata/attribute models carry filePath + type alongside the list."""

    def _base(self):
        return {"filePath": "/folder/my file.glb", "type": "metadata"}

    def test_rejects_create_list_over_max_items(self):
        import models.metadata as m
        with pytest.raises(ValidationError):
            m.CreateFileMetadataRequestModel(
                **self._base(), metadata=_items(m.MAX_METADATA_ITEMS_PER_REQUEST + 1)
            )

    def test_accepts_create_list_at_max_items(self):
        import models.metadata as m
        model = m.CreateFileMetadataRequestModel(
            **self._base(), metadata=_items(m.MAX_METADATA_ITEMS_PER_REQUEST)
        )
        assert len(model.metadata) == m.MAX_METADATA_ITEMS_PER_REQUEST
        # A path containing spaces is legitimate and must survive validation.
        assert model.filePath == "/folder/my file.glb"

    def test_rejects_update_list_over_max_items(self):
        import models.metadata as m
        with pytest.raises(ValidationError):
            m.UpdateFileMetadataRequestModel(
                **self._base(), metadata=_items(m.MAX_METADATA_ITEMS_PER_REQUEST + 1)
            )

    def test_rejects_delete_keys_over_max_items(self):
        import models.metadata as m
        keys = [f"key{i}" for i in range(m.MAX_METADATA_KEYS_PER_REQUEST + 1)]
        with pytest.raises(ValidationError):
            m.DeleteFileMetadataRequestModel(**self._base(), metadataKeys=keys)

    def test_accepts_delete_keys_at_max_items(self):
        import models.metadata as m
        keys = [f"key{i}" for i in range(m.MAX_METADATA_KEYS_PER_REQUEST)]
        model = m.DeleteFileMetadataRequestModel(**self._base(), metadataKeys=keys)
        assert len(model.metadataKeys) == m.MAX_METADATA_KEYS_PER_REQUEST


@pytest.mark.unit
class TestPaginationAndVersionBounds:
    GET_MODELS_WITH_TOKEN = [
        "GetAssetMetadataRequestModel",
        "GetDatabaseMetadataRequestModel",
        "GetAssetLinkMetadataRequestModel",
    ]

    @pytest.mark.parametrize("model_name", GET_MODELS_WITH_TOKEN)
    def test_rejects_oversized_starting_token(self, model_name):
        import models.metadata as m
        model_cls = getattr(m, model_name)
        with pytest.raises(ValidationError):
            model_cls(startingToken="A" * (m.MAX_PAGINATION_TOKEN_LENGTH + 1))

    @pytest.mark.parametrize("model_name", GET_MODELS_WITH_TOKEN)
    def test_accepts_realistic_starting_token(self, model_name):
        import base64

        import models.metadata as m
        model_cls = getattr(m, model_name)
        token = base64.b64encode(b"3000").decode("utf-8")
        assert model_cls(startingToken=token).startingToken == token

    def test_rejects_oversized_starting_token_on_file_get(self):
        import models.metadata as m
        with pytest.raises(ValidationError):
            m.GetFileMetadataRequestModel(
                filePath="/f.glb",
                type="metadata",
                startingToken="A" * (m.MAX_PAGINATION_TOKEN_LENGTH + 1),
            )

    def test_rejects_oversized_asset_version_id(self):
        import models.metadata as m
        with pytest.raises(ValidationError):
            m.GetAssetMetadataRequestModel(
                assetVersionId="9" * (m.MAX_ASSET_VERSION_ID_LENGTH + 1)
            )

    def test_accepts_realistic_asset_version_id(self):
        import models.metadata as m
        assert m.GetAssetMetadataRequestModel(assetVersionId="12").assetVersionId == "12"

    def test_rejects_oversized_asset_version_id_on_file_get(self):
        import models.metadata as m
        with pytest.raises(ValidationError):
            m.GetFileMetadataRequestModel(
                filePath="/f.glb",
                type="metadata",
                assetVersionId="9" * (m.MAX_ASSET_VERSION_ID_LENGTH + 1),
            )
