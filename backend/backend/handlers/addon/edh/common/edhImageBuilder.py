# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""EC2 Image Builder resources for an AMI recipe, created idempotently from the recipe's content.

Every object is named ``<prefix>-<recipeId>`` (components ``<prefix>-<recipeId>-cNN-<slug>``) and
versioned from a hash of the content (and, for a recipe with assets, of the bucket its trees were
uploaded to, and of the deployment values its documents name), so the same content yields the same
objects and a changed recipe yields new versions beside the old ones. ``ResourceAlreadyExists`` is the
ordinary second run, answered by a lookup; the infrastructure and distribution configurations and the
pipeline are updated in place.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from customLogging.logger import safeLogger
from models.addon.edh import AMI_RECIPE_COMPONENT_DATA_MAX_BYTES
from .edhCatalogFields import KIND_BY_IMAGE_KIND

logger = safeLogger(service_name="EdhImageBuilder")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
# The clients a recipe save looks the architecture up with: two attempts in all, each bounded by its connect
# and read timeouts. edhAmiRecipes.ARCHITECTURE_LOOKUP_BUDGET_SECONDS bounds the lookups of one save.
lookup_retry_config = Config(connect_timeout=2, read_timeout=3, retries={'total_max_attempts': 2, 'mode': 'standard'})

RECIPE_TAG = "vams:edhAmiRecipe"
HASH_TAG = "vams:edhAmiRecipeHash"
DEPLOYMENT_TAG = "vams:deployment"
# EDH's cluster visibility tag. EDH reads it on Batch, ECR, EKS and agent resources, not on an AMI (an
# image is offered through its registered software stack); on the AMI it records the target cluster.
VISIBILITY_TAG_PREFIX = "edh:visibility:"
VISIBILITY_TAG_VALUE = "true"

PARENT_SSM_PREFIX = "ssm:"
PARENT_EDH_STACK_PREFIX = "edhStack:"
EDH_STACK_NAME_FIELD = "stack_name"
EDH_STACK_AMI_FIELD = "ami_id"

PLATFORM_BY_OS_FAMILY = {"linux": "Linux", "windows": "Windows"}
# IMDSv2 on the build and test instances whatever the parent AMI declares; two hops so a container on
# the instance still reaches the metadata service.
BUILD_INSTANCE_METADATA_OPTIONS = {"httpTokens": "required", "httpPutResponseHopLimit": 2}
# The deployment values a component document reaches as ``${DEPLOY_<key>}``: a closed set, each read
# from the named environment variable and substituted only when it has the form given.
DEPLOYMENT_PARAMETERS = {
    # The Secrets Manager name prefix of the per-session secrets a desktop's sign-in reads.
    "sessionSecretPrefix": ("EDH_SESSION_SECRET_PREFIX", re.compile(r"^[A-Za-z0-9/_+=.@-]+/\Z")),
}
_SLUG = re.compile(r"[^A-Za-z0-9_-]")
_NOT_FOUND_CODES = ("InvalidAMIID.NotFound", "InvalidAMIID.Unavailable", "InvalidSnapshot.NotFound")
# The DescribeImages errors of a parent AMI id EC2 does not find, and the refusal they become.
_PARENT_NOT_FOUND_CODES = ("InvalidAMIID.NotFound", "InvalidAMIID.Malformed")
PARENT_IMAGE_NOT_FOUND = "the parent image could not be found"
# The DescribeInstanceTypes error of a call naming an instance type EC2 does not offer.
_INSTANCE_TYPE_NOT_OFFERED_CODE = "InvalidInstanceType"
# The root volume's device name when the parent image does not report one.
DEFAULT_ROOT_DEVICE_NAME = "/dev/xvda"
# The states of an instance that runs from its AMI or can be started from it again.
IN_USE_INSTANCE_STATES = ("pending", "running", "stopping", "stopped")

