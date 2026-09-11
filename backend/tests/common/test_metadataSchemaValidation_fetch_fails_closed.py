# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-060 follow-up: an incomplete schema lookup must not read as "no schema".

``get_aggregated_schemas`` used to catch its own per-database query exception, note it in a
local flag, and carry on -- returning the schemas it had managed to read, or an EMPTY dict when
the failing database was the only one in scope. Every control derived from the aggregate treats
absence as permission:

* ``restrictMetadataOutsideSchemas`` is applied by the handlers only inside
  ``if aggregated_schema:``, so an empty aggregate skips the off-schema key prohibition outright;
* a field the schema marks ``required`` is not required if it is not in the aggregate;
* a controlled list that is not in the aggregate constrains nothing.

So a DynamoDB throttle turned a configured governance control off and answered 200, with a
WARNING line as the only evidence. The lookup now raises ``SchemaLookupError``.

## What is asserted, and why in this shape

The raise is the mechanism; the property is that a partial read is never returned as an answer.
``test_a_partial_read_is_not_returned_as_the_answer`` is the one that carries it: the first
database's schemas ARE available and the second query fails, so a test that only checked "the
empty case raises" would pass against an implementation that still returned the partial set.

Each negative is paired with a positive control in the same class, because an implementation
that raised unconditionally -- or a harness whose query never ran at all -- would satisfy every
"it raised" assertion while making the module useless.
"""

import pytest
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from backend.backend.common import metadataSchemaValidation as msv
from backend.backend.common.metadataSchemaValidation import (
    SchemaLookupError,
    get_aggregated_schemas,
)

THROTTLE = ClientError(
    {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttled"}},
    "Query",
)

_TABLE = "test-metadata-schema-table"
_ENTITY = "assetMetadata"


def _schema_item(field_name, database_id, schema_id):
    """One typed DynamoDB schema row carrying a single required field."""
    return {
        "metadataSchemaId": {"S": schema_id},
        "databaseId": {"S": database_id},
        "schemaName": {"S": f"{schema_id}-name"},
        "metadataEntityType": {"S": _ENTITY},
        "enabled": {"BOOL": True},
        "fields": {
            "L": [
                {
                    "M": {
                        "metadataFieldKeyName": {"S": field_name},
                        "metadataFieldName": {"S": field_name},
                        "metadataFieldValueType": {"S": "string"},
                        "required": {"BOOL": True},
                        "sequence": {"N": "1"},
                    }
                }
            ]
        },
    }


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """The 60-second aggregate cache is a module global with no per-test reset.

    Without this, one test's successful lookup answers the next test's query and the negative
    assertions pass without the reader ever being consulted.
    """
    msv._schema_cache.clear()
    yield
    msv._schema_cache.clear()


def _client(side_effect):
    client = MagicMock()
    client.query.side_effect = side_effect
    return client


@pytest.mark.unit
class TestAFailedSchemaQueryFailsClosed:
    def test_a_failing_query_raises_instead_of_returning_an_empty_schema(self):
        client = _client(THROTTLE)

        with pytest.raises(SchemaLookupError):
            get_aggregated_schemas(
                database_ids=["db1"],
                entity_type=_ENTITY,
                file_path=None,
                dynamodb_client=client,
                schema_table_name=_TABLE,
            )

        assert client.query.called, (
            "the reader was never consulted, so the raise above says nothing about how a "
            "query failure is handled"
        )

    def test_a_partial_read_is_not_returned_as_the_answer(self):
        """The real shape: one database answers, the next one throttles.

        An implementation that swallowed the failure would return db1's schema here -- a
        non-empty aggregate, so the handlers' `if aggregated_schema:` guard opens and
        restrictMetadataOutsideSchemas IS applied, but against a field set that is missing
        db2's declarations. Every db2 field then reads as off-schema or as not required.
        """
        client = _client(
            [
                {"Items": [_schema_item("programCode", "db1", "s1")]},
                THROTTLE,
            ]
        )

        with pytest.raises(SchemaLookupError):
            get_aggregated_schemas(
                database_ids=["db1", "db2"],
                entity_type=_ENTITY,
                file_path=None,
                dynamodb_client=client,
                schema_table_name=_TABLE,
            )

        assert client.query.call_count >= 2, (
            f"the second database was never queried, so this is the single-database case "
            f"again rather than a partial read: {client.query.call_count} queries"
        )

    def test_a_completed_lookup_still_returns_its_aggregate(self):
        """Positive control: the module still works, so the raises above are about failure."""
        client = _client([{"Items": [_schema_item("programCode", "db1", "s1")]}])

        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert "programCode" in aggregated, aggregated
        assert aggregated["programCode"]["required"] is True

    def test_a_lookup_that_finds_no_schema_is_not_a_failure(self):
        """An empty answer from a completed query is legitimately an empty aggregate.

        The over-tightening catcher: a deployment with no metadata schemas at all must keep
        writing metadata, so "empty" and "could not read" have to stay distinguishable.
        """
        client = _client([{"Items": []}])

        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert aggregated == {}

    def test_a_failed_lookup_does_not_populate_the_cache(self):
        """A failure must not be remembered as an answer for the next 60 seconds.

        Caching an incomplete aggregate would extend one throttle into a minute of writes
        validated against the wrong field set, on every Lambda instance that saw it.
        """
        failing = _client(THROTTLE)
        with pytest.raises(SchemaLookupError):
            get_aggregated_schemas(
                database_ids=["db1"],
                entity_type=_ENTITY,
                file_path=None,
                dynamodb_client=failing,
                schema_table_name=_TABLE,
            )

        assert msv._schema_cache == {}, (
            f"a failed lookup left an entry in the aggregate cache: {msv._schema_cache}"
        )

        # And the retry really re-queries rather than being served from cache.
        recovered = _client([{"Items": [_schema_item("programCode", "db1", "s1")]}])
        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=recovered,
            schema_table_name=_TABLE,
        )
        assert recovered.query.called, "the retry was served from the cache"
        assert "programCode" in aggregated

    def test_a_successful_lookup_is_still_cached(self):
        """Positive control for the cache assertion above: caching itself still happens."""
        client = _client([{"Items": [_schema_item("programCode", "db1", "s1")]}])
        get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert msv._schema_cache, "a completed lookup was not cached at all"

        second = _client(THROTTLE)
        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=second,
            schema_table_name=_TABLE,
        )
        assert "programCode" in aggregated
        assert not second.query.called, "the cached aggregate was not used"


@pytest.mark.unit
class TestAnUnreadableSchemaRowIsStillSkipped:
    """The distinction the fix must preserve: a bad ROW is not a failed LOOKUP.

    Individual schemas whose fields cannot be parsed or validated are skipped with a warning,
    which is deliberate -- one malformed schema must not block metadata writes for a whole
    database. Only the QUERY failing is fail-closed. Without this, a fix that raised on any
    exception inside the loop would take the deployment down on one bad row.
    """

    def test_a_schema_with_unparseable_fields_is_skipped_not_fatal(self):
        broken = _schema_item("programCode", "db1", "s-broken")
        broken["fields"] = {"S": "{not json"}
        client = _client([{"Items": [broken, _schema_item("good", "db1", "s-good")]}])

        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert "good" in aggregated, aggregated
        assert "programCode" not in aggregated

    def test_a_disabled_schema_is_still_skipped(self):
        disabled = _schema_item("programCode", "db1", "s-disabled")
        disabled["enabled"] = {"BOOL": False}
        client = _client([{"Items": [disabled]}])

        aggregated = get_aggregated_schemas(
            database_ids=["db1"],
            entity_type=_ENTITY,
            file_path=None,
            dynamodb_client=client,
            schema_table_name=_TABLE,
        )

        assert aggregated == {}
