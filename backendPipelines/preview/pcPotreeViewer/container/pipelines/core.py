# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from ..utils.pipeline.objects import (
    PipelineDefinition,
    PipelineExecutionParams,
    PipelineStage,
    PipelineStatus,
    PipelineType,
)
from ..utils.aws import sfn
from ..utils.logging import log
from ..utils.pipeline import extensions as ext


logger = log.get_logger()


def hello():
    logger.info(
        "Point Cloud Data Compute Pipeline - For Web Point Cloud Visualizer")


def run(params: dict) -> PipelineExecutionParams:
    """
    Core runner for Data Compute Pipeline
    """
    # convert input to data type
    definition = PipelineDefinition(**params)
    logger.info(f"Pipeline Definition: {definition}")

    # set pipeline current stage
    if definition.currentStage is None:
        current_stage = PipelineStage(**definition.stages.pop(0))
        definition.currentStage = current_stage
        logger.info(f"Pipeline Current Stage: {current_stage}")
    else:
        # A resumed definition already carries its stage. It arrives deserialized from JSON, where
        # the stage is a plain dict, so it is coerced back; an in-process definition already holds
        # the object. Without this branch current_stage is unbound and the reference below raises
        # UnboundLocalError -- and nothing above run() catches it, so the container ends without
        # reporting against the workflow's task token and the step waits out its full taskTimeout.
        current_stage = (
            PipelineStage(**definition.currentStage)
            if isinstance(definition.currentStage, dict)
            else definition.currentStage
        )

    # import pipeline based on pipeline type
    if current_stage.type == PipelineType.PDAL:
        from .pdal import pipeline
    elif current_stage.type == PipelineType.POTREE:
        from .potree import pipeline
    else:
        logger.error(f"Pipeline Type {current_stage.type} not supported")
        output = PipelineExecutionParams(
            definition.jobName,
            current_stage.type,
            [definition.to_json()],
            definition.inputMetadataS3Location,
            definition.inputConfigurationS3Location,
            definition.externalSfnTaskToken,
            PipelineStatus.FAILED,
        )

        #Send SFN response on non localTest
        if definition.localTest == 'False':
            sfn.send_task_failure(f"Pipeline Type {current_stage.type} not supported")
        return output

    # run core pipeline
    resultStageCompleted = pipeline.run(current_stage, definition.inputMetadataS3Location, definition.inputConfigurationS3Location, definition.localTest == 'True')
    logger.info(f"Pipeline Result: {resultStageCompleted}")

    if len(definition.stages) > 0 and definition.stages[0] != None:
        next_stage_type = definition.stages[0]["type"]
    else:
        next_stage_type = None

    # complete stage and reset current stage
    if definition.completedStages == None:
        definition.completedStages = []

    definition.completedStages.append(resultStageCompleted)
    definition.currentStage = None

    output = PipelineExecutionParams(
        definition.jobName,
        next_stage_type,
        [definition.to_json()],
        definition.inputMetadataS3Location,
        definition.inputConfigurationS3Location,
        definition.externalSfnTaskToken,
        resultStageCompleted.status,
    )

    #Send external sfn heartbeat (will fail silently on any problems)
    sfn.send_external_task_heartbeat(definition.externalSfnTaskToken)

    #Send SFN response on non localTest
    if definition.localTest == 'False':
        if resultStageCompleted.status is PipelineStatus.FAILED:
            sfn.send_task_failure(resultStageCompleted.errorMessage)
        else:
            sfn.send_task_success(output)

    return output
