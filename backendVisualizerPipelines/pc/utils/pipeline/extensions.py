# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from enum import EnumMeta
import os
from pathlib import Path
from .objects import (
    PipelineStage,
    PipelineStatus,
)
from ..logging import log

logger = log.get_logger()

PIPELINE_STAGES = list()


class Extensions(EnumMeta):
    LAS = ".las"
    LAZ = ".laz"
    E57 = ".e57"


def success_response(stage: PipelineStage, message=None) -> PipelineStage:
    if not message:
        message = "Pipeline executed successfully."

    logger.info(message)
    stage.status = PipelineStatus.COMPLETE
    return stage


def error_response(message=None) -> dict:
    if not message:
        message = "Pipeline failed to execute. Please check the logs for more details."

    logger.error(message)
    return {"status": PipelineStatus.FAILED, "message": message}


def create_dir(parts: list) -> str:
    dir_path = os.path.join(*parts)

    if not os.path.exists(dir_path):
        Path(dir_path).mkdir(parents=True)

    return dir_path


def split_large_file(filename: str, chunk_size: int = 100000000) -> list:
    parts = []
    with open(filename, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            parts.append(chunk)
    return parts


def create_key(s3_dir: str, path: str, filename: str) -> str:
    ext = os.path.splitext(filename)[1][1:]

    return os.path.join(s3_dir, ext, filename)