# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
    # The S3 version the run reads the input at; empty reads the current object.
    versionId: str = ""


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
    inputMetadataS3Location: str = ""
    inputConfigurationS3Location: str = ""
    externalSfnTaskToken: str = ""
    localTest: str = "False"
    completedStages: list[PipelineStage] = None
    currentStage: PipelineStage = None
    # Bucket name -> Region for the asset buckets this job reads and writes (constructPipeline builds it
    # from the manifest); buckets absent from it are in the deployment Region.
    bucketRegions: dict = None


@dataclass
class PipelineExecutionParams(JsonEncodable):
    jobName: str
    currentStageType: str
    definition: list[str] #Serialized definition list array of PipelineDefinition for CommandInput
    inputMetadataS3Location: str = ""
    inputConfigurationS3Location: str = ""
    externalSfnTaskToken: str = ""
    status: PipelineStatus = PipelineStatus.PENDING
    

# The Batch command argument after the definition that names the input's launch version (#994):
# {"inputVersions": {"<stage type>": "<S3 version id>"}}. constructPipeline keeps the version out of the
# definition itself, because an image built before the field parses the definition into these dataclasses
# with no room for an unknown key, while it reads only the command's first argument.
INPUT_VERSIONS_KEY = "inputVersions"


def apply_input_versions(params: dict, extra_args) -> dict:
    """Put the launch version named in the command's later arguments on the ``inputFile`` of each stage of
    that type, so ``StageInput.versionId`` carries it. A stage whose ``inputFile`` already names a version
    keeps it. With no later argument ``params`` is returned unchanged and the stage reads the current
    object, as before. An argument that is not a version document raises: reading the current object
    instead would render a file other than the one the run was launched on."""
    versions = {}
    for arg in extra_args or []:
        try:
            document = json.loads(arg)
        except (TypeError, ValueError):
            document = None
        named = document.get(INPUT_VERSIONS_KEY) if isinstance(document, dict) else None
        if not isinstance(named, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in named.items()):
            raise ValueError("The Batch command argument after the pipeline definition is not an "
                             f"{{\"{INPUT_VERSIONS_KEY}\": {{<stage type>: <version id>}}}} document")
        versions.update(named)
    if not versions:
        return params
    stages = list(params.get("stages") or [])
    if isinstance(params.get("currentStage"), dict):
        stages.append(params["currentStage"])
    for stage in stages:
        input_file = stage.get("inputFile") if isinstance(stage, dict) else None
        version_id = versions.get(stage.get("type")) if isinstance(input_file, dict) else None
        if version_id and not input_file.get("versionId"):
            input_file["versionId"] = version_id
    return params
