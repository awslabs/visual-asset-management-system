# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-run timing and host facts for comparing models, paths and settings.

Comparing models across segmentation paths and quality settings needs each run to report what it
actually did and what it ran on, otherwise the comparison rests on wall-clock from the workflow
history alone -- which folds instance provisioning, image pull and weight download into the model's
score and makes a cold first run look like a slow model.

The report records durations and the hardware, not money: instance pricing is regional and changes,
so the aggregating harness derives cost from the instance type and the billed duration recorded here.
A fact that cannot be collected is recorded with the reason rather than omitted, so a missing GPU
name in an aggregate reads as a collection failure instead of a hardware difference.
"""

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Instance identity comes from the instance metadata service, which requires a token on IMDSv2.
IMDS_ROOT = "http://169.254.169.254/latest"
IMDS_TIMEOUT_SECONDS = 2

# Batch publishes these into every job container; they tie a report back to a specific attempt.
BATCH_ENV_KEYS = ("AWS_BATCH_JOB_ID", "AWS_BATCH_JOB_ATTEMPT", "AWS_BATCH_CE_NAME",
                  "AWS_BATCH_JQ_NAME")


class StageTimer:
    """Wall-clock per named stage, in the order the stages ran."""

    def __init__(self):
        self._durations = {}
        self._order = []

    @contextmanager
    def stage(self, name: str):
        """Time one stage. A stage that raises is still recorded, so a failure keeps its duration."""
        started = time.time()
        try:
            yield
        finally:
            elapsed = time.time() - started
            if name not in self._durations:
                self._order.append(name)
                self._durations[name] = 0.0
            self._durations[name] += elapsed
            logger.info(f"Stage '{name}' took {elapsed:.1f}s")

    def record(self, name: str, seconds: float) -> None:
        """Record a stage timed elsewhere."""
        if name not in self._durations:
            self._order.append(name)
            self._durations[name] = 0.0
        self._durations[name] += float(seconds)

    def as_dict(self) -> dict:
        return {name: round(self._durations[name], 2) for name in self._order}


def _imds(path: str):
    """One instance metadata lookup, returning ``None`` when the service is unreachable."""
    try:
        token_request = urllib.request.Request(
            f"{IMDS_ROOT}/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
        token = urllib.request.urlopen(  # nosemgrep: dynamic-urllib-use-detected
            token_request, timeout=IMDS_TIMEOUT_SECONDS).read().decode()
        request = urllib.request.Request(
            f"{IMDS_ROOT}/meta-data/{path}", headers={"X-aws-ec2-metadata-token": token})
        return urllib.request.urlopen(  # nosemgrep: dynamic-urllib-use-detected
            request, timeout=IMDS_TIMEOUT_SECONDS).read().decode()
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _gpu_name():
    """The GPU the run landed on, which varies across the instance types the queue can schedule."""
    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15, check=True)
        return result.stdout.strip().splitlines()[0].strip() or None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def collect_host_facts() -> dict:
    """The hardware and Batch identity a duration has to be read against to mean anything."""
    facts = {}
    instance_type = _imds("instance-type")
    if instance_type:
        facts["instanceType"] = instance_type
    else:
        facts["instanceTypeUnavailable"] = (
            "the instance metadata service did not answer; a cost derived from this run has no "
            "instance type to price against")

    gpu = _gpu_name()
    if gpu:
        facts["gpuName"] = gpu
    else:
        facts["gpuNameUnavailable"] = "nvidia-smi did not report a GPU"

    for key in BATCH_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            facts[key[len("AWS_BATCH_"):].lower()] = value
    return facts


def output_sizes(paths) -> dict:
    """Byte size per produced file, which is the crude proxy for mesh and texture complexity."""
    sizes = {}
    for path in paths or ():
        try:
            sizes[os.path.basename(str(path))] = os.path.getsize(str(path))
        except OSError:
            continue
    return sizes


def build_report(timer: StageTimer, model: str, settings: dict, image_info: dict,
                 output_paths=None, extra=None) -> dict:
    """Assemble the benchmark block recorded alongside the generation and gate report."""
    report = {
        "model": model,
        "stageSeconds": timer.as_dict(),
        "totalStageSeconds": round(sum(timer.as_dict().values()), 2),
        "host": collect_host_facts(),
        "sourceImage": image_info or {},
        "settings": {key: settings.get(key, "") for key in sorted(settings or {})},
        "outputBytes": output_sizes(output_paths),
    }
    if extra:
        report.update(extra)
    return report


def is_enabled(value) -> bool:
    """Whether the template asked for a benchmark report on this run."""
    return str(value or "").strip().lower() == "true"


def as_metadata_value(report: dict) -> str:
    """The report as a single metadata value, so it travels with the asset."""
    return json.dumps(report, separators=(",", ":"), sort_keys=True)
