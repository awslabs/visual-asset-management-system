# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Lifecycle of an AMI recipe: seeding from configuration, Admin edits, versioned builds, the
completion of a build (EDH registration, retirement of the previous stack, pruning of old AMIs), the
periodic sweep and deletion. Shared by the seeding custom resource, the API service and the registrar."""

import os
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from botocore.exceptions import ClientError

from customLogging.logger import safeLogger
from models.addon.edh import (
    AmiRecipeRequestModel, RegisterSimulationImageRequestModel, RegisterVdiImageRequestModel, architecture_family,
)
from .edhCatalogFields import (
    IMAGE_KIND_VDI, KIND_BY_IMAGE_KIND, ensure_stack_in_project, project_assign_error,
    simulation_stack_fields, vdi_stack_fields,
)
from .edhClient import EdhClient, EdhError
from . import edhAmiRecipeStore as store
from . import edhImageBuilder as builder

logger = safeLogger(service_name="EdhAmiRecipes")

KEEP_VERSIONS_DEFAULT = int(os.environ.get("EDH_AMI_KEEP_VERSIONS_DEFAULT", "3"))
BUILD_TIMEOUT_MINUTES = int(os.environ.get("EDH_AMI_BUILD_TIMEOUT_MINUTES", "180"))
REGISTERING_STALE_MINUTES = 30
# A claimed build whose pipeline start was never recorded; longer than any request can run.
CLAIM_STALE_MINUTES = 30
# How many times a request re-reads after another writer took the build version it claimed, or wrote
# the recipe row it was replacing.
CLAIM_ATTEMPTS = 5
# A delete marker older than any request can run belongs to a delete that did not finish.
DELETE_STALE_MINUTES = 30
# How many times the sweep re-registers a build on its own before it waits for an administrator.
REGISTER_RETRY_LIMIT = 5
REGISTER_ATTEMPTS_FIELD = "registerAttempts"
REGISTER_RETRIES_EXHAUSTED = "registration retries exhausted; use Retry registration"
RETIRE_OR_PRUNE_FAILED = "registered; retirement or pruning of earlier versions failed"
# Why a registered stack has no id to assign to the launch project.
CATALOG_NOT_READ_BACK = "the catalog could not be read back"
STACK_ID_NOT_READ_BACK = "its EDH id could not be read back"
IMAGE_GONE = "the Image Builder image no longer exists"
QUEUED_NOT_STARTED = "not started"
LAUNCH_NOT_COMPLETED = "launch did not complete"
RECIPE_ID_TAKEN = "recipeId is already in use"
ASSETS_SHIPPED_ONLY = "assets are only supported on shipped recipes"
DUPLICATE_SOURCE_MISSING = "the recipe to duplicate does not exist"
DUPLICATE_ASSETS_FIXED = "the assets of a duplicated recipe cannot be changed"
# An admin recipe duplicated from another names it, and keeps that recipe's bundled files.
DUPLICATED_FROM_FIELD = "duplicatedFrom"
BUNDLED_FILE_FIELDS = ("assets", "assetPrefix")
RECIPE_DELETING = "the recipe is being deleted"
RECIPE_GONE = "the recipe no longer exists"
RECIPE_CHANGED = "the recipe changed while it was saved; try again"
# A delete refused while pruning still has to remove the AMI of one of the recipe's builds.
AMI_REMOVAL_PENDING = "a build's AMI removal is pending"
# A parent image or build instance type EC2 reports as running another architecture family than the
# recipe's: refused at build start (409) and, for the build instance types and an ami- or ssm: parent,
# when the recipe is saved (400).
PARENT_ARCHITECTURE_MISMATCH = "the parent image's architecture does not match the recipe's architecture"
BUILD_TYPE_ARCHITECTURE_MISMATCH = "buildInstanceType does not match the recipe's architecture"
PARENT_IMAGE_ARCHITECTURE_INVALID = "parentImage does not match the recipe's architecture"
# A recipe save starts no parent image lookup once its architecture lookups have run this many seconds.
# One lookup on edhImageBuilder.lookup_retry_config ends within about 11 s, so a save's lookups end within
# about 16 s, inside the API's deadline.
ARCHITECTURE_LOOKUP_BUDGET_SECONDS = 5
# An AMI an instance uses is kept: its build row names the instances (sorted, at most
# AMI_IN_USE_LIST_MAX), and a retired build whose AMI was kept is flagged until the sweep removes it.
AMI_IN_USE_FIELD = "amiInUseBy"
AMI_REMOVAL_PENDING_FIELD = "amiRemovalPending"
AMI_IN_USE_LIST_MAX = 20
IN_USE_CHECK_FAILED = "in-use check failed"

REQUESTED_BY_DEPLOY = "deploy"
OUTCOME_STARTED = "started"
OUTCOME_QUEUED = "queued"
OUTCOME_SKIPPED_UNCHANGED = "skipped-unchanged"
OUTCOME_REFUSED_DISABLED = "refused-disabled"
OUTCOME_IN_PROGRESS = "in-progress"

IMAGE_STATUS_AVAILABLE = "AVAILABLE"
IMAGE_TERMINAL_FAILURES = ("FAILED", "CANCELLED", "DELETED", "DEPRECATED")
# Statuses whose content hash counts as "this content is already built or being built".
SETTLED_OR_ACTIVE = (store.STATUS_QUEUED, store.STATUS_BUILDING, store.STATUS_REGISTERING, store.STATUS_AVAILABLE)
# Statuses of a build queued or in progress: it answers a manual request for the same content and
# holds off a delete of its recipe.
IN_FLIGHT = (store.STATUS_QUEUED, store.STATUS_BUILDING, store.STATUS_REGISTERING)
# Statuses of a build beyond the keepVersions window whose AMI pruning removes.
PRUNABLE = (store.STATUS_RETIRED, store.STATUS_REGISTER_FAILED, store.STATUS_FAILED)
EDH_STACK_ID_FIELD = "id"
EDH_STACK_NAME_FIELD = "stack_name"


class RecipeConflict(Exception):
    """A request the recipe's current state refuses; the API answers it with 409."""


class RecipeInvalid(ValueError):
    """A request body the recipe's architecture refuses; the API answers it with 400."""


def _ctx() -> builder.ImageBuilderContext:
    return builder.context_from_env()


