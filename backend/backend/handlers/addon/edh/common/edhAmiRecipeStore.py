# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Table access for EDH AMI recipes and their builds.

One partition per recipe: the recipe row under ``sk = RECIPE`` and one row per build under
``sk = BUILD#<version>`` (zero-padded so a range query lists versions in order). A build's Image
Builder ARN is indexed so the completion event finds its row in one read.
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from botocore.exceptions import ClientError

from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import get_table_name
from customLogging.logger import safeLogger

logger = safeLogger(service_name="EdhAmiRecipeStore")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)

# The workstation service imports this module for its launch catalog whether or not the table is
# published to it, so a name not resolved at import is resolved when a read first needs the table.
try:
    table_name = get_table_name(AddonResourceKeys.EDH_AMI_RECIPE_STORAGE_TABLE)
except Exception as e:
    logger.warning(f"AMI recipe table name not resolved at import ({type(e).__name__})")
    table_name = None

RECIPE_PK_PREFIX = "RECIPE#"
RECIPE_SK = "RECIPE"
BUILD_SK_PREFIX = "BUILD#"
IMAGE_BUILD_ARN_INDEX = "imageBuildArnIndex"

STATUS_QUEUED = "QUEUED"
STATUS_BUILDING = "BUILDING"
STATUS_REGISTERING = "REGISTERING"
STATUS_AVAILABLE = "AVAILABLE"
STATUS_REGISTER_FAILED = "REGISTER_FAILED"
STATUS_FAILED = "FAILED"
STATUS_RETIRED = "RETIRED"
ACTIVE_BUILD_STATUSES = frozenset({STATUS_BUILDING, STATUS_REGISTERING})
# The statuses of a build whose row still names the EDH stack a desktop may have been launched from: the
# AVAILABLE build the catalog offers, and a build retired since, whose stack left the catalog while the
# desktops launched from it keep running.
REGISTERED_BUILD_STATUSES = (STATUS_AVAILABLE, STATUS_RETIRED)

SOURCE_CONFIG = "config"
SOURCE_ADMIN = "admin"
# Set on a recipe row while its delete runs.
DELETE_MARKER_FIELD = "deletingAt"

# The recipe fields that shape the AMI; everything else is metadata and never forces a rebuild.
HASHED_FIELDS = ("parentImage", "architecture", "baseOs", "rootSizeGb", "recipeRevision")


def _table():
    global table_name
    if table_name is None:
        table_name = get_table_name(AddonResourceKeys.EDH_AMI_RECIPE_STORAGE_TABLE)
    return dynamodb.Table(table_name)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(recipe: Dict[str, Any], deployment_parameters: Optional[Dict[str, str]] = None) -> str:
    """The hash of the fields that shape the AMI, with the deployment values the recipe's documents
    name (``deployment_parameters``)."""
    canonical: Dict[str, Any] = {field: recipe.get(field) for field in HASHED_FIELDS}
    canonical["components"] = [
        {"arn": c["arn"]} if c.get("arn") else {"name": c.get("name"), "data": c.get("data")}
        for c in recipe.get("components", [])
    ]
    # Asset, parameter and deployment fields join the canonical form only when the recipe carries
    # them; a recipe that declares none hashes the fields above alone.
    if recipe.get("assetPrefix"):
        canonical["assetPrefix"] = recipe["assetPrefix"]
    if recipe.get("assets"):
        canonical["assets"] = sorted(
            ({"id": a.get("id"), "repoPath": a.get("repoPath")} for a in recipe["assets"]),
            key=lambda a: str(a["id"]),
        )
    if recipe.get("parameters"):
        canonical["parameters"] = {str(k): str(v) for k, v in recipe["parameters"].items()}
    if deployment_parameters:
        canonical["deploymentParameters"] = {str(k): str(v) for k, v in deployment_parameters.items()}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recipe_key(recipe_id: str) -> Dict[str, str]:
    return {"pk": f"{RECIPE_PK_PREFIX}{recipe_id}", "sk": RECIPE_SK}


