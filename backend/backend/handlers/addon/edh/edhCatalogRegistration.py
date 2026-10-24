# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""CloudFormation custom resource that registers the deployment's predefined images with EDH, its
container job definitions with AWS Batch, and seeds AMI recipes with buildOnDeploy builds.

Idempotent by natural key: the existing records are listed, entries whose key is already present are
skipped, and the rest are created. EDH answers a create with prose and no id, so the stack name is
the identity an entry is known by on every later run. A failed item is recorded as a warning and the
resource still succeeds; the ``Timestamp`` property changes every deploy, so the next deploy retries
whatever is still missing.

As the ``onEventHandler`` of a custom-resource Provider, this handler returns the
``{PhysicalResourceId, Data}`` shape; the provider framework writes the CloudFormation response.
"""

import json
import os
import re

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from typing import Any, Dict, List

from aws_lambda_powertools.utilities.parser import ValidationError
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.resourceNames import get_bucket_name, ResourceKeys
from customLogging.logger import safeLogger
from models.addon.edh import RegisterSimulationImageRequestModel, RegisterVdiImageRequestModel
from .common.edhCatalogFields import (
    EDH_STACK_FIELD_AMI_ID, KIND_DCV, KIND_TARGET_NODES, ensure_stack_in_project, project_assign_error,
    simulation_stack_fields, vdi_stack_fields,
)
from .common.edhClient import EdhClient, EdhError
from .common import edhAmiRecipes as recipes
from .common import edhLeasePolicy

logger = safeLogger(service_name="EdhCatalogRegistration")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
# EDH offers no route that creates a Batch job definition; it lists the definitions that carry the
# cluster's visibility tag, so this handler registers them with Batch directly, tag included.
batch = boto3.client("batch", config=retry_config)
s3 = boto3.client("s3", config=retry_config)

try:
    edh_cluster_id = os.environ["EDH_CLUSTER_ID"]
except Exception as e:
    logger.exception("Failed loading configuration")
    raise e

# Resource properties carrying the JSON-encoded image lists and the lease policy defaults object.
PROPERTY_VDI = "predefinedVdiImages"
PROPERTY_SIMULATION = "predefinedSimulationAmis"
PROPERTY_LEASE_POLICY_DEFAULTS = "leasePolicyDefaults"

# Resource properties of the AMI recipe seeding: the switch and the JSON list of uploaded recipe bundles.
PROPERTY_AMI_BUILDS_ENABLED = "amiBuildsEnabled"
PROPERTY_AMI_RECIPE_BUNDLES = "amiRecipeBundles"
_COMPONENT_FILE_SUFFIX = re.compile(r"\.ya?ml$")

# The field of an existing EDH software-stack record that identifies an entry already registered.
NATURAL_KEY = "stack_name"
# The field carrying the record's EDH id, which is what a project's stack lists hold.
STACK_ID_FIELD = "id"

# Resource property carrying the JSON-encoded job-definition list; an entry's `name` is the Batch job
# definition name. EDH's catalog is the set of ACTIVE definitions carrying the visibility tag.
PROPERTY_JOB_DEFINITIONS = "jobDefinitions"
JOB_DEFINITION_NATURAL_KEY = "name"
BATCH_JOB_DEFINITION_NAME_FIELD = "jobDefinitionName"
BATCH_JOB_DEFINITION_ARN_FIELD = "jobDefinitionArn"
EDH_VISIBILITY_TAG = f"edh:visibility:{edh_cluster_id}"
EDH_VISIBILITY_TAG_VALUE = "true"
JOB_DEFINITION_REQUIRED_KEYS = ("name", "imageUri", "vcpus", "memory")
# Only EC2 definitions are registered: a Fargate definition needs an execution role and a network
# configuration the deployment configuration does not carry.
JOB_DEFINITION_DEFAULT_PLATFORM = "EC2"


def job_definition_request(entry: Any) -> Dict[str, Any]:
    """The RegisterJobDefinition request for one entry; ValueError on a malformed entry."""
    if not isinstance(entry, dict):
        raise ValueError("job definition entry must be an object")
    missing = [key for key in JOB_DEFINITION_REQUIRED_KEYS if entry.get(key) in (None, "")]
    if missing:
        raise ValueError(f"job definition entry is missing {', '.join(missing)}")
    for key in ("vcpus", "memory"):
        if isinstance(entry[key], bool) or not isinstance(entry[key], int) or entry[key] < 1:
            raise ValueError(f"job definition {key} must be a positive integer")
    gpu = entry.get("gpu")
    if gpu is not None and (isinstance(gpu, bool) or not isinstance(gpu, int) or gpu < 0):
        raise ValueError("job definition gpu must be a non-negative integer")
    requirements = [
        {"type": "VCPU", "value": str(entry["vcpus"])},
        {"type": "MEMORY", "value": str(entry["memory"])},
    ]
    if gpu:
        requirements.append({"type": "GPU", "value": str(gpu)})
    return {
        BATCH_JOB_DEFINITION_NAME_FIELD: str(entry[JOB_DEFINITION_NATURAL_KEY]),
        "type": "container",
        "containerProperties": {
            "image": str(entry["imageUri"]),
            "resourceRequirements": requirements,
        },
        "platformCapabilities": [str(entry.get("platform") or JOB_DEFINITION_DEFAULT_PLATFORM)],
        "tags": {EDH_VISIBILITY_TAG: EDH_VISIBILITY_TAG_VALUE},
    }


def _parse_list_property(resource_properties: Dict[str, Any], name: str) -> list:
    value = resource_properties.get(name) or []
    if isinstance(value, str):
        value = json.loads(value) if value.strip() else []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a JSON array")
    return value


def _existing_stacks(client: EdhClient, kind: str) -> Dict[str, Dict[str, Any]]:
    """EDH's software stacks of the kind, by natural key."""
    return {
        str(record.get(NATURAL_KEY)): record
        for record in client.list_software_stacks(kind)
        if isinstance(record, dict) and record.get(NATURAL_KEY)
    }


