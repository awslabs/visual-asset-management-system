# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A failed Blender run fails the stage.

`allconvert_blenderrenderer_pipeline` launches Blender as a subprocess and the stage's only outcome
signal used to be whether the output directory was empty. Blender can exit non-zero having already
written frames -- an unsupported model, a failed import, a mid-render abort -- and those frames then
travel on to the labelling step, which writes model-derived keywords onto the asset while the
execution reports SUCCESS.
"""

import os
from unittest.mock import MagicMock

import pytest

import main.pipelines.blenderRenderer.pipeline as pipeline
from main.utils.pipeline.objects import PipelineStage, PipelineStatus


def _stage():
    return PipelineStage(
        type="BLENDERRENDERER",
        inputFile={"bucketName": "asset-bucket", "objectKey": "xid/test/pump.glb"},
        outputFiles={"bucketName": "aux-bucket", "objectDir": "xid/test/pump.glb/genAi/"},
        outputMetadata={"bucketName": "", "objectDir": ""},
        temporaryFiles={"bucketName": "aux-bucket", "objectDir": "xid/test/pump.glb/genAi/"},
    )


def _run(monkeypatch, tmp_path, returncode, output_files=("render0.jpg", "render1.jpg")):
    monkeypatch.chdir(tmp_path)

    def _download(bucket, key, path):
        open(path, "w").write("model bytes")
        return path

    monkeypatch.setattr(pipeline.s3, "download", _download)
    monkeypatch.setattr(pipeline.s3, "uploadV2", lambda bucket, key, path: key)
    monkeypatch.setattr(pipeline.s3, "delete_all_path_contents", lambda bucket, key: None)
    monkeypatch.setattr(pipeline, "allconvert_blenderrenderer_pipeline",
                        lambda input_file_path, output_dir: {
                            "output_dir": output_dir,
                            "output_files": list(output_files),
                            "returncode": returncode,
                        })
    return pipeline.run(_stage(), "", "", False)


@pytest.mark.unit
def test_nonzero_blender_exit_fails_the_stage(monkeypatch, tmp_path):
    """Frames produced by a failed render must not be reported as a successful stage."""
    stage = _run(monkeypatch, tmp_path, returncode=1)
    assert stage.status == PipelineStatus.FAILED
    assert "exit code 1" in stage.errorMessage


@pytest.mark.unit
def test_zero_exit_with_output_completes(monkeypatch, tmp_path):
    stage = _run(monkeypatch, tmp_path, returncode=0)
    assert stage.status == PipelineStatus.COMPLETE


@pytest.mark.unit
def test_zero_exit_without_output_still_fails(monkeypatch, tmp_path):
    stage = _run(monkeypatch, tmp_path, returncode=0, output_files=())
    assert stage.status == PipelineStatus.FAILED
    assert "No output files generated." in stage.errorMessage


@pytest.mark.unit
def test_allconvert_reports_the_subprocess_exit_code(monkeypatch, tmp_path):
    """The exit code the stage checks comes from the real subprocess call, not only from a stub."""
    output_dir = str(tmp_path / "output")
    os.makedirs(output_dir)
    open(os.path.join(output_dir, "render0.jpg"), "w").write("jpg")

    run_mock = MagicMock(return_value=MagicMock(returncode=3))
    monkeypatch.setattr(pipeline.subprocess, "run", run_mock)

    response = pipeline.allconvert_blenderrenderer_pipeline("/tmp/input/pump.glb", output_dir)

    assert response["returncode"] == 3
    assert response["output_files"] == ["render0.jpg"]
    command = run_mock.call_args.args[0]
    assert command[0] == "blender"
    assert command[-2:] == ["/tmp/input/pump.glb", output_dir]
    assert "main/blenderAppScripts/renderScene.py" in command


@pytest.mark.unit
def test_nonzero_exit_fails_the_local_test_stage(monkeypatch, tmp_path):
    """The local-test path reports the same failure rather than an unconditional success."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(pipeline.os, "walk", lambda path: [])
    monkeypatch.setattr(pipeline, "allconvert_blenderrenderer_pipeline",
                        lambda input_file_path, output_dir: {
                            "output_dir": output_dir,
                            "output_files": ["render0.jpg"],
                            "returncode": 1,
                        })
    stage = pipeline.run(_stage(), "", "", True)
    assert stage.status == PipelineStatus.FAILED
    assert "exit code 1" in stage.errorMessage
