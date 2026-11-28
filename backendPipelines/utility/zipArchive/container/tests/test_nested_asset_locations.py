#  SPDX-License-Identifier: Apache-2.0

"""A zip download of an asset leaves out the files of other assets nested in its location: the job
manifest's ``excludeLocations`` names their stored locations, and the files they hold (a folder: every key
under it; a single file: its key, its companion folder and its previews) are not archived."""

import io
import json
import zipfile

import pytest

from test_runner import ASSETS, AUX, BASE, FakeS3, OneClient, _manifest, _zip_job
from zip_archive_pipeline import runner
from zip_archive_pipeline.resolve import expand_selection, held_by_excluded

pytestmark = pytest.mark.unit
OWN = ["a.glb", "sub/b.obj"]
NESTED = ["inner/c.glb", "model.glb", "model.glb/notes.txt", "model.glb.previewFile.png"]
EXCLUDE = [BASE + "inner/", BASE + "model.glb"]
ARCHIVE_KEY = "vams.preparedDownloads/db1/asset1/h/r1/out.zip"


@pytest.fixture
def s3():
    s = FakeS3()
    for name in OWN + NESTED + ["inner/", "model.glb2.txt"]:
        s.add(BASE + name, name.encode(), content_type="model/x")
    return s


@pytest.mark.parametrize("key,held", [
    (BASE + "inner/c.glb", True), (BASE + "inner/", True), (BASE + "model.glb", True),
    (BASE + "model.glb/notes.txt", True), (BASE + "model.glb.previewFile.png", True),
    (BASE + "model.glb2.txt", False), (BASE + "innerx/c.glb", False), (BASE + "a.glb", False),
])
def test_the_keys_an_excluded_location_holds(key, held):
    assert held_by_excluded(key, EXCLUDE) is held


def test_a_folder_selection_leaves_the_nested_files_and_folder_markers_out(s3):
    members, empty_dirs = expand_selection(s3, "assets", BASE, [{"type": "folder", "key": BASE}],
                                           include_preview_files=True, exclude_locations=EXCLUDE)
    assert sorted(m["relativePath"] for m in members) == ["/a.glb", "/model.glb2.txt", "/sub/b.obj"]
    assert empty_dirs == []


def test_without_excluded_locations_the_selection_is_as_before(s3):
    members, _ = expand_selection(s3, "assets", BASE, [{"type": "folder", "key": BASE}], include_preview_files=True)
    assert len(members) == len(OWN + NESTED) + 1


def test_a_zip_download_job_archives_only_the_assets_own_files(s3):
    job, _ = _zip_job(s3, _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}],
                                    excludeLocations=EXCLUDE))
    assert job.run()["status"] == "succeeded"
    with zipfile.ZipFile(io.BytesIO(s3.completed[(AUX, ARCHIVE_KEY)])) as z:
        assert sorted(z.namelist()) == ["a.glb", "model.glb2.txt", "sub/b.obj"]


def _archive_run(s3, entries):
    """A Zip Archive workflow run (archiveRun) whose run manifest is the one executeWorkflow wrote."""
    s3.add("pipelines/run/manifest.json", json.dumps({"inputFiles": entries}).encode(), bucket=ASSETS)
    definition = {"mode": "archiveRun", "archiveRun": {
        "runManifest": {"bucket": ASSETS, "key": "pipelines/run/manifest.json"}, "assetId": "xasset1",
        "databaseId": "db1", "outputBucket": ASSETS, "outputDir": "pipelines/run/output/files/",
        "config": {"format": "zip", "includePreviewFiles": True}}}
    return runner.Job(definition, OneClient(s3), None, logger=lambda *a: None)


def _archived(s3, result):
    with zipfile.ZipFile(io.BytesIO(s3.completed[(ASSETS, result["archiveKey"])])) as z:
        return sorted(z.namelist())


