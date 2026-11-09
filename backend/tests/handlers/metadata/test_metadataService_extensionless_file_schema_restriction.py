# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A file with no extension meets only the metadata schemas with no file type restriction (#1036).

`get_aggregated_schemas` applied a schema's `fileKeyTypeRestriction` only when the file had an
extension, so a file named `README` or `LICENSE` was validated against every schema of its database,
restricted ones included: a required field of a `.glb`-only schema was required on `/README`, and a
field that a restricted schema defines with another type than an unrestricted one was checked against
the conflict-resolved definition. The maintainers' decision is that a restricted schema applies only
to files with one of its extensions, so an extensionless file meets only the unrestricted schemas, in
validation and in the read path alike.

## What is asserted, and in what shape

* Every scenario runs through the REAL `get_aggregated_schemas`: this directory's conftest loads the
  real `common.metadataSchemaValidation`, and the DynamoDB stub answers the schema query per
  `databaseId:entityType`, so the database's restricted schemas and the GLOBAL unrestricted one are
  distinct rows exactly as on a deployment.
* Each "the restricted schema no longer applies to README" assertion is paired with a control on a
  `.glb` file under the same schemas, where the restricted schema must still apply. A lookup that
  dropped every restricted schema, or a stub that served nothing, would satisfy the README half alone.
* An unrestricted field is still validated on README (its type is enforced), so the fix narrows which
  schemas apply rather than switching validation off for extensionless files.
* Metadata and attributes are both driven; they are separate entity types with separate schema rows.
* The `restrictMetadataOutsideSchemas` case pins the other direction of the same rule: a field only a
  restricted schema defines is outside the schemas for README, exactly as it already is for a `.stp`
  file, so that database setting refuses it.

