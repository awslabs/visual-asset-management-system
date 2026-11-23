# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.assetLocations.AssetFilePaths: the one mapping between asset-relative file paths and S3 keys.

A single-file asset (location ``p1/model.glb``) lists its own object at ``/model.glb`` and its previews at
``/model.glb.previewFile.{ext}``, and those paths name them at every file route. The exception is an asset
that holds a companion entry named like the object, ``p1/model.glb/model.glb``: that entry keeps the path
``/model.glb`` and the object keeps its earlier ``/`` (no change for that asset). Every other path keeps its
earlier meaning (``/x`` is ``p1/model.glb/x``). A path never resolves to a key the asset does not hold:
``..`` segments, another asset's key and keys that only share the location's characters stay refused or
out of reach. Folder locations are unchanged. The module is the REAL one, registered by tests/conftest.py.
"""

import sys

import pytest

al = sys.modules['common.assetLocations']

LOCATION = "p1/model.glb"
NAME = "model.glb"
OWN_PREVIEW = "p1/model.glb.previewFile.png"
COPY = "p1/model.glb/model.glb"
COMPANION = "p1/model.glb/sub/x.obj"
NEIGHBOURS = ("p1/model.glb.bak", "p1/model.glbx/a.txt", "p1/model.glb_tiles/t.b3dm", "p1/other.glb")
FOLDER = "proj/a/"


class _Probe:
    """A ``key_exists`` stand-in that records the keys it was asked about."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.calls = []

    def __call__(self, key):
        self.calls.append(key)
        return key in self.existing


def _paths(location=LOCATION, existing=()):
    probe = _Probe(existing)
    return al.AssetFilePaths(location, key_exists=probe), probe


@pytest.mark.unit
class TestCompanionKey:
    def test_a_single_file_location_names_the_entry_named_like_its_object(self):
        assert al.companion_key(LOCATION) == COPY
        assert al.companion_key("model.glb") == "model.glb/model.glb"

    @pytest.mark.parametrize("location", [FOLDER, "", None])
    def test_a_folder_or_no_location_has_none(self, location):
        assert al.companion_key(location) is None


@pytest.mark.unit
class TestObjectListedAtItsFileName:
    def test_the_object_is_listed_at_its_file_name_without_a_companion_entry(self):
        paths, probe = _paths()
        assert paths.object_listed() is True
        assert probe.calls == [COPY]

    def test_a_companion_entry_named_like_the_object_keeps_the_earlier_mapping(self):
        paths, _ = _paths(existing=[COPY])
        assert paths.object_listed() is False

    def test_the_read_is_made_once_per_instance(self):
        paths, probe = _paths()
        for _ in range(3):
            paths.object_listed()
            paths.key_for("/model.glb")
            paths.path_for(LOCATION)
        assert probe.calls == [COPY]

    def test_a_path_that_does_not_depend_on_it_makes_no_read(self):
        paths, probe = _paths()
        assert paths.key_for("/sub/x.obj") == COMPANION
        assert paths.path_for(COMPANION) == "/sub/x.obj"
        assert paths.request_key_for("p1/model.glb/sub/x.obj") == COMPANION
        assert probe.calls == []

    def test_a_folder_location_never_reads(self):
        paths, probe = _paths(FOLDER)
        assert paths.object_listed() is False
        assert paths.key_for("/x.txt") == "proj/a/x.txt"
        assert probe.calls == []

    def test_without_a_reader_the_earlier_mapping_holds(self):
        paths = al.AssetFilePaths(LOCATION)
        assert paths.object_listed() is False
        assert paths.key_for("/model.glb") == COPY
        assert paths.path_for(LOCATION) == "/"