def test_a_workflow_run_over_the_whole_asset_archives_only_the_assets_own_files(s3):
    result = _archive_run(s3, [{"bucket": ASSETS, "key": BASE, "versionId": "", "excludeLocations": EXCLUDE}]).run()
    assert _archived(s3, result) == ["a.glb", "model.glb2.txt", "sub/b.obj"]


def test_a_workflow_run_over_a_folder_leaves_the_nested_asset_under_it_out(s3):
    s3.add(BASE + "sub/inner/d.glb", b"d", content_type="model/x")
    result = _archive_run(s3, [{"bucket": ASSETS, "key": BASE + "sub/", "versionId": "",
                                "excludeLocations": [BASE + "sub/inner/"]}]).run()
    assert _archived(s3, result) == ["sub/b.obj"]


def test_a_workflow_run_without_the_list_reads_the_nested_files_as_before(s3):
    # Without the list the nested single-file asset's 'model.glb' and its companion 'model.glb/notes.txt'
    # are both selected. No folder holds a file and a folder of one name when the archive is extracted, so
    # the run is refused naming both (#2070); without the companion the folder is archived as before.
    with pytest.raises(runner.ArchiveFailure) as failure:
        _archive_run(s3, [{"bucket": ASSETS, "key": BASE, "versionId": ""}]).run()
    assert failure.value.refusal
    assert "'/model.glb'" in failure.value.reason and "'/model.glb/notes.txt'" in failure.value.reason
    del s3.objects[(ASSETS, BASE + "model.glb/notes.txt")]
    result = _archive_run(s3, [{"bucket": ASSETS, "key": BASE, "versionId": ""}]).run()
    assert _archived(s3, result) == sorted(OWN + [n for n in NESTED if n != "model.glb/notes.txt"]
                                           + ["model.glb2.txt"])


def test_the_lists_of_several_inputs_are_merged(s3):
    result = _archive_run(s3, [
        {"bucket": ASSETS, "key": BASE + "sub/", "versionId": "", "excludeLocations": [BASE + "inner/"]},
        {"bucket": ASSETS, "key": BASE, "versionId": "", "excludeLocations": [BASE + "model.glb", 7, ""]}]).run()
    assert _archived(s3, result) == ["a.glb", "model.glb2.txt", "sub/b.obj"]


def _existing_key_bucket():
    """An asset created on an existing bucket folder (its keys carry no asset ID) holding a nested asset."""
    s = FakeS3()
    for name in ["projects/site-a/left/part.glb", "projects/site-a/right/part.glb",
                 "projects/site-a/inner/i1.glb", "projects/site-a/inner/sub/i2.glb"]:
        s.add(name, name.encode(), content_type="model/x")
    return s


@pytest.mark.parametrize("key,expected", [
    ("projects/site-a/", ["left/part.glb", "right/part.glb"]),
    ("projects/site-a/right/", ["right/part.glb"]),
], ids=["whole-asset", "folder"])
def test_a_run_over_an_existing_key_asset_runs_and_leaves_its_nested_asset_out(key, expected):
    """#1751 derives the root from the entry's assetRootS3Key (the keys carry no asset ID, so the run was
    refused as outside the run's asset); this branch's excludeLocations leaves the nested asset's files out.
    Both apply to one entry."""
    s3 = _existing_key_bucket()
    entry = {"bucket": ASSETS, "key": key, "versionId": "", "assetRootS3Key": "projects/site-a/",
             "assetRootBucket": ASSETS, "excludeLocations": ["projects/site-a/inner/"]}
    result = _archive_run(s3, [entry]).run()
    assert _archived(s3, result) == expected


def test_control_without_the_root_the_existing_key_run_is_refused():
    s3 = _existing_key_bucket()
    with pytest.raises(runner.ArchiveFailure) as e:
        _archive_run(s3, [{"bucket": ASSETS, "key": "projects/site-a/", "versionId": "",
                           "excludeLocations": ["projects/site-a/inner/"]}]).run()
    assert e.value.refusal and "outside the run's asset" in e.value.reason
