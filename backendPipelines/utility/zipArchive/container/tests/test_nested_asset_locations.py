#  SPDX-License-Identifier: Apache-2.0

"""A zip download of an asset leaves out the files of other assets nested in its location: the job
manifest's ``excludeLocations`` names their stored locations, and the files they hold (a folder: every key
under it; a single file: its key, its companion folder and its previews) are not archived."""

import io
import zipfile

import pytest

from test_runner import AUX, BASE, FakeS3, _manifest, _zip_job
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
