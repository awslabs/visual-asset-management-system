# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-file-hierarchy download stays inside the container's input directory.

With `includeAllAssetFileHierarchyFiles` set, the stage lists every sibling object under the input
file's prefix and joins each object's `relativePath` onto the local input directory. An S3 key is an
opaque byte string, so that relative path can carry a leading separator or `..` segments and resolve
anywhere on the container filesystem -- which the container writes to as root, before launching a
subprocess in the same run.

The stand-ins here never write outside `tmp_path`: they record the path they were asked for and
create it only when it is inside the temporary tree. That keeps the traversal assertions meaningful
against unconfined code without letting such a run touch the machine.
"""

import io
import os

import pytest

import main.pipelines.blenderRenderer.pipeline as pipeline
from main.utils.pipeline.objects import PipelineStage, PipelineStatus

PRIMARY_KEY = "xid/test/pump.glb"

# Captured before the stand-in replaces os.makedirs: the recorder itself has to create directories,
# and `pipeline.os` is the os module, so patching through it would otherwise recurse.
_REAL_MAKEDIRS = os.makedirs


def _stage():
    return PipelineStage(
        type="BLENDERRENDERER",
        inputFile={"bucketName": "asset-bucket", "objectKey": PRIMARY_KEY},
        outputFiles={"bucketName": "aux-bucket", "objectDir": "xid/test/pump.glb/genAi/"},
        outputMetadata={"bucketName": "", "objectDir": ""},
        temporaryFiles={"bucketName": "aux-bucket", "objectDir": "xid/test/pump.glb/genAi/"},
    )


class _Recorder:
    def __init__(self, sandbox):
        self.sandbox = os.path.realpath(sandbox)
        self.downloads = []
        self.makedirs = []
        self.uploads = []

    def _inside_sandbox(self, path):
        return os.path.realpath(path).startswith(self.sandbox + os.sep)

    def makedirs_stub(self, path, exist_ok=False):
        self.makedirs.append(path)
        if self._inside_sandbox(path):
            _REAL_MAKEDIRS(path, exist_ok=exist_ok)

    def download_stub(self, bucket, key, path):
        self.downloads.append({"bucket": bucket, "key": key, "path": path})
        if self._inside_sandbox(path):
            io.open(path, "w", encoding="utf-8").write("model bytes")
        return path

    def upload_stub(self, bucket, key, path):
        self.uploads.append({"bucket": bucket, "key": key, "path": path})
        return key


def _run(monkeypatch, tmp_path, listing, returncode=0, output_files=("render0.jpg",)):
    """Run the stage against a fixed S3 listing, returning the completed stage and the recorder."""
    monkeypatch.chdir(tmp_path)
    recorder = _Recorder(str(tmp_path))

    monkeypatch.setattr(pipeline.s3, "get_all_files_in_path", lambda bucket, path: list(listing))
    monkeypatch.setattr(pipeline.s3, "download", recorder.download_stub)
    monkeypatch.setattr(pipeline.s3, "uploadV2", recorder.upload_stub)
    monkeypatch.setattr(pipeline.s3, "delete_all_path_contents", lambda bucket, key: None)
    monkeypatch.setattr(pipeline.os, "makedirs", recorder.makedirs_stub)
    monkeypatch.setattr(pipeline, "allconvert_blenderrenderer_pipeline",
                        lambda input_file_path, output_dir: {
                            "output_dir": output_dir,
                            "output_files": list(output_files),
                            "returncode": returncode,
                        })

    stage = _run_stage(pipeline, _stage())
    return stage, recorder, os.path.realpath(os.path.join(str(tmp_path), "tmp", "input"))


def _run_stage(module, stage):
    return module.run(stage, "", '{"includeAllAssetFileHierarchyFiles": "True"}', False)


@pytest.mark.unit
def test_traversal_keys_never_write_outside_the_input_directory(monkeypatch, tmp_path):
    """A key resolving above the input directory is skipped; a leading-separator key is re-rooted."""
    listing = [
        {"key": PRIMARY_KEY, "relativePath": "pump.glb"},
        {"key": "xid/test//etc/passwd", "relativePath": "/etc/passwd"},
        {"key": "xid/test/../../../../tmp/evil.txt", "relativePath": "../../../../tmp/evil.txt"},
        {"key": "xid/test/sub/texture.png", "relativePath": "sub/texture.png"},
    ]
    stage, recorder, input_root = _run(monkeypatch, tmp_path, listing)

    for download in recorder.downloads:
        resolved = os.path.realpath(download["path"])
        assert resolved.startswith(input_root + os.sep), \
            f"{download['key']} was written to {resolved}, outside {input_root}"
    for path in recorder.makedirs:
        resolved = os.path.realpath(path)
        assert resolved.startswith(input_root + os.sep) or resolved == input_root, \
            f"created {resolved}, outside {input_root}"

    downloaded_keys = [download["key"] for download in recorder.downloads]
    assert "xid/test/../../../../tmp/evil.txt" not in downloaded_keys
    # The leading-separator key keeps its content, re-rooted under the input directory.
    passwd = [d for d in recorder.downloads if d["key"] == "xid/test//etc/passwd"]
    assert len(passwd) == 1
    assert os.path.realpath(passwd[0]["path"]) == os.path.join(input_root, "etc", "passwd")

    # The legitimate files still arrive, and the stage still completes.
    assert os.path.realpath([d for d in recorder.downloads if d["key"] == PRIMARY_KEY][0]["path"]) \
        == os.path.join(input_root, "pump.glb")
    assert os.path.realpath([d for d in recorder.downloads
                             if d["key"] == "xid/test/sub/texture.png"][0]["path"]) \
        == os.path.join(input_root, "sub", "texture.png")
    assert stage.status == PipelineStatus.COMPLETE


@pytest.mark.unit
def test_interior_parent_segments_are_still_downloaded(monkeypatch, tmp_path):
    """The check confines rather than blanket-rejecting `..`: a path that stays inside is kept."""
    listing = [
        {"key": PRIMARY_KEY, "relativePath": "pump.glb"},
        {"key": "xid/test/sub/../texture.png", "relativePath": "sub/../texture.png"},
    ]
    stage, recorder, input_root = _run(monkeypatch, tmp_path, listing)

    interior = [d for d in recorder.downloads if d["key"] == "xid/test/sub/../texture.png"]
    assert len(interior) == 1
    assert os.path.realpath(interior[0]["path"]) == os.path.join(input_root, "texture.png")
    assert stage.status == PipelineStatus.COMPLETE


@pytest.mark.unit
def test_directory_markers_are_skipped(monkeypatch, tmp_path):
    listing = [
        {"key": PRIMARY_KEY, "relativePath": "pump.glb"},
        {"key": "xid/test/sub/", "relativePath": "sub/"},
        {"key": "xid/test/", "relativePath": ""},
    ]
    stage, recorder, _input_root = _run(monkeypatch, tmp_path, listing)
    assert [d["key"] for d in recorder.downloads] == [PRIMARY_KEY]
    assert stage.status == PipelineStatus.COMPLETE
