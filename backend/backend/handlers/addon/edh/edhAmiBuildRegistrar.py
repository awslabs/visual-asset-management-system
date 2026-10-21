# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Completes AMI builds. Invoked by the Image Builder state-change rule for one image, and by the
30-minute schedule with ``{"sweep": true}`` to close builds whose event was missed, retry failed
registrations, start queued builds and remove the AMIs that pruning kept or a retire left pending once
no instance uses them."""

from aws_lambda_powertools.utilities.typing import LambdaContext

from customLogging.logger import safeLogger
from .common import edhAmiRecipes as recipes

logger = safeLogger(service_name="EdhAmiBuildRegistrar")

IMAGE_STATE_SOURCE = "aws.imagebuilder"
IMAGE_STATE_DETAIL_TYPE = "EC2 Image Builder Image State Change"


def lambda_handler(event, context: LambdaContext):
    if isinstance(event, dict) and event.get("sweep"):
        summary = recipes.sweep()
        logger.info({"sweep": summary})
        return summary
    if (isinstance(event, dict) and event.get("source") == IMAGE_STATE_SOURCE
            and event.get("detail-type") == IMAGE_STATE_DETAIL_TYPE):
        detail = event.get("detail") or {}
        state = detail.get("state") or {}
        # The image build version ARN travels in the event's top-level resources array.
        resources = event.get("resources") or []
        image_arn = (resources[0] if resources else None) or detail.get("arn")
        outcome = recipes.handle_image_state(image_arn, state.get("status"), state.get("reason"))
        logger.info({"imageBuildVersionArn": image_arn, "status": state.get("status"), **outcome})
        return outcome
    logger.warning({"message": "Unrecognised event ignored", "keys": sorted(event.keys()) if isinstance(event, dict) else type(event).__name__})
    return {"ignored": True}
