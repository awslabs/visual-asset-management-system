# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Who may see, or act on, the compliance data of an asset.

An asset's compliance record, evaluations, quarantine, exception, audit entries and cascades are
kept under its live database id, whether the asset is live or archived (archiving moves the
asset-table row to the partition `<databaseId>#deleted` and leaves the compliance data where it
is). This module is the one place that decides whether a caller may see, or act on, that data. It
holds no authorization rule of its own: every part of the decision is the shared engine's.

One rule for live and archived assets. The caller needs the compliance permission the route asks
for, and, when the caller's policy has asset rules, the asset GET the asset routes make before they
show the asset, on the asset's row as stored:

- a live asset: the compliance object on the database id, and the asset GET on the live row;
- an archived asset: the compliance object on the live id and on the stored id
  `<databaseId>#deleted` (the engine passes the stored id only when the live id passes too), and
  the asset GET on the archived row (its own fields: `databaseId` as stored, the asset name, type,
  tags and any constraint field the deployment adds), as the asset listing with archived assets and
  `GET` of an asset with `showArchived` check it.

"Has asset rules" is the engine's answer: any allow or deny line for the object type `asset`, across
all the caller's roles (`CasbinEnforcer.policy_has_object_type`, asked once per request). A caller
without (the global Compliance Admin template) is not asked the asset GET, live or archived; for an
archived asset the compliance check on the stored id still applies. A caller with asset rules who
is refused the asset GET is refused the asset's compliance data, whichever template gave the
compliance permission.

So compliance gives the answer opening the asset gives: a deny that hides an asset from the asset
routes (a deny on one tag), a database's assets, or a database's archive (on `<databaseId>#deleted`,
or `contains #deleted`) hides their compliance data too. Each route asks it in one of three ways:
`access` (one record: visible, denied, or archived and hidden), `decide` (visible or not), and the
database-wide forms for listings and counts (`live_withheld`, `left_out`, `withheld`, `withholds`,
`live_denied`).

The reads and checks, per request:
- Whether the caller has asset rules, and which asset fields other than the database id any of its
  policy lines reads (`CasbinEnforcer.policy_object_fields`), are asked once.
