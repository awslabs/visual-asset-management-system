# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Archived assets in the compliance services.

Archiving an asset moves its asset-table row to the partition `<databaseId>#deleted`. Its compliance
record, evaluations, quarantine and audit entries keep the live database id. The asset routes hand
the archived row to `CasbinEnforcerService.enforce` as stored, so the engine allows it only when
both ids pass (the archived-record rule, `_evaluated_database_id`). The compliance services decide
a record or an action of an archived asset the same way: the compliance object goes to `enforce()`
with the stored id `<databaseId>#deleted`. This module holds no authorization rule of its own; it
finds out whether an asset is archived and picks the id to hand the engine.

`ArchivedAssets.decide` reads the archive only when the answer can change the decision:
- `decide` is first asked with the live id. The engine checks the live id as part of every stored
  id, so a caller refused on the live id is refused on the stored id too, and nothing is read.
- It is then asked with the stored id (cached per request for an object that depends only on the
  database). When that passes too, the decision is the same whether the asset is archived or not,
  and nothing is read.
- Otherwise the asset's archived status decides which of the two answers applies: one
  strongly-consistent GetItem of the archived key for a single record, or, in a listing, one Query
  of the database's archived partition (keys only), cached for the request, whatever the page size.
  Once a listing has those ids, the stored id is asked only of the rows they name.
"""

from typing import Callable, Dict, FrozenSet, Hashable, Optional, Tuple

from boto3.dynamodb.conditions import Key

# The partition-key suffix of an archived asset row, as the asset service writes it and the engine
# reads it (`handlers.authz.ARCHIVED_DATABASE_ID_SUFFIX`; a test pins the two together). Kept here
# rather than imported so this module does not load the engine.
ARCHIVED_DATABASE_ID_SUFFIX = "#deleted"

# Scopes and placeholders that name no asset partition: GLOBAL schemas and the `*` of schema-level
# audit entries. Nothing is read for them.
_NO_ASSET_DATABASES = ("", "*", "GLOBAL")


def archived_database_id(database_id: str) -> str:
    """The partition key an archived asset of `database_id` is stored under."""
    return f"{database_id}{ARCHIVED_DATABASE_ID_SUFFIX}"


def stored_database_id(database_id: str, archived: bool) -> str:
    """The database id an asset's row is stored under: `<databaseId>#deleted` when it is archived."""
    return archived_database_id(database_id) if archived else database_id


def is_asset_database(database_id: Optional[str]) -> bool:
    """Whether a database id can have archived assets: a live database id, not GLOBAL, the `*` of a
    schema-level audit entry or an id that is already an archived partition."""
    return (bool(database_id) and database_id not in _NO_ASSET_DATABASES
            and not database_id.endswith(ARCHIVED_DATABASE_ID_SUFFIX))


def names_an_asset(database_id: Optional[str], asset_id: Optional[str]) -> bool:
    """Whether a compliance row's ids can name an archived asset (a live database id and an asset
    id), so that its archived status is worth asking."""
    return is_asset_database(database_id) and bool(asset_id) and asset_id != "*"


def asset_is_archived(asset_table, database_id: str, asset_id: str) -> bool:
    """Whether the asset table holds the asset's archived row. Read strongly consistent: an archive
    writes the archived row before it deletes the live one, so the read sees the move at once."""
    response = asset_table.get_item(
        Key={"databaseId": archived_database_id(database_id), "assetId": asset_id},
        ConsistentRead=True, ProjectionExpression="assetId")
    return bool(response.get("Item"))


def archived_asset_ids(asset_table, database_id: str) -> FrozenSet[str]:
    """The ids of every archived asset of a database: its archived partition, keys only, read to
    exhaustion and strongly consistent."""
    kwargs = {
        "KeyConditionExpression": Key("databaseId").eq(archived_database_id(database_id)),
        "ProjectionExpression": "assetId",
        "ConsistentRead": True,
    }
    ids = set()
    while True:
        response = asset_table.query(**kwargs)
        ids.update(item["assetId"] for item in response.get("Items", []) if item.get("assetId"))
        if "LastEvaluatedKey" not in response:
            return frozenset(ids)
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


