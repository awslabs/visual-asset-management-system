# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""fileAttribute create must raise ValueError when fields is None, not AttributeError (#818)."""

import pytest


@pytest.mark.unit
def test_file_attribute_root_validator_guards_none_fields():
    """Nested field failures leave fields=None; root_validator must not AttributeError."""
    from models.metadataSchema import (
        CreateMetadataSchemaRequestModel,
        MetadataSchemaEntityType,
    )

    with pytest.raises(ValueError, match="fields is required"):
        CreateMetadataSchemaRequestModel.validate_fields(
            {
                "databaseId": "test-db",
                "schemaName": "Attrs",
                "metadataSchemaEntityType": MetadataSchemaEntityType.FILE_ATTRIBUTE,
                "fields": None,
            }
        )


@pytest.mark.unit
def test_docs_shaped_field_keys_parse_for_asset_metadata():
    """CLI docs must show metadataFieldKeyName / metadataFieldValueType (#818)."""
    from aws_lambda_powertools.utilities.parser import parse
    from models.metadataSchema import CreateMetadataSchemaRequestModel

    body = {
        "databaseId": "my-database",
        "metadataSchemaEntityType": "assetMetadata",
        "schemaName": "Asset core fields",
        "fields": {
            "fields": [
                {
                    "metadataFieldKeyName": "projectCode",
                    "metadataFieldValueType": "string",
                    "required": True,
                }
            ]
        },
    }
    model = parse(body, model=CreateMetadataSchemaRequestModel)
    assert model.fields.fields[0].metadataFieldKeyName == "projectCode"
