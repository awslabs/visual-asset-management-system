# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

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
    Core runner for Point Cloud Data Compute Pipeline
    """
    # convert input json to data type
    definition = PipelineDefinition(**params)
    logger.info(f"Pipeline Definition: {definition}")

    # set pipeline current stage
    if definition.currentStage is None:
        current_stage = PipelineStage(**definition.stages.pop(0))
        definition.currentStage = current_stage
        logger.info(f"Pipeline Current Stage: {current_stage}")

    # import pipeline based on pipeline type
    if current_stage.type == PipelineType.PDAL:
        from .pdal import pipeline
    elif current_stage.type == PipelineType.POTREE:
        from .potree import pipeline
    else:
        logger.error(f"Pipeline Type {current_stage.type} not supported")
        sfn.send_task_failure(PipelineExecutionParams(
            definition.jobName,
            #definition.externalSfnTaskToken,
            {
                "type": "",
                "definition": [definition.to_json()]
            },
            result.status,
        ))

    # run core pipeline
    result = pipeline.run(current_stage)
    logger.info(f"Pipeline Result: {result}")

    if len(definition.stages) > 0 and definition.stages[0] != None:
        next_stage_type = definition.stages[0]["type"]
    else:
        next_stage_type = None

    # complete stage and reset current_stage
    if definition.completedStages == None:
        definition.completedStages = []

    definition.completedStages.append(current_stage)
    definition.currentStage = None

    output = PipelineExecutionParams(
        definition.jobName,
        #definition.externalSfnTaskToken,
        {
            "type": next_stage_type,
            "definition": [definition.to_json()]
        },
        result.status,
    )

    if result.status is PipelineStatus.FAILED:
        sfn.send_task_failure(output)
        return output

    sfn.send_task_success(output)
    return output