- No asset rules: no asset check and no asset read. An archived record needs the stored-id
  compliance check; whether the asset is archived is read only when that check refuses (a single
  record its archived key, a listing the database's archived partition: one Query, cached).
- Asset rules that read no field but the database id (Database Admin, Database User, the
  database-scoped Compliance Admin and Read-only): one asset check per database, on
  `{databaseId}` for the live assets and on `{databaseId: <databaseId>#deleted}` for the archive,
  each deciding every asset of it. No asset row is read; whether an asset is archived is read, as
  above, only when the two answers differ.
- Asset rules that read asset fields (`deny-tagged-assets.json` reads `tags`): each asset is decided
  on its own row. A single record reads its archived key (one strongly consistent GetItem) and,
  when it is live, its live row (one GetItem). A listing reads the database's archived partition
  once (one Query) and the live rows of the assets its pages hold, one BatchGetItem per 100 ids
  (`expect`, from `listing.query_page`'s `prepare`). A count of a whole database (`live_withheld`)
  reads the database's live partition once (one Query). Every read is projected on the keys and
  the fields the rules read. Assets that agree on those fields share one asset check (a deny on a
  tag: one check per distinct tag list), cached for the request.
- In every case the compliance object is asked on the live id first (refused: nothing read), and
  a caller refused the compliance object on the stored id (asked once per database) makes no asset
  check on the archive at all.
- Asset checks run on the engine without writing an authorization audit record each; their denials
  are kept and written in one batch at the end of the request (`flush_denials`), the records
  `CasbinEnforcer.enforce` writes, one per denial.

A live asset that has no row (a record left by a permanently deleted asset, until it is removed) has
no fields to decide on: it is decided on the database alone (`{databaseId}`), as an asset of the
database. A record that names an asset as archived (a cascade's `skipped_archived` node, the
archived descendants and children counted on a cascade's audit entries) is decided on the asset's
archived row as it is stored now (`archived_asset_visible`); decided per database, on the
database's archive. When each asset is decided on its own row, a record whose asset is no longer in
the archive, or that counts archived assets without naming them (an entry written by an earlier
build), cannot be decided on the asset's own fields and is not shown.

The compliance overview (its rows and per-state counts) and the state, quarantine and exception
listings leave an archived asset out for every caller (`left_out`, `listed`), as the asset listing
does unless it is asked to include archived assets. The asset is not evaluated while it is archived,
so its record would otherwise stay counted in the state it held when it was archived. The record is
kept, and a caller allowed to read it gets it with the state `archived`.
"""

import json
from typing import Any, Callable, Dict, FrozenSet, Hashable, Iterable, List, NamedTuple, Optional, Tuple

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

# The outcomes of `AssetAccess.access` on one record: the caller may see it; it is refused (the
# compliance object, or the asset GET on a live asset); or the asset is archived and the caller may
# not see it.
ACCESS_VISIBLE = "visible"
ACCESS_DENIED = "denied"
ACCESS_ARCHIVED_HIDDEN = "archived_hidden"

# Keys one BatchGetItem may name.
_BATCH_GET_KEYS = 100
# Attempts at a BatchGetItem's unprocessed keys before the request fails (a row that cannot be read
# is never decided as absent).
_BATCH_GET_ATTEMPTS = 8

# Denials of the asset checks made in this request, written in one batch by `flush_denials`:
# (claims_and_roles, object, action). A Lambda serves one request at a time.
_PENDING_DENIALS: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []


class LiveWithheld(NamedTuple):
    """What a listing or a count of one database leaves out of its live assets for a caller:
    `everything` (the asset GET refuses the database's assets), or the `ids` it refuses."""
    everything: bool
    ids: FrozenSet[str]


NOTHING_WITHHELD = LiveWithheld(False, frozenset())


def archived_database_id(database_id: str) -> str:
    """The partition key an archived asset of `database_id` is stored under."""
    return f"{database_id}{ARCHIVED_DATABASE_ID_SUFFIX}"


def stored_database_id(database_id: str, archived: bool) -> str:
    """The database id an asset's row is stored under: `<databaseId>#deleted` when it is archived."""
    return archived_database_id(database_id) if archived else database_id


def is_asset_database(database_id: Optional[str]) -> bool:
    """Whether a database id can have assets: a live database id, not GLOBAL, the `*` of a
    schema-level audit entry or an id that is already an archived partition."""
    return (bool(database_id) and database_id not in _NO_ASSET_DATABASES
            and not database_id.endswith(ARCHIVED_DATABASE_ID_SUFFIX))


def names_an_asset(database_id: Optional[str], asset_id: Optional[str]) -> bool:
    """Whether a compliance row's ids can name an asset (a live database id and an asset id), so
    that the asset decision applies to it."""
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


def _partition_rows(asset_table, partition: str, fields=()) -> Dict[str, Dict[str, Any]]:
    """Every asset row of one partition of the asset table (a database's live assets, or its
    archive `<databaseId>#deleted`), by asset id: read to exhaustion and strongly consistent, keys
    only, or projected on `fields` and the keys."""
    kwargs = {
        "KeyConditionExpression": Key("databaseId").eq(partition),
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


def archived_asset_rows(asset_table, database_id: str,
                        fields=()) -> Dict[str, Dict[str, Any]]:
    """Every archived asset row of a database, by asset id (`_partition_rows` of its archive)."""
    return _partition_rows(asset_table, archived_database_id(database_id), fields)


def archived_asset_ids(asset_table, database_id: str) -> FrozenSet[str]:
    """The ids of every archived asset of a database (`archived_asset_rows`)."""
    return frozenset(archived_asset_rows(asset_table, database_id))


def live_asset_rows(asset_table, keys: List[Tuple[str, str]],
                    fields) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """The live rows of `keys` (`(databaseId, assetId)` pairs), by key, read with BatchGetItem
    (100 keys a call, strongly consistent, projected on the keys and `fields`); a key without a
    row is absent. Unprocessed keys are retried; a row that still cannot be read fails the request
    rather than being decided as absent."""
    client = asset_table.meta.client
    table_name = asset_table.name
    projection = _projection(["databaseId", "assetId"] + [name for name in fields
                                                          if name not in ("databaseId", "assetId")])
    rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for start in range(0, len(keys), _BATCH_GET_KEYS):
        # A table resource's client converts plain values to and from DynamoDB's typed form.
        request = {table_name: {
            "Keys": [{"databaseId": database_id, "assetId": asset_id}
                     for database_id, asset_id in keys[start:start + _BATCH_GET_KEYS]],
            "ConsistentRead": True,
            **projection,
        }}
        for _attempt in range(_BATCH_GET_ATTEMPTS):
            response = client.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(table_name, []):
                rows[(item.get("databaseId"), item.get("assetId"))] = item
            request = response.get("UnprocessedKeys") or {}
            if not request:
                break
        else:
            raise RuntimeError("Asset rows could not be read")
    return rows


def _asset_object(row: Dict[str, Any]) -> Dict[str, Any]:
    """The object the asset routes hand the engine for a row: the row as stored, `asset` typed."""
    return {**row, "object__type": ASSET_OBJECT_TYPE}


def flush_denials() -> None:
    """Write the denials kept for this request's asset checks, one authorization audit record per
    denial in one batch, and forget them. A handler calls it once its response is built; it never
    raises."""
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


class AssetAccess:
    """The asset reads and decisions of one request, made only when a decision depends on them.
    `enforcer` is the caller's `CasbinEnforcer`; without one no asset is visible."""

    def __init__(self, asset_table, enforcer=None):
        self._asset_table = asset_table
        self._enforcer = enforcer
        self._archived_rows: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._single_rows: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        self._live_rows: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        self._live_partitions: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # Keys a listing will decide, read with the next live-row batch (a dict as ordered set).
        self._expected: Dict[Tuple[str, str], None] = {}
        self._stored_decisions: Dict[Tuple[Hashable, str], bool] = {}
        self._asset_decisions: Dict[str, bool] = {}
        self._fields_read: Optional[FrozenSet[str]] = None
        self._asset_rules: Optional[bool] = None

    # ---- the engine's answers about the caller's policy --------------------------------------

    def has_asset_rules(self) -> bool:
        """Whether any of the caller's policy lines, allow or deny, can apply to an asset
        (`CasbinEnforcer.policy_has_object_type`), asked once per request. True without an
        enforcer, or when the answer is not a bool (then the asset check, which fails closed, is
        made)."""
        if self._asset_rules is None:
            answer = True
            if self._enforcer is not None:
                try:
                    value = self._enforcer.policy_has_object_type(ASSET_OBJECT_TYPE)
                except Exception:
                    value = True
                answer = value if isinstance(value, bool) else True
            self._asset_rules = answer
        return self._asset_rules

    def _read_fields(self) -> FrozenSet[str]:
        """The asset fields, beyond the database id, that any of the caller's policy lines reads
        (`CasbinEnforcer.policy_object_fields`), asked once per request. The asset check of a row
        depends on no other field. Every field when there is no answer."""
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

    def decided_per_database(self) -> bool:
        """Whether the asset part of every decision depends on the database id alone: the caller
        has no asset rules, or its policy lines read no asset field but the database id. Then one
        check per database (or none) decides every asset of it, and no asset row is read."""
        return not self.has_asset_rules() or not self._read_fields()

    def reads_asset_rows(self) -> bool:
        """Whether each asset is decided on its own row (`decided_per_database` does not hold)."""
        return not self.decided_per_database()

    # ---- reads ---------------------------------------------------------------------------

    def archived_rows(self, database_id: str) -> Dict[str, Dict[str, Any]]:
        """The database's archived asset rows (projected), read once per request."""
        if database_id not in self._archived_rows:
            self._archived_rows[database_id] = archived_asset_rows(
                self._asset_table, database_id, self._row_fields())
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
                ConsistentRead=True, **_row_projection(self._row_fields()))
            self._single_rows[key] = response.get("Item") or None
        return self._single_rows[key]

    def is_archived(self, database_id: str, asset_id: str, listing: bool = False) -> bool:
        """Whether the asset is archived: in a listing, from the database's archived rows; else one
        read of its archived key, made once per request. Ids that name no asset are not."""
        if not names_an_asset(database_id, asset_id):
            return False
        return self.archived_row(database_id, asset_id, listing) is not None

    def live_rows(self, database_id: str) -> Dict[str, Dict[str, Any]]:
        """The database's live asset rows, projected on the keys and the fields the caller's rules
        read: its partition of the asset table, read once per request (one Query, paged)."""
        if database_id not in self._live_partitions:
            self._live_partitions[database_id] = _partition_rows(
                self._asset_table, database_id, self._row_fields())
        return self._live_partitions[database_id]

    def expect(self, keys: Iterable[Tuple[Optional[str], Optional[str]]]) -> None:
        """Note the `(databaseId, assetId)` keys a listing is about to decide, in the order it will
        decide them, so that their live rows are read 100 to a BatchGetItem as the decisions reach
        them (a page that ends early reads no further). Nothing is noted unless each asset is
        decided on its own row."""
        if not self.reads_asset_rows():
            return
        for database_id, asset_id in keys:
            if not names_an_asset(database_id, asset_id):
                continue
            key = (database_id, asset_id)
            if key in self._live_rows or database_id in self._live_partitions:
                continue
            self._expected[key] = None

    def expect_items(self, database_key: str = "databaseId",
                     asset_key: str = "assetId") -> Callable[[List[Dict[str, Any]]], None]:
        """A `prepare` for `listing.query_page`: notes the keys of each page of items read
        (`expect`), the database and asset ids taken from `database_key` and `asset_key`."""
        def prepare(items: List[Dict[str, Any]]) -> None:
            self.expect((item.get(database_key), item.get(asset_key))
                        for item in items if isinstance(item, dict))
        return prepare

    def live_row(self, database_id: str, asset_id: str,
                 listing: bool = False) -> Optional[Dict[str, Any]]:
        """The asset's live row (projected), or None when it has none: from the database's live
        partition when it was read; in a listing, read with the next keys `expect` noted (up to
        100 to a BatchGetItem); otherwise one strongly-consistent GetItem, cached."""
        key = (database_id, asset_id)
        if key in self._live_rows:
            return self._live_rows[key]
        partition = self._live_partitions.get(database_id)
        if partition is not None:
            return partition.get(asset_id)
        if listing:
            # This key and the next keys `expect` noted, in the order they were noted (the order
            # the listing decides them), one BatchGetItem; an asset the database's archived rows
            # already hold has no live row to read.
            chunk = [key] + [expected for expected in self._expected if expected != key
                             and expected[1] not in self._archived_rows.get(expected[0], ())
                             ][:_BATCH_GET_KEYS - 1]
            for read_key in chunk:
                self._expected.pop(read_key, None)
            found = live_asset_rows(self._asset_table, chunk, self._row_fields())
            for read_key in chunk:
                self._live_rows[read_key] = found.get(read_key)
            return self._live_rows[key]
        response = self._asset_table.get_item(
            Key={"databaseId": database_id, "assetId": asset_id},
            ConsistentRead=True, **_row_projection(self._row_fields() or ("databaseId",)))
        self._live_rows[key] = response.get("Item") or None
        return self._live_rows[key]

    def _row_fields(self) -> FrozenSet[str]:
        """The fields an asset row is read with: those the caller's rules read, when each asset is
        decided on its own row; none (keys only) otherwise."""
        return self._read_fields() if self.reads_asset_rows() else frozenset()

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

    # ---- the engine's checks ---------------------------------------------------------------

    def _stored_decision(self, database_id: str, decide: Callable[[str], bool],
                         cache_key: Optional[Hashable]) -> bool:
        if cache_key is None:
            return bool(decide(archived_database_id(database_id)))
        key = (cache_key, database_id)
        if key not in self._stored_decisions:
            self._stored_decisions[key] = bool(decide(archived_database_id(database_id)))
        return self._stored_decisions[key]

    def _decision_key(self, obj: Dict[str, Any]) -> str:
        """The cache key of an asset check: rows that agree on the database id and on the fields
        the caller's rules read get the same answer."""
        return json.dumps({name: obj.get(name)
                           for name in sorted(self._row_fields() | {"databaseId"})},
                          sort_keys=True, default=str)

    def _asset_readable(self, row: Dict[str, Any]) -> bool:
        """The asset GET on a row as stored, as the asset routes make it, cached by the values of
        the fields the caller's rules read. It runs on the engine itself; a denial is kept for
        `flush_denials`, which writes the records `CasbinEnforcer.enforce` would, in one batch."""
        enforcer = self._enforcer
        if enforcer is None:
            return False
        obj = _asset_object(row)
        cache = self._decision_key(obj)
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

    def database_assets_readable(self, database_id: str) -> bool:
        """The asset GET on a database's live assets without an asset's own fields
        (`{databaseId}`): the decision on every live asset of it when it is decided per database,
        and on a live asset that has no row. Always for a caller without asset rules."""
        if not self.has_asset_rules():
            return True
        # The engine passes the archive's id only when the database's own id passes too, so an
        # archive already found readable answers for the database's live assets without a check.
        archive = self._decision_key({"databaseId": archived_database_id(database_id)})
        if self._asset_decisions.get(archive) is True:
            return True
        return self._asset_readable({"databaseId": database_id})

    def _live_allowed(self, database_id: str, row: Optional[Dict[str, Any]] = None) -> bool:
        """The asset GET on a live asset: on its row when each asset is decided on its own row and
        it has one; otherwise on the database alone."""
        if row is None or self.decided_per_database():
            return self.database_assets_readable(database_id)
        return self._asset_readable(row)

    def _archive_readable(self, database_id: str) -> bool:
        """The asset GET on the database's archive without an asset's own fields: the decision on
        every archived asset of it when it is decided per database, and on an asset a record names
        as archived that is no longer in the archive. Always for a caller without asset rules."""
        if not self.has_asset_rules():
            return True
        return self._asset_readable({"databaseId": archived_database_id(database_id)})

    def _archive_visible(self, database_id: str, decide: Callable[[str], bool],
                         cache_key: Optional[Hashable]) -> bool:
        return (self._stored_decision(database_id, decide, cache_key)
                and self._archive_readable(database_id))

    # ---- decisions ---------------------------------------------------------------------------

    def access(self, database_id: str, asset_id: str, decide: Callable[[str], bool],
               cache_key: Optional[Hashable] = None, listing: bool = False) -> str:
        """The decision on the compliance data of one asset's record: ACCESS_VISIBLE,
        ACCESS_DENIED (the compliance object, or the asset GET on the live asset, refuses it), or
        ACCESS_ARCHIVED_HIDDEN (the asset is archived and the caller may not see it).

        `decide(id)` makes the caller's `enforce()` call(s) on the compliance object with the
        given database id; `cache_key` names an object that depends on nothing but the database, so
        its stored-id answer is reused for every row of that database; `listing` reads a
        database's archived rows once, and the live rows of the keys `expect` noted together,
        instead of one key per row.

        Decided per database (module docstring), nothing is read when the live and the archive
        answers agree; once a listing has read the database's archived ids, a row whose asset is not
        among them is answered on the live answers alone (for an object that names more than the
        database, the stored-id question would otherwise be put, and refused, once per row, and
        every refused check writes an authorization audit entry)."""
        if not decide(database_id):
            return ACCESS_DENIED
        if not names_an_asset(database_id, asset_id):
            return ACCESS_VISIBLE
        if self.decided_per_database():
            if listing and database_id in self._archived_rows:
                if asset_id not in self._archived_rows[database_id]:
                    return (ACCESS_VISIBLE if self.database_assets_readable(database_id)
                            else ACCESS_DENIED)
                return (ACCESS_VISIBLE if self._archive_visible(database_id, decide, cache_key)
                        else ACCESS_ARCHIVED_HIDDEN)
            archive = self._archive_visible(database_id, decide, cache_key)
            live = archive or self.database_assets_readable(database_id)
            if live and archive:
                return ACCESS_VISIBLE
            if not live and not archive:
                return ACCESS_DENIED
            if self.is_archived(database_id, asset_id, listing):
                return ACCESS_VISIBLE if archive else ACCESS_ARCHIVED_HIDDEN
            return ACCESS_VISIBLE if live else ACCESS_DENIED
        row = self.archived_row(database_id, asset_id, listing)
        if row is not None:
            visible = (self._stored_decision(database_id, decide, cache_key)
                       and self._asset_readable(row))
            return ACCESS_VISIBLE if visible else ACCESS_ARCHIVED_HIDDEN
        if self._live_allowed(database_id, self.live_row(database_id, asset_id, listing)):
            return ACCESS_VISIBLE
        return ACCESS_DENIED

    def decide(self, database_id: str, asset_id: str, decide: Callable[[str], bool],
               cache_key: Optional[Hashable] = None, listing: bool = False) -> bool:
        """Whether the caller may see, or act on, the compliance data of an asset's record
        (`access` is ACCESS_VISIBLE)."""
        return self.access(database_id, asset_id, decide, cache_key, listing) == ACCESS_VISIBLE

    def archived_asset_visible(self, database_id: str, asset_id: Optional[str],
                               decide: Callable[[str], bool],
                               cache_key: Optional[Hashable] = None) -> bool:
        """Whether the caller may see an asset that is, or that a record names as, archived (a
        sweep's skipped archived asset, a skipped-as-archived cascade node, a cascade's archived
        descendants and children): the stored id's compliance object, and, for a caller with asset
        rules, the asset GET on its archived row. Decided per database when the asset GET is
        (nothing is read). Otherwise an asset no longer in the archive, and `asset_id` None (a
        count recorded without the ids), cannot be decided on the asset's own fields and are not
        shown."""
        if not is_asset_database(database_id):
            return True
        if not self._stored_decision(database_id, decide, cache_key):
            return False
        if self.decided_per_database():
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
        """Whether a listing or a count leaves an asset out as archived: it is archived and the
        caller may not see it. Decided per database when the asset GET is, and then read only for
        a caller who may not see the database's archive; otherwise from the database's archived
        rows."""
        if self.decided_per_database():
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
        if self.decided_per_database():
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

    def live_denied(self, database_id: str, asset_id: str, listing: bool = True) -> bool:
        """Whether the asset is live and the asset GET on its live row refuses it, so that a sweep
        skips it as not authorized. False for a caller without asset rules and for an archived
        asset (decided as archived). Decided per database, read only when the database's assets
        are refused; otherwise from the archived rows and the live rows (`expect`)."""
        if not names_an_asset(database_id, asset_id) or not self.has_asset_rules():
            return False
        if self.decided_per_database():
            if self.database_assets_readable(database_id):
                return False
            return not self.is_archived(database_id, asset_id, listing)
        if self.archived_row(database_id, asset_id, listing) is not None:
            return False
        return not self._live_allowed(database_id, self.live_row(database_id, asset_id, listing))

    def live_withheld(self, database_id: str) -> LiveWithheld:
        """The live assets a listing or a count of one whole database leaves out for the caller,
        beyond its archived ones (`left_out`): none without asset rules; decided per database, all
        of them (`everything`) or none, with no read; otherwise those whose live row the asset GET
        refuses, from the database's live partition read once (`live_rows`). A record of an asset
        with no row is decided on the database alone and is left out only with `everything`."""
        if not is_asset_database(database_id) or not self.has_asset_rules():
            return NOTHING_WITHHELD
        if self.decided_per_database():
            if self.database_assets_readable(database_id):
                return NOTHING_WITHHELD
            return LiveWithheld(True, frozenset())
        return LiveWithheld(False, frozenset(
            asset_id for asset_id, row in self.live_rows(database_id).items()
            if not self._live_allowed(database_id, row)))


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
    for start in range(0, len(ids), _BATCH_GET_KEYS):
        # A table resource's client converts plain values to and from DynamoDB's typed form.
        request = {table_name: {
            "Keys": [{"databaseId": database_id, "assetId": asset_id}
                     for asset_id in ids[start:start + _BATCH_GET_KEYS]],
            "ProjectionExpression": "assetId, complianceState, lastEvaluationStatus",
            "ConsistentRead": True,
        }}
        for _attempt in range(_BATCH_GET_ATTEMPTS):
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