def _assign_to_project(client: EdhClient, kind: str, wanted: Dict[str, str],
                       existing: Dict[str, Dict[str, Any]], result: Dict[str, List[str]],
                       *, reread: bool) -> None:
    """Attach the kind's stacks to the launch project, by name. EDH resolves a user's launchable stacks
    through the projects a stack is attached to, and answers a create with prose and no id, so the
    catalog is read again for the ids of the stacks this run created. A stack the project already
    carries sends no update."""
    if not wanted:
        return
    if reread:
        try:
            existing = _existing_stacks(client, kind)
        except EdhError as err:
            logger.exception(f"Reading {kind} software stack ids back failed: {type(err).__name__}")
            result["warnings"].append(
                f"{kind}: {project_assign_error('the catalog could not be read back')}")
            return
    for name, label in wanted.items():
        stack_id = (existing.get(name) or {}).get(STACK_ID_FIELD)
        if stack_id is None:
            result["warnings"].append(
                f"{label}: {project_assign_error('its EDH id could not be read back')}")
            continue
        try:
            ensure_stack_in_project(client, kind, str(stack_id))
        except EdhError as err:
            logger.exception(f"{label} project assignment failed: {type(err).__name__}")
            result["warnings"].append(f"{label}: {project_assign_error(type(err).__name__)}")


