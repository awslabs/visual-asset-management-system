# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fakes shared by the AMI recipe tests: a moto recipe table and recording Image Builder / EDH clients."""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3

from backend.backend.handlers.addon.edh.common import edhAmiRecipeStore as store
from backend.backend.handlers.addon.edh.common import edhImageBuilder as ib_mod
from backend.tests.handlers.addon.edh.edhTestSupport import FakeProjects

TABLE = os.environ["EDH_AMI_RECIPE_STORAGE_TABLE_NAME"]
ALREADY = type("ResourceAlreadyExistsException", (Exception,), {})
NOT_FOUND = type("ResourceNotFoundException", (Exception,), {})
DEPENDENCY = type("ResourceDependencyException", (Exception,), {})

RECIPE_BODY = {
    "recipeId": "al2023-desktop", "displayName": "Amazon Linux 2023 desktop", "kind": "vdi", "osFamily": "linux",
    "baseOs": "amazonlinux2023", "architecture": "x86_64",
    "parentImage": "ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
    "buildInstanceType": "m7i.large", "rootSizeGb": 60, "stackName": "VAMS AL2023 Desktop", "profileId": 1,
    "components": [
        {"arn": "arn:${AWS::Partition}:imagebuilder:${AWS::Region}:aws:component/update-linux/x.x.x"},
        {"name": "baseline", "data": "name: b\nschemaVersion: 1.0\nphases: []\n"},
    ],
    "keepVersions": 2, "recipeRevision": 1, "description": "desc",
}


def create_recipe_table(monkeypatch):
    """Inside an active mock_aws: create the table and point the store at the mocked resource."""
    resource = boto3.resource("dynamodb", region_name="us-east-1")
    resource.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"},
                              {"AttributeName": "imageBuildVersionArn", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{"IndexName": store.IMAGE_BUILD_ARN_INDEX,
                                 "KeySchema": [{"AttributeName": "imageBuildVersionArn", "KeyType": "HASH"}],
                                 "Projection": {"ProjectionType": "ALL"}}],
        BillingMode="PAY_PER_REQUEST",
    )
    monkeypatch.setattr(store, "dynamodb", resource)
    return resource.Table(TABLE)


def _describe_instances_paginator(ec2):
    """A describe_instances paginator over ``ec2.instances`` (``{"InstanceId", "ImageId"}`` records), two
    instances a page and filtered by image id. Each listing's Filters are recorded on
    ``ec2.instance_filters``; ``ec2.describe_instances_error`` is raised once the pages are read."""
    def pages(found, error):
        if error is not None:
            raise error
        for start in range(0, len(found), 2):
            yield {"Reservations": [{"Instances": [{"InstanceId": i["InstanceId"]} for i in found[start:start + 2]]}]}

    def paginate(Filters=(), **kwargs):
        ec2.instance_filters.append(Filters)
        images = next((f["Values"] for f in Filters if f["Name"] == "image-id"), None)
        found = [i for i in ec2.instances if images is None or i["ImageId"] in images]
        return pages(found, ec2.describe_instances_error)
    return SimpleNamespace(paginate=paginate)


def fake_context(ami="ami-0abc0abc0abc0abc0", image_status="AVAILABLE"):
    ib = MagicMock()
    ib.exceptions = SimpleNamespace(ResourceAlreadyExistsException=ALREADY, ResourceNotFoundException=NOT_FOUND,
                                    ResourceDependencyException=DEPENDENCY)
    ib.create_component.return_value = {"componentBuildVersionArn": "arn:c/1"}
    ib.create_image_recipe.return_value = {"imageRecipeArn": "arn:r/1"}
    ib.create_infrastructure_configuration.return_value = {"infrastructureConfigurationArn": "arn:i"}
    ib.create_distribution_configuration.return_value = {"distributionConfigurationArn": "arn:d"}
    ib.create_image_pipeline.return_value = {"imagePipelineArn": "arn:p"}
    counter = {"n": 0}

    def start(**kwargs):
        counter["n"] += 1
        return {"imageBuildVersionArn": f"arn:aws:imagebuilder:us-east-1:123456789012:image/x/1.0.0/{counter['n']}"}
    ib.start_image_pipeline_execution.side_effect = start
    ib.get_image.return_value = {"image": {"state": {"status": image_status, "reason": None},
                                           "outputResources": {"amis": [{"region": "us-east-1", "image": ami}]}}}
    ec2 = MagicMock()
    # No Architecture on the image and no instance type described: the architecture checks pass unless
    # a test gives them an answer.
    ec2.describe_images.return_value = {"Images": [{"RootDeviceName": "/dev/xvda",
                                                    "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"SnapshotId": "snap-1"}}]}]}
    ec2.describe_instance_types.return_value = {"InstanceTypes": []}
    # No instance uses any AMI unless a test lists one.
    ec2.instances = []
    ec2.instance_filters = []
    ec2.describe_instances_error = None
    paginators = {"describe_instances": _describe_instances_paginator(ec2)}
    ec2.get_paginator.side_effect = lambda name: paginators[name]
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": ami}}
    return ib_mod.ImageBuilderContext(prefix="vams-test-edh", region="us-east-1", partition="aws", account="123456789012",
                                      subnet_id="subnet-1", security_group_id="sg-1", instance_profile_name="prof",
                                      cluster_id=os.environ["EDH_CLUSTER_ID"], ib=ib, ec2=ec2, ssm=ssm,
                                      deployment_parameters=ib_mod.deployment_parameters_from_env(),
                                      lookup_ec2=ec2, lookup_ssm=ssm)


def fake_edh(stack_id=42, projects=None):
    """An EDH client whose catalog lists whatever was registered, under a fixed id, and whose project
    table records the stacks the registration attached to it."""
    client = MagicMock()
    registered = []
    projects = projects if projects is not None else FakeProjects()

    def register(kind, fields, **kwargs):
        registered.append({"id": stack_id + len(registered), "stack_name": fields["stack_name"], "ami_id": fields["ami_id"]})
        return {"message": "ok"}
    client.register_software_stack.side_effect = register
    client.list_software_stacks.side_effect = lambda kind, **kwargs: list(registered)
    client.delete_software_stack.return_value = {"message": "deactivated"}
    client.assign_software_stack_to_project.side_effect = (
        lambda kind, stack_id, project_name, **kwargs: projects.assign(kind, stack_id, project_name))
    client.registered = registered
    client.projects = projects
    return client