_PLACEHOLDER = re.compile(r"\$\{(ASSET|PARAM|DEPLOY)_([^}]*)\}")


class ParentImageUnavailable(Exception):
    """The recipe's parent image cannot be resolved to an AMI id."""


class ComponentDocumentError(ValueError):
    """A component document cannot be materialized: it names an asset or parameter the recipe does
    not declare or a deployment value that is unset or not of its form, the recipe declares assets
    without a bucket to fetch them from, or the resolved document is longer than CreateComponent
    accepts."""


@dataclass
class ImageBuilderContext:
    prefix: str
    region: str
    partition: str
    account: str
    subnet_id: str
    security_group_id: str
    instance_profile_name: str
    cluster_id: str
    ib: Any
    ec2: Any
    ssm: Any
    # Bucket the recipe bundles and asset trees were uploaded to; empty when the deployment seeds no
    # recipe with assets.
    artefacts_bucket: str = ""
    # ``${DEPLOY_<key>}`` values, keyed as DEPLOYMENT_PARAMETERS.
    deployment_parameters: Dict[str, str] = field(default_factory=dict)
    # EC2 and SSM clients on lookup_retry_config, for the architecture lookups of a recipe save.
    lookup_ec2: Any = None
    lookup_ssm: Any = None


def deployment_parameters_from_env() -> Dict[str, str]:
    return {key: os.environ.get(variable, "") for key, (variable, _) in DEPLOYMENT_PARAMETERS.items()}


def context_from_env(*, ib=None, ec2=None, ssm=None, lookup_ec2=None, lookup_ssm=None) -> ImageBuilderContext:
    return ImageBuilderContext(
        prefix=os.environ["EDH_AMI_BUILD_PREFIX"],
        cluster_id=os.environ["EDH_CLUSTER_ID"],
        region=os.environ.get("AWS_REGION") or os.environ["AWS_DEFAULT_REGION"],
        partition=os.environ["EDH_AMI_BUILD_PARTITION"],
        account=os.environ["EDH_AMI_BUILD_ACCOUNT_ID"],
        subnet_id=os.environ["EDH_AMI_BUILD_SUBNET_ID"],
        security_group_id=os.environ["EDH_AMI_BUILD_SECURITY_GROUP_ID"],
        instance_profile_name=os.environ["EDH_AMI_BUILD_INSTANCE_PROFILE_NAME"],
        artefacts_bucket=os.environ.get("EDH_AMI_BUILD_ARTEFACTS_BUCKET", ""),
        deployment_parameters=deployment_parameters_from_env(),
        ib=ib or boto3.client("imagebuilder", config=retry_config),
        ec2=ec2 or boto3.client("ec2", config=retry_config),
        ssm=ssm or boto3.client("ssm", config=retry_config),
        lookup_ec2=lookup_ec2 or boto3.client("ec2", config=lookup_retry_config),
        lookup_ssm=lookup_ssm or boto3.client("ssm", config=lookup_retry_config),
    )


def semantic_version(hex_hash: str) -> str:
    return f"1.{int(hex_hash[:6], 16)}.{int(hex_hash[6:12], 16)}"


def build_key(content_hash: str, resolved_parent_ami: str) -> str:
    return hashlib.sha256(f"{content_hash}:{resolved_parent_ami}".encode("utf-8")).hexdigest()


