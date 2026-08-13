# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input-bound validation on the metadata-schema request models and their sub-models.

A schema is stored as one DynamoDB item, and every field definition inside it is applied
to metadata on every write to the entities the schema covers. An unbounded field array,
dependency list, or controlled-list array therefore drives both the stored item size and
the per-write validation cost.
"""

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError


def _field(key="colour", value_type="string", **extra):
    body = {"metadataFieldKeyName": key, "metadataFieldValueType": value_type}
    body.update(extra)
    return body


def _create_body(fields=None, **extra):
    body = {
        "databaseId": "test-db",
        "metadataSchemaEntityType": "assetMetadata",
        "schemaName": "Test Schema",
        "fields": {"fields": fields if fields is not None else [_field()]},
    }
    body.update(extra)
    return body


@pytest.mark.unit
class TestSchemaFieldArrayBounds:
    def test_accepts_single_field(self):
        from models.metadataSchema import CreateMetadataSchemaRequestModel
        model = parse(_create_body(), model=CreateMetadataSchemaRequestModel)
        assert len(model.fields.fields) == 1

    def test_accepts_fields_at_ceiling(self):
        from models.metadataSchema import CreateMetadataSchemaRequestModel, MAX_SCHEMA_FIELDS
        fields = [_field(key=f"field{i}") for i in range(MAX_SCHEMA_FIELDS)]
        model = parse(_create_body(fields), model=CreateMetadataSchemaRequestModel)
        assert len(model.fields.fields) == MAX_SCHEMA_FIELDS

    def test_rejects_fields_over_ceiling(self):
        from models.metadataSchema import CreateMetadataSchemaRequestModel, MAX_SCHEMA_FIELDS
        fields = [_field(key=f"field{i}") for i in range(MAX_SCHEMA_FIELDS + 1)]
        with pytest.raises(ValidationError):
            parse(_create_body(fields), model=CreateMetadataSchemaRequestModel)

    def test_rejects_empty_fields_array(self):
        from models.metadataSchema import CreateMetadataSchemaRequestModel
        with pytest.raises(ValidationError):
            parse(_create_body([]), model=CreateMetadataSchemaRequestModel)


@pytest.mark.unit
class TestFieldSubModelBounds:
    def test_accepts_realistic_dependency_list(self):
        from models.metadataSchema import MetadataSchemaFieldModel
        model = MetadataSchemaFieldModel(**_field(dependsOnFieldKeyName=["material", "finish"]))
        assert model.dependsOnFieldKeyName == ["material", "finish"]

    def test_rejects_dependency_list_over_ceiling(self):
        from models.metadataSchema import MetadataSchemaFieldModel, MAX_FIELD_DEPENDENCIES
        with pytest.raises(ValueError):
            MetadataSchemaFieldModel(**_field(
                dependsOnFieldKeyName=[f"d{i}" for i in range(MAX_FIELD_DEPENDENCIES + 1)]
            ))

    def test_rejects_oversized_dependency_entry(self):
        """max_items alone leaves a single unbounded element."""
        from models.metadataSchema import MetadataSchemaFieldModel
        with pytest.raises(ValueError):
            MetadataSchemaFieldModel(**_field(dependsOnFieldKeyName=["d" * 257]))

    def test_accepts_dependency_entry_at_ceiling(self):
        from models.metadataSchema import MetadataSchemaFieldModel
        model = MetadataSchemaFieldModel(**_field(dependsOnFieldKeyName=["d" * 256]))
        assert len(model.dependsOnFieldKeyName[0]) == 256

    def test_accepts_realistic_controlled_list(self):
        from models.metadataSchema import MetadataSchemaFieldModel
        model = MetadataSchemaFieldModel(**_field(
            value_type="inline_controlled_list",
            controlledListKeys=["red", "green", "blue"],
        ))
        assert model.controlledListKeys == ["red", "green", "blue"]

    def test_rejects_controlled_list_over_ceiling(self):
        from models.metadataSchema import MetadataSchemaFieldModel, MAX_CONTROLLED_LIST_KEYS
        with pytest.raises(ValueError):
            MetadataSchemaFieldModel(**_field(
                value_type="inline_controlled_list",
                controlledListKeys=[f"v{i}" for i in range(MAX_CONTROLLED_LIST_KEYS + 1)],
            ))

    def test_rejects_oversized_controlled_list_entry(self):
        from models.metadataSchema import MetadataSchemaFieldModel
        with pytest.raises(ValueError):
            MetadataSchemaFieldModel(**_field(
                value_type="inline_controlled_list",
                controlledListKeys=["v" * 257],
            ))

    def test_rejects_oversized_default_value(self):
        from models.metadataSchema import MetadataSchemaFieldModel, MAX_METADATA_VALUE_LENGTH
        with pytest.raises(ValueError):
            MetadataSchemaFieldModel(**_field(
                defaultMetadataFieldValue="x" * (MAX_METADATA_VALUE_LENGTH + 1)
            ))

    def test_accepts_realistic_default_value(self):
        from models.metadataSchema import MetadataSchemaFieldModel
        model = MetadataSchemaFieldModel(**_field(defaultMetadataFieldValue="unspecified"))
        assert model.defaultMetadataFieldValue == "unspecified"

    def test_rejects_sequence_over_field_ceiling(self):
        from models.metadataSchema import MetadataSchemaFieldModel, MAX_SCHEMA_FIELDS
        with pytest.raises(ValueError):
            MetadataSchemaFieldModel(**_field(sequence=MAX_SCHEMA_FIELDS + 1))

    def test_accepts_realistic_sequence(self):
        from models.metadataSchema import MetadataSchemaFieldModel
        model = MetadataSchemaFieldModel(**_field(sequence=3))
        assert model.sequence == 3

    def test_sub_model_bound_reached_through_create_request(self):
        """The field sub-model's validators must run when nested in the request."""
        from models.metadataSchema import CreateMetadataSchemaRequestModel
        with pytest.raises(ValidationError):
            parse(
                _create_body([_field(dependsOnFieldKeyName=["d" * 257])]),
                model=CreateMetadataSchemaRequestModel,
            )


