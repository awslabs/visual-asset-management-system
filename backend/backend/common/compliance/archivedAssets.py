# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Archived assets in the compliance services.

Archiving an asset moves its asset-table row to the partition `<databaseId>#deleted`. Its compliance
record, evaluations, quarantine and audit entries keep the live database id. This module is the one
place that decides whether a caller may see, or act on, the compliance data of an archived asset.
It holds no authorization rule of its own: both halves of the decision are the shared engine's.

An archived asset's compliance data is the caller's only when the engine allows both:
1. the compliance object with the stored id `<databaseId>#deleted` (the compliance permission on
   the database's archive, which the engine passes only when the live id passes too); and
2. the asset GET on the archived row itself, with its own fields (`databaseId` as stored, the asset
   name, type, tags and any constraint field the deployment adds), annotated `object__type`
   `asset`: the check the asset routes make before they show an archived asset (the asset listing
   with archived assets, `GET` of an asset with `showArchived`).

So compliance gives the answer opening the asset gives. A deny that hides a database's archive from
the asset routes (on `<databaseId>#deleted`, or `contains #deleted`) hides it from compliance too,
and an asset constraint on the asset's own fields (a deny on one tag) is decided per archived asset.

The reads, per request:
- Which way the asset check is decided is the engine's to say: `CasbinEnforcer.policy_object_fields`
  tells which asset fields other than the database id (the asset name, type, tags, or a field the
  deployment adds) any of the caller's policy lines reads, asked once per request.
- None does (every shipped template but `deny-tagged-assets.json`, whose rules read `tags`): one
  asset check on the database's archive, `{databaseId: <databaseId>#deleted}`, decides every
  archived asset of it, and the reads are those the compliance check alone made before: a single
  record reads its archived key, and a listing the database's archived partition (one Query,
  cached), only when the answer depends on whether the asset is archived. A caller allowed on
  both, and on the archive, reads nothing.
- One does: each archived asset is decided on its own row. A single record (`decide`) reads its
  archived key (one strongly-consistent GetItem, projected on the keys and the fields the rules
  read); a listing or a count reads the database's archived partition once (one Query, projected the
  same way, cached). Archived assets that agree on the fields the rules read share one asset check
  (a deny on a tag: one check per distinct tag list), cached for the request.
- In both, the compliance object is asked on the live id first (refused: nothing read), and a
  caller refused the compliance object on the stored id (asked once per database) makes no asset
  check at all.
- Asset checks run on the engine without writing an authorization audit record each; their denials
  are kept and written in one batch at the end of the request (`flush_denials`), the records
  `CasbinEnforcer.enforce` writes, one per denial.

