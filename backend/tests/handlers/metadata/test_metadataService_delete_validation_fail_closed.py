# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Defect 7, the half the write-path fix left behind: the four metadata DELETE paths.

`validate_metadata_deletion` is the only control that stops the removal of a metadata field a schema
marks `required`, or of a field another schema field declares a `dependsOnFieldKeyName` on. It runs
inside a `try` in each of the four delete functions (asset-link, asset, file, database), and that
`try` used to end in::

    except Exception as e:
        logger.warning(f"Error during deletion validation: {e}")
        # Continue without validation if it fails

So an incomplete schema read -- the `SchemaLookupError` the write path was taught to refuse on, or any
transient DynamoDB error on the existing-metadata read -- deleted the keys with no validation at all
and answered 200. The control was silently disabled by exactly the condition it was hardened against,
and the `SchemaLookupError` docstring claimed the opposite ("raising reaches the correct outcome").

## What is asserted, and why in this shape

* Every scenario runs through the REAL `get_aggregated_schemas`: this directory's conftest loads the
  real `common.metadataSchemaValidation`, so a throttle travels the whole path from the DynamoDB
  client to the caller's error rather than being asserted against a patched stand-in.
* Each refusal is paired with a positive control in the same class. A delete path that refused
  unconditionally, or a harness whose schema query never ran, would satisfy every "it raised"
  assertion while making metadata deletion impossible.
* The legacy-row scenario is the over-tightening catcher. A row written by an earlier version can
  carry no `metadataValueType`; with the block now fail-closed, reading that attribute by subscript
  would have made such an entity's metadata permanently undeletable.
* The REFUSAL message is covered too, not just the could-not-validate one. Every reason
  `validate_metadata_deletion` builds names the metadata key the caller asked to delete -- and the
  schema field that depends on it -- so joining them into the 400 echoed request input back to the
  client (backend/CLAUDE.md Rule 11). The reasons are logged instead, which is asserted in the same
  test so the negative has a positive control.