@pytest.mark.unit
class TestPathToKeyWithoutACompanionEntry:
    @pytest.mark.parametrize("path", ["/model.glb", "model.glb", "//model.glb"])
    def test_the_file_name_names_the_object(self, path):
        paths, _ = _paths()
        assert paths.key_for(path) == LOCATION

    @pytest.mark.parametrize("path", ["/model.glb", "model.glb", "p1/model.glb"])
    def test_a_file_route_reaches_the_object_by_its_path_or_its_own_key(self, path):
        paths, _ = _paths()
        assert paths.request_key_for(path) == LOCATION

    def test_the_own_key_with_a_leading_slash_keeps_its_earlier_meaning(self):
        paths, _ = _paths()
        assert paths.request_key_for("/p1/model.glb") == "p1/model.glb/p1/model.glb"

    def test_the_own_preview_path_names_the_own_preview(self):
        paths, _ = _paths()
        assert paths.key_for("/model.glb.previewFile.png") == OWN_PREVIEW
        assert paths.request_key_for("/model.glb.previewFile.png") == OWN_PREVIEW

    @pytest.mark.parametrize("path,key", [
        ("/sub/x.obj", COMPANION),
        ("/", "p1/model.glb/"),
        ("", "p1/model.glb/"),
        ("/.previewFile.png", "p1/model.glb/.previewFile.png"),
        ("/model.glb/inner.txt", "p1/model.glb/model.glb/inner.txt"),
        ("/model.glb.previewFile.png/x", "p1/model.glb/model.glb.previewFile.png/x"),
    ])
    def test_every_other_path_keeps_its_earlier_meaning(self, path, key):
        paths, _ = _paths()
        assert paths.key_for(path) == key
        assert paths.request_key_for(path) == key


@pytest.mark.unit
class TestPathToKeyWithACompanionEntry:
    @pytest.mark.parametrize("path", ["/model.glb", "model.glb"])
    def test_the_file_name_keeps_naming_the_companion_entry(self, path):
        paths, _ = _paths(existing=[COPY])
        assert paths.key_for(path) == COPY
        assert paths.request_key_for(path) == COPY

    def test_the_own_key_keeps_its_earlier_meaning(self):
        paths, _ = _paths(existing=[COPY])
        assert paths.request_key_for("p1/model.glb") == "p1/model.glb/p1/model.glb"

    def test_the_preview_path_names_the_companion_entrys_preview(self):
        paths, _ = _paths(existing=[COPY])
        assert paths.key_for("/model.glb.previewFile.png") == "p1/model.glb/model.glb.previewFile.png"


@pytest.mark.unit
class TestKeyToPath:
    def test_without_a_companion_entry_the_object_and_its_previews_are_listed_at_the_file_name(self):
        paths, _ = _paths()
        assert paths.path_for(LOCATION) == "/model.glb"
        assert paths.path_for(OWN_PREVIEW) == "/model.glb.previewFile.png"
        assert paths.path_for(COMPANION) == "/sub/x.obj"
        assert paths.path_for("p1/model.glb/") == "/"

    def test_with_a_companion_entry_the_earlier_paths_are_kept(self):
        paths, _ = _paths(existing=[COPY])
        assert paths.path_for(LOCATION) == "/"
        assert paths.path_for(OWN_PREVIEW) == "/.previewFile.png"
        assert paths.path_for(COPY) == "/model.glb"

    @pytest.mark.parametrize("existing", [(), (COPY,)], ids=["no-companion", "companion"])
    def test_each_listed_path_resolves_back_to_its_key(self, existing):
        paths, _ = _paths(existing=existing)
        keys = [COMPANION, "p1/model.glb/sub/y.obj.previewFile.png"] + list(existing)
        if not existing:
            keys += [LOCATION, OWN_PREVIEW]
        for key in keys:
            assert paths.key_for(paths.path_for(key)) == key, key
            assert paths.request_key_for(paths.path_for(key)) == key, key

    @pytest.mark.parametrize("key", NEIGHBOURS + ("", None))
    def test_a_key_the_asset_does_not_hold_has_no_path(self, key):
        paths, _ = _paths()
        assert paths.path_for(key) is None

    def test_a_folder_lists_the_key_after_the_folder(self):
        paths, _ = _paths(FOLDER)
        assert paths.path_for("proj/a/x.txt") == "/x.txt"
        assert paths.path_for("proj/a/") == "/"
        assert paths.path_for("proj/a-other/x.txt") is None