A record that names an asset as archived (a cascade's `skipped_archived` node, the archived
descendants and children counted on a cascade's audit entries) is decided on the asset's archived
row as it is stored now (`archived_asset_visible`); decided per database, on the database's
archive. When each asset is decided on its own row, a record whose asset is no longer in the
archive, or that counts archived assets without naming them (an entry written by an earlier build),
cannot be decided on the asset's own fields and is not shown.

The compliance overview (its rows and per-state counts) and the state, quarantine and exception
listings leave an archived asset out for every caller (`left_out`, `listed`), as the asset listing
does unless it is asked to include archived assets. The asset is not evaluated while it is archived,
so its record would otherwise stay counted in the state it held when it was archived. The record is
kept, and a caller allowed to read it gets it with the state `archived`.
"""

import json
from typing import Any, Callable, Dict, FrozenSet, Hashable, List, Optional, Tuple

from boto3.dynamodb.conditions import Key

# The partition-key suffix of an archived asset row, as the asset service writes it and the engine
# reads it (`handlers.authz.ARCHIVED_DATABASE_ID_SUFFIX`; a test pins the two together). Kept here
# rather than imported so this module does not load the engine.
ARCHIVED_DATABASE_ID_SUFFIX = "#deleted"

# Scopes and placeholders that name no asset partition: GLOBAL schemas and the `*` of schema-level
# audit entries. Nothing is read for them.
_NO_ASSET_DATABASES = ("", "*", "GLOBAL")

# The object type and action of the check the asset routes make before they show an asset.
ASSET_OBJECT_TYPE = "asset"
ASSET_READ_ACTION = "GET"

# Denials of the asset checks made for listings and counts in this request, written in one batch
# by `flush_denials`: (claims_and_roles, object, action). A Lambda serves one request at a time.
_PENDING_DENIALS: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []


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


def asset_check_fields() -> Tuple[str, ...]:
    """The attributes of an asset row the asset check reads: the keys and every constraint field of
    the asset object type, the core ones and any a deployment adds (the engine ignores the others,
    `_scrub_object_fields`). Read when asked, as the engine reads the field matrix."""
    from common.constants import get_constraint_fields_for_object_type

    fields = ["databaseId", "assetId"]
    for name in get_constraint_fields_for_object_type(ASSET_OBJECT_TYPE) or ():
        if isinstance(name, str) and name and name not in fields:
            fields.append(name)
    return tuple(fields)


def _projection(fields) -> Dict[str, Any]:
    """`ProjectionExpression` and `ExpressionAttributeNames` for `fields` (each a placeholder, so
    a reserved word such as `name` or `status` reads as an attribute)."""
    names = {f"#f{index}": name for index, name in enumerate(fields)}
    return {"ProjectionExpression": ", ".join(names), "ExpressionAttributeNames": names}


def _row_projection(fields) -> Dict[str, Any]:
    """The projection of an asset row read: keys only (`assetId`) for no field, otherwise the keys
    and `fields`."""
    if not fields:
        return {"ProjectionExpression": "assetId"}
    return _projection(["databaseId", "assetId"] + [name for name in fields
                                                    if name not in ("databaseId", "assetId")])


def archived_asset_rows(asset_table, database_id: str,
                        fields=()) -> Dict[str, Dict[str, Any]]:
    """Every archived asset row of a database, by asset id: its archived partition, read to
    exhaustion and strongly consistent, keys only, or projected on `fields` and the keys."""
    kwargs = {
        "KeyConditionExpression": Key("databaseId").eq(archived_database_id(database_id)),
        "ConsistentRead": True,
        **_row_projection(fields),
    }
    rows: Dict[str, Dict[str, Any]] = {}
    while True:
        response = asset_table.query(**kwargs)
        for item in response.get("Items", []):
            if item.get("assetId"):
                rows[item["assetId"]] = item
        if "LastEvaluatedKey" not in response:
            return rows
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def archived_asset_ids(asset_table, database_id: str) -> FrozenSet[str]:
    """The ids of every archived asset of a database (`archived_asset_rows`)."""
    return frozenset(archived_asset_rows(asset_table, database_id))


def _asset_object(row: Dict[str, Any]) -> Dict[str, Any]:
    """The object the asset routes hand the engine for a row: the row as stored, `asset` typed."""
    return {**row, "object__type": ASSET_OBJECT_TYPE}


def flush_denials() -> None:
    """Write the denials kept for this request's listing and count checks, one authorization audit
    record per denial in one batch, and forget them. A handler calls it once its response is
    built; it never raises."""
    denials = list(_PENDING_DENIALS)
    _PENDING_DENIALS.clear()
    if not denials:
        return
    try:
        from customLogging.auditLogging import log_authorization_denials

        by_claims: Dict[int, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
        for claims, obj, action in denials:
            entry = by_claims.setdefault(id(claims), (claims, []))
            entry[1].append({"action": action, "obj": obj})
        for claims, records in by_claims.values():
            log_authorization_denials(claims, records)
    except Exception:  # never fail a response on its audit records
        pass


class ArchivedAssets:
    """The archived-asset reads and decisions of one request, made only when a decision depends on
    them. `enforcer` is the caller's `CasbinEnforcer`; without one no archived asset is visible."""

    def __init__(self, asset_table, enforcer=None):
        self._asset_table = asset_table
        self._enforcer = enforcer
        self._archived_rows: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._single_rows: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        self._stored_decisions: Dict[Tuple[Hashable, str], bool] = {}
        self._asset_decisions: Dict[str, bool] = {}
        self._fields_read: Optional[FrozenSet[str]] = None

    # ---- reads ---------------------------------------------------------------------------

    def archived_rows(self, database_id: str) -> Dict[str, Dict[str, Any]]:
        """The database's archived asset rows (projected), read once per request."""
        if database_id not in self._archived_rows:
            self._archived_rows[database_id] = archived_asset_rows(
                self._asset_table, database_id, self._read_fields())
        return self._archived_rows[database_id]

    def archived_ids(self, database_id: str) -> FrozenSet[str]:
        """The database's archived asset ids, read once per request."""
        return frozenset(self.archived_rows(database_id))

    def archived_row(self, database_id: str, asset_id: str,
                     listing: bool = False) -> Optional[Dict[str, Any]]:
        """The asset's archived row, or None when it is not archived: in a listing, from the
        database's archived partition (read once per request); otherwise one strongly-consistent
        GetItem of the archived key, cached."""
        if listing:
            return self.archived_rows(database_id).get(asset_id)
        key = (database_id, asset_id)
        if key not in self._single_rows:
            response = self._asset_table.get_item(
                Key={"databaseId": archived_database_id(database_id), "assetId": asset_id},
                ConsistentRead=True, **_row_projection(self._read_fields()))
            self._single_rows[key] = response.get("Item") or None
        return self._single_rows[key]

    def is_archived(self, database_id: str, asset_id: str, listing: bool = False) -> bool:
        """Whether the asset is archived: in a listing, from the database's archived rows; else one
        read of its archived key, made once per request. Ids that name no asset are not."""
        if not names_an_asset(database_id, asset_id):
            return False
        return self.archived_row(database_id, asset_id, listing) is not None

    def left_out(self, database_id: str) -> FrozenSet[str]:
        """The asset ids whose records the compliance overview and its counts, and the state,
        quarantine and exception listings of a database leave out, whoever the caller: its archived
        assets (read once per request). A scope that names no asset partition (GLOBAL) has none."""
        if not is_asset_database(database_id):
            return frozenset()
        return self.archived_ids(database_id)

    def listed(self, database_id: str, asset_id: str) -> bool:
        """Whether a compliance listing shows the record of an asset: not when it is archived
        (`left_out`)."""
        return not self.is_archived(database_id, asset_id, listing=True)

    # ---- the engine's two checks -----------------------------------------------------------

    def _stored_decision(self, database_id: str, decide: Callable[[str], bool],
                         cache_key: Optional[Hashable]) -> bool:
        if cache_key is None:
            return bool(decide(archived_database_id(database_id)))
        key = (cache_key, database_id)
        if key not in self._stored_decisions:
            self._stored_decisions[key] = bool(decide(archived_database_id(database_id)))
        return self._stored_decisions[key]

    def _read_fields(self) -> FrozenSet[str]:
        """The asset fields, beyond the database id, that any of the caller's policy lines reads
        (`CasbinEnforcer.policy_object_fields`), asked once per request. The asset check of an
        archived row depends on no other field. Every field when there is no answer."""
        if self._fields_read is None:
            other_fields = [name for name in asset_check_fields()
                            if name not in ("databaseId", "assetId")]
            answer = None
            if self._enforcer is not None:
                try:
                    answer = self._enforcer.policy_object_fields(other_fields)
                except Exception:
                    answer = None
            if isinstance(answer, (set, frozenset)):
                self._fields_read = frozenset(name for name in answer if name in other_fields)
            else:
                self._fields_read = frozenset(other_fields)
        return self._fields_read

    def _decided_per_database(self) -> bool:
        """Whether the asset check of an archived row depends on its database id alone: the
        caller's policy lines read no other asset field. Then one check per database decides every
        archived asset of it, and its rows need not be read."""
        return not self._read_fields()

    def _asset_readable(self, row: Dict[str, Any]) -> bool:
        """The asset GET on a row as stored, as the asset routes make it, cached by row. It runs on
        the engine itself; a denial is kept for `flush_denials`, which writes the records
        `CasbinEnforcer.enforce` would, in one batch."""
        enforcer = self._enforcer
        if enforcer is None:
            return False
        obj = _asset_object(row)
        # Rows that agree on the fields the caller's rules read get the same answer.
        cache = json.dumps({name: obj.get(name)
                            for name in sorted(self._read_fields() | {"databaseId"})},
                           sort_keys=True, default=str)
        if cache in self._asset_decisions:
            return self._asset_decisions[cache]
        service = getattr(enforcer, "service_object", None)
        allowed = service is not None and service.enforce(obj, ASSET_READ_ACTION) is True
        if not allowed:
            claims = getattr(enforcer, "claims_and_roles", None)
            _PENDING_DENIALS.append((claims if isinstance(claims, dict) else {}, obj,
                                     ASSET_READ_ACTION))
        self._asset_decisions[cache] = allowed
        return allowed

    def _archive_readable(self, database_id: str) -> bool:
        """The asset GET on the database's archive without an asset's own fields: the decision on
        every archived asset of it when it is decided per database, and on an asset a record names
        as archived that is no longer in the archive."""
        return self._asset_readable({"databaseId": archived_database_id(database_id)})

    def _archive_visible(self, database_id: str, decide: Callable[[str], bool],
                         cache_key: Optional[Hashable]) -> bool:
        return (self._stored_decision(database_id, decide, cache_key)
                and self._archive_readable(database_id))

    # ---- decisions ---------------------------------------------------------------------------

    def decide(self, database_id: str, asset_id: str, decide: Callable[[str], bool],
               cache_key: Optional[Hashable] = None, listing: bool = False) -> bool:
        """The decision on the compliance object of an asset's record. `decide(id)` makes the
        caller's `enforce()` call(s) on the compliance object with the given database id;
        `cache_key` names an object that depends on nothing but the database, so its stored-id
        answer is reused for every row of that database; `listing` reads the archived rows of the
        whole database once instead of one key per row.

        A live asset: `decide(database_id)`. An archived asset: that, and `decide` of the stored id
        `<databaseId>#deleted`, and the asset GET on its archived row (module docstring). When the
        asset GET is decided per database and both pass, the answer is the same whether the asset
        is archived or not, and nothing is read; once a listing has read the database's archived
        ids, a row whose asset is not among them is answered on the live id alone (for an object
        that names more than the database, the stored-id question would otherwise be put, and
        refused, once per row, and every refused check writes an authorization audit entry)."""
        if not decide(database_id):
            return False
        if not names_an_asset(database_id, asset_id):
            return True
        if self._decided_per_database():
            if listing and database_id in self._archived_rows:
                if asset_id not in self._archived_rows[database_id]:
                    return True
                return self._archive_visible(database_id, decide, cache_key)
            if self._archive_visible(database_id, decide, cache_key):
                return True
            return not self.is_archived(database_id, asset_id, listing)
        row = self.archived_row(database_id, asset_id, listing)
        if row is None:
            return True
        return (self._stored_decision(database_id, decide, cache_key)
                and self._asset_readable(row))

    def archived_asset_visible(self, database_id: str, asset_id: Optional[str],
                               decide: Callable[[str], bool],
                               cache_key: Optional[Hashable] = None) -> bool:
        """Whether the caller may see an asset that is, or that a record names as, archived (a
        sweep's skipped archived asset, a skipped-as-archived cascade node, a cascade's archived
        descendants and children): the stored id's compliance object, and the asset GET on its
        archived row. Decided per database when the asset GET is (nothing is read). Otherwise an
        asset no longer in the archive, and `asset_id` None (a count recorded without the ids),
        cannot be decided on the asset's own fields and are not shown."""
        if not is_asset_database(database_id):
            return True
        if not self._stored_decision(database_id, decide, cache_key):
            return False
        if self._decided_per_database():
            return self._archive_readable(database_id)
        if not asset_id:
            return False
        # The listing's archived rows first, then the archived key itself: the asset may have
        # been archived after the partition was read.
        row = (self.archived_rows(database_id).get(asset_id)
               or self.archived_row(database_id, asset_id))
        if row is None:
            return False
        return self._asset_readable(row)

    def withholds(self, database_id: str, asset_id: str, decide: Callable[[str], bool],
                  cache_key: Optional[Hashable] = None) -> bool:
        """Whether a listing or a count leaves an asset out: it is archived and the caller may not
        see it. Decided per database when the asset GET is, and then read only for a caller who
        may not see the database's archive; otherwise from the database's archived rows."""
        if self._decided_per_database():
            if self._archive_visible(database_id, decide, cache_key):
                return False
            return asset_id in self.archived_ids(database_id)
        row = self.archived_rows(database_id).get(asset_id)
        if row is None:
            return False
        return not (self._stored_decision(database_id, decide, cache_key)
                    and self._asset_readable(row))

    def withheld(self, database_id: str, decide: Callable[[str], bool],
                 cache_key: Optional[Hashable] = None) -> FrozenSet[str]:
        """The archived asset ids a listing of one database withholds from a caller whom `decide`
        allows on the live id: those the caller may not see (`archived_asset_visible`). Decided
        per database, all of them or none, and read only when it is all of them."""
        if self._decided_per_database():
            if self._archive_visible(database_id, decide, cache_key):
                return frozenset()
            return self.archived_ids(database_id)
        rows = self.archived_rows(database_id)
        if not rows:
            return frozenset()
        if not self._stored_decision(database_id, decide, cache_key):
            return frozenset(rows)
        return frozenset(asset_id for asset_id, row in rows.items()
                         if not self._asset_readable(row))


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
