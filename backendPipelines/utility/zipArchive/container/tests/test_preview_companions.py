#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""#1622: preview companions ({file}.previewFile.{ext}) are left out of an archive unless
``includePreviewFiles`` is true: in a zip download the request manifest carries the value, in a Zip Archive
run the run's configBody does. A missing key is false and a value that is not a boolean refuses the archive,
in both modes. The marker mirrors backend common/s3PathPatterns.PREVIEW_FILE_PATTERN."""

import ast
import io
import json
import os
import zipfile

import pytest

from test_runner import AUX, ASSETS, BASE, FakeS3, OneClient, _manifest, _zip_job
from zip_archive_pipeline import runner
from zip_archive_pipeline.resolve import expand_selection
from zip_archive_pipeline.rules import (INCLUDE_PREVIEW_FILES_KEY, PREVIEW_FILE_PATTERN, Refusal,
                                        include_preview_files, is_preview_companion)

pytestmark = pytest.mark.unit
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 5))
ARCHIVE_KEY = "vams.preparedDownloads/db1/asset1/h/r1/out.zip"
LISTED = ["a.glb", "scan.e57", "tex/t1.png", "tex/t2.png"]
COMPANIONS = ["a.glb.previewFile.png", "scan.e57.previewFile.gif", "tex/t1.png.previewFile.jpg"]
MISSING = object()


@pytest.fixture
def s3():
    """The asset of the live report: four listed files, three with a generated preview companion."""
    s = FakeS3()
    for name in LISTED + COMPANIONS:
        s.add(BASE + name, name.encode(), content_type="image/png" if PREVIEW_FILE_PATTERN in name else "model/x")
    return s


def _names(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return sorted(z.namelist())


def _download(s3, value, entries=None):
    extra = {} if value is MISSING else {INCLUDE_PREVIEW_FILES_KEY: value}
    return _zip_job(s3, _manifest(entries=entries or [{"type": "asset", "key": BASE, "relativePath": "/"}],
                                  **extra))


def _archive_run(s3, value):
    config = {"format": "zip"}
    if value is not MISSING:
        config[INCLUDE_PREVIEW_FILES_KEY] = value
    s3.add("pipelines/run/manifest.json", json.dumps({"inputFiles": [
        {"bucket": ASSETS, "key": BASE, "versionId": ""}]}).encode(), bucket=ASSETS)
    definition = {"mode": "archiveRun", "archiveRun": {
        "runManifest": {"bucket": ASSETS, "key": "pipelines/run/manifest.json"}, "assetId": "xasset1",
        "databaseId": "db1", "outputBucket": ASSETS, "outputDir": "pipelines/run/output/files/", "config": config}}
    return runner.Job(definition, OneClient(s3), None, logger=lambda *a: None)


class TestZipDownload:
    @pytest.mark.parametrize("value", [False, MISSING])
    def test_the_whole_asset_leaves_the_companions_out(self, s3, value):
        # Control: seven current objects under the asset, as the live report counted 7 entries.
        assert len(LISTED + COMPANIONS) == 7
        job, table = _download(s3, value)
        result = job.run()
        assert result["status"] == "succeeded" and result["files"] == 4
        assert _names(s3.completed[(AUX, ARCHIVE_KEY)]) == LISTED
        assert table.row["filesTotal"] == 4

    def test_true_archives_the_companions_too(self, s3):
        job, _ = _download(s3, True)
        assert job.run()["files"] == 7
        assert _names(s3.completed[(AUX, ARCHIVE_KEY)]) == sorted(LISTED + COMPANIONS)

    @pytest.mark.parametrize("value", ["true", 1, None, ["x"]])
    def test_a_value_that_is_not_a_boolean_refuses_the_job(self, s3, value):
        job, table = _download(s3, value)
        with pytest.raises(runner.ArchiveFailure) as e:
            job.run()
        assert e.value.refusal and "includePreviewFiles must be true or false" in e.value.reason
        assert table.row["status"] == "FAILED" and s3.completed == {} and s3.uploads == {}

    def test_an_explicit_companion_is_left_out_and_alone_is_no_files(self, s3):
        job, _ = _download(s3, False, entries=[
            {"type": "file", "key": BASE + "a.glb", "relativePath": "/a.glb"},
            {"type": "file", "key": BASE + "a.glb.previewFile.png", "relativePath": "/a.glb.previewFile.png"}])
        assert job.run()["files"] == 1
        alone, table = _download(FakeS3(), False, entries=[
            {"type": "file", "key": BASE + "a.glb.previewFile.png", "relativePath": "/a.glb.previewFile.png"}])
        with pytest.raises(runner.ArchiveFailure):
            alone.run()
        assert table.row["errorMessage"] == "The selection has no files"

    def test_pinned_companions_are_left_out(self, s3):
        pinned = [{"key": BASE + "a.glb", "relativePath": "/a.glb", "versionId": "v1", "size": 5},
                  {"key": BASE + "a.glb.previewFile.png", "relativePath": "/a.glb.previewFile.png",
                   "versionId": "v1", "size": 21}]
        job, _ = _zip_job(s3, _manifest(pinned=pinned, **{INCLUDE_PREVIEW_FILES_KEY: False}))
        assert job.run()["files"] == 1


class TestZipArchiveRun:
    @pytest.mark.parametrize("value", [False, MISSING])
    def test_the_run_leaves_the_companions_out(self, s3, value):
        result = _archive_run(s3, value).run()
        assert _names(s3.completed[(ASSETS, result["archiveKey"])]) == LISTED

    def test_true_archives_the_companions_too(self, s3):
        result = _archive_run(s3, True).run()
        assert _names(s3.completed[(ASSETS, result["archiveKey"])]) == sorted(LISTED + COMPANIONS)

    @pytest.mark.parametrize("value", ["false", 0, None])
    def test_a_value_that_is_not_a_boolean_refuses_the_run(self, s3, value):
        with pytest.raises(runner.ArchiveFailure) as e:
            _archive_run(s3, value).run()
        assert e.value.refusal and "includePreviewFiles" in e.value.reason
        assert s3.completed == {} and s3.uploads == {}


class TestResolution:
    def test_a_folder_of_companions_only_reads_as_empty(self):
        s = FakeS3()
        s.add(BASE + "thumbs/x.glb.previewFile.png", b"p")
        with pytest.raises(Refusal, match="empty or does not exist"):
            expand_selection(s, ASSETS, BASE, [{"type": "folder", "key": BASE + "thumbs/"}])
        members, _ = expand_selection(s, ASSETS, BASE, [{"type": "folder", "key": BASE + "thumbs/"}],
                                      include_preview_files=True)
        assert [m["relativePath"] for m in members] == ["/thumbs/x.glb.previewFile.png"]

    def test_the_value_reader(self):
        assert include_preview_files({}) is False and include_preview_files(None) is False
        assert include_preview_files({INCLUDE_PREVIEW_FILES_KEY: True}) is True
        with pytest.raises(Refusal):
            include_preview_files({INCLUDE_PREVIEW_FILES_KEY: "yes"})

    @pytest.mark.parametrize("key,companion", [
        ("a/model.glb.previewFile.png", True), ("a/model.glb", False), ("a/previewFile.png", False),
        ("a/model.glb.previewfile.png", False), ("a/x.previewFile.y/z.glb", True)])
    def test_the_marker_is_the_file_lists_substring_rule(self, key, companion):
        assert is_preview_companion(key) is companion


def test_the_marker_mirrors_the_backend():
    """PREVIEW_FILE_PATTERN must equal backend common/s3PathPatterns.PREVIEW_FILE_PATTERN, the marker the
    file list hides and the export folds."""
    path = os.path.join(REPO, "backend", "backend", "common", "s3PathPatterns.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    value = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "PREVIEW_FILE_PATTERN")
    assert PREVIEW_FILE_PATTERN == value == ".previewFile."


def test_the_manifest_key_mirrors_the_backend():
    path = os.path.join(REPO, "backend", "backend", "common", "zipDownloads.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    values = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              and isinstance(n.targets[0], ast.Name)
              and n.targets[0].id in ("INCLUDE_PREVIEW_FILES_KEY", "ZIP_DOWNLOAD_INCLUDE_PREVIEW_FILES")}
    assert values == {"INCLUDE_PREVIEW_FILES_KEY": INCLUDE_PREVIEW_FILES_KEY,
                      "ZIP_DOWNLOAD_INCLUDE_PREVIEW_FILES": False}