@pytest.mark.unit
class TestNothingOutsideTheAssetIsReachable:
    @pytest.mark.parametrize("path", [
        "/../other.glb", "../other.glb", "/sub/../../other.glb", "/model.glb/..", "..", "/..",
    ])
    @pytest.mark.parametrize("existing", [(), (COPY,)], ids=["no-companion", "companion"])
    def test_a_traversal_segment_names_nothing(self, path, existing):
        paths, _ = _paths(existing=existing)
        assert paths.key_for(path) is None
        assert paths.request_key_for(path) is None
        assert paths.download_key_for(path) is None
        assert paths.aux_stream_key_for("db1", path) is None

    @pytest.mark.parametrize("value", ["p1/other.glb", "/p1/other.glb", "other-db/a1/model.glb"])
    def test_another_assets_key_resolves_inside_this_asset(self, value):
        paths, _ = _paths()
        key = paths.request_key_for(value)
        assert al.contains(LOCATION, key), key

    @pytest.mark.parametrize("value", ["p1/model.glb.bak", "p1/model.glbx/a.txt", "p1/model.glb_tiles/t.b3dm"])
    def test_the_base_key_prefix_trick_does_not_reach_a_neighbour(self, value):
        paths, _ = _paths()
        assert paths.request_key_for(value) == "p1/model.glb/" + value
        assert paths.download_key_for(value) is None

    def test_a_neighbour_named_like_the_own_preview_stem_is_not_the_own_preview(self):
        paths, _ = _paths()
        assert paths.key_for("/model.glb.previewFile.png/x") == "p1/model.glb/model.glb.previewFile.png/x"
        assert paths.key_for("/model.glb.previewFile.") == "p1/model.glb/model.glb.previewFile."

    def test_an_empty_location_holds_nothing(self):
        paths = al.AssetFilePaths("", key_exists=lambda key: False)
        for value in ("/x", "x", "", "/"):
            assert paths.key_for(value) is None
            assert paths.request_key_for(value) is None
            assert paths.download_key_for(value) is None


@pytest.mark.unit
class TestDownloadForms:
    def test_the_location_key_is_the_object_whatever_the_companion_state(self):
        for existing in ((), (COPY,)):
            paths, _ = _paths(existing=existing)
            assert paths.download_key_for(LOCATION) == LOCATION

    def test_the_file_name_names_the_object_without_a_companion_entry_only(self):
        assert _paths()[0].download_key_for("/model.glb") == LOCATION
        assert _paths(existing=[COPY])[0].download_key_for("/model.glb") == COPY

    def test_a_folder_location_keeps_its_forms(self):
        paths, _ = _paths(FOLDER)
        assert paths.download_key_for("proj/a/") == "proj/a/"
        assert paths.download_key_for("proj/a/x.txt") == "proj/a/x.txt"
        assert paths.download_key_for("/x.txt") == "proj/a/x.txt"
        assert paths.download_key_for("proj/a-other/x.txt") is None


@pytest.mark.unit
class TestFolderLocationUnchanged:
    @pytest.mark.parametrize("value,key", [
        ("/x.txt", "proj/a/x.txt"),
        ("x.txt", "proj/a/x.txt"),
        ("proj/a/x.txt", "proj/a/x.txt"),
        ("/proj/a/x.txt", "proj/a/x.txt"),
        ("/sub/", "proj/a/sub/"),
        ("/", "proj/a/"),
        ("//x.txt", "proj/a//x.txt"),
    ])
    def test_file_route_forms(self, value, key):
        paths, _ = _paths(FOLDER)
        assert paths.request_key_for(value) == key

    def test_relative_form_drops_every_leading_slash(self):
        paths, _ = _paths(FOLDER)
        assert paths.key_for("//x.txt") == "proj/a/x.txt"
        assert paths.key_for("proj/a/x.txt") == "proj/a/proj/a/x.txt"


