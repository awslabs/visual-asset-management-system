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
    POTREE = "POTREE"
    PDAL = "PDAL"


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
    input: StageInput
    output: StageOutput
    status: PipelineStatus = PipelineStatus.PENDING
    id: str = ""

    def __post_init__(self):
        self.id = str(uuid.uuid4())


@dataclass 
class PipelineDefinition(JsonEncodable):
    jobName: str
    #externalSfnTaskToken: str
    stages: list[PipelineStage ]
    completedStages: list[PipelineStage] = None
    currentStage: PipelineStage = None


@dataclass
class PipelineExecutionParams(JsonEncodable):
    jobName: str
    #externalSfnTaskToken: str
    pipeline: dict
    status: PipelineStatus = PipelineStatus.PENDING