The module cache is a module-global on `common.metadataSchemaValidation`, which this directory's
conftest loads as a SEPARATE module object from `backend.backend.common.metadataSchemaValidation` --
clearing only the latter leaves the handler's own cache answering the next test's query.
"""

import contextlib
import sys

import pytest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import (
    SCHEMA_DELETION_NOT_ALLOWED_MESSAGE,
    SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE,
    VAMSGeneralErrorResponse,
)
from backend.backend.models.metadata import (
    DeleteAssetLinkMetadataRequestModel,
    DeleteAssetMetadataRequestModel,
    DeleteDatabaseMetadataRequestModel,
    DeleteFileMetadataRequestModel,
)

THROTTLE = ClientError(
    {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttled"}},
    "Query",
)

CLAIMS = {"tokens": ["user1"]}

# Every delete path reads its existing metadata as one typed row per key.
_EXISTING_ROW = {
    "metadataKey": {"S": "governedKey"},
    "metadataValue": {"S": "v"},
    "metadataValueType": {"S": "string"},
}

# The same row as an earlier version could have written it: no metadataValueType at all. The read
# path in handlers/indexing/fileIndexer.py tolerates exactly this shape, which is why it is not
# hypothetical.
_LEGACY_ROW = {"metadataKey": {"S": "governedKey"}, "metadataValue": {"S": "v"}}


def _field(field_name, required=False, depends_on=None):
    """One typed schema field definition."""
    field = {
        "metadataFieldKeyName": {"S": field_name},
        "metadataFieldName": {"S": field_name},
        "metadataFieldValueType": {"S": "string"},
        "required": {"BOOL": required},
        "sequence": {"N": "1"},
    }
    if depends_on is not None:
        field["dependsOnFieldKeyName"] = {"L": [{"S": name} for name in depends_on]}
    return {"M": field}


def _schema_page(*fields):
    """One completed schema query response declaring the given fields."""
    return {
        "Items": [
            {
                "metadataSchemaId": {"S": "schema1"},
                "databaseId": {"S": "db1"},
                "schemaName": {"S": "Schema One"},
                "metadataEntityType": {"S": "assetMetadata"},
                "enabled": {"BOOL": True},
                "fields": {"L": list(fields)},
            }
        ]
    }


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


class _DeleteHarness:
    """Module globals every metadata delete touches before the batch delete.

    The per-key existence `get_item` answers with an Item, so a delete that is allowed to proceed
    reaches `batch_write_item` -- which is what makes "nothing was deleted" measurable.
    """

    def __init__(self, query_side_effect=None, query_return=None, existing_rows=(_EXISTING_ROW,)):
        self.client = MagicMock()
        self.client.get_paginator.return_value = _paginator(existing_rows)
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}
        if query_side_effect is not None:
            self.client.query.side_effect = query_side_effect
        if query_return is not None:
            # A return_value rather than a list: the real lookup issues one query per database in
            # scope (the entity's database plus GLOBAL), and pinning the count would make this a
            # test of how many databases the handler aggregates.
            self.client.query.return_value = query_return

        self.asset_table = MagicMock()
        self.asset_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "asset1", "assetName": "A", "tags": []}
        }
        self.database_table = MagicMock()
        self.database_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "restrictMetadataOutsideSchemas": True}
        }
        self.asset_links_table = MagicMock()
        self.asset_links_table.get_item.return_value = {
            "Item": {
                "assetLinkId": "link1",
                "fromAssetDatabaseId": "db1", "fromAssetId": "asset1",
                "toAssetDatabaseId": "db1", "toAssetId": "asset2",
            }
        }

        self.metadata_tables = {}
        for name in ("asset_links_metadata_table", "asset_file_metadata_table",
                     "file_attribute_table", "database_metadata_table"):
            table = MagicMock()
            table.get_item.return_value = {"Item": {"metadataKey": "governedKey"}}
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
            "asset_links_table": self.asset_links_table,
            "CasbinEnforcer": self.enforcer_cls,
        }
        targets.update(self.metadata_tables)
        for target, replacement in targets.items():
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False

    @property
    def deleted(self):
        return self.client.batch_write_item.call_count > 0


def _delete_asset_metadata(keys=("governedKey",)):
    return metadataService.delete_asset_metadata(
        "db1", "asset1", DeleteAssetMetadataRequestModel(metadataKeys=list(keys)), CLAIMS)


def _delete_file_metadata(keys=("governedKey",)):
    return metadataService.delete_file_metadata(
        "db1", "asset1",
        DeleteFileMetadataRequestModel(
            filePath="/folder/file.txt", type="metadata", metadataKeys=list(keys)),
        CLAIMS)


def _delete_file_attributes(keys=("governedKey",)):
    return metadataService.delete_file_metadata(
        "db1", "asset1",
        DeleteFileMetadataRequestModel(
            filePath="/folder/file.txt", type="attribute", metadataKeys=list(keys)),
        CLAIMS)


def _delete_database_metadata(keys=("governedKey",)):
    return metadataService.delete_database_metadata(
        "db1", DeleteDatabaseMetadataRequestModel(metadataKeys=list(keys)), CLAIMS)


def _delete_asset_link_metadata(keys=("governedKey",)):
    return metadataService.delete_asset_link_metadata(
        "link1", DeleteAssetLinkMetadataRequestModel(metadataKeys=list(keys)), CLAIMS)


# All four delete paths carry their own copy of the validation block, so all four are exercised --
# "fixed at three of four sites" is the failure mode this parametrisation exists to catch. The file
# path is covered in both of its modes because the two read different tables.
DELETE_PATHS = [
    ("assetMetadata", _delete_asset_metadata),
    ("fileMetadata", _delete_file_metadata),
    ("fileAttribute", _delete_file_attributes),
    ("databaseMetadata", _delete_database_metadata),
    ("assetLinkMetadata", _delete_asset_link_metadata),
]


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke", DELETE_PATHS, ids=[n for n, _ in DELETE_PATHS])
class TestAnIncompleteSchemaLookupRefusesTheDeletion:
    def test_a_throttled_schema_query_refuses_the_deletion(self, path_name, invoke):
        """The schema read is the input to the control, so an incomplete read cannot mean "allow"."""
        with _DeleteHarness(query_side_effect=THROTTLE) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                invoke()

        assert SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value), (
            f"{path_name} refused the deletion with an unexpected message: {raised.value}")
        assert harness.client.query.called, (
            "the real schema query was never reached, so this asserts nothing about it")
        assert not harness.deleted, (
            f"{path_name} deleted metadata even though the schema lookup did not complete")

    def test_the_same_path_deletes_when_the_schema_query_completes(self, path_name, invoke):
        """Positive control: an unrelated schema is not a reason to refuse.

        The schema governs a field nobody is deleting, so validation passes and the delete must go
        through -- otherwise the fix has taken metadata deletion down entirely.
        """
        with _DeleteHarness(query_return=_schema_page(_field("someOtherField"))) as harness:
            response = invoke()

        assert response.success is True, f"{path_name}: {response}"
        assert harness.client.query.called
        assert harness.deleted, f"{path_name} refused a deletion the schema does not govern"

    def test_a_failed_existing_metadata_read_refuses_the_deletion(self, path_name, invoke):
        """The other input: the remaining-keys set the dependsOn half of the check needs.

        Without it the check cannot run, so swallowing this error disabled the control just as
        surely as swallowing the schema failure did.
        """
        with _DeleteHarness(query_return=_schema_page(_field("someOtherField"))) as harness:
            harness.client.get_paginator.return_value.paginate.return_value \
                .build_full_result.side_effect = THROTTLE
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                invoke()

        assert SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value)
        assert not harness.deleted, (
            f"{path_name} deleted metadata without knowing what would remain")


@pytest.mark.unit
class TestTheControlItselfStillWorks:
    """Positive controls for the whole file: `validate_metadata_deletion` really is reached, and
    really does refuse. Without these, every assertion above could be satisfied by a delete path
    that never consulted the schema at all."""

    def test_deleting_a_schema_required_field_is_refused(self):
        with _DeleteHarness(query_return=_schema_page(_field("governedKey", required=True))) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                _delete_asset_metadata()

        assert "Deletion validation failed" in str(raised.value), raised.value
        assert not harness.deleted, "a schema-required field was deleted"

    def test_deleting_a_field_another_field_depends_on_is_refused(self):
        """The second half of the control, which needs the remaining-metadata set to fire."""
        remaining = {
            "metadataKey": {"S": "dependentKey"},
            "metadataValue": {"S": "v"},
            "metadataValueType": {"S": "string"},
        }
        page = _schema_page(
            _field("governedKey"),
            _field("dependentKey", depends_on=["governedKey"]),
        )
        with _DeleteHarness(query_return=page,
                            existing_rows=(_EXISTING_ROW, remaining)) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                _delete_asset_metadata()

        assert "Deletion validation failed" in str(raised.value), raised.value
        assert not harness.deleted

    def test_a_field_no_schema_governs_is_still_deletable(self):
        """The control must stay narrow: an off-schema key is not protected."""
        with _DeleteHarness(query_return=_schema_page(_field("someOtherField"))) as harness:
            response = _delete_asset_metadata(keys=("governedKey",))

        assert response.success is True, response
        assert harness.deleted


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke", DELETE_PATHS, ids=[n for n, _ in DELETE_PATHS])
class TestALegacyRowShapeDoesNotBlockDeletion:
    """The over-tightening catcher, and the upgrade path.

    A metadata row written by a 2.5.x deployment can carry no `metadataValueType` -- the file
    indexer tolerates exactly that shape. The deletion check only needs to know which KEYS remain,
    so reading these attributes by subscript inside a now-fail-closed block would turn one legacy
    row into "this entity's metadata can never be deleted", with a validation error as the message.
    """

    def test_a_row_without_a_value_type_is_still_deletable(self, path_name, invoke):
        with _DeleteHarness(query_return=_schema_page(_field("someOtherField")),
                            existing_rows=(_LEGACY_ROW,)) as harness:
            response = invoke()

        assert response.success is True, (
            f"{path_name} refused the deletion because a stored row predates metadataValueType: "
            f"{response}")
        assert harness.deleted


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke", DELETE_PATHS, ids=[n for n, _ in DELETE_PATHS])
class TestARefusalDoesNotEchoTheRequestedKey:
    """Rule 11 at the refusal itself, which is where the caller's own input was reflected.

    `validate_metadata_deletion` builds one reason per refused key and every reason quotes that key,
    so joining the reasons into the message returned to the client echoed request input verbatim. The
    key still has to reach the LOG, or the operator cannot tell which field blocked the delete -- and
    asserting that in the same test is what makes the "not in the response" assertion non-vacuous:
    the key demonstrably was available at the site and was deliberately kept out of the response.
    """

    def test_the_refusal_logs_the_key_and_does_not_return_it(self, path_name, invoke):
        with _DeleteHarness(
                query_return=_schema_page(_field("governedKey", required=True))) as harness, \
                patch.object(metadataService, "logger") as log:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                invoke()

        message = str(raised.value)
        assert "Deletion validation failed" in message, (
            f"{path_name} did not refuse the deletion at all, so this asserts nothing: {message}")
        assert "governedKey" not in message, (
            f"{path_name} echoed the caller's metadata key back in the 400 (Rule 11): {message}")
        logged = " ".join(str(call) for call in log.method_calls)
        assert "governedKey" in logged, (
            f"{path_name} refused without logging which key was refused, so the specifics are lost "
            f"rather than moved to the log: {logged}")
        assert not harness.deleted


@pytest.mark.unit
class TestTheClientFacingMessage:
    """Rule 11 plus honesty: generic, states the outcome, no retry advice."""

    def test_the_message_says_nothing_was_deleted(self):
        assert "nothing was deleted" in SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE

    def test_the_refusal_message_says_nothing_was_deleted(self):
        assert "nothing was deleted" in SCHEMA_DELETION_NOT_ALLOWED_MESSAGE

    def test_the_refusal_message_carries_no_request_input_or_internal_detail(self):
        for fragment in ("db1", "asset1", "link1", "governedKey", "dependentKey",
                         "dynamodb", "Table", "arn:", "Traceback"):
            assert fragment not in SCHEMA_DELETION_NOT_ALLOWED_MESSAGE

    def test_the_two_messages_stay_distinguishable(self):
        """One says the check refused the deletion; the other says the check could not run.

        Conflating them would make a governance refusal look like an infrastructure error in a log
        or a client, which is how the fail-open arm used to read.
        """
        assert SCHEMA_DELETION_NOT_ALLOWED_MESSAGE != SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE
        assert "could not be validated" not in SCHEMA_DELETION_NOT_ALLOWED_MESSAGE

    def test_the_message_does_not_promise_a_retry_will_help(self):
        assert "try again" not in SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE.lower(), (
            f"the message invites a retry for a condition the guard cannot tell apart from a "
            f"permanent one: {SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE!r}")

    def test_the_message_carries_no_request_input_or_internal_detail(self):
        for fragment in ("db1", "asset1", "governedKey", "dynamodb", "Table", "arn:", "Traceback"):
            assert fragment not in SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE
