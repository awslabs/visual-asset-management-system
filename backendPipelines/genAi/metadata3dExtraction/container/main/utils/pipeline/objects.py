# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from enum import EnumMeta
from dataclasses import dataclass, asdict
import uuid


"""
Pipeline Definition Dataclasses
"""
class PipelineStatus(EnumMeta):
    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class PipelineType(EnumMeta):
    BLENDERRENDERER = "BLENDERRENDERER"

@dataclass 
class JsonEncodable:
    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class StageDefinitionBase(JsonEncodable):
    bucketName: str


@dataclass
class StageInput(StageDefinitionBase):
    objectKey: str
    fileExtension: str = ""


@dataclass
class StageOutput(StageDefinitionBase):
    objectDir: str
    fileNames: list[str] = None


@dataclass 
class StageError(JsonEncodable): 
    error: Exception
    status: PipelineStatus = PipelineStatus.FAILED


@dataclass
class PipelineStage(JsonEncodable):
    type: PipelineType 
    inputFile: StageInput
    outputFiles: StageOutput
    outputMetadata: StageOutput
    temporaryFiles: StageOutput
    status: PipelineStatus = PipelineStatus.PENDING
    errorMessage: str = None
    id: str = ""

    def __post_init__(self):
        self.id = str(uuid.uuid4())


@dataclass 
class PipelineDefinition(JsonEncodable):
    jobName: str
    stages: list[PipelineStage ]
    inputMetadata: str
    inputParameters: str
    externalSfnTaskToken: str = ""
    localTest: str = "False"
    completedStages: list[PipelineStage] = None
    currentStage: PipelineStage = None


@dataclass
class PipelineExecutionParams(JsonEncodable):
    jobName: str
    currentStageType: str
    definition: list[str] #Serialized definition list array of PipelineDefinition for CommandInput
    inputMetadata: str
    inputParameters: str
    externalSfnTaskToken: str = ""
    status: PipelineStatus = PipelineStatus.PENDING
    