class ArchivedAssets:
    """The archived-asset reads of one request, made only when a decision depends on them."""

    def __init__(self, asset_table):
        self._asset_table = asset_table
        self._archived_ids: Dict[str, FrozenSet[str]] = {}
        self._stored_decisions: Dict[Tuple[Hashable, str], bool] = {}

    def archived_ids(self, database_id: str) -> FrozenSet[str]:
        """The database's archived asset ids, read once per request."""
        if database_id not in self._archived_ids:
            self._archived_ids[database_id] = archived_asset_ids(self._asset_table, database_id)
        return self._archived_ids[database_id]

    def is_archived(self, database_id: str, asset_id: str, listing: bool = False) -> bool:
        if listing:
            return asset_id in self.archived_ids(database_id)
        return asset_is_archived(self._asset_table, database_id, asset_id)

    def _stored_decision(self, database_id: str, decide: Callable[[str], bool],
                         cache_key: Optional[Hashable]) -> bool:
        if cache_key is None:
            return bool(decide(archived_database_id(database_id)))
        key = (cache_key, database_id)
        if key not in self._stored_decisions:
            self._stored_decisions[key] = bool(decide(archived_database_id(database_id)))
        return self._stored_decisions[key]

    def decide(self, database_id: str, asset_id: str, decide: Callable[[str], bool],
               cache_key: Optional[Hashable] = None, listing: bool = False) -> bool:
        """`decide(id)` for the id the asset is stored under: `<databaseId>#deleted` when it is
        archived, `database_id` otherwise. `decide` makes the caller's `enforce()` call(s) on the
        compliance object with the given database id. `cache_key` names an object that depends on
        nothing but the database, so its stored-id answer is reused for every row of that database;
        `listing` reads the archived ids of the whole database once instead of one key per row.

        The answer is `decide(database_id)` and, for an archived asset, `decide` of the stored id
        too. Once a listing has read the database's archived ids, a row whose asset is not among
        them is therefore answered on the live id alone, and the stored id is not asked again: for
        an object that names more than the database (a cascade's id), that question would be put,
        and refused, once per row, and every refused check writes an authorization audit entry."""
        if not decide(database_id):
            return False
        if not names_an_asset(database_id, asset_id):
            return True
        if listing and database_id in self._archived_ids:
            if asset_id not in self._archived_ids[database_id]:
                return True
            return self._stored_decision(database_id, decide, cache_key)
        if self._stored_decision(database_id, decide, cache_key):
            return True
        return not self.is_archived(database_id, asset_id, listing)

    def archive_visible(self, database_id: str, decide: Callable[[str], bool],
                        cache_key: Optional[Hashable] = None) -> bool:
        """Whether the caller may see the database's archived assets: `decide` of the stored id
        `<databaseId>#deleted` (which the engine passes only when the live id passes too), asked
        once per database for a `cache_key`. Nothing is read. A scope that names no asset
        partition (GLOBAL, `*`) has no archive to withhold."""
        if not is_asset_database(database_id):
            return True
        return self._stored_decision(database_id, decide, cache_key)

    def withheld(self, database_id: str, decide: Callable[[str], bool],
                 cache_key: Optional[Hashable] = None) -> FrozenSet[str]:
        """The archived asset ids a listing of one database withholds from a caller whom `decide`
        allows on the live id: all of them when the stored id is refused, none when it passes."""
        if self.archive_visible(database_id, decide, cache_key):
            return frozenset()
        return self.archived_ids(database_id)


def withheld_state_counts(asset_state_table, database_id: str, asset_ids,
                          states, error_status: str) -> Tuple[Dict[str, int], int]:
    """`(per-state counts, errors)` of the compliance records of `asset_ids` in a database, read with
    BatchGetItem (100 keys a call, keys and two attributes only), so a summary can leave the
    withheld assets out of its counts. Ids without a record count nowhere."""
    client = asset_state_table.meta.client
    table_name = asset_state_table.name
    counts = dict.fromkeys(states, 0)
    errors = 0
    ids = sorted(asset_ids)
    for start in range(0, len(ids), 100):
        # A table resource's client converts plain values to and from DynamoDB's typed form.
        request = {table_name: {
            "Keys": [{"databaseId": database_id, "assetId": asset_id}
                     for asset_id in ids[start:start + 100]],
            "ProjectionExpression": "assetId, complianceState, lastEvaluationStatus",
            "ConsistentRead": True,
        }}
        for attempt in range(8):
            response = client.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(table_name, []):
                state = item.get("complianceState")
                if state in counts:
                    counts[state] += 1
                if item.get("lastEvaluationStatus") == error_status:
                    errors += 1
            request = response.get("UnprocessedKeys") or {}
            if not request:
                break
        else:
            raise RuntimeError("Compliance records of archived assets could not be read")
    return counts, errors