def _register_kind(
    client: EdhClient,
    kind: str,
    entries: List[Dict[str, Any]],
    model_cls,
    fields_fn,
    result: Dict[str, List[str]],
) -> None:
    """List the kind's existing stacks, skip entries already present by name -- with a warning when the
    stack EDH holds under that name carries another AMI, since EDH has no update route -- create the
    rest, and attach every entry's stack to the launch project."""
    if not entries:
        return
    try:
        existing = _existing_stacks(client, kind)
    except EdhError as err:
        logger.exception(f"Listing {kind} software stacks failed: {type(err).__name__}")
        result["warnings"].append(
            f"{kind}: listing existing software stacks failed; {len(entries)} entries not attempted")
        return

    # The entries whose stack the launch project is to carry, by name; a skipped one is checked too, so
    # a redeploy attaches a stack an earlier run registered outside every project.
    wanted: Dict[str, str] = {}
    created = False
    for index, entry in enumerate(entries):
        try:
            model = model_cls(**entry)
        except (ValidationError, TypeError, ValueError) as err:
            logger.exception(f"{kind}[{index}] is not a valid registration entry: {err}")
            result["warnings"].append(f"{kind}[{index}]: invalid entry")
            continue
        label = f"{kind}:{model.stackName}"
        if model.stackName in existing:
            result["skipped"].append(label)
            wanted[model.stackName] = label
            registered_ami = str(existing[model.stackName].get(EDH_STACK_FIELD_AMI_ID) or "")
            if registered_ami and registered_ami != model.amiId:
                result["warnings"].append(
                    f"{label}: already registered in EDH with a different AMI; not updated "
                    f"(delete it from the Images tab or register it under a new stackName)")
            continue
        try:
            client.register_software_stack(kind, fields_fn(model))
        except EdhError as err:
            logger.exception(f"{label} registration failed: {type(err).__name__}")
            result["warnings"].append(f"{label}: registration failed ({type(err).__name__})")
            continue
        result["registered"].append(label)
        created = True
        wanted[model.stackName] = label
        existing[model.stackName] = {NATURAL_KEY: model.stackName, EDH_STACK_FIELD_AMI_ID: model.amiId}

    _assign_to_project(client, kind, wanted, existing, result, reread=created)


def _existing_job_definitions() -> Dict[str, List[Dict[str, Any]]]:
    """The ACTIVE Batch job definitions tagged for this cluster, every revision, grouped by name."""
    existing: Dict[str, List[Dict[str, Any]]] = {}
    for page in batch.get_paginator("describe_job_definitions").paginate(status="ACTIVE"):
        for record in page.get("jobDefinitions", []):
            tags = record.get("tags") or {}
            if str(tags.get(EDH_VISIBILITY_TAG, "")).lower() != EDH_VISIBILITY_TAG_VALUE:
                continue
            name = record.get(BATCH_JOB_DEFINITION_NAME_FIELD)
            if name:
                existing.setdefault(str(name), []).append(record)
    return existing


def _matches_request(record: Dict[str, Any], request: Dict[str, Any]) -> bool:
    """Whether an existing revision already names the requested image, resources and platform."""
    properties = record.get("containerProperties") or {}
    wanted = request["containerProperties"]
    if properties.get("image") != wanted["image"]:
        return False
    have = {(item.get("type"), str(item.get("value"))) for item in properties.get("resourceRequirements") or []}
    want = {(item["type"], item["value"]) for item in wanted["resourceRequirements"]}
    if have != want:
        return False
    platforms = record.get("platformCapabilities") or [JOB_DEFINITION_DEFAULT_PLATFORM]
    return sorted(platforms) == sorted(request["platformCapabilities"])


