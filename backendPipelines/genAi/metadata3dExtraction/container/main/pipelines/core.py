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
        "Data Compute Pipeline")


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

    # import pipeline based on pipeline type
    if current_stage.type == PipelineType.BLENDERRENDERER:
        from .blenderRenderer import pipeline
    else:
        logger.error(f"Pipeline Type {current_stage.type} not supported")
        output = PipelineExecutionParams(
            definition.jobName,
            current_stage.type,
            [definition.to_json()],
            definition.inputMetadata,
            definition.inputParameters,
            definition.externalSfnTaskToken,
            PipelineStatus.FAILED,
        )

        #Send SFN response on non localTest
        if definition.localTest == 'False':
            sfn.send_task_failure(f"Pipeline Type {current_stage.type} not supported")
        return output

    # run core pipeline
    resultStageCompleted = pipeline.run(current_stage, definition.inputMetadata, definition.inputParameters, definition.localTest == 'True')
    logger.info(f"Pipeline Result: {resultStageCompleted}")

    if len(definition.stages) > 0 and definition.stages[0] != None:
        next_stage_type = definition.stages[0]["type"]
    else:
        next_stage_type = None

    # complete stage and reset current_stage
    if definition.completedStages == None:
        definition.completedStages = []

    definition.completedStages.append(resultStageCompleted)
    definition.currentStage = None

    output = PipelineExecutionParams(
        definition.jobName,
        next_stage_type,
        [definition.to_json()],
        definition.inputMetadata,
        definition.inputParameters,
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