def _edh() -> EdhClient:
    return EdhClient(profile="batch")


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------
def recipe_item(model: AmiRecipeRequestModel, *, source: str, user_id: str,
                existing: Optional[Dict[str, Any]] = None,
                asset_prefix: Optional[str] = None,
                inherited: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The recipe row for ``model``. ``inherited`` holds the bundled files and provenance an admin
    recipe takes from the recipe it was duplicated from; they replace whatever the body carried."""
    item = model.dict(exclude_none=True, exclude={"duplicateOf"})
    item["components"] = [c.dict(exclude_none=True) for c in model.components]
    item["source"] = source
    item["keepVersions"] = model.keepVersions or KEEP_VERSIONS_DEFAULT
    if asset_prefix:
        item["assetPrefix"] = asset_prefix
    item.update(inherited or {})
    item["contentHash"] = store.content_hash(
        item, builder.referenced_deployment_parameters(item, builder.deployment_parameters_from_env()))
    item["createdBy"] = existing.get("createdBy", user_id) if existing else user_id
    item["updatedBy"] = user_id
    if existing:
        for key in ("latestVersion", "imageBuilder", "createdAt", "buildOnDeploy", store.DELETE_MARKER_FIELD):
            if key in existing:
                item[key] = existing[key]
    return item


def upsert_config_recipe(manifest: Dict[str, Any], *, build_on_deploy: bool) -> Tuple[Optional[Dict[str, Any]], str]:
    model = AmiRecipeRequestModel(**manifest)
    for _ in range(CLAIM_ATTEMPTS):
        existing = store.get_recipe(model.recipeId)
        if existing and existing.get("source") == store.SOURCE_ADMIN:
            return None, "admin-owned"
        item = recipe_item(model, source=store.SOURCE_CONFIG, user_id=REQUESTED_BY_DEPLOY,
                           existing=existing, asset_prefix=manifest.get("assetPrefix"))
        item["buildOnDeploy"] = build_on_deploy
        item["enabled"] = True
        saved = store.put_recipe(item, replacing=existing)
        if saved:
            changed = existing is None or existing.get("contentHash") != item["contentHash"]
            return saved, ("changed" if changed else "unchanged")
    raise RecipeConflict(RECIPE_CHANGED)


def disable_missing_config_recipes(present_ids: Iterable[str]) -> List[str]:
    present = set(present_ids)
    disabled = []
    for recipe in store.list_recipes():
        if recipe.get("source") == store.SOURCE_CONFIG and recipe["recipeId"] not in present and recipe.get("enabled", True):
            store.update_recipe(recipe["recipeId"], enabled=False)
            disabled.append(recipe["recipeId"])
    return disabled


def _require_materializable(item: Dict[str, Any]) -> None:
    """Resolve the recipe's component documents as its build would, so a document that cannot be
    materialized is refused when the recipe is saved."""
    try:
        builder.component_documents(item, os.environ.get("EDH_AMI_BUILD_ARTEFACTS_BUCKET", ""),
                                    builder.deployment_parameters_from_env())
    except builder.ComponentDocumentError as err:
        raise RecipeConflict(str(err))


def _error_name(err: BaseException) -> str:
    """The AWS error code of a ClientError, else the exception's class name."""
    if isinstance(err, ClientError):
        return str(err.response.get("Error", {}).get("Code") or type(err).__name__)
    return type(err).__name__


def _build_types_mismatch(ec2, recipe_id: str, family: str, instance_types: List[str]) -> bool:
    """Whether EC2 lists a build instance type under architectures of other families only. A lookup that
    fails is logged and refuses nothing; a type EC2 does not describe, or reports no architecture of a
    known family for, is logged and refuses nothing while the other types are still checked."""
    try:
        supported = builder.instance_type_architectures(ec2, instance_types)
    except Exception as err:
        logger.warning(f"{recipe_id}: build instance types not checked against the architecture ({_error_name(err)})")
        return False
    mismatched = []
    for instance_type in instance_types:
        if instance_type not in supported:
            logger.warning(f"{recipe_id}: EC2 does not describe {instance_type}; not checked against the architecture")
            continue
        families = {architecture_family(a) for a in supported[instance_type]} - {None}
        if not families:
            logger.warning(f"{recipe_id}: EC2 reports no architecture of a known family for {instance_type}")
        elif family not in families:
            mismatched.append(instance_type)
    if mismatched:
        logger.warning(f"{recipe_id}: build instance type(s) {', '.join(mismatched)} run no {family} architecture")
    return bool(mismatched)


def _lookup_budget_spent(recipe_id: str, started: float) -> bool:
    """Whether a save's architecture lookups, begun at ``started``, have run ARCHITECTURE_LOOKUP_BUDGET_SECONDS;
    the parent image is then not checked, which is logged."""
    if monotonic() - started < ARCHITECTURE_LOOKUP_BUDGET_SECONDS:
        return False
    logger.warning(f"{recipe_id}: parent image architecture not checked "
                   f"(lookup budget of {ARCHITECTURE_LOOKUP_BUDGET_SECONDS} s spent)")
    return True


def require_architecture_match(model: AmiRecipeRequestModel, ec2, ssm) -> None:
    """Refuse (RecipeInvalid) a recipe whose build instance types, or whose ``ami-`` or ``ssm:`` parent
    image, EC2 reports as running another architecture family than the recipe's. A lookup that fails, a
    parent of no known architecture and an ``edhStack:`` parent are logged and refuse nothing, and so is a
    parent lookup due after the lookups have run ARCHITECTURE_LOOKUP_BUDGET_SECONDS; the build start checks
    them again."""
    recipe_id = model.recipeId
    family = architecture_family(model.architecture)
    if family is None:
        return
    started = monotonic()
    if _build_types_mismatch(ec2, recipe_id, family, builder.build_instance_types(model.dict())):
        raise RecipeInvalid(BUILD_TYPE_ARCHITECTURE_MISMATCH)
    parent = str(model.parentImage)
    if parent.startswith(builder.PARENT_EDH_STACK_PREFIX):
        logger.warning(f"{recipe_id}: an edhStack parent image is checked against the architecture at build start")
        return
    if _lookup_budget_spent(recipe_id, started):
        return
    ami_id = parent
    if parent.startswith(builder.PARENT_SSM_PREFIX):
        try:
            ami_id = str(ssm.get_parameter(Name=parent[len(builder.PARENT_SSM_PREFIX):])["Parameter"]["Value"])
        except Exception as err:
            logger.warning(f"{recipe_id}: parent image parameter not read; architecture not checked ({_error_name(err)})")
            return
        if _lookup_budget_spent(recipe_id, started):
            return
    try:
        architecture = builder.describe_parent_image(ec2, ami_id)["architecture"]
    except Exception as err:
        logger.warning(f"{recipe_id}: parent image {ami_id} not described; architecture not checked ({_error_name(err)})")
        return
    parent_family = architecture_family(architecture)
    if parent_family is None:
        logger.warning(f"{recipe_id}: EC2 reports no architecture of a known family for parent image {ami_id}")
    elif parent_family != family:
        logger.warning(f"{recipe_id}: parent image {ami_id} is {architecture}, the recipe {model.architecture}")
        raise RecipeInvalid(PARENT_IMAGE_ARCHITECTURE_INVALID)


def _require_architecture_match_at_save(model: AmiRecipeRequestModel) -> None:
    """require_architecture_match on the context's short-timeout lookup clients; a context that cannot be
    built is logged and refuses nothing."""
    try:
        ctx = _ctx()
    except Exception as err:
        logger.warning(f"{model.recipeId}: architecture not checked; no build context ({_error_name(err)})")
        return
    require_architecture_match(model, ctx.lookup_ec2, ctx.lookup_ssm)


def _asset_key(assets: Iterable[Dict[str, Any]]) -> List[Tuple[str, str]]:
    return sorted((str(asset.get("id")), str(asset.get("repoPath"))) for asset in assets)


def _bundled_files(holder: Dict[str, Any], model: AmiRecipeRequestModel, *, refusal_without: str) -> Dict[str, Any]:
    """The bundled files ``holder`` carries, which a recipe duplicated from it, or saved over it, keeps:
    the body leaves ``assets`` out or repeats them exactly. A holder without assets accepts none, with
    ``refusal_without`` as the refusal."""
    held = holder.get("assets") or []
    if model.assets and _asset_key(a.dict() for a in model.assets) != _asset_key(held):
        raise RecipeConflict(DUPLICATE_ASSETS_FIXED if held else refusal_without)
    return {key: holder[key] for key in BUNDLED_FILE_FIELDS if holder.get(key)}


def _stack_name_key(stack_name: Any) -> str:
    return str(stack_name or "").strip().lower()


def _require_unique_stack_name(recipe_id: str, stack_name: str) -> None:
    """A build registers, or adopts, the EDH stack named ``<stackName> v<N>``, so no two recipes share a
    stack name, compared trimmed and without case."""
    wanted = _stack_name_key(stack_name)
    for other in store.list_recipes():
        if other["recipeId"] != recipe_id and _stack_name_key(other.get("stackName")) == wanted:
            raise RecipeConflict(f"stackName is already used by recipe '{other['recipeId']}'")


def create_admin_recipe(model: AmiRecipeRequestModel, user_id: str, *,
                        original: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create an admin recipe. With ``duplicateOf`` it copies that recipe's bundled files and records
    it; ``original`` is that recipe as the caller read and authorized it, read here when not given."""
    inherited: Dict[str, Any] = {}
    if model.duplicateOf:
        if original is None:
            original = store.get_recipe(model.duplicateOf)
        if original is None:
            raise RecipeConflict(DUPLICATE_SOURCE_MISSING)
        inherited = {**_bundled_files(original, model, refusal_without=DUPLICATE_ASSETS_FIXED),
                     DUPLICATED_FROM_FIELD: original["recipeId"]}
    elif model.assets:
        raise RecipeConflict(ASSETS_SHIPPED_ONLY)
    if store.get_recipe(model.recipeId):
        raise RecipeConflict(RECIPE_ID_TAKEN)
    _require_unique_stack_name(model.recipeId, model.stackName)
    item = recipe_item(model, source=store.SOURCE_ADMIN, user_id=user_id, inherited=inherited)
    item["buildOnDeploy"] = False
    _require_materializable(item)
    _require_architecture_match_at_save(model)
    created = store.put_recipe(item)
    if created is None:
        raise RecipeConflict(RECIPE_ID_TAKEN)
    return created


def update_admin_recipe(existing: Dict[str, Any], model: AmiRecipeRequestModel, user_id: str) -> Dict[str, Any]:
    """Save ``model`` over an admin recipe. Its bundled files and the recipe it was duplicated from are
    kept; ``duplicateOf`` is ignored. The architecture is looked up once, however often the write is
    overtaken."""
    checked = False
    for _ in range(CLAIM_ATTEMPTS):
        if existing is None:
            raise RecipeConflict(RECIPE_GONE)
        if existing.get("source") != store.SOURCE_ADMIN:
            raise RecipeConflict("a config recipe is edited in the repository, not here")
        if model.recipeId != existing["recipeId"]:
            raise RecipeConflict("recipeId is immutable")
        inherited = _bundled_files(existing, model, refusal_without=ASSETS_SHIPPED_ONLY)
        if existing.get(DUPLICATED_FROM_FIELD):
            inherited[DUPLICATED_FROM_FIELD] = existing[DUPLICATED_FROM_FIELD]
        _require_unique_stack_name(model.recipeId, model.stackName)
        item = recipe_item(model, source=store.SOURCE_ADMIN, user_id=user_id, existing=existing,
                           inherited=inherited)
        _require_materializable(item)
        if not checked:
            _require_architecture_match_at_save(model)
            checked = True
        saved = store.put_recipe(item, replacing=existing)
        if saved:
            return saved
        existing = store.get_recipe(model.recipeId)
    raise RecipeConflict(RECIPE_CHANGED)


def recipe_view(recipe: Dict[str, Any]) -> Dict[str, Any]:
    return {**recipe, "latestBuild": store.latest_build(recipe["recipeId"])}


# ---------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------
def _current_unless_deleted(recipe_id: str) -> Dict[str, Any]:
    """The recipe as it stands, read after the caller wrote a build row. A delete marks the recipe before
    it reads the builds, so of a delete and a build that overlap at least one sees the other. Raises
    RecipeConflict for a recipe that is gone or being deleted."""
    current = store.get_recipe(recipe_id)
    if current is None:
        raise RecipeConflict(RECIPE_GONE)
    if _being_deleted(current):
        raise RecipeConflict(RECIPE_DELETING)
    return current


def _being_deleted(recipe: Dict[str, Any]) -> bool:
    """The recipe carries a delete marker no older than a delete can run."""
    marked_at = recipe.get(store.DELETE_MARKER_FIELD)
    return bool(marked_at) and not _older_than(marked_at, DELETE_STALE_MINUTES)


def _require_buildable(ctx: builder.ImageBuilderContext, recipe: Dict[str, Any], parent_ami: str) -> Optional[str]:
    """Describe the resolved parent once and refuse (RecipeConflict) a parent EC2 does not find, and a
    parent or build instance type EC2 reports as running another architecture family than the recipe's.
    Returns the parent's root device name, or None when the describe failed otherwise, which is logged
    and refuses nothing."""
    recipe_id = recipe["recipeId"]
    family = architecture_family(recipe.get("architecture"))
    root_device_name = None
    try:
        described = builder.describe_parent_image(ctx.ec2, parent_ami)
    except builder.ParentImageUnavailable as err:
        logger.warning(f"{recipe_id}: parent image {parent_ami} could not be found")
        raise RecipeConflict(str(err))
    except Exception as err:
        logger.warning(f"{recipe_id}: parent image {parent_ami} not described; architecture not checked "
                       f"({_error_name(err)})")
    else:
        root_device_name = described["rootDeviceName"]
        parent_family = architecture_family(described["architecture"])
        if parent_family is None:
            logger.warning(f"{recipe_id}: EC2 reports no architecture of a known family for parent image {parent_ami}")
        elif family is not None and parent_family != family:
            logger.warning(f"{recipe_id}: parent image {parent_ami} is {described['architecture']}, "
                           f"the recipe {recipe.get('architecture')}")
            raise RecipeConflict(PARENT_ARCHITECTURE_MISMATCH)
    if family is not None and _build_types_mismatch(ctx.ec2, recipe_id, family, builder.build_instance_types(recipe)):
        raise RecipeConflict(BUILD_TYPE_ARCHITECTURE_MISMATCH)
    return root_device_name


def _launch(recipe: Dict[str, Any], version: int, requested_by: str, *, requested_at: Optional[str],
            not_started: Callable[[Exception], Any]) -> Dict[str, Any]:
    """Start the pipeline of a build row the caller has claimed, then record the row as BUILDING. The
    build is of the recipe as it stands once the row is held; one gone or being deleted starts nothing.
    ``not_started`` settles the claimed row when the pipeline does not start."""
    try:
        recipe = _current_unless_deleted(recipe["recipeId"])
        ctx = _ctx()
        edh_client = _edh() if str(recipe["parentImage"]).startswith(builder.PARENT_EDH_STACK_PREFIX) else None
        try:
            parent = builder.resolve_parent_image(recipe["parentImage"], ctx=ctx, edh_client=edh_client, kind=recipe["kind"])
        except builder.ParentImageUnavailable as err:
            raise RecipeConflict(str(err))
        root_device_name = _require_buildable(ctx, recipe, parent)
        try:
            materialized = builder.materialize(ctx, recipe, parent, root_device_name=root_device_name)
        except (builder.ComponentDocumentError, builder.ParentImageUnavailable) as err:
            raise RecipeConflict(str(err))
        image_arn = builder.start_pipeline(ctx, materialized["pipelineArn"])
    except Exception as err:
        not_started(err)
        raise
    now = store.utc_now()
    build = store.put_build({
        "recipeId": recipe["recipeId"], "version": version, "status": store.STATUS_BUILDING,
        "contentHash": recipe["contentHash"], "requestedBy": requested_by, "requestedAt": requested_at or now,
        "startedAt": now, "imageBuildVersionArn": image_arn, "resolvedParentAmi": parent,
        "buildKey": materialized["buildKey"],
    })
    if store.update_recipe(recipe["recipeId"], imageBuilder=materialized, latestVersion=version) is None:
        logger.warning(f"{recipe['recipeId']} v{version}: recipe gone; its pipeline and version were not recorded")
    return build


def start_build(recipe: Dict[str, Any], requested_by: str, *, only_if_changed: bool) -> Tuple[Optional[Dict[str, Any]], str]:
    """Start a build of the recipe, or queue it behind the build in progress. A manual request for
    content already queued or building is answered with that build. The version row is claimed before
    any pipeline starts, so a request that finds its version taken re-reads the builds and decides again."""
    if not recipe.get("enabled", True):
        return None, OUTCOME_REFUSED_DISABLED
    recipe_id = recipe["recipeId"]
    for _ in range(CLAIM_ATTEMPTS):
        builds = store.list_builds(recipe_id)
        latest = builds[0] if builds else None
        if (only_if_changed and latest and latest.get("status") in SETTLED_OR_ACTIVE
                and latest.get("contentHash") == recipe.get("contentHash")):
            return latest, OUTCOME_SKIPPED_UNCHANGED
        active = any(b.get("status") in store.ACTIVE_BUILD_STATUSES for b in builds)
        if not active and any(b.get("status") == store.STATUS_QUEUED for b in builds):
            # A queued build that waits on nothing starts ahead of this request.
            promoted = _promote_queued(recipe)
            if promoted:
                return promoted, OUTCOME_STARTED
            continue
        if not only_if_changed:
            in_flight = next((b for b in builds if b.get("status") in IN_FLIGHT
                              and b.get("contentHash") == recipe.get("contentHash")), None)
            if in_flight:
                return in_flight, OUTCOME_IN_PROGRESS
        version = int(latest["version"]) + 1 if latest else 1
        row = {"recipeId": recipe_id, "version": version, "contentHash": recipe["contentHash"],
               "requestedBy": requested_by}
        if active:
            queued = store.claim_new_build({**row, "status": store.STATUS_QUEUED})
            if queued:
                try:
                    _current_unless_deleted(recipe_id)
                except RecipeConflict:
                    store.release_claim(recipe_id, version, store.STATUS_QUEUED)
                    raise
                store.update_recipe(recipe_id, latestVersion=version)
                return queued, OUTCOME_QUEUED
            continue
        claimed = store.claim_new_build({**row, "status": store.STATUS_BUILDING, "claimedAt": store.utc_now()})
        if claimed:
            return _launch(recipe, version, requested_by, requested_at=claimed["requestedAt"],
                           not_started=lambda err: store.release_claim(recipe_id, version)), OUTCOME_STARTED
    raise RecipeConflict("the recipe's builds changed while the request was placed; try again")


def _promote_queued(recipe: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Start the recipe's oldest queued build once nothing is active. The row is claimed from QUEUED
    before its pipeline starts, so of two promoters one starts it. A build that cannot start goes back
    to QUEUED with the reason on its row, so the next attempt starts it once the cause is fixed; the
    started row replaces the queued one, reason included."""
    recipe_id = recipe["recipeId"]
    builds = store.list_builds(recipe_id)
    if any(b.get("status") in store.ACTIVE_BUILD_STATUSES for b in builds):
        return None
    queued = [b for b in builds if b.get("status") == store.STATUS_QUEUED]
    if not queued:
        return None
    oldest = queued[-1]
    version = int(oldest["version"])
    if not store.claim_build(recipe_id, version, store.STATUS_QUEUED, status=store.STATUS_BUILDING,
                             claimedAt=store.utc_now()):
        return None

    def not_started(err: Exception) -> None:
        logger.exception(f"{recipe_id} v{version}: queued build not started")
        reason = str(err) if isinstance(err, RecipeConflict) else type(err).__name__
        store.settle_unlaunched(recipe_id, version, status=store.STATUS_QUEUED,
                                error=f"{QUEUED_NOT_STARTED}: {reason}")

    return _launch(recipe, version, oldest.get("requestedBy", REQUESTED_BY_DEPLOY),
                   requested_at=oldest.get("requestedAt"), not_started=not_started)


def _stack_id_named(client: EdhClient, edh_kind: str, stack_name: str):
    for record in client.list_software_stacks(edh_kind):
        if isinstance(record, dict) and str(record.get(EDH_STACK_NAME_FIELD)) == stack_name:
            return record.get(EDH_STACK_ID_FIELD)
    return None


def _register_failed(recipe_id: str, version: int, error: str, *, automatic: bool, **attrs) -> Dict[str, Any]:
    """Record a failed registration. An automatic attempt counts against ``REGISTER_RETRY_LIMIT``."""
    attrs = {**attrs, "status": store.STATUS_REGISTER_FAILED, "error": error}
    if automatic:
        return store.update_build_counting(recipe_id, version, REGISTER_ATTEMPTS_FIELD, **attrs)
    return store.update_build(recipe_id, version, **attrs)


def _mark_registered(recipe_id: str, version: int, *, stack_name: str, stack_id: str, kind: str,
                     registered_at: Optional[str] = None, error: str = "") -> Dict[str, Any]:
    return store.update_build(recipe_id, version, status=store.STATUS_AVAILABLE, edhStackName=stack_name,
                              edhStackId=stack_id, edhKind=kind, registeredAt=registered_at or store.utc_now(),
                              error=error)


def _assign_and_mark(recipe_id: str, version: int, client: EdhClient, edh_kind: str, stack_id: str, *,
                     stack_name: str, kind: str, automatic: bool) -> Dict[str, Any]:
    """Attach the stack to the launch project, then record the build as registered. A stack no project
    carries cannot be launched, so a refused assignment is a failed registration; the row keeps the
    stack name and no id, which is what makes the retry adopt the stack and assign it again."""
    try:
        ensure_stack_in_project(client, edh_kind, stack_id)
    except EdhError as err:
        logger.exception(f"{recipe_id} v{version}: project assignment failed")
        return _register_failed(recipe_id, version, project_assign_error(type(err).__name__),
                                automatic=automatic, edhStackName=stack_name, edhKind=kind)
    return _mark_registered(recipe_id, version, stack_name=stack_name, stack_id=stack_id, kind=kind)


def register_build(recipe: Dict[str, Any], build: Dict[str, Any], *, automatic: bool = True) -> Dict[str, Any]:
    """Register the build's AMI as an EDH software stack. Repeatable: a build already registered, or a
    stack already carrying this build's name, is adopted rather than registered a second time."""
    recipe_id, version = recipe["recipeId"], int(build["version"])
    stack_name = f"{recipe['stackName']} v{version}"
    kind = recipe["kind"]
    edh_kind = KIND_BY_IMAGE_KIND[kind]
    if build.get("registeredAt") or build.get("edhStackId"):
        return _mark_registered(recipe_id, version, stack_name=build.get("edhStackName") or stack_name,
                                stack_id=str(build.get("edhStackId") or ""), kind=build.get("edhKind") or kind,
                                registered_at=build.get("registeredAt"), error=build.get("error") or "")
    if kind == IMAGE_KIND_VDI:
        fields = vdi_stack_fields(RegisterVdiImageRequestModel(
            amiId=build["amiId"], stackName=stack_name, baseOs=recipe["baseOs"], rootSize=recipe["rootSizeGb"],
            virtualDesktopProfileId=recipe["profileId"], description=recipe.get("description")))
    else:
        fields = simulation_stack_fields(RegisterSimulationImageRequestModel(
            amiId=build["amiId"], stackName=stack_name, rootSize=recipe["rootSizeGb"],
            targetNodeProfileId=recipe["profileId"], description=recipe.get("description")))
    client = _edh()
    try:
        existing = _stack_id_named(client, edh_kind, stack_name)
    except EdhError:
        logger.exception(f"{recipe_id} v{version}: catalog could not be read before registering")
        existing = None
    if existing is not None:
        return _assign_and_mark(recipe_id, version, client, edh_kind, str(existing),
                                stack_name=stack_name, kind=kind, automatic=automatic)
    try:
        client.register_software_stack(edh_kind, fields)
    except EdhError as err:
        logger.exception(f"{recipe_id} v{version}: EDH registration failed")
        return _register_failed(recipe_id, version, f"EDH registration failed ({type(err).__name__})",
                                automatic=automatic, edhStackName=stack_name, edhKind=kind)
    # Without the id the stack cannot be assigned to the project; the retry adopts it by name.
    try:
        stack_id = _stack_id_named(client, edh_kind, stack_name)
    except EdhError:
        logger.exception(f"{recipe_id} v{version}: stack id read-back failed")
        return _register_failed(recipe_id, version, project_assign_error(CATALOG_NOT_READ_BACK),
                                automatic=automatic, edhStackName=stack_name, edhKind=kind)
    if stack_id is None:
        logger.warning(f"{recipe_id} v{version}: the catalog does not list the registered stack")
        return _register_failed(recipe_id, version, project_assign_error(STACK_ID_NOT_READ_BACK),
                                automatic=automatic, edhStackName=stack_name, edhKind=kind)
    return _assign_and_mark(recipe_id, version, client, edh_kind, str(stack_id),
                            stack_name=stack_name, kind=kind, automatic=automatic)


def _complete(recipe: Dict[str, Any], build: Dict[str, Any]) -> Dict[str, Any]:
    recipe_id, version = recipe["recipeId"], int(build["version"])
    ctx = _ctx()
    described = builder.describe_image(ctx, build["imageBuildVersionArn"])
    if not described["amiId"]:
        store.update_build(recipe_id, version, status=store.STATUS_FAILED, completedAt=store.utc_now(),
                           error="the image reports no AMI for the deployment region")
        _promote_queued(recipe)
        return {"outcome": "failed", "version": version}
    build = store.claim_build(recipe_id, version, store.STATUS_BUILDING, status=store.STATUS_REGISTERING,
                              amiId=described["amiId"], snapshotIds=described["snapshotIds"], completedAt=store.utc_now())
    if not build:
        return {"outcome": "already-handled", "version": version}
    try:
        builder.tag_ami(ctx, described["amiId"], described["snapshotIds"], builder.ami_tags(recipe, ctx))
        build = register_build(recipe, build)
    except Exception as err:
        logger.exception(f"{recipe_id} v{version}: completion failed")
        _register_failed(recipe_id, version, f"completion failed ({type(err).__name__})", automatic=True)
        _promote_queued(recipe)
        return {"outcome": "register_failed", "version": version}
    if build["status"] == store.STATUS_AVAILABLE:
        build = _retire_and_prune(recipe, build)
    _promote_queued(recipe)
    return {"outcome": build["status"].lower(), "version": version}


def handle_image_state(image_build_version_arn: str, status: Optional[str], reason: Optional[str]) -> Dict[str, Any]:
    build = store.build_by_image_arn(image_build_version_arn) if image_build_version_arn else None
    if not build:
        return {"outcome": "unknown-build"}
    recipe = store.get_recipe(build["recipeId"])
    if not recipe or "recipeId" not in recipe:
        return {"outcome": "unknown-recipe"}
    if build.get("status") != store.STATUS_BUILDING:
        return {"outcome": "already-handled", "status": build.get("status")}
    if status == IMAGE_STATUS_AVAILABLE:
        return _complete(recipe, build)
    if status in IMAGE_TERMINAL_FAILURES:
        store.update_build(recipe["recipeId"], int(build["version"]), status=store.STATUS_FAILED,
                           error=reason or status, completedAt=store.utc_now())
        _promote_queued(recipe)
        return {"outcome": "failed", "version": int(build["version"])}
    return {"outcome": "in-progress", "status": status}


def _retire_and_prune(recipe: Dict[str, Any], build: Dict[str, Any]) -> Dict[str, Any]:
    """Retire the earlier stacks and prune old AMIs. The build stays registered whatever happens here:
    a failure is a warning on its row, not a registration failure."""
    recipe_id, version = recipe["recipeId"], int(build["version"])
    try:
        retire_previous(recipe, build)
        prune_amis(recipe)
    except Exception as err:
        logger.exception(f"{recipe_id} v{version}: retirement or pruning of earlier versions failed")
        return store.update_build(recipe_id, version, error=f"{RETIRE_OR_PRUNE_FAILED} ({type(err).__name__})")
    return build


def retry_registration(recipe: Dict[str, Any], build: Dict[str, Any], *, automatic: bool = False) -> Dict[str, Any]:
    """Re-tag the AMI and register it again. An administrator's retry clears the attempt count; the
    sweep's retry counts against ``REGISTER_RETRY_LIMIT``."""
    recipe_id, version = recipe["recipeId"], int(build["version"])
    if build.get("amiDeregisteredAt"):
        raise RecipeConflict("the build's AMI has been deregistered")
    if build.get("status") != store.STATUS_REGISTER_FAILED:
        raise RecipeConflict("only a REGISTER_FAILED build can be re-registered")
    claimed = {"status": store.STATUS_REGISTERING}
    if not automatic:
        claimed[REGISTER_ATTEMPTS_FIELD] = 0
    build = store.claim_build(recipe_id, version, store.STATUS_REGISTER_FAILED, **claimed)
    if not build:
        raise RecipeConflict("the build status changed")
    try:
        if build.get("amiId"):
            ctx = _ctx()
            builder.tag_ami(ctx, build["amiId"], build.get("snapshotIds", []), builder.ami_tags(recipe, ctx))
        build = register_build(recipe, build, automatic=automatic)
    except Exception as err:
        logger.exception(f"{recipe_id} v{version}: retry registration failed")
        return _register_failed(recipe_id, version, f"retry registration failed ({type(err).__name__})",
                                automatic=automatic)
    if build["status"] == store.STATUS_AVAILABLE:
        build = _retire_and_prune(recipe, build)
    return build


def retire_previous(recipe: Dict[str, Any], build: Dict[str, Any]) -> List[int]:
    recipe_id = recipe["recipeId"]
    edh_kind = KIND_BY_IMAGE_KIND[recipe["kind"]]
    client = _edh()
    retired: List[int] = []
    for other in store.list_builds(recipe_id):
        if int(other["version"]) == int(build["version"]) or other.get("status") != store.STATUS_AVAILABLE:
            continue
        stack_id = other.get("edhStackId")
        if not stack_id and other.get("edhStackName"):
            try:
                looked_up = _stack_id_named(client, edh_kind, other["edhStackName"])
                if looked_up:
                    stack_id = str(looked_up)
                    store.update_build(recipe_id, int(other["version"]), edhStackId=stack_id)
            except EdhError as err:
                logger.exception(f"{recipe_id} v{other['version']}: stack id lookup failed")
        if not stack_id:
            store.update_build(recipe_id, int(other["version"]), retireWarning="stack id unknown; not deactivated")
            continue
        try:
            client.delete_software_stack(edh_kind, stack_id)
        except EdhError as err:
            logger.exception(f"{recipe_id} v{other['version']}: deactivation failed")
            store.update_build(recipe_id, int(other["version"]), retireWarning=f"deactivation failed ({type(err).__name__})")
            continue
        store.update_build(recipe_id, int(other["version"]), status=store.STATUS_RETIRED, retiredAt=store.utc_now())
        retired.append(int(other["version"]))
    return retired


def _ami_kept(recipe_id: str, build: Dict[str, Any], ctx: builder.ImageBuilderContext) -> bool:
    """Whether the build's AMI stays because an instance uses it or the check failed. The reason is
    written to the row only when it changed, so a pass that finds the same instances writes nothing, and
    only while the row still has ``build``'s status, so a row that moved on or was deleted is left alone;
    the AMI is kept for this pass either way."""
    version = int(build["version"])
    try:
        in_use = builder.instances_using_ami(ctx, build["amiId"])[:AMI_IN_USE_LIST_MAX]
    except Exception as err:
        logger.exception(f"{recipe_id} v{version}: in-use check failed")
        code = err.response.get("Error", {}).get("Code") if isinstance(err, ClientError) else None
        warning = f"{IN_USE_CHECK_FAILED} ({code or type(err).__name__})"
        if build.get("pruneWarning") != warning:
            store.claim_build(recipe_id, version, build["status"], pruneWarning=warning)
        return True
    if not in_use:
        return False
    # A failed check the instances now answer is no longer the reason.
    stale_warning = str(build.get("pruneWarning") or "").startswith(IN_USE_CHECK_FAILED)
    if build.get(AMI_IN_USE_FIELD) != in_use or stale_warning:
        store.claim_build(recipe_id, version, build["status"], remove=("pruneWarning",) if stale_warning else (),
                          **{AMI_IN_USE_FIELD: in_use})
    return True


def _deregistered(recipe_id: str, version: int, build: Dict[str, Any], now: str) -> Optional[Dict[str, Any]]:
    """Record a build whose AMI is gone; the reasons it was kept go with it. Written only over the RETIRED
    row the caller claimed: returns None, and writes nothing, for a row a recipe delete removed meanwhile."""
    row = store.claim_build(recipe_id, version, store.STATUS_RETIRED,
                            retiredAt=build.get("retiredAt") or now, amiDeregisteredAt=now,
                            remove=(AMI_IN_USE_FIELD, AMI_REMOVAL_PENDING_FIELD, "pruneWarning"))
    if row is None:
        logger.warning(f"{recipe_id} v{version}: build row gone; its AMI was deregistered")
    return row


def _ami_removal_candidates(recipe: Dict[str, Any], builds: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The builds, of ``builds`` listed newest first, whose AMI pruning removes: those holding an AMI not
    yet deregistered that are beyond ``keepVersions`` with a ``PRUNABLE`` status, or RETIRED with the
    removal pending."""
    keep = int(recipe.get("keepVersions") or KEEP_VERSIONS_DEFAULT)
    with_ami = [b for b in builds if b.get("amiId") and not b.get("amiDeregisteredAt")]
    return [b for index, b in enumerate(with_ami)
            if (index >= keep and b.get("status") in PRUNABLE)
            or (b.get("status") == store.STATUS_RETIRED and b.get(AMI_REMOVAL_PENDING_FIELD))]


def prune_amis(recipe: Dict[str, Any]) -> List[int]:
    """Deregister the AMIs of the retired and failed builds beyond ``keepVersions`` and of the retired
    builds whose removal is pending, unless an instance uses the AMI. Returns the versions pruned."""
    recipe_id = recipe["recipeId"]
    ctx = _ctx()
    pruned: List[int] = []
    for build in _ami_removal_candidates(recipe, store.list_builds(recipe_id)):
        status, version = build["status"], int(build["version"])
        # The in-use check only reads, so it runs before the claim.
        if _ami_kept(recipe_id, build, ctx):
            continue
        # The status read is claimed first, so a build a registration retry has taken since is left to it.
        if not store.claim_build(recipe_id, version, status, status=store.STATUS_RETIRED):
            continue
        try:
            builder.deregister_ami(ctx, build["amiId"], build.get("snapshotIds", []))
            if build.get("imageBuildVersionArn"):
                builder.delete_image_build(ctx, build["imageBuildVersionArn"])
        except Exception as err:
            logger.exception(f"{recipe_id} v{version}: pruning failed")
            store.claim_build(recipe_id, version, store.STATUS_RETIRED, status=status,
                              pruneWarning=f"pruning failed ({type(err).__name__})", remove=(AMI_IN_USE_FIELD,))
            continue
        # A row a recipe delete removed meanwhile is not written again; its AMI is gone all the same.
        _deregistered(recipe_id, version, build, store.utc_now())
        pruned.append(version)
    return pruned


def retire_build(recipe: Dict[str, Any], build: Dict[str, Any]) -> Dict[str, Any]:
    status = build.get("status")
    if status not in (store.STATUS_AVAILABLE, store.STATUS_REGISTER_FAILED):
        raise RecipeConflict("only an AVAILABLE or REGISTER_FAILED build can be retired")
    edh_kind = KIND_BY_IMAGE_KIND[recipe["kind"]]
    client = _edh()
    version = int(build["version"])
    stack_id = build.get("edhStackId")
    if not stack_id and build.get("edhStackName"):
        try:
            looked_up = _stack_id_named(client, edh_kind, build["edhStackName"])
            if looked_up:
                stack_id = str(looked_up)
                # Recorded only on the row as read; one that moved on or is gone fails the claim below.
                store.claim_build(recipe["recipeId"], version, status, edhStackId=stack_id)
        except EdhError as err:
            logger.exception(f"{recipe['recipeId']} v{build['version']}: stack id lookup failed")
    if not stack_id:
        raise RecipeConflict("stack id unknown; cannot deactivate")
    # The status read is claimed before the stack is deactivated, so a registration retry that took the
    # build first keeps it; a deactivation that fails hands the build back.
    if not store.claim_build(recipe["recipeId"], version, status, status=store.STATUS_RETIRED):
        raise RecipeConflict("the build status changed")
    try:
        client.delete_software_stack(edh_kind, stack_id)
    except Exception:
        store.claim_build(recipe["recipeId"], version, store.STATUS_RETIRED, status=status)
        raise
    now = store.utc_now()
    # Each write lands only on the RETIRED row claimed above, so a row a recipe delete removed meanwhile
    # is not written again.
    if not build.get("amiId"):
        return _still_retired(store.claim_build(recipe["recipeId"], version, store.STATUS_RETIRED, retiredAt=now))
    # Flagged before the AMI is touched, so an AMI kept now, or a deregistration that fails, is removed by
    # the sweep.
    pending = _still_retired(store.claim_build(recipe["recipeId"], version, store.STATUS_RETIRED, retiredAt=now,
                                               **{AMI_REMOVAL_PENDING_FIELD: True}))
    ctx = _ctx()
    if _ami_kept(recipe["recipeId"], pending, ctx):
        return _still_retired(store.get_build(recipe["recipeId"], version))
    try:
        builder.deregister_ami(ctx, build["amiId"], build.get("snapshotIds", []))
        if build.get("imageBuildVersionArn"):
            builder.delete_image_build(ctx, build["imageBuildVersionArn"])
    except Exception:
        # The check found no instance, so an earlier check's list goes; the sweep retries the removal.
        if pending.get(AMI_IN_USE_FIELD):
            store.claim_build(recipe["recipeId"], version, store.STATUS_RETIRED, remove=(AMI_IN_USE_FIELD,))
        raise
    return _still_retired(_deregistered(recipe["recipeId"], version, pending, now))


def _still_retired(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The retired build row; one a recipe delete removed during the retire is a conflict."""
    if row is None:
        raise RecipeConflict(RECIPE_DELETING)
    return row


def _older_than(timestamp_text: Optional[str], minutes: int) -> bool:
    """Check if an ISO timestamp is older than the given number of minutes."""
    if not timestamp_text:
        return False
    try:
        ts = datetime.fromisoformat(str(timestamp_text))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - ts > timedelta(minutes=minutes)


def _older_than_timeout(build: Dict[str, Any]) -> bool:
    return _older_than(build.get("startedAt"), BUILD_TIMEOUT_MINUTES)


def _fail_building(recipe_id: str, version: int, error: str) -> bool:
    """Fail a build only while it is still BUILDING: a completion that claimed it first keeps its claim,
    and a completion event that arrives later finds it already handled."""
    return store.claim_build(recipe_id, version, store.STATUS_BUILDING, status=store.STATUS_FAILED,
                             error=error, completedAt=store.utc_now()) is not None


def _fail_timed_out(ctx: builder.ImageBuilderContext, recipe_id: str, build: Dict[str, Any],
                    warnings: List[str]) -> bool:
    """Fail a build Image Builder still reports running past the timeout, then cancel its image creation.
    The cancel is best effort; a refused one is named on the row and in ``warnings``, since that build
    may still be running."""
    version = int(build["version"])
    error = f"no result after {BUILD_TIMEOUT_MINUTES} minutes"
    if not _fail_building(recipe_id, version, error):
        return False
    try:
        builder.cancel_image(ctx, build["imageBuildVersionArn"])
    except Exception as err:
        logger.exception(f"{recipe_id} v{version}: image creation not cancelled")
        code = err.response.get("Error", {}).get("Code") if isinstance(err, ClientError) else None
        refusal = f"cancel failed ({code or type(err).__name__})"
        warnings.append(f"{recipe_id} v{version}: {refusal}")
        store.update_build(recipe_id, version, error=f"{error}; {refusal}")
    return True


def sweep() -> Dict[str, Any]:
    summary: Dict[str, Any] = {"checked": 0, "completed": 0, "failed": 0, "retried": 0, "promoted": 0,
                               "pruned": 0, "warnings": []}
    ctx = _ctx()
    for recipe in store.list_recipes():
        for build in store.list_builds(recipe["recipeId"]):
            label = f"{recipe['recipeId']} v{build['version']}"
            try:
                if build.get("status") == store.STATUS_BUILDING and build.get("imageBuildVersionArn"):
                    summary["checked"] += 1
                    try:
                        described = builder.describe_image(ctx, build["imageBuildVersionArn"])
                    except ctx.ib.exceptions.ResourceNotFoundException:
                        described = None
                    if described is None:
                        if _fail_building(recipe["recipeId"], int(build["version"]), IMAGE_GONE):
                            summary["failed"] += 1
                    elif described["status"] == IMAGE_STATUS_AVAILABLE:
                        _complete(recipe, build)
                        summary["completed"] += 1
                    elif described["status"] in IMAGE_TERMINAL_FAILURES:
                        if _fail_building(recipe["recipeId"], int(build["version"]),
                                          described["reason"] or described["status"]):
                            summary["failed"] += 1
                    elif _older_than_timeout(build):
                        if _fail_timed_out(ctx, recipe["recipeId"], build, summary["warnings"]):
                            summary["failed"] += 1
                elif (build.get("status") == store.STATUS_BUILDING
                      and _older_than(build.get("claimedAt") or build.get("requestedAt"), CLAIM_STALE_MINUTES)):
                    if store.settle_unlaunched(recipe["recipeId"], int(build["version"]), status=store.STATUS_FAILED,
                                               error=LAUNCH_NOT_COMPLETED, completedAt=store.utc_now()):
                        summary["failed"] += 1
                elif build.get("status") == store.STATUS_REGISTER_FAILED and build.get("amiId") and not build.get("amiDeregisteredAt"):
                    if int(build.get(REGISTER_ATTEMPTS_FIELD) or 0) >= REGISTER_RETRY_LIMIT:
                        prior = build.get("error") or ""
                        if not prior.endswith(REGISTER_RETRIES_EXHAUSTED):
                            store.update_build(recipe["recipeId"], int(build["version"]),
                                               error=f"{prior}; {REGISTER_RETRIES_EXHAUSTED}" if prior else REGISTER_RETRIES_EXHAUSTED)
                        summary["warnings"].append(f"{label}: {REGISTER_RETRIES_EXHAUSTED}")
                    else:
                        retry_registration(recipe, build, automatic=True)
                        summary["retried"] += 1
                elif build.get("status") == store.STATUS_REGISTERING and _older_than(build.get("updatedAt"), REGISTERING_STALE_MINUTES):
                    _register_failed(recipe["recipeId"], int(build["version"]), "registration did not complete",
                                     automatic=True)
                    summary["retried"] += 1
            except Exception as err:
                logger.exception(f"{label}: sweep step failed")
                summary["warnings"].append(f"{label}: {type(err).__name__}")
        try:
            if _promote_queued(recipe):
                summary["promoted"] += 1
        except Exception as err:
            logger.exception(f"{recipe['recipeId']}: queued build not promoted")
            summary["warnings"].append(f"{recipe['recipeId']}: {type(err).__name__}")
        try:
            # Read again, so a delete that began during this sweep is left alone.
            current = store.get_recipe(recipe["recipeId"])
            if current and not _being_deleted(current):
                summary["pruned"] += len(prune_amis(current))
        except Exception as err:
            logger.exception(f"{recipe['recipeId']}: AMIs not pruned")
            summary["warnings"].append(f"{recipe['recipeId']}: prune {type(err).__name__}")
    return summary


def delete_recipe(recipe: Dict[str, Any]) -> List[str]:
    """Remove the recipe's Image Builder resources and rows, refused while a build is queued or in
    progress and while pruning still has a build's AMI to remove, since the build row is the sweep's
    only record of that AMI. The recipe is marked before its builds are read, and a build's writer reads
    the recipe after writing its row, so a build that overlaps the delete either holds it off or stands
    down. A refused delete releases its mark."""
    recipe_id = recipe["recipeId"]
    stale_before = (datetime.now(timezone.utc) - timedelta(minutes=DELETE_STALE_MINUTES)).isoformat()
    marked = store.mark_recipe_deleting(recipe_id, stale_before=stale_before)
    if marked is None:
        raise RecipeConflict(RECIPE_DELETING)
    try:
        builds = store.list_builds(recipe_id, consistent=True)
        if any(b.get("status") in IN_FLIGHT for b in builds):
            raise RecipeConflict("a build is in progress")
        if _ami_removal_candidates(marked, builds):
            raise RecipeConflict(AMI_REMOVAL_PENDING)
        warnings = builder.delete_recipe_resources(_ctx(), marked.get("imageBuilder") or {})
        store.delete_recipe_rows(recipe_id)
    except Exception:
        store.release_recipe_deleting(recipe_id, marked[store.DELETE_MARKER_FIELD])
        raise
    return warnings
