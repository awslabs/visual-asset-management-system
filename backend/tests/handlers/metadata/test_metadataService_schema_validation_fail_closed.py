# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-060 (HIGH): the metadata schema-validation block must fail CLOSED.

The block guarded by ``if not skip_schema_validation:`` carries four governance controls
-- schema conformance, the controlled-list check, the type-change guard and the
``restrictMetadataOutsideSchemas`` off-schema key prohibition. It used to end in::

    except Exception as e:
        logger.warning(f"Error during schema validation: {e}")
        # Continue without schema validation if it fails

so any error inside it -- a ClientError from the paginated existing-metadata query being
the realistic one -- skipped every control and the write proceeded, answering 200 with
off-schema data stored and only a WARNING line as evidence.

The same eight-line arm appears at eight call sites (create/update asset-link, asset, file
and database metadata). All eight now raise ``VAMSGeneralErrorResponse``, which the
enclosing handler surfaces as a 400. The four DELETE paths carry the same shape around
``validate_metadata_deletion`` and are covered in
test_metadataService_delete_validation_fail_closed.py; the source-level sweep here spans both
families.

## What is asserted, and the positive control

Each denial test also asserts the write never happened -- ``batch_write_item`` uncalled --
because a raised exception with the write already issued would be no better than the
fail-open. ``test_validation_that_completes_still_writes`` is the positive control: with
the paginator working the very same harness reaches the write, so a denial assertion
cannot be passing merely because the harness never gets that far.
"""

import ast
import contextlib
import inspect

import pytest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import (
    SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE,
    VAMSGeneralErrorResponse,
)
from backend.backend.models.metadata import (
    MetadataItemModel,
    UpdateAssetMetadataRequestModel,
    UpdateDatabaseMetadataRequestModel,
)

THROTTLE = ClientError(
    {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttled"}},
    "Query",
)


def _validation_except_handlers(module):
    """Every except-handler that terminates a schema- or deletion-validation block in `module`.

    Located by the log line each arm emits -- the module's own convention, and the same phrase the
    fail-open arms used -- so the assertion can be "none of them falls through" without depending on
    how many such blocks there are.
    """
    tree = ast.parse(inspect.getsource(module))
    handlers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for child in ast.walk(node):
            if (isinstance(child, ast.Constant) and isinstance(child.value, str)
                    and child.value.startswith(("Error during schema validation",
                                                "Error during deletion validation"))):
                handlers.append(node)
                break
    return handlers


def _paginator(raises=None, items=()):
    """A get_paginator stand-in whose build_full_result raises or returns real items."""
    paginator = MagicMock()
    page_iterator = MagicMock()
    if raises is not None:
        page_iterator.build_full_result.side_effect = raises
    else:
        page_iterator.build_full_result.return_value = {"Items": list(items)}
    paginator.paginate.return_value = page_iterator
    return paginator


class _Harness:
    """Module globals an asset-metadata update touches before the write."""

    def __init__(self, paginator):
        self.client = MagicMock()
        self.client.get_paginator.return_value = paginator
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}

        self.asset_table = MagicMock()
        self.asset_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "asset1", "assetName": "A", "tags": []}
        }
        self.database_table = MagicMock()
        self.database_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "restrictMetadataOutsideSchemas": True}
        }
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        self.enforcer_cls = MagicMock(return_value=enforcer)

        self._stack = contextlib.ExitStack()

    def __enter__(self):
        for target, replacement in (
            ("dynamodb_client", self.client),
            ("asset_storage_table", self.asset_table),
            ("database_storage_table", self.database_table),
            ("asset_file_metadata_table", MagicMock()),
            ("database_metadata_table", MagicMock()),
            ("CasbinEnforcer", self.enforcer_cls),
        ):
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


def _request(keys=("offSchemaKey",)):
    return UpdateAssetMetadataRequestModel(
        metadata=[MetadataItemModel(metadataKey=k, metadataValue="x") for k in keys],
        updateType="update",
    )


@pytest.mark.unit
class TestAssetMetadataSchemaValidationFailsClosed:
    def test_existing_metadata_query_failure_denies_the_write(self):
        with _Harness(_paginator(raises=THROTTLE)) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                metadataService.update_asset_metadata(
                    "db1", "asset1", _request(), {"tokens": ["user1"]}
                )

            assert SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value)
            assert harness.client.batch_write_item.call_count == 0, (
                "metadata was written even though validation never ran"
            )

    def test_schema_fetch_failure_denies_the_write(self):
        with _Harness(_paginator(items=[])) as harness, \
                patch.object(metadataService, "get_aggregated_schemas", side_effect=THROTTLE):
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                metadataService.update_asset_metadata(
                    "db1", "asset1", _request(), {"tokens": ["user1"]}
                )

            assert SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value)
            assert harness.client.batch_write_item.call_count == 0

    def test_off_schema_key_check_failure_denies_the_write(self):
        """The restrictMetadataOutsideSchemas prohibition itself erroring must deny."""
        schema = {"declared": {"metadataFieldValueType": "string", "required": False}}
        with _Harness(_paginator(items=[])) as harness, \
                patch.object(metadataService, "get_aggregated_schemas", return_value=schema), \
                patch.object(metadataService, "validate_metadata_against_schema",
                             return_value=(True, [], {})), \
                patch.object(metadataService, "validate_metadata_keys_against_schema",
                             side_effect=RuntimeError("boom")):
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                metadataService.update_asset_metadata(
                    "db1", "asset1", _request(), {"tokens": ["user1"]}
                )

            assert SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value)
            assert harness.client.batch_write_item.call_count == 0

    def test_validation_that_completes_still_writes(self):
        """Positive control: the same harness reaches the write when validation runs."""
        with _Harness(_paginator(items=[])) as harness, \
                patch.object(metadataService, "get_aggregated_schemas", return_value={}), \
                patch.object(metadataService, "validate_metadata_against_schema",
                             return_value=(True, [], {})):
            response = metadataService.update_asset_metadata(
                "db1", "asset1", _request(), {"tokens": ["user1"]}
            )

        assert response.success is True
        assert harness.client.batch_write_item.call_count > 0

    def test_a_declared_schema_violation_still_reports_its_own_reason(self):
        """A real validation failure keeps its specific message, not the generic one."""
        with _Harness(_paginator(items=[])) as harness, \
                patch.object(metadataService, "get_aggregated_schemas", return_value={}), \
                patch.object(metadataService, "validate_metadata_against_schema",
                             return_value=(False, ["field 'declared' is required"], {})):
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                metadataService.update_asset_metadata(
                    "db1", "asset1", _request(), {"tokens": ["user1"]}
                )

        assert "Schema validation failed" in str(raised.value)
        assert harness.client.batch_write_item.call_count == 0


@pytest.mark.unit
class TestOtherEntityTypesShareTheFailClosedArm:
    """The arm is duplicated at every entity type; a second one proves it is not a single site."""

    def test_database_metadata_update_denies_on_query_failure(self):
        with _Harness(_paginator(raises=THROTTLE)) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                metadataService.update_database_metadata(
                    "db1",
                    UpdateDatabaseMetadataRequestModel(
                        metadata=[MetadataItemModel(metadataKey="k", metadataValue="x")],
                        updateType="update",
                    ),
                    {"tokens": ["user1"]},
                )

            assert SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value)
            assert harness.client.batch_write_item.call_count == 0

    def test_no_validation_block_falls_through_its_error_arm(self):
        """The property, over however many blocks exist: none of them logs and continues.

        Stated as "every validation error arm raises" rather than as a count of occurrences. A count
        turns red on a strictly safer implementation -- a ninth write site, or the four metadata
        DELETE blocks being taught to refuse on the same condition -- which is the opposite of what
        this file is for.
        """
        handlers = _validation_except_handlers(metadataService)
        assert handlers, (
            "no schema- or deletion-validation error arm was found at all, so this test would "
            "pass vacuously")

        for handler in handlers:
            assert any(isinstance(node, ast.Raise) for node in ast.walk(handler)), (
                f"the validation error arm at line {handler.lineno} logs and falls through, so "
                f"every control in that block is skipped and the operation proceeds unvalidated")

        # The two old wordings, as a cheap guard against an arm being reinstated verbatim.
        source = inspect.getsource(metadataService)
        assert "Continue without schema validation" not in source
        assert "Continue without validation if it fails" not in source