def _register_job_definitions(entries: List[Dict[str, Any]], result: Dict[str, List[str]]) -> None:
    """Register the entries with Batch under the visibility tag: a name whose tagged ACTIVE revision
    already matches is skipped; otherwise a new revision is registered and the tagged revisions it
    supersedes are retired, so a rebuilt image is what the name resolves to."""
    if not entries:
        return
    try:
        existing = _existing_job_definitions()
    except (BotoCoreError, ClientError) as err:
        logger.exception(f"Listing Batch job definitions failed: {type(err).__name__}")
        result["warnings"].append(
            f"jobDefinition: listing existing job definitions failed; {len(entries)} entries not attempted")
        return

    for index, entry in enumerate(entries):
        try:
            request = job_definition_request(entry)
        except ValueError as err:
            logger.exception(f"jobDefinition[{index}] is not a valid registration entry: {err}")
            result["warnings"].append(f"jobDefinition[{index}]: invalid entry")
            continue
        name = request[BATCH_JOB_DEFINITION_NAME_FIELD]
        label = f"jobDefinition:{name}"
        if request["platformCapabilities"] != [JOB_DEFINITION_DEFAULT_PLATFORM]:
            result["warnings"].append(f"{label}: only EC2 job definitions are registered here")
            continue
        revisions = existing.get(name, [])
        if any(_matches_request(record, request) for record in revisions):
            result["skipped"].append(label)
            continue
        try:
            batch.register_job_definition(**request)
        except (BotoCoreError, ClientError) as err:
            logger.exception(f"{label} registration failed: {type(err).__name__}")
            result["warnings"].append(f"{label}: registration failed ({type(err).__name__})")
            continue
        result["registered"].append(label)
        for record in revisions:
            arn = record.get(BATCH_JOB_DEFINITION_ARN_FIELD)
            if not arn:
                continue
            try:
                batch.deregister_job_definition(jobDefinition=arn)
            except (BotoCoreError, ClientError) as err:
                logger.exception(f"{label} superseded revision not deregistered: {type(err).__name__}")
                result["warnings"].append(
                    f"{label}: superseded revision not deregistered ({type(err).__name__})")


def _read_object(bucket: str, key: str) -> str:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")


