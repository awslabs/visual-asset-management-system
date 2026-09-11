# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import uuid
from dataclasses import asdict, dataclass
from enum import EnumMeta


class PipelineStatus(EnumMeta):
    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class PipelineType(EnumMeta):
    COORD_TRANSFORM = "COORD_TRANSFORM"


@dataclass
class JsonEncodable:
    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class StageInput(JsonEncodable):
    bucketName: str
    objectKey: str
    fileExtension: str = ""


@dataclass
class StageOutput(JsonEncodable):
    bucketName: str
    objectDir: str
    fileNames: list[str] = None


@dataclass
class PipelineStage(JsonEncodable):
    type: PipelineType
    inputFile: StageInput
    outputFiles: StageOutput
    outputMetadata: StageOutput
    transformConfig: str = ""
    status: PipelineStatus = PipelineStatus.PENDING
    errorMessage: str = None
    id: str = ""

    def __post_init__(self):
        self.id = str(uuid.uuid4())


@dataclass
class PipelineDefinition(JsonEncodable):
    jobName: str
    stages: list[PipelineStage]
    inputMetadata: str
    inputParameters: str
    externalSfnTaskToken: str = ""
    localTest: str = "False"
    completedStages: list[PipelineStage] = None
    currentStage: PipelineStage = None
    assetId: str = ""
    databaseId: str = ""


@dataclass
class PipelineExecutionParams(JsonEncodable):
    jobName: str
    currentStageType: str
    definition: list[str]
    inputMetadata: str
    inputParameters: str
    externalSfnTaskToken: str = ""
    status: PipelineStatus = PipelineStatus.PENDING
