# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A CRS-mismatch rejection must report the task token, not leave the workflow task waiting.

Run from this directory:  python -m pytest tests/test_mismatch_failure_reporting.py -q

This is the second half of the S33-CDK-015 fix, and it exists because the first half would have made
things worse on its own. Setting the built-in template's `onMismatch` to `error` makes coord_xform reject
a declared source CRS the file contradicts — but it rejects it by raising `SystemExit`
(coord_xform/pipeline.py:152), which derives from BaseException, NOT from Exception. The container's
`except Exception` therefore could not catch it.

The consequence is specific to this pipeline's two-token design. The container reports the INTERNAL token
(`TASK_TOKEN`, consumed by the sub-state-machine's WAIT_FOR_TASK_TOKEN task); `pipelineEnd` reports the
EXTERNAL VAMS workflow token. An uncaught SystemExit skips `run()`'s reporting block, so:

    container exits 1  ->  Batch job FAILED  ->  but WAIT_FOR_TASK_TOKEN does not observe a job exit
                       ->  the task waits its full 4-hour taskTimeout
                       ->  States.ALL catch -> pipelineEnd -> external SendTaskFailure

The final verdict is right and arrives four hours late. Caught instead, the same rejection is a FAILED
stage and an immediate SendTaskFailure. So the assertion below is about WHICH callback fires, not merely
about the stage status: a status-only test would pass against a build that still hangs.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import PIPELINE_STUB  # noqa: E402
from coord_transform_pipeline import core  # noqa: E402
from coord_transform_pipeline.utils.pipeline.objects import PipelineStatus  # noqa: E402


def _stage():
    """A COORD_TRANSFORM stage carrying the minimum the handler reads before it calls run_pipeline."""
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


PARAMS = '{"sourceCrs":"EPSG:4326","targetCrs":"EPSG:27700","onMismatch":"error"}'


@pytest.fixture
def stub_download(monkeypatch):
    """Make the S3 download succeed without S3, so the test reaches run_pipeline."""
    monkeypatch.setattr(core.s3, "download", lambda bucket, key, dest: dest)


def _raise_system_exit(*_args, **_kwargs):
    """What coord_xform does when on_mismatch is ERROR and the file's CRS disagrees."""
    raise SystemExit("CRS validation failed:\n  cloud.laz: detected EPSG:32613, configured EPSG:4326")


def test_a_crs_mismatch_rejection_marks_the_stage_failed(monkeypatch, stub_download):
    """The stage must be FAILED, and the operator-visible message must carry the reason."""
    monkeypatch.setattr(PIPELINE_STUB, "run_pipeline", _raise_system_exit)

    stage = core._run_transform_stage(_stage(), "", PARAMS, local_test=False)

    assert stage.status is PipelineStatus.FAILED
    assert "CRS validation failed" in stage.errorMessage
    assert "EPSG:32613" in stage.errorMessage, "the detected CRS is what tells the operator what to fix"


def test_the_rejection_does_not_escape_the_handler(monkeypatch, stub_download):
    """The regression guard proper.

    Before the fix this call raised SystemExit out of `_run_transform_stage`, past `run()`'s reporting
    block. Pinning that it RETURNS is what distinguishes an immediate failure from a 4-hour wait.
    """
    monkeypatch.setattr(PIPELINE_STUB, "run_pipeline", _raise_system_exit)

    stage = core._run_transform_stage(_stage(), "", PARAMS, local_test=False)  # must not raise

    assert stage is not None


def test_send_task_failure_is_what_fires(monkeypatch):
    """The load-bearing assertion: run() must call SendTaskFailure, not SendTaskSuccess and not neither.

    A test that checked only the stage status would pass against a build where the exception escaped
    `run()` — the status is set on an object nobody goes on to report.
    """
    calls = []
    monkeypatch.setattr(core.s3, "download", lambda bucket, key, dest: dest)
    monkeypatch.setattr(PIPELINE_STUB, "run_pipeline", _raise_system_exit)
    monkeypatch.setattr(core.sfn, "send_task_failure", lambda msg="": calls.append(("failure", msg)))
    monkeypatch.setattr(core.sfn, "send_task_success", lambda out: calls.append(("success", out)))
    monkeypatch.setattr(core.sfn, "send_task_heartbeat", lambda token: None)

    core.run({
        "jobName": "MismatchRun",
        "stages": [{
            "type": "COORD_TRANSFORM",
            "inputFile": {
                "bucketName": "bucket",
                "objectKey": "asset/cloud.laz",
                "fileExtension": ".laz",
            },
            "outputFiles": {"bucketName": "bucket", "objectDir": "asset/"},
            "outputMetadata": {"bucketName": "bucket", "objectDir": "asset/meta/"},
        }],
        "inputMetadata": "",
        "inputParameters": PARAMS,
        "externalSfnTaskToken": "token-abc",
        "localTest": "False",
    })

    kinds = [kind for kind, _ in calls]
    assert kinds == ["failure"], f"expected exactly one SendTaskFailure, got {kinds}"
    assert "CRS validation failed" in calls[0][1]


def test_an_output_validation_failure_also_reports_failure(monkeypatch, tmp_path):
    """The other new failure route (corrupt written output) must report the same way.

    Both halves of the fix converge on one path — a RuntimeError from `_validate_transform_outputs` —
    so pinning it here keeps the two from diverging.
    """
    calls = []
    monkeypatch.setattr(core.s3, "download", lambda bucket, key, dest: dest)
    monkeypatch.setattr(core.sfn, "send_task_failure", lambda msg="": calls.append(("failure", msg)))
    monkeypatch.setattr(core.sfn, "send_task_success", lambda out: calls.append(("success", out)))
    monkeypatch.setattr(core.sfn, "send_task_heartbeat", lambda token: None)
    monkeypatch.setattr(
        core,
        "_validate_transform_outputs",
        lambda report, output_dir: (_ for _ in ()).throw(
            RuntimeError("Transform wrote output that failed validation: out.laz: bounding box is not finite")
        ),
    )

    class _Report:
        errors = []
        total_points_processed = 0
        output_files = []

    monkeypatch.setattr(PIPELINE_STUB, "run_pipeline", lambda cfg, inputs: _Report())

    core.run({
        "jobName": "CorruptRun",
        "stages": [{
            "type": "COORD_TRANSFORM",
            "inputFile": {
                "bucketName": "bucket",
                "objectKey": "asset/cloud.laz",
                "fileExtension": ".laz",
            },
            "outputFiles": {"bucketName": "bucket", "objectDir": "asset/"},
            "outputMetadata": {"bucketName": "bucket", "objectDir": "asset/meta/"},
        }],
        "inputMetadata": "",
        "inputParameters": '{"sourceCrs":"EPSG:4326","targetCrs":"EPSG:27700"}',
        "externalSfnTaskToken": "token-abc",
        "localTest": "False",
    })

    assert [kind for kind, _ in calls] == ["failure"]
    assert "not finite" in calls[0][1]


def test_a_reported_error_fails_the_stage_rather_than_completing_it(monkeypatch):
    """coord_xform can also REPORT a per-file error instead of raising.

    That route used to log a warning and still mark the stage COMPLETE, which is the other way a run
    that transformed nothing was recorded as a successful conversion.
    """
    calls = []
    monkeypatch.setattr(core.s3, "download", lambda bucket, key, dest: dest)
    monkeypatch.setattr(core.sfn, "send_task_failure", lambda msg="": calls.append(("failure", msg)))
    monkeypatch.setattr(core.sfn, "send_task_success", lambda out: calls.append(("success", out)))
    monkeypatch.setattr(core.sfn, "send_task_heartbeat", lambda token: None)

    class _Report:
        errors = ["cloud.laz: ValueError: no readable points"]
        total_points_processed = 0
        output_files = []

    monkeypatch.setattr(PIPELINE_STUB, "run_pipeline", lambda cfg, inputs: _Report())

    core.run({
        "jobName": "ReportedErrorRun",
        "stages": [{
            "type": "COORD_TRANSFORM",
            "inputFile": {
                "bucketName": "bucket",
                "objectKey": "asset/cloud.laz",
                "fileExtension": ".laz",
            },
            "outputFiles": {"bucketName": "bucket", "objectDir": "asset/"},
            "outputMetadata": {"bucketName": "bucket", "objectDir": "asset/meta/"},
        }],
        "inputMetadata": "",
        "inputParameters": '{"sourceCrs":"EPSG:4326","targetCrs":"EPSG:27700"}',
        "externalSfnTaskToken": "token-abc",
        "localTest": "False",
    })

    assert [kind for kind, _ in calls] == ["failure"]
    assert "no readable points" in calls[0][1]


def test_a_clean_run_still_reports_success(monkeypatch, tmp_path):
    """The positive control.

    Without it, every assertion above is satisfied by a container that fails unconditionally — which
    would be a worse regression than the one being fixed.
    """
    calls = []
    monkeypatch.setattr(core.s3, "download", lambda bucket, key, dest: dest)
    monkeypatch.setattr(core.s3, "upload", lambda bucket, key, path: None)
    monkeypatch.setattr(core.sfn, "send_task_failure", lambda msg="": calls.append(("failure", msg)))
    monkeypatch.setattr(core.sfn, "send_task_success", lambda out: calls.append(("success", out)))
    monkeypatch.setattr(core.sfn, "send_task_heartbeat", lambda token: None)
    monkeypatch.setattr(core, "_validate_transform_outputs", lambda report, output_dir: None)

    class _Report:
        errors = []
        total_points_processed = 4_004_326
        output_files = ["cloud_EPSG_27700.laz"]

    monkeypatch.setattr(PIPELINE_STUB, "run_pipeline", lambda cfg, inputs: _Report())

    core.run({
        "jobName": "CleanRun",
        "stages": [{
            "type": "COORD_TRANSFORM",
            "inputFile": {
                "bucketName": "bucket",
                "objectKey": "asset/cloud.laz",
                "fileExtension": ".laz",
            },
            "outputFiles": {"bucketName": "bucket", "objectDir": "asset/"},
            "outputMetadata": {"bucketName": "bucket", "objectDir": "asset/meta/"},
        }],
        "inputMetadata": "",
        "inputParameters": '{"sourceCrs":"EPSG:4326","targetCrs":"EPSG:27700"}',
        "externalSfnTaskToken": "token-abc",
        "localTest": "False",
    })

    assert [kind for kind, _ in calls] == ["success"], f"a clean run must succeed, got {calls}"


def test_pipeline_contract_is_unstubbed():
    """Guard the conftest stub against the contract it stands in for changing underneath it.

    `coord_xform.pipeline` cannot be imported here — it needs pyproj and open3d, and this container
    declares pydantic>=2.0 while the interpreter runs the backend's pydantic 1.10.13. So the module is
    stubbed, and every assertion above would stay green if the real module renamed `run_pipeline` or
    stopped raising for a mismatch. These read it as text instead.
    """
    source = _read_source("coord_xform/pipeline.py")

    assert "def run_pipeline(" in source, (
        "core.py imports run_pipeline by name; a rename breaks the container while the stub keeps "
        "these tests green"
    )
    assert "raise SystemExit(" in source, (
        "the SystemExit is the whole reason core.py names it in the except clause — if this raise "
        "became a plain Exception the except clause could be narrowed again"
    )
    assert source.count("raise SystemExit(") == 1, (
        "a second SystemExit path would need its own consideration: SystemExit is caught broadly in "
        "core.py, so a new one raised for an unrelated reason would be silently converted to a "
        "FAILED stage"
    )

    # _handle_validation runs before the per-file loop, outside any try, which is why the SystemExit
    # reaches core.py at all rather than being folded into report.errors by the loop's own handler.
    validation_call = source.index("_handle_validation(config, validation_results)")
    loop_start = source.index("for input_path in inputs:")
    assert validation_call < loop_start, (
        "if the validation call moved inside the per-file loop its SystemExit would bypass the loop's "
        "`except Exception` anyway, but the ordering is what the failure path documented in core.py "
        "depends on"
    )


def test_the_library_default_for_on_mismatch_is_error():
    """Pin that the permissive default is the CONTAINER's, not the library's.

    `ValidationConfig.on_mismatch` defaults to `OnMismatch.ERROR` in coord_xform. `core.py` passes
    `OnMismatch(transform_params.get("onMismatch", "warn"))`, so it overrides that safe default with the
    permissive one whenever a template or asset metadata does not say otherwise. That inversion is the
    root of the silent-corruption finding, and it is worth failing a test if the library default ever
    moves — because then the container's fallback would be the only thing left setting the behaviour.

    config.py imports cleanly under pydantic v1, so this reads the real default rather than the text.
    """
    from coord_xform.config import OnMismatch, ValidationConfig

    assert ValidationConfig().on_mismatch is OnMismatch.ERROR

    core_source = _read_source("coord_transform_pipeline/core.py")
    assert '"onMismatch", "warn"' in core_source, (
        "if the container's fallback is changed to 'error' this test's premise changes: update the "
        "comment above and the S33-CDK-015 record rather than deleting the assertion"
    )


def _read_source(relative_path):
    """Read a file from the container root as text."""
    import io

    return io.open(
        os.path.join(os.path.dirname(_HERE), *relative_path.split("/")),
        encoding="utf-8",
    ).read()
