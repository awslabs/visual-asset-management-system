# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The transform refuses a run the ephemeral volume cannot hold, before the reprojection is paid for.

Run from the container directory:  python -m pytest tests/test_disk_budget_preflight.py -q

Transformed points are spilled to the task's volume, so the volume holds the downloaded input, one spill
copy of the point payload, and every requested output format at once. Without a pre-flight estimate the
volume fills part-way through the transform pass -- on exactly the large inputs the spill exists for --
and the run has already spent its reprojection time. The `OSError` errno 28 the spill write would raise is
reported (`_run_transform_stage` catches it and sends `SendTaskFailure`), but as a bare "No space left on
device" with no figures an operator could size the volume from.

Asserted here rather than live. A live arm would need an input large enough to fill the CONFIGURED 120 GiB
allocation, which makes it the most expensive input in the plan rather than the cheapest; monkeypatching
`shutil.disk_usage` exercises the same contract for nothing.

Three properties, and the last is the one an over-eager check would break:

* a run whose estimate exceeds the free space is REFUSED, with a message naming disk and the figures;
* it is refused BEFORE `run_pipeline` is called, so the reprojection is not paid for;
* a run that fits is not refused, and neither is one whose staged input cannot be sized -- a pre-flight
  estimate must never be the thing that fails a run that would otherwise work.
"""

import os
import shutil
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import PIPELINE_STUB  # noqa: E402
from coord_transform_pipeline import core  # noqa: E402
from coord_transform_pipeline.utils.pipeline.objects import PipelineStatus  # noqa: E402

PARAMS = '{"sourceCrs":"EPSG:4326","targetCrs":"EPSG:27700","outputFormats":["laz","las","e57","ply"]}'

GIB = 2**30


def _stage():
    from coord_transform_pipeline.utils.pipeline.objects import PipelineStage

    return PipelineStage(
        type="COORD_TRANSFORM",
        inputFile={
            "bucketName": "bucket",
            "objectKey": "asset/cloud.laz",
            "fileExtension": ".laz",
        },
        outputFiles={"bucketName": "bucket", "objectDir": "asset/"},
        outputMetadata={"bucketName": "bucket", "objectDir": "asset/meta/"},
    )


class _Report:
    errors: list = []
    output_files = ["cloud.laz"]
    total_points_processed = 1
    residual_error_mm = 0.0


STAGED_INPUT_BYTES = 1 * GIB


@pytest.fixture
def staged_input(monkeypatch):
    """Make the download produce a 1 GiB staged input, and record whether the transform ran.

    The size matters: the estimate is a multiple of the STAGED input's size, which is the only figure
    available before the file is opened. The file is created sparse -- truncated to length rather than
    written -- so it costs no disk while `os.path.getsize` still reports a gigabyte.

    Returns the list `run_pipeline` appends to, so a test reads "did the transform run" from it.
    """
    ran = []

    def _download(bucket, key, dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.truncate(STAGED_INPUT_BYTES)
        return dest

    def _run_pipeline(config, inputs):
        ran.append(inputs)
        return _Report()

    monkeypatch.setattr(core.s3, "download", _download)
    monkeypatch.setattr(core, "_upload_outputs", lambda *a, **k: None)
    monkeypatch.setattr(core, "_upload_metadata", lambda *a, **k: None)
    monkeypatch.setattr(core, "_validate_transform_outputs", lambda *a, **k: None)
    monkeypatch.setattr(PIPELINE_STUB, "run_pipeline", _run_pipeline)
    return ran


def _with_free_space(monkeypatch, free_bytes):
    """Report `free_bytes` free wherever the check looks, without needing a volume of that size."""
    usage = shutil._ntuple_diskusage(free_bytes * 2, free_bytes, free_bytes)
    monkeypatch.setattr(core.shutil, "disk_usage", lambda path: usage)


def test_a_run_that_does_not_fit_is_refused_with_a_disk_message(
    monkeypatch, staged_input
):
    # 1 GiB input with four output formats: 1 (input) + 4 (spill) + 4x3 (outputs) = 17 GiB needed.
    _with_free_space(monkeypatch, 2 * GIB)

    stage = core._run_transform_stage(_stage(), "", PARAMS, local_test=False)

    assert stage.status is PipelineStatus.FAILED
    message = stage.errorMessage or ""
    # The figures are the point: a bare "no space" leaves an operator nothing to size the volume from.
    assert "disk" in message.lower(), message
    assert "GiB" in message, message
    assert "ephemeralStorageGiB" in message, (
        "the message must name the knob that fixes it, or an operator cannot act on it"
    )


def test_the_refusal_happens_before_the_reprojection_is_paid_for(
    monkeypatch, staged_input
):
    """A refusal after the transform pass would cost the whole reprojection to learn nothing new."""
    _with_free_space(monkeypatch, 2 * GIB)

    core._run_transform_stage(_stage(), "", PARAMS, local_test=False)

    assert staged_input == [], "run_pipeline must not be reached once the budget is refused"


def test_a_run_that_fits_is_not_refused(monkeypatch, staged_input):
    """The positive control. Without it, a check that refused everything would pass the two above."""
    _with_free_space(monkeypatch, 200 * GIB)

    stage = core._run_transform_stage(_stage(), "", PARAMS, local_test=False)

    assert stage.status is PipelineStatus.COMPLETE, stage.errorMessage
    assert staged_input != [], "the transform must run when the budget allows it"


def test_fewer_output_formats_need_less_room(monkeypatch, staged_input):
    """The estimate scales with the number of requested formats, which is what makes it actionable.

    An operator told "request fewer output formats" has to be able to act on it: an estimate that
    ignored the format count would refuse a one-format run for a four-format reason.
    """
    _with_free_space(monkeypatch, 8 * GIB)
    one_format = '{"sourceCrs":"EPSG:4326","targetCrs":"EPSG:27700","outputFormats":["laz"]}'

    # 1 + 4 + 1x3 = 8 GiB needed, which 8 GiB free satisfies.
    stage = core._run_transform_stage(_stage(), "", one_format, local_test=False)
    assert stage.status is PipelineStatus.COMPLETE, stage.errorMessage

    # The same free space with four formats needs 17 GiB and is refused.
    staged_input.clear()
    stage_four = core._run_transform_stage(_stage(), "", PARAMS, local_test=False)
    assert stage_four.status is PipelineStatus.FAILED
    assert "output format" in (stage_four.errorMessage or "")


def test_an_unsizeable_input_skips_the_check_rather_than_failing_the_run(monkeypatch):
    """The estimate is an early warning, not a gate: it must not invent a failure.

    When the staged input cannot be sized the download did not produce a file, and the download's own
    error is the one worth reporting. Skipping loses the early warning and nothing else -- the spill
    write still raises errno 28 if the volume fills, and `_run_transform_stage` reports that.
    """
    ran = []
    monkeypatch.setattr(core.s3, "download", lambda bucket, key, dest: dest)
    monkeypatch.setattr(core, "_upload_outputs", lambda *a, **k: None)
    monkeypatch.setattr(core, "_upload_metadata", lambda *a, **k: None)
    monkeypatch.setattr(core, "_validate_transform_outputs", lambda *a, **k: None)
    monkeypatch.setattr(
        PIPELINE_STUB, "run_pipeline", lambda config, inputs: ran.append(inputs) or _Report()
    )

    stage = core._run_transform_stage(_stage(), "", PARAMS, local_test=False)

    assert stage.status is PipelineStatus.COMPLETE, stage.errorMessage
    assert ran != [], "an unsizeable input must not stop the transform from being attempted"
