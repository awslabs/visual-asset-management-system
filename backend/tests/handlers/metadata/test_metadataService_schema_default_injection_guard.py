# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-060, the part the first fix over-reached on: the additive step stays fail-open.

The finding's recommendation was "fail closed ... Keep the fail-open path only for the purely
additive default-injection step". The applied fix wrapped the WHOLE block, including that step,
so a pydantic ``ValidationError`` while constructing a ``MetadataItemModel`` for a schema-supplied
default turned an otherwise-valid write into a 400. That step runs AFTER schema conformance, the
controlled-list check and the ``restrictMetadataOutsideSchemas`` prohibition have all passed, and
it only ADDS keys the schema declares defaults for -- so a failure there can only lose defaults.
It cannot admit anything the checks refused, which is why the finding singled it out.

The guard is now the inner ``try`` around that step alone, and the outer arm still denies. Both
halves are asserted here, because a fix that narrowed the guard by deleting the outer one would
satisfy "the additive failure still writes" while reopening the whole finding.

Also covered: the client-facing message. It used to end "; try again", which is wrong for the
permanent causes the guard cannot distinguish from the transient ones -- a stored schema this
code cannot read produces the same arm as a throttle.

And the end-to-end fail-closed chain for the incomplete-lookup half: this directory's conftest
loads the REAL ``common.metadataSchemaValidation``, so ``get_aggregated_schemas`` here is the
real function. A throttled schema query therefore travels the real path from the DynamoDB client
to the caller's 400, rather than being asserted against a patched stand-in.
"""

import ast
import contextlib
import inspect
import sys

import pytest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from backend.backend.common import metadataSchemaValidation as msv
from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import (
    SCHEMA_DEFAULT_INJECTION_FAILED_LOG,
    SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE,
    VAMSGeneralErrorResponse,
)
from backend.backend.models.metadata import (
    CreateAssetLinkMetadataRequestModel,
    CreateAssetMetadataRequestModel,
    CreateDatabaseMetadataRequestModel,
    CreateFileMetadataRequestModel,
    MetadataItemModel,
    UpdateAssetMetadataRequestModel,
)

THROTTLE = ClientError(
    {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttled"}},
    "Query",
)


def _default_injection_sites(module):
    """Every additive default-injection step in `module`, with the `try` blocks enclosing it.

    Returns [(for_node, [innermost_try, ..., outermost_try]), ...]. The step is located by what it
    does -- iterating `metadata_with_defaults` to append the keys the schema declares defaults for --
    rather than by a comment or by counting occurrences, so the assertion stays a statement about
    every site instead of about the current shape of the file.
    """
    tree = ast.parse(inspect.getsource(module))
    parent_of = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_of[child] = node

    def enclosing_tries(node):
        chain = []
        current = parent_of.get(node)
        while current is not None:
            if isinstance(current, ast.Try):
                chain.append(current)
            current = parent_of.get(current)
        return chain

    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Call):
            continue
        called = node.iter.func
        if (isinstance(called, ast.Attribute)
                and isinstance(called.value, ast.Name)
                and called.value.id == "metadata_with_defaults"):
            sites.append((node, enclosing_tries(node)))
    return sites


def _handler_names(try_node):
    """Every identifier referenced by any except-handler of a `try`."""
    names = set()
    for handler in try_node.handlers:
        for node in ast.walk(handler):
            if isinstance(node, ast.Name):
                names.add(node.id)
    return names


def _paginator(items=()):
    paginator = MagicMock()
    page_iterator = MagicMock()
    page_iterator.build_full_result.return_value = {"Items": list(items)}
    paginator.paginate.return_value = page_iterator
    return paginator


class _Harness:
    """Module globals an asset-metadata update touches before the write."""

    def __init__(self, paginator=None, query_side_effect=None, query_return=None):
        self.client = MagicMock()
        self.client.get_paginator.return_value = paginator or _paginator()
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}
        if query_side_effect is not None:
            self.client.query.side_effect = query_side_effect
        if query_return is not None:
            # A return_value rather than a side_effect list: the real lookup issues one query
            # per database in scope (the entity's database plus GLOBAL), and pinning the count
            # would make this a test of how many databases the handler aggregates.
            self.client.query.return_value = query_return

        self.asset_table = MagicMock()
        self.asset_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "asset1", "assetName": "A", "tags": []}
        }
        self.database_table = MagicMock()
        self.database_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "restrictMetadataOutsideSchemas": True}
        }
        # The asset-link create path resolves the link and then both of its assets.
        self.asset_links_table = MagicMock()
        self.asset_links_table.get_item.return_value = {
            "Item": {
                "assetLinkId": "link1",
                "fromAssetDatabaseId": "db1", "fromAssetId": "asset1",
                "toAssetDatabaseId": "db1", "toAssetId": "asset2",
            }
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
            ("asset_links_table", self.asset_links_table),
            ("asset_links_metadata_table", MagicMock()),
            ("asset_file_metadata_table", MagicMock()),
            ("file_attribute_table", MagicMock()),
            ("database_metadata_table", MagicMock()),
            ("CasbinEnforcer", self.enforcer_cls),
            # The file create path checks S3 for the file; the guard under test is downstream.
            ("validate_file_exists", MagicMock(return_value=True)),
        ):
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


def _request(keys=("declared",)):
    return UpdateAssetMetadataRequestModel(
        metadata=[MetadataItemModel(metadataKey=k, metadataValue="x") for k in keys],
        updateType="update",
    )


def _create_request(keys=("declared",)):
    """The additive default-injection step lives on the four CREATE paths only."""
    return CreateAssetMetadataRequestModel(
        metadata=[MetadataItemModel(metadataKey=k, metadataValue="x") for k in keys]
    )


def _items(keys=("declared",)):
    return [MetadataItemModel(metadataKey=k, metadataValue="x") for k in keys]


def _create_asset_link_metadata():
    return metadataService.create_asset_link_metadata(
        "link1", CreateAssetLinkMetadataRequestModel(metadata=_items()), {"tokens": ["user1"]})


def _create_asset_metadata():
    return metadataService.create_asset_metadata(
        "db1", "asset1", CreateAssetMetadataRequestModel(metadata=_items()), {"tokens": ["user1"]})


def _create_file_metadata():
    return metadataService.create_file_metadata(
        "db1", "asset1",
        CreateFileMetadataRequestModel(
            filePath="/folder/file.txt", type="metadata", metadata=_items()),
        {"tokens": ["user1"]})


def _create_database_metadata():
    return metadataService.create_database_metadata(
        "db1", CreateDatabaseMetadataRequestModel(metadata=_items()), {"tokens": ["user1"]})


# Each of these carries its own copy of the additive step, so all four are exercised: "narrowed at
# one of four sites" is the failure mode this parametrisation exists to catch.
CREATE_PATHS = [
    ("assetLinkMetadata", _create_asset_link_metadata),
    ("assetMetadata", _create_asset_metadata),
    ("fileMetadata", _create_file_metadata),
    ("databaseMetadata", _create_database_metadata),
]


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """The real aggregate cache is a module global with no per-test reset.

    Cleared on BOTH module objects that expose it: this directory's conftest loads
    `common.metadataSchemaValidation` by file path, which is a different object from
    `backend.backend.common.metadataSchemaValidation` with its own cache dict -- and the handler
    imports the former, so clearing only the latter leaves one test's aggregate answering the next
    test's query.
    """
    modules = [msv, sys.modules.get("common.metadataSchemaValidation")]
    for module in modules:
        if module is not None:
            module._schema_cache.clear()
    yield
    for module in modules:
        if module is not None:
            module._schema_cache.clear()


@pytest.mark.unit
class TestTheAdditiveDefaultInjectionStaysFailOpen:
    """A failure while adding schema defaults loses the defaults; it must not deny the write."""

    def test_a_default_that_cannot_be_constructed_still_writes_the_validated_metadata(self):
        """The `metadataValueType` here is not a member of the enum, so the model raises.

        That is the finding's own sub-scenario: a pydantic ValidationError at the default
        injection. It sits after every check has passed, so the caller's own metadata is
        validated and must be stored.
        """
        defaults = {
            "schemaDefaulted": {
                "metadataValue": "auto",
                "metadataValueType": "not-a-real-type",
            }
        }
        with _Harness() as harness, \
                patch.object(metadataService, "get_aggregated_schemas", return_value={
                    "declared": {"metadataFieldValueType": "string", "required": False}
                }), \
                patch.object(metadataService, "validate_metadata_against_schema",
                             return_value=(True, [], defaults)), \
                patch.object(metadataService, "validate_metadata_keys_against_schema",
                             return_value=(True, [])):
            response = metadataService.create_asset_metadata(
                "db1", "asset1", _create_request(), {"tokens": ["user1"]}
            )

        assert response.success is True, (
            f"a failure in the purely additive default-injection step denied the write: "
            f"{response}"
        )
        assert harness.client.batch_write_item.call_count > 0, (
            "the validated metadata was not written"
        )
        written = harness.client.batch_write_item.call_args.kwargs["RequestItems"]
        rendered = str(written)
        assert "declared" in rendered, f"the caller's own key was dropped: {rendered}"
        assert "schemaDefaulted" not in rendered, (
            f"the default that could not be constructed was written anyway: {rendered}"
        )

    def test_a_default_that_can_be_constructed_is_still_injected(self):
        """Positive control: the injection step works, so the test above is about failure."""
        defaults = {
            "schemaDefaulted": {"metadataValue": "auto", "metadataValueType": "string"}
        }
        with _Harness() as harness, \
                patch.object(metadataService, "get_aggregated_schemas", return_value={
                    "declared": {"metadataFieldValueType": "string", "required": False}
                }), \
                patch.object(metadataService, "validate_metadata_against_schema",
                             return_value=(True, [], defaults)), \
                patch.object(metadataService, "validate_metadata_keys_against_schema",
                             return_value=(True, [])):
            response = metadataService.create_asset_metadata(
                "db1", "asset1", _create_request(), {"tokens": ["user1"]}
            )

        assert response.success is True, response
        rendered = str(harness.client.batch_write_item.call_args.kwargs["RequestItems"])
        assert "schemaDefaulted" in rendered, (
            f"the schema default was never injected, so the fail-open assertion above holds "
            f"for the wrong reason: {rendered}"
        )

    def test_the_outer_arm_still_denies(self):
        """The narrowing must not have been achieved by deleting the fail-closed guard.

        A failure in the off-schema key prohibition -- inside the block, before the additive
        step -- must still refuse the write.
        """
        with _Harness() as harness, \
                patch.object(metadataService, "get_aggregated_schemas", return_value={
                    "declared": {"metadataFieldValueType": "string", "required": False}
                }), \
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

    def test_every_default_injection_site_is_guarded(self):
        """The property, over however many sites exist: each additive step has BOTH arms.

        Asserted structurally rather than behaviourally only because it must hold at every site,
        including one added later -- the behavioural half is
        TestTheAdditiveStepIsGuardedOnEveryCreatePath below. Each site is located by what it DOES
        (iterating `metadata_with_defaults`), so no comment wording, call count or occurrence total
        is pinned: adding a fifth site, or another fail-closed arm anywhere else in the module, does
        not fail this.
        """
        sites = _default_injection_sites(metadataService)
        assert sites, (
            "no additive default-injection step was found at all, so this test would pass "
            "vacuously -- the loop it looks for is `for ... in metadata_with_defaults.items()`")

        for loop, enclosing in sites:
            assert enclosing, (
                f"the additive step at line {loop.lineno} is in no try block at all, so a "
                f"ValidationError there escapes")
            inner = enclosing[0]
            assert "SCHEMA_DEFAULT_INJECTION_FAILED_LOG" in _handler_names(inner), (
                f"the additive step at line {loop.lineno} is not guarded by its own fail-open "
                f"handler, so a failure there denies an otherwise-valid write")
            assert any("SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE" in _handler_names(outer)
                       for outer in enclosing[1:]), (
                f"the additive step at line {loop.lineno} has no enclosing fail-closed arm, so "
                f"the guard was narrowed by deleting the outer one")

    def test_the_fail_open_log_is_distinguishable_from_the_refusal(self):
        """The two arms must not read alike, or a log search cannot tell them apart.

        One says a write was refused because validation could not run; the other says a write
        WENT AHEAD without schema-supplied defaults. Conflating them in the logs is how the
        narrowed guard would look like the fail-open the finding was about.
        """
        assert SCHEMA_DEFAULT_INJECTION_FAILED_LOG != SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE
        assert "without schema-supplied defaults" in SCHEMA_DEFAULT_INJECTION_FAILED_LOG


@pytest.mark.unit
@pytest.mark.parametrize("path_name,invoke", CREATE_PATHS, ids=[n for n, _ in CREATE_PATHS])
class TestTheAdditiveStepIsGuardedOnEveryCreatePath:
    """The behavioural half of "every site is guarded", run through each create entry point.

    Same scenario as the asset-metadata case above -- a schema default whose declared value type is
    not a member of the enum, so constructing the MetadataItemModel raises -- but driven through all
    four paths, because each holds its own copy of the block.
    """

    BROKEN_DEFAULT = {
        "schemaDefaulted": {"metadataValue": "auto", "metadataValueType": "not-a-real-type"}
    }
    GOOD_DEFAULT = {
        "schemaDefaulted": {"metadataValue": "auto", "metadataValueType": "string"}
    }

    @staticmethod
    def _run(invoke, defaults):
        with _Harness() as harness, \
                patch.object(metadataService, "get_aggregated_schemas", return_value={
                    "declared": {"metadataFieldValueType": "string", "required": False}
                }), \
                patch.object(metadataService, "validate_metadata_against_schema",
                             return_value=(True, [], defaults)), \
                patch.object(metadataService, "validate_metadata_keys_against_schema",
                             return_value=(True, [])):
            return invoke(), harness

    def test_a_default_that_cannot_be_constructed_still_writes(self, path_name, invoke):
        response, harness = self._run(invoke, self.BROKEN_DEFAULT)

        assert response.success is True, (
            f"{path_name}: a failure in the purely additive default-injection step denied the "
            f"write: {response}")
        assert harness.client.batch_write_item.call_count > 0, (
            f"{path_name}: the validated metadata was not written")
        rendered = str(harness.client.batch_write_item.call_args.kwargs["RequestItems"])
        assert "declared" in rendered, f"{path_name}: the caller's own key was dropped: {rendered}"

    def test_a_default_that_can_be_constructed_is_still_injected(self, path_name, invoke):
        """Positive control per path: the injection works, so the test above is about failure."""
        response, harness = self._run(invoke, self.GOOD_DEFAULT)

        assert response.success is True, f"{path_name}: {response}"
        rendered = str(harness.client.batch_write_item.call_args.kwargs["RequestItems"])
        assert "schemaDefaulted" in rendered, (
            f"{path_name}: the schema default was never injected, so the fail-open assertion holds "
            f"for the wrong reason: {rendered}")


@pytest.mark.unit
class TestTheClientFacingMessage:
    """Rule 11 plus honesty: generic, and no retry advice for a permanent condition."""

    def test_the_message_does_not_promise_a_retry_will_help(self):
        assert "try again" not in SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE.lower(), (
            f"the message invites a retry for a condition the guard cannot tell apart from a "
            f"permanent one: {SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE!r}"
        )

    def test_the_message_says_nothing_was_written(self):
        """The caller needs the outcome, since the request is refused after validation ran."""
        assert "nothing was written" in SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE

    def test_the_message_carries_no_request_input_or_internal_detail(self):
        for fragment in ("db1", "asset1", "dynamodb", "Table", "arn:", "Traceback"):
            assert fragment not in SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE


@pytest.mark.unit
class TestAnIncompleteSchemaLookupDeniesTheWriteEndToEnd:
    """The Defect-7 chain, through the REAL get_aggregated_schemas.

    This directory's conftest installs the real `common.metadataSchemaValidation`, so nothing
    between the DynamoDB client and the caller's response is stubbed except the storage layer.
    A throttled schema query must not be answered with an empty aggregate that skips the
    off-schema key prohibition.
    """

    def test_a_throttled_schema_query_denies_the_write(self):
        with _Harness(query_side_effect=THROTTLE) as harness:
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                metadataService.update_asset_metadata(
                    "db1", "asset1", _request(keys=("offSchemaKey",)), {"tokens": ["user1"]}
                )

        assert SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE in str(raised.value)
        assert harness.client.query.called, (
            "the real schema query was never reached, so this asserts nothing about it"
        )
        assert harness.client.batch_write_item.call_count == 0, (
            "off-schema metadata was written even though the schema lookup did not complete"
        )

    def test_the_same_path_writes_when_the_schema_query_completes(self):
        """Positive control: an empty-but-complete answer is not a failure.

        A deployment with no metadata schemas returns `{"Items": []}` from the same query, and
        the write must proceed -- otherwise the fix has taken metadata writes down entirely.
        """
        with _Harness(query_return={"Items": []}) as harness:
            response = metadataService.update_asset_metadata(
                "db1", "asset1", _request(keys=("anyKey",)), {"tokens": ["user1"]}
            )

        assert response.success is True, response
        assert harness.client.query.called
        assert harness.client.batch_write_item.call_count > 0

    def test_the_system_user_path_is_unaffected(self):
        """SYSTEM_USER skips the whole schema block, so pipelines cannot be broken by this.

        `skip_schema_validation = (username == "SYSTEM_USER")` gates the block, so a throttled
        schema query never reaches a pipeline write. Pinned because a fail-closed change to a
        validation path is exactly where automation goes down silently.
        """
        with _Harness(query_side_effect=THROTTLE) as harness:
            response = metadataService.update_asset_metadata(
                "db1", "asset1", _request(keys=("offSchemaKey",)), {"tokens": ["SYSTEM_USER"]}
            )

        assert response.success is True, (
            f"a SYSTEM_USER cross-call was denied by the schema-lookup guard: {response}"
        )
        assert harness.client.batch_write_item.call_count > 0
