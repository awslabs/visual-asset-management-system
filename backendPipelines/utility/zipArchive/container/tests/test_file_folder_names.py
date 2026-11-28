#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A selection whose file name is also a folder of another entry is refused (#2070).

A folder asset can hold a file 'a.txt' and files under 'a.txt/'. The archive with both entries is valid,
but no file system holds a file and a folder of one name, so extracting it loses entries. As for two
entries of one name, the selection is refused before any byte is written, naming the file and one path in
the folder of its name; nothing is renamed."""

import pytest

from zip_archive_pipeline.resolve import check_names
from zip_archive_pipeline.rules import Refusal

pytestmark = pytest.mark.unit


def _members(*paths):
    return [{"relativePath": path} for path in paths]


class TestAFileAndAFolderOfItsName:
    @pytest.mark.parametrize("paths,dirs,file,other", [
        (("/a.txt", "/a.txt/b.txt"), (), "/a.txt", "/a.txt/b.txt"),
        (("/a.txt/b.txt", "/a.txt"), (), "/a.txt", "/a.txt/b.txt"),
        (("/a.txt",), ("/a.txt/",), "/a.txt", "/a.txt/"),
        (("/x/a.txt", "/x/a.txt/deep/er/c.bin"), (), "/x/a.txt", "/x/a.txt/deep/er/c.bin"),
        (("/x/a.txt",), ("/x/a.txt/deep/",), "/x/a.txt", "/x/a.txt/deep/"),
        (("/model.glb", "/model.glb/sub/x.obj", "/model.glb/model.glb"), (), "/model.glb", "/model.glb/sub/x.obj"),
    ], ids=["file-then-folder", "folder-then-file", "empty-folder", "deeper", "deeper-empty-folder",
            "converted-asset"])
    def test_is_refused_naming_the_file_and_a_path_in_its_folder(self, paths, dirs, file, other):
        with pytest.raises(Refusal) as refusal:
            check_names(_members(*paths), list(dirs))
        message = str(refusal.value)
        assert repr(file) in message and repr(other) in message and "download them separately" in message

    def test_an_empty_segment_path_under_the_file_name_is_refused(self):
        """'/a//x' extracts into the folder 'a' as 'a/x'."""
        with pytest.raises(Refusal):
            check_names(_members("/a", "/a//x"), [])


class TestNoFalsePositive:
    @pytest.mark.parametrize("paths,dirs", [
        (("/a.txt", "/a.txtx/b"), ()),
        (("/a.txt", "/a.tx/b", "/b/a.txt/c"), ()),
        (("/a.txt", "/sub/a.txt"), ("/a.txtx/",)),
        (("/d/a.glb", "/d/b.glb"), ("/d/",)),
        (("/model.glb/model.glb", "/model.glb/sub/x.obj"), ()),
    ], ids=["sibling-with-longer-name", "other-folders", "same-name-elsewhere", "folder-of-files",
            "moved-asset"])
    def test_is_archived(self, paths, dirs):
        check_names(_members(*paths), list(dirs))

    def test_two_files_of_one_name_keep_their_own_refusal(self):
        with pytest.raises(Refusal) as refusal:
            check_names(_members("/a.glb", "//a.glb"), [])
        assert "would have the same name in the archive" in str(refusal.value)