The aggregate cache is a module global on `common.metadataSchemaValidation`, which this directory's
conftest loads as a SEPARATE module object from `backend.backend.common.metadataSchemaValidation`;
both are cleared around every test.
"""

import contextlib
import sys

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import VAMSGeneralErrorResponse
from backend.backend.models.assetsV3 import AssetVersionMetadataItemModel
from backend.backend.models.metadata import (
    CreateFileMetadataRequestModel,
    DeleteFileMetadataRequestModel,
    MetadataItemModel,
    UpdateFileMetadataRequestModel,
)

CLAIMS = {"tokens": ["user1"]}

README = "/README"
DOTTED_FOLDER_README = "/rev.2/LICENSE"
GLB_FILE = "/models/part.glb"
OBJ_FILE = "/models/part.obj"

# File metadata fields.
GLB_REQUIRED = "glbRequired"        # required by the database's `.glb`-only schema, no default
EVERY_FILE_NUMBER = "everyFileNumber"  # GLOBAL schema, no restriction, type number
# File attribute fields (attributes are string-only).
GLB_REQUIRED_ATTR = "glbRequiredAttr"  # required by the database's `.glb,.usd` attribute schema
EVERY_FILE_ATTR = "everyFileAttr"      # GLOBAL attribute schema, no restriction


def _field(name, value_type="string", required=False):
    return {
        "M": {
            "metadataFieldKeyName": {"S": name},
            "metadataFieldValueType": {"S": value_type},
            "required": {"BOOL": required},
            "sequence": {"N": "1"},
        }
    }


def _schema(schema_id, database_id, entity_type, fields, restriction=None):
    item = {
        "metadataSchemaId": {"S": schema_id},
        "databaseId": {"S": database_id},
        "schemaName": {"S": schema_id},
        "metadataEntityType": {"S": entity_type},
        "enabled": {"BOOL": True},
        "fields": {"L": list(fields)},
    }
    if restriction is not None:
        item["fileKeyTypeRestriction"] = {"S": restriction}
    return item


# One deployment's schemas, keyed the way the GSI partitions them.
SCHEMA_ROWS = {
    "db1:fileMetadata": [
        _schema("glbOnly", "db1", "fileMetadata",
                [_field(GLB_REQUIRED, required=True)], restriction=".glb"),
        # Defines the GLOBAL number field as a string for `.obj` files: on a file both schemas reach,
        # the definitions conflict and aggregate to an optional string.
        _schema("objOnly", "db1", "fileMetadata",
                [_field(EVERY_FILE_NUMBER, value_type="string")], restriction=".obj"),
    ],
    "GLOBAL:fileMetadata": [
        _schema("everyFile", "GLOBAL", "fileMetadata",
                [_field(EVERY_FILE_NUMBER, value_type="number")]),
    ],
    "db1:fileAttribute": [
        _schema("glbAttrs", "db1", "fileAttribute",
                [_field(GLB_REQUIRED_ATTR, required=True)], restriction=".glb,.usd"),
    ],
    "GLOBAL:fileAttribute": [
        _schema("everyFileAttrs", "GLOBAL", "fileAttribute", [_field(EVERY_FILE_ATTR)]),
    ],
}


def _schema_query(**kwargs):
    composite_key = kwargs["ExpressionAttributeValues"][":pkValue"]["S"]
    return {"Items": list(SCHEMA_ROWS.get(composite_key, []))}


def _paginator(items):
    paginator = MagicMock()
    page_iterator = MagicMock()
    page_iterator.build_full_result.return_value = {"Items": list(items)}
    paginator.paginate.return_value = page_iterator
    return paginator


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """Clear the aggregate cache on BOTH module objects that expose it (see the module docstring)."""
    modules = [
        sys.modules.get("common.metadataSchemaValidation"),
        sys.modules.get("backend.backend.common.metadataSchemaValidation"),
    ]
    for module in modules:
        if module is not None:
            module._schema_cache.clear()
    yield
    for module in modules:
        if module is not None:
            module._schema_cache.clear()


class _Harness:
    """Module globals a file metadata write, delete or read touches.

    `query` answers the REAL schema lookup per database and entity type; the paginator serves the
    file's stored rows; `batch_write_item` is what makes "the write went through" measurable.
    """

    def __init__(self, existing_rows=(), restrict_outside_schemas=False):
        self.client = MagicMock()
        self.client.query.side_effect = _schema_query
        self.client.get_paginator.return_value = _paginator(existing_rows)
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        self.asset_table = MagicMock()
        self.asset_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "asset1", "assetName": "A", "tags": []}
        }
        self.database_table = MagicMock()
        self.database_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "restrictMetadataOutsideSchemas": restrict_outside_schemas}
        }
        self.metadata_tables = {}
        for name in ("asset_file_metadata_table", "file_attribute_table"):
            table = MagicMock()
            table.get_item.return_value = {"Item": {"metadataKey": "stored"}}
            self.metadata_tables[name] = table

        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        self.enforcer_cls = MagicMock(return_value=enforcer)
        self._stack = contextlib.ExitStack()

    def __enter__(self):
        targets = {
            "dynamodb_client": self.client,
            "asset_storage_table": self.asset_table,
            "database_storage_table": self.database_table,
            "CasbinEnforcer": self.enforcer_cls,
            # The write paths check S3 for the file; validation is downstream of it.
            "validate_file_exists": MagicMock(return_value=True),
        }
        targets.update(self.metadata_tables)
        for target, replacement in targets.items():
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False

    @property
    def wrote(self):
        return self.client.batch_write_item.call_count > 0

    @property
    def schema_queries(self):
        return [call.kwargs["ExpressionAttributeValues"][":pkValue"]["S"]
                for call in self.client.query.call_args_list]


def _item(key, value, value_type="string"):
    return MetadataItemModel(metadataKey=key, metadataValue=value, metadataValueType=value_type)


def _update(file_path, items, metadata_type="metadata", update_type="update"):
    return metadataService.update_file_metadata(
        "db1", "asset1",
        UpdateFileMetadataRequestModel(
            filePath=file_path, type=metadata_type, metadata=list(items), updateType=update_type),
        CLAIMS)


def _create(file_path, items, metadata_type="metadata"):
    return metadataService.create_file_metadata(
        "db1", "asset1",
        CreateFileMetadataRequestModel(filePath=file_path, type=metadata_type, metadata=list(items)),
        CLAIMS)


def _refusal(invoke):
    """The refusal message of a write the schemas refuse."""
    with pytest.raises(VAMSGeneralErrorResponse) as raised:
        invoke()
    return str(raised.value)


# --------------------------------------------------------------------------------------------------
# Write paths: create, update and replace-all, metadata and attributes.
# --------------------------------------------------------------------------------------------------

WRITES = [
    ("update", lambda path, items, t: _update(path, items, metadata_type=t)),
    ("replace_all", lambda path, items, t: _update(path, items, metadata_type=t,
                                                   update_type="replace_all")),
    ("create", lambda path, items, t: _create(path, items, metadata_type=t)),
]


@pytest.mark.unit
@pytest.mark.parametrize("mode,write", WRITES, ids=[m for m, _ in WRITES])
class TestAnExtensionlessFileMeetsOnlyUnrestrictedSchemas:

    @pytest.mark.parametrize("file_path", [README, DOTTED_FOLDER_README])
    def test_a_glb_only_required_field_is_not_required_on_an_extensionless_file(
            self, mode, write, file_path):
        """The reported defect: the write was refused with "Required field 'glbRequired' is missing".

        The value is a number on purpose. The `.obj` schema defines the same field as a string, and
        on a file both schemas reached the conflict resolved it to a string, so a number was refused
        too. README meets only the GLOBAL schema, which says number.
        """
        with _Harness() as harness:
            response = write(file_path, [_item(EVERY_FILE_NUMBER, "5", "number")], "metadata")

        assert response.success is True, response
        assert harness.wrote, f"{mode} on {file_path} wrote nothing"
        assert "GLOBAL:fileMetadata" in harness.schema_queries, harness.schema_queries

    def test_the_unrestricted_schema_still_validates_the_extensionless_file(self, mode, write):
        """README is still validated, against the schema that does apply: the GLOBAL number field."""
        with _Harness() as harness:
            message = _refusal(lambda: write(README, [_item(EVERY_FILE_NUMBER, "abc")], "metadata"))

        assert f"Field '{EVERY_FILE_NUMBER}' has incorrect type" in message, message
        assert GLB_REQUIRED not in message, (
            f"the .glb-only schema still applies to {README}: {message}")
        assert not harness.wrote

    def test_a_glb_file_is_still_refused_without_the_glb_required_field(self, mode, write):
        """Control: the restricted schema still applies to a file with one of its extensions."""
        with _Harness() as harness:
            message = _refusal(lambda: write(GLB_FILE, [_item(EVERY_FILE_NUMBER, "5", "number")],
                                             "metadata"))

        assert f"Required field '{GLB_REQUIRED}' is missing" in message, message
        assert not harness.wrote

    def test_a_glb_file_with_the_glb_required_field_is_accepted(self, mode, write):
        with _Harness() as harness:
            response = write(GLB_FILE, [_item(EVERY_FILE_NUMBER, "5", "number"),
                                        _item(GLB_REQUIRED, "g")], "metadata")

        assert response.success is True, response
        assert harness.wrote

    def test_an_obj_file_is_unaffected(self, mode, write):
        """`.obj` meets the `.obj` schema and the GLOBAL one, so the field aggregates to an optional
        string, and the `.glb` required field does not apply."""
        with _Harness() as harness:
            response = write(OBJ_FILE, [_item(EVERY_FILE_NUMBER, "text")], "metadata")

        assert response.success is True, response
        assert harness.wrote

    def test_a_glb_only_required_attribute_is_not_required_on_an_extensionless_file(
            self, mode, write):
        with _Harness() as harness:
            response = write(README, [_item(EVERY_FILE_ATTR, "a")], "attribute")

        assert response.success is True, response
        assert harness.wrote
        assert "GLOBAL:fileAttribute" in harness.schema_queries, harness.schema_queries

    def test_a_glb_file_is_still_refused_without_the_required_attribute(self, mode, write):
        with _Harness() as harness:
            message = _refusal(lambda: write(GLB_FILE, [_item(EVERY_FILE_ATTR, "a")], "attribute"))

        assert f"Required field '{GLB_REQUIRED_ATTR}' is missing" in message, message
        assert not harness.wrote


@pytest.mark.unit
class TestRestrictMetadataOutsideSchemasOnAnExtensionlessFile:
    """A field only a restricted schema defines is outside the schemas for README.

    That is the same answer a `.stp` file already gets, so with `restrictMetadataOutsideSchemas` on
    the key is refused on README and accepted on a `.glb` file.
    """

    def test_a_field_only_the_glb_schema_defines_is_refused_on_an_extensionless_file(self):
        with _Harness(restrict_outside_schemas=True) as harness:
            message = _refusal(lambda: _update(README, [_item(GLB_REQUIRED, "g")]))

        assert f"Field '{GLB_REQUIRED}' is not defined in the metadata schema" in message, message
        assert not harness.wrote

    def test_the_same_field_is_accepted_on_a_glb_file(self):
        with _Harness(restrict_outside_schemas=True) as harness:
            response = _update(GLB_FILE, [_item(GLB_REQUIRED, "g")])

        assert response.success is True, response
        assert harness.wrote

    def test_an_unrestricted_field_is_accepted_on_an_extensionless_file(self):
        with _Harness(restrict_outside_schemas=True) as harness:
            response = _update(README, [_item(EVERY_FILE_NUMBER, "5", "number")])

        assert response.success is True, response
        assert harness.wrote

    def test_a_stored_restricted_only_field_passes_later_writes_and_a_new_one_is_refused(self):
        """The upgrade effect on a database that restricts keys to its schemas.

        A README that stores a value for a field only the `.glb` schema defines (written while that
        schema applied to it) holds a key outside its schemas. A key already stored passes the
        off-schema key check, so writes of other fields and of that stored field itself go through,
        and the field can still be deleted because no schema that applies to README protects it. A
        restricted-only field the record does not store yet is still refused.
        """
        stored = (_stored(GLB_REQUIRED),)
        with _Harness(existing_rows=stored, restrict_outside_schemas=True) as harness:
            response = _update(README, [_item(EVERY_FILE_NUMBER, "5", "number")])
        assert response.success is True, response
        assert harness.wrote

        with _Harness(existing_rows=stored, restrict_outside_schemas=True) as harness:
            response = _update(README, [_item(GLB_REQUIRED, "changed")])
        assert response.success is True, response
        assert harness.wrote

        with _Harness(existing_rows=(_stored(EVERY_FILE_NUMBER, "5", "number"),),
                      restrict_outside_schemas=True) as harness:
            message = _refusal(lambda: _update(README, [_item(GLB_REQUIRED, "g")]))
        assert f"Field '{GLB_REQUIRED}' is not defined in the metadata schema" in message, message
        assert not harness.wrote

        with _Harness(existing_rows=stored, restrict_outside_schemas=True) as harness:
            response = _delete(README, [GLB_REQUIRED])
        assert response.success is True, response
        assert harness.wrote

    def test_the_same_stored_field_does_not_block_a_glb_file(self):
        with _Harness(existing_rows=(_stored(GLB_REQUIRED),),
                      restrict_outside_schemas=True) as harness:
            response = _update(GLB_FILE, [_item(EVERY_FILE_NUMBER, "5", "number")])

        assert response.success is True, response
        assert harness.wrote


# --------------------------------------------------------------------------------------------------
# Delete path: a field only a restricted schema requires is not protected on README.
# --------------------------------------------------------------------------------------------------

def _stored(key, value="v", value_type="string"):
    return {"metadataKey": {"S": key}, "metadataValue": {"S": value},
            "metadataValueType": {"S": value_type}}


def _delete(file_path, keys):
    return metadataService.delete_file_metadata(
        "db1", "asset1",
        DeleteFileMetadataRequestModel(filePath=file_path, type="metadata", metadataKeys=list(keys)),
        CLAIMS)


@pytest.mark.unit
class TestDeletionOnAnExtensionlessFile:

    def test_a_value_only_the_glb_schema_requires_can_be_deleted_from_an_extensionless_file(self):
        with _Harness(existing_rows=(_stored(GLB_REQUIRED),)) as harness:
            response = _delete(README, [GLB_REQUIRED])

        assert response.success is True, response
        assert harness.wrote, "the delete never reached the table"

    def test_the_same_value_is_still_protected_on_a_glb_file(self):
        with _Harness(existing_rows=(_stored(GLB_REQUIRED),)) as harness:
            message = _refusal(lambda: _delete(GLB_FILE, [GLB_REQUIRED]))

        assert "Deletion validation failed" in message, message
        assert not harness.wrote


# --------------------------------------------------------------------------------------------------
# Read path: the schema fields a file's metadata editor shows are the ones validation applies.
# --------------------------------------------------------------------------------------------------

def _schema_rows(response):
    return {row.metadataKey: row for row in response.metadata if row.metadataSchemaField}


@pytest.mark.unit
class TestTheReadPathReportsTheSchemasThatApply:
    """GET returns one row per applicable schema field, stored or not, which is what the web metadata
    editor renders (it computes no schema rule of its own for files)."""

    def test_an_extensionless_file_lists_only_unrestricted_metadata_fields(self):
        with _Harness():
            response = metadataService.get_file_metadata("db1", "asset1", README, "metadata", {}, CLAIMS)

        fields = _schema_rows(response)
        assert EVERY_FILE_NUMBER in fields, (
            f"no schema field came back for {README}, so the enrichment did not run: {fields}")
        assert GLB_REQUIRED not in fields, sorted(fields)
        assert fields[EVERY_FILE_NUMBER].metadataValueType.value == "number"
        assert fields[EVERY_FILE_NUMBER].metadataSchemaMultiFieldConflict is False

    def test_a_glb_file_still_lists_the_glb_required_field(self):
        with _Harness():
            response = metadataService.get_file_metadata("db1", "asset1", GLB_FILE, "metadata", {}, CLAIMS)

        fields = _schema_rows(response)
        assert GLB_REQUIRED in fields, sorted(fields)
        assert fields[GLB_REQUIRED].metadataSchemaRequired is True

    def test_an_extensionless_file_lists_only_unrestricted_attribute_fields(self):
        with _Harness():
            response = metadataService.get_file_metadata("db1", "asset1", README, "attribute", {}, CLAIMS)

        fields = _schema_rows(response)
        assert EVERY_FILE_ATTR in fields, sorted(fields)
        assert GLB_REQUIRED_ATTR not in fields, sorted(fields)

    def test_a_restrict_flag_is_reported_from_the_unrestricted_schemas_alone(self):
        """`restrictMetadataOutsideSchemas` is reported true when any schema applies; README still
        meets the GLOBAL one, so the editor still enforces schema-only keys there."""
        with _Harness(restrict_outside_schemas=True):
            response = metadataService.get_file_metadata("db1", "asset1", README, "metadata", {}, CLAIMS)

        assert response.restrictMetadataOutsideSchemas is True

    def test_a_version_snapshot_read_applies_the_same_rule(self):
        snapshot = [AssetVersionMetadataItemModel(
            type="metadata", filePath=README, metadataKey=EVERY_FILE_NUMBER,
            metadataValue="5", metadataValueType="number")]
        with _Harness(), \
                patch.object(metadataService, "validate_asset_version_exists",
                             MagicMock(return_value=True)), \
                patch.object(metadataService, "get_asset_metadata_version",
                             MagicMock(return_value=snapshot)):
            response = metadataService.get_file_metadata(
                "db1", "asset1", README, "metadata", {"assetVersionId": "v1"}, CLAIMS)

        fields = _schema_rows(response)
        assert EVERY_FILE_NUMBER in fields, sorted(fields)
        assert GLB_REQUIRED not in fields, sorted(fields)