def build_key(recipe_id: str, version: int) -> Dict[str, str]:
    return {"pk": f"{RECIPE_PK_PREFIX}{recipe_id}", "sk": f"{BUILD_SK_PREFIX}{int(version):06d}"}


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def public(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if item is None:
        return None
    return {k: _plain(v) for k, v in item.items() if k not in ("pk", "sk")}


def _clean(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """None values are dropped and floats become Decimals, which is what the table accepts."""
    out = {}
    for k, v in attrs.items():
        if v is None:
            continue
        out[k] = Decimal(str(v)) if isinstance(v, float) else v
    return out


def get_recipe(recipe_id: str) -> Optional[Dict[str, Any]]:
    return public(_table().get_item(Key=recipe_key(recipe_id), ConsistentRead=True).get("Item"))


def put_recipe(item: Dict[str, Any], *, replacing: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Write a recipe row. A new recipe is written only where none exists, and one ``replacing`` a row
    read earlier only while that row is unchanged, so a write made since, such as a build recording its
    pipeline and version, is not overwritten. Returns None when the row is not as expected."""
    now = utc_now()
    row = {**_clean(item), **recipe_key(item["recipeId"])}
    row.setdefault("createdAt", now)
    row["updatedAt"] = now
    if replacing is None:
        condition: Dict[str, Any] = {"ConditionExpression": "attribute_not_exists(pk)"}
    else:
        condition = {"ConditionExpression": "#updatedAt = :read",
                     "ExpressionAttributeNames": {"#updatedAt": "updatedAt"},
                     "ExpressionAttributeValues": {":read": replacing.get("updatedAt")}}
    try:
        _table().put_item(Item=row, **condition)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return public(row)


def update_recipe(recipe_id: str, **attrs) -> Optional[Dict[str, Any]]:
    """Update an existing recipe row. Returns None, and writes nothing, if the recipe does not exist."""
    return _conditional_update(recipe_key(recipe_id), {"ConditionExpression": "attribute_exists(pk)"}, attrs)


def mark_recipe_deleting(recipe_id: str, *, stale_before: str) -> Optional[Dict[str, Any]]:
    """Mark an existing recipe as being deleted and return the marked row. Returns None if the recipe is
    gone or another delete set its marker after ``stale_before``."""
    condition = {
        "ConditionExpression": "attribute_exists(pk) AND (attribute_not_exists(#marker) OR #marker < :stale)",
        "ExpressionAttributeNames": {"#marker": DELETE_MARKER_FIELD},
        "ExpressionAttributeValues": {":stale": stale_before},
    }
    return _conditional_update(recipe_key(recipe_id), condition, {DELETE_MARKER_FIELD: utc_now()})


def release_recipe_deleting(recipe_id: str, marked_at: str) -> bool:
    """Remove the delete marker set at ``marked_at``. False if the recipe is gone or carries another marker."""
    condition = {"ConditionExpression": "#marker = :marked",
                 "ExpressionAttributeNames": {"#marker": DELETE_MARKER_FIELD},
                 "ExpressionAttributeValues": {":marked": marked_at}}
    return _conditional_update(recipe_key(recipe_id), condition, {}, remove=(DELETE_MARKER_FIELD,)) is not None


def list_recipes() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {"FilterExpression": Key("sk").eq(RECIPE_SK)}
    while True:
        page = _table().scan(**kwargs)
        for item in page.get("Items", []):
            if "recipeId" in item:
                rows.append(public(item))
            else:
                logger.warning(f"{item['pk']}: recipe row without a recipeId skipped")
        if "LastEvaluatedKey" not in page:
            return sorted(rows, key=lambda r: r.get("recipeId", ""))
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _partition(recipe_id: str, sk_prefix: Optional[str] = None, *, consistent: bool = False) -> List[Dict[str, Any]]:
    condition = Key("pk").eq(f"{RECIPE_PK_PREFIX}{recipe_id}")
    if sk_prefix:
        condition = condition & Key("sk").begins_with(sk_prefix)
    rows: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {"KeyConditionExpression": condition, "ScanIndexForward": False}
    if consistent:
        kwargs["ConsistentRead"] = True
    while True:
        page = _table().query(**kwargs)
        rows.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return rows
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def delete_recipe_rows(recipe_id: str) -> int:
    rows = _partition(recipe_id, consistent=True)
    with _table().batch_writer() as batch:
        for row in rows:
            batch.delete_item(Key={"pk": row["pk"], "sk": row["sk"]})
    return len(rows)


def list_builds(recipe_id: str, *, consistent: bool = False) -> List[Dict[str, Any]]:
    return [public(r) for r in _partition(recipe_id, BUILD_SK_PREFIX, consistent=consistent)]


def _stack_builds(kind: str, status_condition) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Each build matching ``status_condition`` of image ``kind`` that records its EDH stack id, with its
    recipe, in version order, from one scan. EDH numbers each kind's stacks apart, so a build counts under
    the kind it registered as (``edhKind``), else its recipe's; a build without its recipe row is left out."""
    recipes: Dict[str, Dict[str, Any]] = {}
    builds: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {"FilterExpression": Attr("sk").eq(RECIPE_SK) | (
        Attr("sk").begins_with(BUILD_SK_PREFIX) & status_condition)}
    while True:
        page = _table().scan(**kwargs)
        for item in page.get("Items", []):
            if item.get("sk") == RECIPE_SK:
                if "recipeId" in item:
                    recipes[item["recipeId"]] = public(item)
            elif item.get("edhStackId"):
                builds.append(public(item))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for build in sorted(builds, key=lambda b: int(b["version"])):
        recipe = recipes.get(build.get("recipeId"))
        if recipe and (build.get("edhKind") or recipe.get("kind")) == kind:
            pairs.append((recipe, build))
    return pairs


def _by_stack_id(pairs) -> Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]:
    """The (recipe, build) pairs keyed by the build's EDH stack id; of two builds naming one stack the later
    version, which follows in version order, is kept."""
    return {str(build["edhStackId"]): (recipe, build) for recipe, build in pairs}


def available_builds_by_stack_id(kind: str) -> Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Each AVAILABLE build of image ``kind`` that records its EDH stack id, with its recipe, keyed by
    that id, from one scan. EDH numbers each kind's stacks apart, so a build counts under the kind it
    registered as (``edhKind``), else its recipe's. Of two builds naming one stack the later version is
    kept; a build without its recipe row is left out."""
    return _by_stack_id(_stack_builds(kind, Attr("status").eq(STATUS_AVAILABLE)))


def latest_available_versions(pairs: Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]) -> Dict[str, int]:
    """The newest AVAILABLE build version of each recipe among the (recipe, build) ``pairs``; a recipe with
    no AVAILABLE build among them has no entry."""
    latest: Dict[str, int] = {}
    for recipe, build in pairs:
        if build.get("status") != STATUS_AVAILABLE:
            continue
        version = int(build["version"])
        if version > latest.get(recipe["recipeId"], 0):
            latest[recipe["recipeId"]] = version
    return latest


def registered_builds_by_stack_id(kind: str) -> Tuple[Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]],
                                                      Dict[str, int]]:
    """(each AVAILABLE or RETIRED build of image ``kind`` that records its EDH stack id, with its recipe,
    keyed by that id; the newest AVAILABLE build version of each recipe among them), from one scan. The
    kind rules are those of ``available_builds_by_stack_id``. A desktop keeps the stack it was launched
    from after a newer build retires the build that registered it, and the retired row still names it."""
    pairs = _stack_builds(kind, Attr("status").is_in(list(REGISTERED_BUILD_STATUSES)))
    return _by_stack_id(pairs), latest_available_versions(pairs)


def latest_build(recipe_id: str) -> Optional[Dict[str, Any]]:
    builds = list_builds(recipe_id)
    return builds[0] if builds else None


def next_version(recipe_id: str) -> int:
    latest = latest_build(recipe_id)
    return int(latest["version"]) + 1 if latest else 1


def get_build(recipe_id: str, version: int) -> Optional[Dict[str, Any]]:
    return public(_table().get_item(Key=build_key(recipe_id, version), ConsistentRead=True).get("Item"))


def _build_row(item: Dict[str, Any]) -> Dict[str, Any]:
    row = {**_clean(item), **build_key(item["recipeId"], item["version"])}
    row.setdefault("requestedAt", utc_now())
    return row


def put_build(item: Dict[str, Any]) -> Dict[str, Any]:
    row = _build_row(item)
    _table().put_item(Item=row)
    return public(row)


def claim_new_build(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Write a build row only where its version is free. Returns None if another writer holds the version."""
    row = _build_row(item)
    try:
        _table().put_item(Item=row, ConditionExpression="attribute_not_exists(sk)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return public(row)


def update_build(recipe_id: str, version: int, *, remove: Iterable[str] = (), **attrs) -> Dict[str, Any]:
    """Update a build row, removing the ``remove`` attributes in the same write."""
    return _update(build_key(recipe_id, version), attrs, remove=remove)


def update_build_counting(recipe_id: str, version: int, counter: str, **attrs) -> Dict[str, Any]:
    """Update a build row and add one to an integer counter on it in the same write, so concurrent
    writers each count once."""
    return _update(build_key(recipe_id, version), attrs, increment=counter)


def _status_condition(expected_status: str, *, unlaunched: bool = False) -> Dict[str, Any]:
    """A condition on a build row's status; ``unlaunched`` also requires that no image ARN is recorded,
    which is a claimed row whose pipeline has not started."""
    names = {"#status": "status"}
    expression = "#status = :expected"
    if unlaunched:
        names["#arn"] = "imageBuildVersionArn"
        expression += " AND attribute_not_exists(#arn)"
    return {"ConditionExpression": expression, "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": {":expected": expected_status}}


def _conditional_update(key: Dict[str, str], condition: Dict[str, Any], attrs: Dict[str, Any],
                        remove: Iterable[str] = ()) -> Optional[Dict[str, Any]]:
    attrs = _clean({**attrs, "updatedAt": utc_now()})
    names = {f"#a{i}": k for i, k in enumerate(attrs)}
    values = {f":v{i}": v for i, v in enumerate(attrs.values())}
    expression = "SET " + ", ".join(f"{n} = :v{i}" for i, n in enumerate(names))
    removed = {f"#r{i}": k for i, k in enumerate(remove)}
    if removed:
        expression += " REMOVE " + ", ".join(removed)
    try:
        result = _table().update_item(
            Key=key,
            UpdateExpression=expression,
            ExpressionAttributeNames={**names, **removed, **condition.get("ExpressionAttributeNames", {})},
            ExpressionAttributeValues={**values, **condition.get("ExpressionAttributeValues", {})},
            ConditionExpression=condition["ConditionExpression"],
            ReturnValues="ALL_NEW",
        )
        return public(result.get("Attributes", {}))
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise


def claim_build(recipe_id: str, version: int, expected_status: str, *, remove: Iterable[str] = (),
                **attrs) -> Optional[Dict[str, Any]]:
    """Conditionally update a build row, claiming it from expected_status and removing the ``remove``
    attributes in the same write. Returns None if the condition fails, which includes a row that is gone."""
    return _conditional_update(build_key(recipe_id, version), _status_condition(expected_status), attrs,
                               remove=remove)


def settle_unlaunched(recipe_id: str, version: int, **attrs) -> Optional[Dict[str, Any]]:
    """Update a claimed build row whose pipeline never started. Returns None if the row has moved on."""
    return _conditional_update(build_key(recipe_id, version), _status_condition(STATUS_BUILDING, unlaunched=True),
                               attrs)


def release_claim(recipe_id: str, version: int, expected_status: str = STATUS_BUILDING) -> bool:
    """Delete a claimed build row whose pipeline never started, freeing its version. False if the row
    has moved on or is gone."""
    try:
        _table().delete_item(Key=build_key(recipe_id, version),
                             **_status_condition(expected_status, unlaunched=True))
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def build_by_image_arn(image_build_version_arn: str) -> Optional[Dict[str, Any]]:
    page = _table().query(
        IndexName=IMAGE_BUILD_ARN_INDEX,
        KeyConditionExpression=Key("imageBuildVersionArn").eq(image_build_version_arn),
        Limit=1,
    )
    items = page.get("Items", [])
    return public(items[0]) if items else None


def _update(key: Dict[str, str], attrs: Dict[str, Any], increment: Optional[str] = None,
            remove: Iterable[str] = ()) -> Dict[str, Any]:
    attrs = _clean({**attrs, "updatedAt": utc_now()})
    names = {f"#a{i}": k for i, k in enumerate(attrs)}
    values = {f":v{i}": v for i, v in enumerate(attrs.values())}
    expression = "SET " + ", ".join(f"{n} = :v{i}" for i, n in enumerate(names))
    if increment:
        names["#counter"] = increment
        values[":one"] = 1
        expression += " ADD #counter :one"
    removed = {f"#r{i}": k for i, k in enumerate(remove)}
    if removed:
        names.update(removed)
        expression += " REMOVE " + ", ".join(removed)
    result = _table().update_item(
        Key=key,
        UpdateExpression=expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return public(result.get("Attributes", {}))