@pytest.mark.unit
class TestSchemaRequestStringBounds:
    def test_rejects_oversized_file_key_type_restriction(self):
        from models.metadataSchema import (
            CreateMetadataSchemaRequestModel, MAX_FILE_KEY_TYPE_RESTRICTION_LENGTH,
        )
        body = _create_body(
            metadataSchemaEntityType="fileMetadata",
            fileKeyTypeRestriction="," .join([".ext"] * MAX_FILE_KEY_TYPE_RESTRICTION_LENGTH),
        )
        with pytest.raises(ValidationError):
            parse(body, model=CreateMetadataSchemaRequestModel)

    def test_accepts_realistic_file_key_type_restriction(self):
        from models.metadataSchema import CreateMetadataSchemaRequestModel
        body = _create_body(
            metadataSchemaEntityType="fileMetadata",
            fileKeyTypeRestriction=".glb,.gltf,.usdz",
        )
        model = parse(body, model=CreateMetadataSchemaRequestModel)
        assert model.fileKeyTypeRestriction == ".glb,.gltf,.usdz"

    def test_rejects_oversized_starting_token(self):
        from models.metadataSchema import (
            GetMetadataSchemasRequestModel, MAX_PAGINATION_TOKEN_LENGTH,
        )
        with pytest.raises(ValidationError):
            parse({"startingToken": "A" * (MAX_PAGINATION_TOKEN_LENGTH + 1)},
                  model=GetMetadataSchemasRequestModel)

    def test_accepts_realistic_starting_token(self):
        from models.metadataSchema import GetMetadataSchemasRequestModel
        model = parse({"startingToken": "MTAw"}, model=GetMetadataSchemasRequestModel)
        assert model.startingToken == "MTAw"

    def test_rejects_oversized_database_id_filter(self):
        from models.metadataSchema import GetMetadataSchemasRequestModel
        with pytest.raises(ValidationError):
            parse({"databaseId": "d" * 257}, model=GetMetadataSchemasRequestModel)

    def test_accepts_global_database_id_filter(self):
        from models.metadataSchema import GetMetadataSchemasRequestModel
        model = parse({"databaseId": "GLOBAL"}, model=GetMetadataSchemasRequestModel)
        assert model.databaseId == "GLOBAL"

    def test_rejects_oversized_metadata_schema_id(self):
        from models.metadataSchema import UpdateMetadataSchemaRequestModel
        with pytest.raises(ValidationError):
            parse({"metadataSchemaId": "a" * 64, "enabled": True},
                  model=UpdateMetadataSchemaRequestModel)

    def test_accepts_uuid_metadata_schema_id(self):
        """Schema IDs are generated as UUID4 strings (36 characters)."""
        from models.metadataSchema import UpdateMetadataSchemaRequestModel
        model = parse(
            {"metadataSchemaId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "enabled": True},
            model=UpdateMetadataSchemaRequestModel,
        )
        assert model.metadataSchemaId == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