@pytest.mark.unit
class TestListingPrefix:
    def test_without_a_companion_entry_the_whole_location_is_listed(self):
        assert _paths()[0].listing_prefix() == LOCATION

    def test_with_a_companion_entry_only_the_companion_folder_is_listed_as_before(self):
        assert _paths(existing=[COPY])[0].listing_prefix() == LOCATION + "/"

    def test_a_folder_is_listed_whole(self):
        assert _paths(FOLDER)[0].listing_prefix() == FOLDER


@pytest.mark.unit
class TestAuxStreamKey:
    def test_the_own_key_names_the_objects_own_viewer_data(self):
        paths = al.AssetFilePaths(LOCATION)
        assert (paths.aux_stream_key_for("db1", "p1/model.glb/preview/PotreeViewer/metadata.json")
                == "db1/p1/model.glb/preview/PotreeViewer/metadata.json")

    @pytest.mark.parametrize("value,key", [
        ("sub/x.e57/preview/PotreeViewer/m.json", "db1/p1/model.glb/sub/x.e57/preview/PotreeViewer/m.json"),
        ("model.glb/preview/m.json", "db1/p1/model.glb/model.glb/preview/m.json"),
        ("db1/p1/model.glb/sub/x/preview/m.json", "db1/p1/model.glb/sub/x/preview/m.json"),
    ])
    def test_every_other_path_keeps_its_earlier_meaning(self, value, key):
        assert al.AssetFilePaths(LOCATION).aux_stream_key_for("db1", value) == key

    def test_a_folder_location_is_unchanged(self):
        paths = al.AssetFilePaths(FOLDER)
        assert paths.aux_stream_key_for("db1", "x.e57/preview/m.json") == "db1/proj/a/x.e57/preview/m.json"
        assert paths.aux_stream_key_for("db1", "/x.e57/preview/m.json") == "db1/proj/a/x.e57/preview/m.json"

    def test_the_result_never_leaves_the_assets_auxiliary_base(self):
        paths = al.AssetFilePaths(LOCATION)
        for value in ("db1/p1/other.glb/preview/m.json", "db2/p1/model.glb/preview/m.json"):
            assert paths.aux_stream_key_for("db1", value).startswith("db1/p1/model.glb/")


@pytest.mark.unit
class TestOwnAuxScope:
    def test_the_objects_own_data_is_its_preview_folder_without_the_companion_entries_data(self):
        prefix, keep = al.own_aux_scope("db1", LOCATION, LOCATION)
        assert prefix == "db1/p1/model.glb/preview/"
        assert keep("db1/p1/model.glb/preview/PotreeViewer/m.json")
        assert not keep("db1/p1/model.glb/preview/x.glb/preview/m.json")

    def test_a_companion_preview_folder_does_not_take_the_objects_data(self):
        prefix, keep = al.own_aux_scope("db1", LOCATION, "p1/model.glb/preview/")
        assert prefix == "db1/p1/model.glb/preview/"
        assert not keep("db1/p1/model.glb/preview/PotreeViewer/m.json")
        assert keep("db1/p1/model.glb/preview/x.glb/preview/m.json")

    def test_the_companion_folder_takes_its_files_data_and_not_the_objects(self):
        """D2: deleting the companion folder ``{file key}/`` deletes its files' viewer data, the object's stays."""
        prefix, keep = al.own_aux_scope("db1", LOCATION, "p1/model.glb/")
        assert prefix == "db1/p1/model.glb/"
        assert keep("db1/p1/model.glb/sub/x.obj/preview/PotreeViewer/m.json")
        assert keep("db1/p1/model.glb/preview/x.glb/preview/m.json")
        assert not keep("db1/p1/model.glb/preview/PotreeViewer/m.json")

    def test_other_files_use_their_whole_base(self):
        assert al.own_aux_scope("db1", LOCATION, COMPANION) == ("db1/p1/model.glb/sub/x.obj/", None)
        assert al.own_aux_scope("db1", FOLDER, "proj/a/x.txt") == ("db1/proj/a/x.txt/", None)
