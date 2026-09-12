# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 14: the run-bucket writes process-output ingests — files flat under sop-bom/, one metadata file, one results file."""

import logging
import os
from dataclasses import dataclass, field

from botocore.exceptions import BotoCoreError, ClientError

from . import OUTPUT_FOLDER
from .errors import PIPELINE_ERROR, PipelineRejection

logger = logging.getLogger("video_sop_bom_pipeline.upload")

METADATA_FILE = "asset.metadata.json"
SUMMARY_FILE = "summary.json"


@dataclass
class UploadReport:
    uploaded: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    file_count: int = 0
    summary_key: str = ""


def output_dirs(work_dir):
    dirs = {name: os.path.join(work_dir, "out", name) for name in ("files", "metadata", "results")}
    for path in dirs.values():
        os.makedirs(path, exist_ok=True)
    return dirs


def upload_outputs(clients, definition, work_dir):
    """Every file in out/files -> outputs.files + "sop-bom/" + name (flat); out/metadata/asset.metadata.json
    -> outputs.metadata; out/results/summary.json -> outputs.results. Failures are collected, every upload
    is attempted, and one readable rejection names the count."""
    outputs = definition["outputs"]
    bucket = outputs["bucket"]
    dirs = output_dirs(work_dir)
    plan = []
    for name in sorted(os.listdir(dirs["files"])):
        path = os.path.join(dirs["files"], name)
        if os.path.isdir(path):
            raise PipelineRejection(PIPELINE_ERROR, f"output folder {name}/ is not allowed: deliverables are flat under {OUTPUT_FOLDER}")
        plan.append((path, outputs["files"] + OUTPUT_FOLDER + name, True))
    metadata_path = os.path.join(dirs["metadata"], METADATA_FILE)
    if os.path.isfile(metadata_path):
        plan.append((metadata_path, outputs["metadata"] + METADATA_FILE, False))
    summary_path = os.path.join(dirs["results"], SUMMARY_FILE)
    if os.path.isfile(summary_path):
        plan.append((summary_path, outputs["results"] + SUMMARY_FILE, False))

    report = UploadReport(summary_key=outputs["results"] + SUMMARY_FILE)
    for path, key, is_deliverable in plan:
        try:
            clients.s3.upload_file(path, bucket, key)
        except (ClientError, BotoCoreError, OSError) as exc:
            logger.error("upload failed file=%s error=%s", os.path.basename(path), type(exc).__name__)
            report.failed.append(os.path.basename(path))
            continue
        report.uploaded.append((bucket, key))
        if is_deliverable:
            report.file_count += 1
    logger.info("upload deliverables=%d objects=%d failed=%d", report.file_count, len(report.uploaded), len(report.failed))
    if report.failed:
        raise PipelineRejection(PIPELINE_ERROR, f"{len(report.failed)} of {len(plan)} output uploads failed; first: {report.failed[0]}")
    return report