def _read_bundle(bucket: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
    """The recipe manifest with every ``file`` component inlined as ``{name, data}``; the name is the
    file's base name without its extension, and, for a recipe that declares assets, the key prefix its
    asset trees were uploaded under."""
    prefix = bundle["prefix"]
    manifest = json.loads(_read_object(bucket, f"{prefix}/recipe.json"))
    components = []
    for component in manifest.get("components", []):
        if isinstance(component, dict) and "file" in component:
            file_name = str(component["file"])
            components.append({
                "name": _COMPONENT_FILE_SUFFIX.sub("", os.path.basename(file_name)),
                "data": _read_object(bucket, f"{prefix}/{file_name}"),
            })
        else:
            components.append(component)
    manifest["components"] = components
    # The tree the CDK deployment uploaded sits beside the recipe files; the build resolves
    # `${ASSET_<id>}` under this prefix.
    manifest.pop("assetPrefix", None)
    if manifest.get("assets"):
        manifest["assetPrefix"] = f"{prefix}/assets"
    return manifest


def _seed_lease_policy(resource_properties: Dict[str, Any], result: Dict[str, List[str]]) -> None:
    """Seed the lease settings row from config defaults, once (insert-if-absent)."""
    raw = resource_properties.get(PROPERTY_LEASE_POLICY_DEFAULTS)
    if not raw:
        return
    try:
        defaults = json.loads(raw) if isinstance(raw, str) else raw
        if edhLeasePolicy.seed_lease_policy(defaults):
            result["registered"].append("leasePolicy")
        else:
            result["skipped"].append("leasePolicy (admin-owned)")
    except Exception as err:
        logger.exception("leasePolicy: seeding failed")
        result["warnings"].append(f"leasePolicy: {type(err).__name__}")


def _seed_ami_recipes(resource_properties: Dict[str, Any], result: Dict[str, List[str]]) -> None:
    if str(resource_properties.get(PROPERTY_AMI_BUILDS_ENABLED, "false")).lower() != "true":
        return
    bundles = _parse_list_property(resource_properties, PROPERTY_AMI_RECIPE_BUNDLES)
    bucket = get_bucket_name(ResourceKeys.ARTEFACTS_BUCKET)
    present: List[str] = []
    unread: List[str] = []
    for bundle in bundles:
        label = f"amiRecipe:{bundle.get('recipeId')}"
        build_on_deploy = bool(bundle.get("buildOnDeploy"))
        try:
            manifest = _read_bundle(bucket, bundle)
            row, outcome = recipes.upsert_config_recipe(manifest, build_on_deploy=build_on_deploy)
        except Exception as err:
            logger.exception(f"{label}: seeding failed")
            result["warnings"].append(f"{label}: {type(err).__name__}")
            unread.append(label)
            continue
        if row is None:
            result["skipped"].append(f"{label} (admin-owned)")
            continue
        present.append(row["recipeId"])
        result["registered" if outcome == "changed" else "skipped"].append(label)
        if not build_on_deploy:
            continue
        try:
            build, build_outcome = recipes.start_build(row, recipes.REQUESTED_BY_DEPLOY, only_if_changed=True)
        except recipes.RecipeConflict as err:
            result["warnings"].append(f"{label}: build not started ({err})")
            continue
        except Exception as err:
            logger.exception(f"{label}: build not started")
            result["warnings"].append(f"{label}: build not started ({type(err).__name__})")
            continue
        if build_outcome in (recipes.OUTCOME_STARTED, recipes.OUTCOME_QUEUED):
            result["queued"].append(f"{label} v{build['version']} ({build_outcome})")
    if unread:
        # A bundle that did not read is indistinguishable from a recipe removed from the
        # configuration, so this run disables nothing.
        result["warnings"].append(
            f"AMI recipe seeding: {len(unread)} bundle(s) could not be read; disable pass skipped")
        return
    for recipe_id in recipes.disable_missing_config_recipes(present):
        result["skipped"].append(f"amiRecipe:{recipe_id} (disabled)")


def register_catalog(resource_properties: Dict[str, Any]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {"registered": [], "skipped": [], "warnings": [], "queued": []}
    vdi_entries = _parse_list_property(resource_properties, PROPERTY_VDI)
    simulation_entries = _parse_list_property(resource_properties, PROPERTY_SIMULATION)
    job_definition_entries = _parse_list_property(resource_properties, PROPERTY_JOB_DEFINITIONS)

    if vdi_entries or simulation_entries:
        client = EdhClient(profile="batch")
        _register_kind(client, KIND_DCV, vdi_entries, RegisterVdiImageRequestModel,
                       vdi_stack_fields, result)
        _register_kind(client, KIND_TARGET_NODES, simulation_entries,
                       RegisterSimulationImageRequestModel, simulation_stack_fields, result)
    _register_job_definitions(job_definition_entries, result)
    _seed_ami_recipes(resource_properties, result)
    _seed_lease_policy(resource_properties, result)
    return result


def _response_data(result: Dict[str, List[str]]) -> Dict[str, str]:
    """Flat string attributes for CloudFormation. Every key is always present ('none' when empty) so
    a GetAtt on any of them resolves."""
    def joined(values):
        return "; ".join(values) if values else "none"

    return {
        "registered": joined(result["registered"]),
        "skipped": joined(result["skipped"]),
        "warnings": joined(result["warnings"]),
        "queued": joined(result.get("queued", [])),
    }


def lambda_handler(event, context: LambdaContext):
    request_type = event.get("RequestType")
    resource_properties = event.get("ResourceProperties") or {}
    # Echo the id CloudFormation already holds; mint one only when the event carries none.
    physical_id = event.get("PhysicalResourceId") or f"EdhCatalogRegistration:{edh_cluster_id}"

    if request_type == "Delete":
        # Registered images outlive the deployment; nothing to undo.
        return {
            "PhysicalResourceId": physical_id,
            "Data": _response_data({"registered": [], "skipped": [], "warnings": [], "queued": []}),
        }

    if request_type not in ("Create", "Update"):
        raise ValueError(f"Unsupported RequestType: {request_type}")

    logger.info({
        "requestType": request_type,
        "contentHash": resource_properties.get("contentHash"),
        "registrationRevision": resource_properties.get("registrationRevision"),
    })
    result = register_catalog(resource_properties)
    logger.info(result)
    return {"PhysicalResourceId": physical_id, "Data": _response_data(result)}
