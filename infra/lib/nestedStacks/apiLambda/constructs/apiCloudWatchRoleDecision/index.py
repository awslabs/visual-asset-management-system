# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Decides whether the core stack creates the API Gateway account CloudWatch role or reuses one.

The role carries a fixed name and is retained on teardown and rollback, so a fresh deploy of the
same configuration would fail on a name that already exists. The answer is decided once, when this
custom resource is created, and kept for the life of the stack:

- "managed": the stack owns the role (today's behaviour). Every stack that already exists when
  this resource is added is being updated, so it always gets "managed" and keeps its role as is.
- "reuse:<arn>": a fresh stack found a retained role with its name and points the account
  setting at it instead of creating it.

Any failure to read the stack status or the role answers "managed", which is the behaviour without
this resource.
"""
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MANAGED = "managed"
REUSE_PREFIX = "reuse:"


def _stack_is_being_created(stack_id):
    """True only when the stack is in its first create. Unknown is treated as an existing stack."""
    try:
        stacks = boto3.client("cloudformation").describe_stacks(StackName=stack_id)["Stacks"]
        status = stacks[0]["StackStatus"]
    except (BotoCoreError, ClientError, KeyError, IndexError) as error:
        logger.warning("Could not read the stack status, keeping the stack-owned role: %s", error)
        return False
    logger.info("Stack status: %s", status)
    return status == "CREATE_IN_PROGRESS"


def _existing_role_arn(role_name):
    """The ARN of an existing role with this name, or None when there is none or it cannot be read."""
    try:
        return boto3.client("iam").get_role(RoleName=role_name)["Role"]["Arn"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "NoSuchEntity":
            logger.warning("Could not read role %s, creating it: %s", role_name, error)
        return None
    except (BotoCoreError, KeyError) as error:
        logger.warning("Could not read role %s, creating it: %s", role_name, error)
        return None


def _decision(physical_id):
    if physical_id.startswith(REUSE_PREFIX):
        arn = physical_id[len(REUSE_PREFIX):]
        return {"PhysicalResourceId": physical_id, "Data": {"Mode": "reuse", "RoleArn": arn}}
    return {"PhysicalResourceId": MANAGED, "Data": {"Mode": MANAGED, "RoleArn": ""}}


def _decide_for_name(role_name):
    arn = _existing_role_arn(role_name)
    return _decision(REUSE_PREFIX + arn if arn else MANAGED)


def on_event(event, context):
    request_type = event["RequestType"]
    props = event.get("ResourceProperties", {})
    role_name = props.get("RoleName", "")
    logger.info("Request %s for role %s", request_type, role_name)

    if request_type == "Delete":
        # Nothing to clean up: the role, when this stack owns it, is a resource of its own.
        return {"PhysicalResourceId": event["PhysicalResourceId"]}

    if request_type == "Create":
        if not _stack_is_being_created(props.get("StackId", "")):
            return _decision(MANAGED)
        return _decide_for_name(role_name)

    # Update: keep the decision taken at create. A stack-owned role stays stack-owned whatever
    # exists in the account. A reused role is checked again only if its name changed.
    previous = event.get("PhysicalResourceId", MANAGED)
    old_name = event.get("OldResourceProperties", {}).get("RoleName", "")
    if previous.startswith(REUSE_PREFIX) and old_name != role_name:
        return _decide_for_name(role_name)
    return _decision(previous)