def materialization_hash(recipe: Dict[str, Any], ctx: ImageBuilderContext) -> str:
    """The content hash, joined with the artefacts bucket when the recipe declares assets and with the
    deployment values its documents name: each is part of the substituted documents, so a different
    bucket or value must version the objects anew. A recipe that names neither keeps its content hash."""
    content_hash = recipe["contentHash"]
    if recipe.get("assets"):
        content_hash = hashlib.sha256(f"{content_hash}:{ctx.artefacts_bucket}".encode("utf-8")).hexdigest()
    deployment = referenced_deployment_parameters(recipe, ctx.deployment_parameters)
    if deployment:
        encoded = json.dumps(deployment, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(f"{content_hash}:{encoded}".encode("utf-8")).hexdigest()
    return content_hash


def substitute_placeholders(arn: str, ctx: ImageBuilderContext) -> str:
    return arn.replace("${AWS::Partition}", ctx.partition).replace("${AWS::Region}", ctx.region)


def referenced_deployment_parameters(recipe: Dict[str, Any], deployment_parameters: Dict[str, str]) -> Dict[str, str]:
    """The DEPLOYMENT_PARAMETERS keys the recipe's inline documents name, with their values."""
    names = {name for component in recipe.get("components", []) if not component.get("arn")
             for kind, name in _PLACEHOLDER.findall(component.get("data") or "")
             if kind == "DEPLOY" and name in DEPLOYMENT_PARAMETERS}
    return {name: deployment_parameters.get(name, "") for name in sorted(names)}


def substitute_component_data(data: str, artefacts_bucket: str, recipe: Dict[str, Any],
                              deployment_parameters: Dict[str, str]) -> str:
    """An inline component document with its recipe's asset URIs and parameters, and the deployment's
    values, resolved.

    ``${ASSET_<id>}`` becomes the s3:// prefix the asset tree was uploaded to, with a trailing slash so a
    recursive copy stops at the tree, ``${PARAM_<key>}`` the parameter's value and ``${DEPLOY_<key>}``
    the deployment value of a DEPLOYMENT_PARAMETERS key. Every token is resolved in one pass, so a
    substituted value is never scanned again. A placeholder the recipe does not declare, or a deployment
    value that is unset or not of its key's form, is an error: a build that ran with it left in place
    would fetch or read nothing and fail far from its cause."""
    assets = {asset["id"] for asset in recipe.get("assets") or []}
    asset_prefix = recipe.get("assetPrefix")
    parameters = recipe.get("parameters") or {}
    if assets and not (artefacts_bucket and asset_prefix):
        raise ComponentDocumentError(
            "recipe declares assets but EDH_AMI_BUILD_ARTEFACTS_BUCKET or assetPrefix is not set")
    unresolved: List[str] = []
    malformed: List[str] = []

    def resolve(match: "re.Match[str]") -> str:
        kind, name = match.groups()
        if kind == "ASSET" and name in assets:
            return f"s3://{artefacts_bucket}/{asset_prefix}/{name}/"
        if kind == "PARAM" and name in parameters:
            return str(parameters[name])
        if kind == "DEPLOY" and name in DEPLOYMENT_PARAMETERS and deployment_parameters.get(name):
            value = deployment_parameters[name]
            if DEPLOYMENT_PARAMETERS[name][1].match(value):
                return value
            malformed.append(match.group(0))
            return match.group(0)
        unresolved.append(match.group(0))
        return match.group(0)

    resolved = _PLACEHOLDER.sub(resolve, data)
    if unresolved:
        raise ComponentDocumentError(
            f"unresolved placeholder(s) in component document: {', '.join(sorted(set(unresolved)))}")
    if malformed:
        raise ComponentDocumentError(
            f"deployment value(s) not in the expected form for: {', '.join(sorted(set(malformed)))}")
    return resolved


def component_documents(recipe: Dict[str, Any], artefacts_bucket: str,
                        deployment_parameters: Dict[str, str]) -> List[Optional[str]]:
    """Each component's document as CreateComponent receives it, ``None`` for one named by ARN. The
    length limit applies to the resolved document. Recipe create and update resolve the documents the
    same way, so a recipe whose documents cannot be materialized is refused when it is saved."""
    documents: List[Optional[str]] = []
    for index, component in enumerate(recipe["components"], start=1):
        if component.get("arn"):
            documents.append(None)
            continue
        document = substitute_component_data(component["data"], artefacts_bucket, recipe, deployment_parameters)
        if len(document) > AMI_RECIPE_COMPONENT_DATA_MAX_BYTES:
            raise ComponentDocumentError(
                f"component {index} is {len(document)} characters once its placeholders are resolved; "
                f"Image Builder accepts at most {AMI_RECIPE_COMPONENT_DATA_MAX_BYTES}")
        documents.append(document)
    return documents


def build_instance_types(recipe: Dict[str, Any]) -> List[str]:
    """The recipe's build instance type as the list Image Builder chooses among by availability."""
    value = recipe["buildInstanceType"]
    return [value] if isinstance(value, str) else list(value)


def resolve_parent_image(parent_image: str, *, ctx: ImageBuilderContext, edh_client, kind: str) -> str:
    if parent_image.startswith("ami-"):
        return parent_image
    if parent_image.startswith(PARENT_SSM_PREFIX):
        name = parent_image[len(PARENT_SSM_PREFIX):]
        try:
            return ctx.ssm.get_parameter(Name=name)["Parameter"]["Value"]
        except ClientError as err:
            raise ParentImageUnavailable(f"SSM parameter {name} could not be read ({err.response['Error']['Code']})")
    if parent_image.startswith(PARENT_EDH_STACK_PREFIX):
        stack_name = parent_image[len(PARENT_EDH_STACK_PREFIX):]
        for record in edh_client.list_software_stacks(KIND_BY_IMAGE_KIND[kind]):
            if (isinstance(record, dict) and str(record.get(EDH_STACK_NAME_FIELD)) == stack_name
                    and record.get(EDH_STACK_AMI_FIELD)):
                return str(record[EDH_STACK_AMI_FIELD])
        raise ParentImageUnavailable(f"EDH lists no {kind} stack named {stack_name!r}")
    raise ParentImageUnavailable(f"unsupported parent image reference: {parent_image!r}")


def describe_parent_image(ec2, ami_id: str) -> Dict[str, Any]:
    """The parent AMI's ``architecture`` (None when EC2 reports none) and ``rootDeviceName``, from one
    DescribeImages call. An AMI EC2 does not find raises ParentImageUnavailable; any other error is
    raised as it is."""
    try:
        images = ec2.describe_images(ImageIds=[ami_id]).get("Images") or []
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") in _PARENT_NOT_FOUND_CODES:
            raise ParentImageUnavailable(PARENT_IMAGE_NOT_FOUND)
        raise
    if not images:
        raise ParentImageUnavailable(PARENT_IMAGE_NOT_FOUND)
    image = images[0]
    return {"architecture": image.get("Architecture"),
            "rootDeviceName": image.get("RootDeviceName") or DEFAULT_ROOT_DEVICE_NAME}


def _described_architectures(ec2, instance_types: List[str]) -> Optional[Dict[str, List[str]]]:
    """The architectures of each type one DescribeInstanceTypes call describes; None when EC2 refuses the
    call for naming an instance type it does not offer. Any other error is raised as it is."""
    try:
        described = ec2.describe_instance_types(InstanceTypes=list(instance_types)).get("InstanceTypes") or []
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == _INSTANCE_TYPE_NOT_OFFERED_CODE:
            return None
        raise
    return {str(item.get("InstanceType")): list((item.get("ProcessorInfo") or {}).get("SupportedArchitectures") or [])
            for item in described if isinstance(item, dict)}


def instance_type_architectures(ec2, instance_types: List[str]) -> Dict[str, List[str]]:
    """Each instance type EC2 describes, mapped to the architectures it supports (``[]`` when EC2
    reports none), from one DescribeInstanceTypes call. A list EC2 refuses for naming a type it does not
    offer is described one type at a time, so its other types are still mapped. A type EC2 does not list
    or does not offer is absent; any other error is raised as it is."""
    supported = _described_architectures(ec2, instance_types)
    if supported is not None:
        return supported
    supported = {}
    if len(instance_types) > 1:
        for instance_type in instance_types:
            supported.update(_described_architectures(ec2, [instance_type]) or {})
    return supported


def resource_name(ctx: ImageBuilderContext, recipe_id: str) -> str:
    return f"{ctx.prefix}-{recipe_id}"


def component_name(ctx: ImageBuilderContext, recipe_id: str, index: int, name: str) -> str:
    slug = _SLUG.sub("-", name)[:40].lower()
    return f"{ctx.prefix}-{recipe_id}-c{index:02d}-{slug}"


def _arn(ctx: ImageBuilderContext, resource_type: str, name: str, version: Optional[str] = None) -> str:
    arn = f"arn:{ctx.partition}:imagebuilder:{ctx.region}:{ctx.account}:{resource_type}/{name.lower()}"
    return f"{arn}/{version}" if version else arn


def _tags(recipe: Dict[str, Any], ctx: ImageBuilderContext) -> Dict[str, str]:
    return {RECIPE_TAG: recipe["recipeId"], HASH_TAG: recipe["contentHash"], DEPLOYMENT_TAG: ctx.prefix}


def visibility_tag(cluster_id: str) -> str:
    return f"{VISIBILITY_TAG_PREFIX}{cluster_id}"


def ami_tags(recipe: Dict[str, Any], ctx: ImageBuilderContext) -> Dict[str, str]:
    """The tag set the built AMI and its snapshots carry: the VAMS tags plus the EDH cluster's
    visibility tag, applied by the distribution configuration and again once the image is built."""
    return {**_tags(recipe, ctx), visibility_tag(ctx.cluster_id): VISIBILITY_TAG_VALUE}


def _ensure_component(ctx, name, version, platform, data, tags) -> str:
    try:
        return ctx.ib.create_component(name=name, semanticVersion=version, platform=platform, data=data, tags=tags,
                                       description=f"VAMS EDH AMI recipe component {name}")["componentBuildVersionArn"]
    except ctx.ib.exceptions.ResourceAlreadyExistsException:
        versions = ctx.ib.list_component_build_versions(componentVersionArn=_arn(ctx, "component", name, version))
        return versions["componentSummaryList"][0]["arn"]


def _ensure_image_recipe(ctx, name, version, parent_ami, root_device_name, root_size_gb, component_arns, tags) -> str:
    try:
        return ctx.ib.create_image_recipe(
            name=name, semanticVersion=version, parentImage=parent_ami,
            components=[{"componentArn": arn} for arn in component_arns],
            blockDeviceMappings=[{"deviceName": root_device_name,
                                  "ebs": {"volumeSize": int(root_size_gb), "volumeType": "gp3",
                                          "deleteOnTermination": True}}],
            tags=tags, description=f"VAMS EDH AMI recipe {name}")["imageRecipeArn"]
    except ctx.ib.exceptions.ResourceAlreadyExistsException:
        return _arn(ctx, "image-recipe", name, version)


def _ensure_infrastructure(ctx, name, instance_types, tags) -> str:
    kwargs = dict(instanceProfileName=ctx.instance_profile_name, instanceTypes=instance_types,
                  subnetId=ctx.subnet_id, securityGroupIds=[ctx.security_group_id],
                  terminateInstanceOnFailure=True, instanceMetadataOptions=dict(BUILD_INSTANCE_METADATA_OPTIONS))
    try:
        return ctx.ib.create_infrastructure_configuration(name=name, tags=tags, **kwargs)["infrastructureConfigurationArn"]
    except ctx.ib.exceptions.ResourceAlreadyExistsException:
        listing = ctx.ib.list_infrastructure_configurations(filters=[{"name": "name", "values": [name]}])
        arn = listing["infrastructureConfigurationSummaryList"][0]["arn"]
        ctx.ib.update_infrastructure_configuration(infrastructureConfigurationArn=arn, **kwargs)
        return arn


def _ensure_distribution(ctx, name, ami_tags, tags) -> str:
    distributions = [{"region": ctx.region, "amiDistributionConfiguration": {
        "name": f"{name}-{{{{ imagebuilder:buildDate }}}}", "amiTags": ami_tags}}]
    try:
        return ctx.ib.create_distribution_configuration(name=name, distributions=distributions,
                                                        tags=tags)["distributionConfigurationArn"]
    except ctx.ib.exceptions.ResourceAlreadyExistsException:
        listing = ctx.ib.list_distribution_configurations(filters=[{"name": "name", "values": [name]}])
        arn = listing["distributionConfigurationSummaryList"][0]["arn"]
        ctx.ib.update_distribution_configuration(distributionConfigurationArn=arn, distributions=distributions)
        return arn


def _ensure_pipeline(ctx, name, recipe_arn, infra_arn, dist_arn, tags) -> str:
    kwargs = dict(imageRecipeArn=recipe_arn, infrastructureConfigurationArn=infra_arn,
                  distributionConfigurationArn=dist_arn, status="ENABLED")
    try:
        return ctx.ib.create_image_pipeline(name=name, tags=tags, **kwargs)["imagePipelineArn"]
    except ctx.ib.exceptions.ResourceAlreadyExistsException:
        listing = ctx.ib.list_image_pipelines(filters=[{"name": "name", "values": [name]}])
        arn = listing["imagePipelineList"][0]["arn"]
        ctx.ib.update_image_pipeline(imagePipelineArn=arn, **kwargs)
        return arn


def materialize(ctx: ImageBuilderContext, recipe: Dict[str, Any], resolved_parent_ami: str,
                root_device_name: Optional[str] = None) -> Dict[str, Any]:
    """Create or find the recipe's Image Builder objects. The root volume takes ``root_device_name``,
    or, when none is given, the parent AMI's own, read before any object is created."""
    tags = _tags(recipe, ctx)
    platform = PLATFORM_BY_OS_FAMILY[recipe["osFamily"]]
    content_hash = materialization_hash(recipe, ctx)
    component_version = semantic_version(content_hash)
    documents = component_documents(recipe, ctx.artefacts_bucket, ctx.deployment_parameters)
    if root_device_name is None:
        root_device_name = describe_parent_image(ctx.ec2, resolved_parent_ami)["rootDeviceName"]
    component_arns: List[str] = []
    for index, (component, document) in enumerate(zip(recipe["components"], documents), start=1):
        if document is None:
            component_arns.append(substitute_placeholders(component["arn"], ctx))
        else:
            component_arns.append(_ensure_component(
                ctx, component_name(ctx, recipe["recipeId"], index, component["name"]), component_version,
                platform, document, tags))
    key = build_key(content_hash, resolved_parent_ami)
    recipe_version = semantic_version(key)
    name = resource_name(ctx, recipe["recipeId"])
    recipe_arn = _ensure_image_recipe(ctx, name, recipe_version, resolved_parent_ami, root_device_name,
                                      recipe["rootSizeGb"], component_arns, tags)
    infra_arn = _ensure_infrastructure(ctx, name, build_instance_types(recipe), tags)
    dist_arn = _ensure_distribution(ctx, name, ami_tags(recipe, ctx), tags)
    pipeline_arn = _ensure_pipeline(ctx, name, recipe_arn, infra_arn, dist_arn, tags)
    return {
        "componentArns": component_arns, "recipeArn": recipe_arn, "recipeVersion": recipe_version,
        "infrastructureConfigurationArn": infra_arn, "distributionConfigurationArn": dist_arn,
        "pipelineArn": pipeline_arn, "buildKey": key,
    }


def start_pipeline(ctx: ImageBuilderContext, pipeline_arn: str) -> str:
    return ctx.ib.start_image_pipeline_execution(imagePipelineArn=pipeline_arn)["imageBuildVersionArn"]


def describe_image(ctx: ImageBuilderContext, image_build_version_arn: str) -> Dict[str, Any]:
    image = ctx.ib.get_image(imageBuildVersionArn=image_build_version_arn).get("image", {})
    state = image.get("state", {})
    ami_id = next((a.get("image") for a in image.get("outputResources", {}).get("amis", [])
                   if a.get("region") == ctx.region and a.get("image")), None)
    snapshot_ids: List[str] = []
    if ami_id:
        for described in ctx.ec2.describe_images(ImageIds=[ami_id]).get("Images", []):
            for mapping in described.get("BlockDeviceMappings", []):
                snapshot = mapping.get("Ebs", {}).get("SnapshotId")
                if snapshot:
                    snapshot_ids.append(snapshot)
    return {"status": state.get("status"), "reason": state.get("reason"), "amiId": ami_id, "snapshotIds": snapshot_ids}


def cancel_image(ctx: ImageBuilderContext, image_build_version_arn: str) -> None:
    ctx.ib.cancel_image_creation(imageBuildVersionArn=image_build_version_arn)


def tag_ami(ctx: ImageBuilderContext, ami_id: str, snapshot_ids: List[str], tags: Dict[str, str]) -> None:
    ctx.ec2.create_tags(Resources=[ami_id, *snapshot_ids], Tags=[{"Key": k, "Value": v} for k, v in tags.items()])


def instances_using_ami(ctx: ImageBuilderContext, ami_id: str) -> List[str]:
    """The ids of the instances in the account and Region that run from the AMI or can start from it again."""
    pages = ctx.ec2.get_paginator("describe_instances").paginate(Filters=[
        {"Name": "image-id", "Values": [ami_id]},
        {"Name": "instance-state-name", "Values": list(IN_USE_INSTANCE_STATES)},
    ])
    return sorted({instance["InstanceId"] for page in pages for reservation in page.get("Reservations", [])
                   for instance in reservation.get("Instances", [])})


def _ignore_not_found(call, **kwargs) -> None:
    try:
        call(**kwargs)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") not in _NOT_FOUND_CODES:
            raise


def deregister_ami(ctx: ImageBuilderContext, ami_id: str, snapshot_ids: List[str]) -> None:
    _ignore_not_found(ctx.ec2.deregister_image, ImageId=ami_id)
    for snapshot in snapshot_ids:
        _ignore_not_found(ctx.ec2.delete_snapshot, SnapshotId=snapshot)


def delete_image_build(ctx: ImageBuilderContext, image_build_version_arn: str) -> None:
    try:
        ctx.ib.delete_image(imageBuildVersionArn=image_build_version_arn)
    except ctx.ib.exceptions.ResourceNotFoundException:
        pass


def delete_recipe_resources(ctx: ImageBuilderContext, image_builder: Dict[str, Any]) -> List[str]:
    """Pipeline, recipe, inline components, then the two configurations. A component another image
    still references cannot be deleted; that is a warning, not a failure. Managed (``aws``) component
    ARNs are never deleted."""
    warnings: List[str] = []
    steps = [
        ("delete_image_pipeline", "imagePipelineArn", image_builder.get("pipelineArn")),
        ("delete_image_recipe", "imageRecipeArn", image_builder.get("recipeArn")),
    ]
    steps += [("delete_component", "componentBuildVersionArn", arn) for arn in image_builder.get("componentArns", [])
              if f":{ctx.account}:" in str(arn)]
    steps += [
        ("delete_infrastructure_configuration", "infrastructureConfigurationArn",
         image_builder.get("infrastructureConfigurationArn")),
        ("delete_distribution_configuration", "distributionConfigurationArn",
         image_builder.get("distributionConfigurationArn")),
    ]
    for method, parameter, arn in steps:
        if not arn:
            continue
        try:
            getattr(ctx.ib, method)(**{parameter: arn})
        except ctx.ib.exceptions.ResourceNotFoundException:
            continue
        except Exception as err:
            warnings.append(f"{method} {arn}: {type(err).__name__}")
    return warnings
