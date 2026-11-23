# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.assetLocations: what an asset's stored location key covers.

A location ending in '/' is a folder and holds every key under it. Any other location is a single-file
asset: it holds its own object, everything under '{file key}/' (its companion folder) and its own
previews '{file key}.previewFile.{ext}'. A key that merely starts with the same characters (a sibling
such as 'model.glb.bak', or 'model.glbx/...') is not part of it. A companion entry named exactly like the
object is listed one level down, so every key has one path and nothing is hidden. The module is the REAL one, registered
by tests/conftest.py.
"""

import sys

import pytest

al = sys.modules['common.assetLocations']

FILE_LOCATION = "projects/model.glb"
FOLDER_LOCATION = "projects/building-a/"


@pytest.mark.unit
class TestNormalizeLocation:
    @pytest.mark.parametrize("key,expected", [
        ("projects/model.glb", "projects/model.glb"),
        ("/projects/model.glb", "projects/model.glb"),
        ("//projects/a/", "projects/a/"),
        ("", ""),
        (None, ""),
    ])
    def test_strips_leading_slashes_only(self, key, expected):
        assert al.normalize_location(key) == expected


@pytest.mark.unit
class TestIsFolderLocation:
    @pytest.mark.parametrize("key", ["projects/building-a/", "/x1234/", "a/"])
    def test_a_trailing_slash_is_a_folder(self, key):
        assert al.is_folder_location(key) is True

    @pytest.mark.parametrize("key", ["projects/model.glb", "projects/building-a", "model.glb"])
    def test_any_other_key_is_a_single_file_asset(self, key):
        assert al.is_folder_location(key) is False

    @pytest.mark.parametrize("key", ["", None, "/"])
    def test_no_location_is_not_a_folder(self, key):
        assert al.is_folder_location(key) is False


@pytest.mark.unit
class TestContains:
    @pytest.mark.parametrize("key", [
        "projects/model.glb",
        "projects/model.glb/",
        "projects/model.glb/inner.txt",
        "projects/model.glb/sub/deep/x.obj",
        "projects/model.glb.previewFile.png",
        "projects/model.glb.previewFile.gif",
    ])
    def test_a_single_file_location_holds_its_object_companion_folder_and_previews(self, key):
        assert al.contains(FILE_LOCATION, key) is True
        assert al.contains("/" + FILE_LOCATION, key) is True

    @pytest.mark.parametrize("key", [
        "projects/model.glb.bak",
        "projects/model.glbx/inner.txt",
        "projects/model.glb.previewFile.",
        "projects/model.glb.previewFile.png/x",
        "projects/model.gl",
        "projects/",
    ])
    def test_a_single_file_location_holds_no_key_that_only_shares_its_characters(self, key):
        assert al.contains(FILE_LOCATION, key) is False

    @pytest.mark.parametrize("key", [
        "projects/building-a/",
        "projects/building-a/m.glb",
        "projects/building-a/sub/deep/m.glb",
    ])
    def test_a_folder_holds_every_key_under_it(self, key):
        assert al.contains(FOLDER_LOCATION, key) is True

    @pytest.mark.parametrize("key", [
        "projects/building-a",
        "projects/building-ab/m.glb",
        "projects/building-a.bak",
    ])
    def test_a_folder_never_holds_a_sibling_prefix(self, key):
        assert al.contains(FOLDER_LOCATION, key) is False

    @pytest.mark.parametrize("location", ["", None, "/"])
    def test_no_location_holds_nothing(self, location):
        assert al.contains(location, "projects/model.glb") is False
        assert al.contains(location, "") is False

    def test_an_empty_key_is_never_held(self):
        assert al.contains(FOLDER_LOCATION, "") is False
        assert al.contains(FOLDER_LOCATION, None) is False


# The locations the overlap grid compares pairwise: names ending in or containing '.previewFile', nested
# names, folder and file pairs, companion chains, and neighbours that only share characters.
GRID_LOCATIONS = (
    "a/", "a", "b/x", "a/xy", "a/x.bak",
    "a/x", "a/x/", "a/x/x", "a/x/x/", "a/x/x/x", "a/x/sub/", "a/x/sub/m.glb",
    "a/x.previewFil", "a/x.previewFile", "a/x.previewFile.", "a/x.previewFile.z", "a/x.previewFile/",
    "a/x.previewFile.z/", "a/x.previewFile.z/q", "a/x.previewFile.previewFile", "a/x.previewFile.previewFile.z",
    "a/x.previewFile.z.previewFile", "a/x/x.previewFile", "a/x/x.previewFile.z", "/a/x.previewFile",
)
_CANDIDATE_SUFFIXES = ("", "z", ".z", "/", "/z", "/z/", "/x", "/x/x", ".previewFile.", ".previewFile.z",
                       ".previewFile.z/q", ".previewFile.previewFile.z")


def _candidate_keys(*locations):
    """Every leading part of each location followed by each suffix: the location keys, their companion
    folders and entries, keys past each preview stem, and the keys that only share characters with them."""
    keys = set()
    for location in locations:
        loc = al.normalize_location(location)
        for end in range(1, len(loc) + 1):
            keys.update(loc[:end] + suffix for suffix in _CANDIDATE_SUFFIXES)
    return keys


def _brute_force_share(first, second):
    return any(al.contains(first, key) and al.contains(second, key) for key in _candidate_keys(first, second))


@pytest.mark.unit
class TestLocationsShareKey:
    @pytest.mark.parametrize("first", GRID_LOCATIONS)
    def test_two_locations_share_a_key_exactly_when_some_key_is_held_by_both(self, first):
        wrong = [second for second in GRID_LOCATIONS
                 if al.locations_share_key(first, second) is not _brute_force_share(first, second)]
        assert wrong == []

    @pytest.mark.parametrize("first,second,witness", [
        # A preview of a/x with extension previewFile.z, and a preview of a/x.previewFile with extension z
        ("a/x", "a/x.previewFile", "a/x.previewFile.previewFile.z"),
        ("a/x", "a/x.previewFile.", "a/x.previewFile..previewFile.z"),
        ("p/model.glb", "p/model.glb.previewFile", "p/model.glb.previewFile.previewFile.png"),
    ])
    def test_two_preview_stems_share_keys_that_neither_location_names(self, first, second, witness):
        assert not al.contains(first, second) and not al.contains(second, first)
        assert al.contains(first, witness) and al.contains(second, witness)
        assert al.locations_share_key(first, second) is True
        assert al.locations_share_key(second, first) is True

    @pytest.mark.parametrize("first,second", [
        ("a/x", "a/x"), ("a/x", "a/x/"), ("a/x", "a/x/sub/m.glb"), ("a/x", "a/x.previewFile.z"),
        ("a/", "a/x"), ("a/x/", "a/x/x/"), ("a/x/x", "a/x/x/x"), ("/a/x", "a/x.previewFile"),
    ])
    def test_a_location_holding_the_other_or_its_key_shares_a_key(self, first, second):
        assert al.locations_share_key(first, second) is True
        assert al.locations_share_key(second, first) is True

    @pytest.mark.parametrize("first,second", [
        ("a/x", "a/x.bak"), ("a/x", "a/xy"), ("a/x", "a/x.previewFile/"), ("a/x", "a/x.previewFile.z/"),
        ("a/x", "a/x.previewFil"), ("a/x/", "a/x.previewFile.z"), ("a/x", "b/x"), ("a/x/", "a/xy"),
    ])
    def test_locations_that_only_share_characters_share_no_key(self, first, second):
        assert al.locations_share_key(first, second) is False
        assert al.locations_share_key(second, first) is False

    @pytest.mark.parametrize("empty", ["", None, "/"])
    def test_no_location_shares_a_key(self, empty):
        assert al.locations_share_key(empty, "a/x") is False
        assert al.locations_share_key("a/", empty) is False
        assert al.locations_share_key(empty, empty) is False


@pytest.mark.unit
class TestRelativePathIn:
    @pytest.mark.parametrize("key,expected", [
        (FILE_LOCATION, "/model.glb"),
        ("projects/model.glb/", "/"),
        ("projects/model.glb/sub/x.obj", "/sub/x.obj"),
        ("projects/model.glb.previewFile.png", "/model.glb.previewFile.png"),
    ])
    def test_single_file_paths(self, key, expected):
        assert al.relative_path_in(FILE_LOCATION, key) == expected

    def test_a_root_level_single_file(self):
        assert al.relative_path_in("model.glb", "model.glb") == "/model.glb"

    def test_a_folder_key_is_relative_to_the_folder(self):
        assert al.relative_path_in(FOLDER_LOCATION, "projects/building-a/sub/m.glb") == "/sub/m.glb"
        assert al.relative_path_in(FOLDER_LOCATION, FOLDER_LOCATION) == "/"

    @pytest.mark.parametrize("location,key", [
        (FILE_LOCATION, "projects/model.glb.bak"),
        (FILE_LOCATION, "projects/model.glbx/inner.txt"),
        (FOLDER_LOCATION, "projects/building-ab/m.glb"),
        ("", "projects/model.glb"),
    ])
    def test_a_key_outside_the_location_has_no_path(self, location, key):
        assert al.relative_path_in(location, key) is None


@pytest.mark.unit
class TestKeysNamedLikeTheFile:
    """A companion entry named exactly like the object is listed one level down; nothing is hidden."""

    @pytest.mark.parametrize("key,path", [
        ("projects/model.glb", "/model.glb"),
        ("projects/model.glb.previewFile.png", "/model.glb.previewFile.png"),
        ("projects/model.glb/model.glb", "/model.glb/model.glb"),
        ("projects/model.glb/model.glb.previewFile.png", "/model.glb/model.glb.previewFile.png"),
        # A real key one level further down shifts with the chain, so every path names one key
        ("projects/model.glb/model.glb/model.glb", "/model.glb/model.glb/model.glb"),
        ("projects/model.glb/model.glb/model.glb.previewFile.gif", "/model.glb/model.glb/model.glb.previewFile.gif"),
        # Every other key keeps the path after the file key
        ("projects/model.glb/model.glb/sub.txt", "/model.glb/sub.txt"),
        ("projects/model.glb/model.glb/", "/model.glb/"),
        ("projects/model.glb/model.glb.bak", "/model.glb.bak"),
        ("projects/model.glb/sub/model.glb", "/sub/model.glb"),
    ])
    def test_chain_keys_and_their_previews_are_one_level_deeper(self, key, path):
        assert al.contains(FILE_LOCATION, key) is True
        assert al.relative_path_in(FILE_LOCATION, key) == path
        assert al.object_key_for(FILE_LOCATION, path) == key

    def test_no_two_keys_share_a_path(self):
        keys = ["projects/model.glb", "projects/model.glb/model.glb", "projects/model.glb/model.glb/model.glb",
                "projects/model.glb.previewFile.png", "projects/model.glb/model.glb.previewFile.png",
                "projects/model.glb/model.glb/sub.txt", "projects/model.glb/sub.txt"]
        paths = [al.relative_path_in(FILE_LOCATION, key) for key in keys]
        assert len(set(paths)) == len(keys)
        # A folder location has no chain
        assert al.relative_path_in(FOLDER_LOCATION, "projects/building-a/building-a") == "/building-a"


@pytest.mark.unit
class TestFolderPaths:
    @pytest.mark.parametrize("location,folder,prefix", [
        (FOLDER_LOCATION, "/sub", "projects/building-a/sub/"),
        (FOLDER_LOCATION, "/sub/", "projects/building-a/sub/"),
        (FILE_LOCATION, "/sub/", "projects/model.glb/sub/"),
        (FILE_LOCATION, "/model.glb/", "projects/model.glb/model.glb"),
        (FILE_LOCATION, "/model.glb/model.glb/", "projects/model.glb/model.glb/model.glb"),
    ])
    def test_listing_prefix(self, location, folder, prefix):
        assert al.folder_listing_prefix(location, folder) == prefix

    @pytest.mark.parametrize("key,expected", [
        ("projects/model.glb/model.glb", True),
        ("projects/model.glb/model.glb.previewFile.png", True),
        ("projects/model.glb/model.glb/x.txt", True),
        ("projects/model.glb/model.glb.bak", False),
        ("projects/model.glb/sub/x.txt", False),
        ("projects/model.glb", False),
    ])
    def test_a_folder_named_like_the_file_holds_the_chain_key_below_it(self, key, expected):
        assert al.in_folder(FILE_LOCATION, "/model.glb/", key) is expected

    @pytest.mark.parametrize("key,expected", [
        ("projects/building-a/sub/x.txt", True),
        ("projects/building-a/sub.txt", False),
        ("projects/building-a/subway.glb", False),
    ])
    def test_a_folder_path_selects_no_sibling_that_only_starts_with_its_name(self, key, expected):
        assert al.in_folder(FOLDER_LOCATION, "/sub", key) is expected


@pytest.mark.unit
class TestObjectKeyFor:
    @pytest.mark.parametrize("path", ["/model.glb", "model.glb", FILE_LOCATION, "/" + FILE_LOCATION])
    def test_the_file_name_names_the_object(self, path):
        assert al.object_key_for(FILE_LOCATION, path) == FILE_LOCATION
        assert al.names_own_object(FILE_LOCATION, path) is True

    @pytest.mark.parametrize("path", ["/model.glb.previewFile.png", "projects/model.glb.previewFile.png"])
    def test_a_preview_path_names_the_own_preview(self, path):
        assert al.object_key_for(FILE_LOCATION, path) == "projects/model.glb.previewFile.png"

    @pytest.mark.parametrize("path,expected", [
        ("/sub/x.obj", "projects/model.glb/sub/x.obj"),
        ("sub/x.obj", "projects/model.glb/sub/x.obj"),
        ("projects/model.glb/sub/x.obj", "projects/model.glb/sub/x.obj"),
        ("/sub/", "projects/model.glb/sub/"),
        ("/model.glb.bak", "projects/model.glb/model.glb.bak"),
        ("projects/model.glb.bak", "projects/model.glb/projects/model.glb.bak"),
        ("/model.glb/inner.txt", "projects/model.glb/model.glb/inner.txt"),
    ])
    def test_any_other_path_names_a_companion_entry(self, path, expected):
        assert al.object_key_for(FILE_LOCATION, path) == expected
        assert al.contains(FILE_LOCATION, expected) is True
        assert al.names_own_object(FILE_LOCATION, path) is False

    @pytest.mark.parametrize("path", ["/", "", None])
    def test_the_whole_single_file_asset_is_no_single_key(self, path):
        assert al.object_key_for(FILE_LOCATION, path) is None

    @pytest.mark.parametrize("path,expected", [
        ("/sub/m.glb", "projects/building-a/sub/m.glb"),
        ("sub/m.glb", "projects/building-a/sub/m.glb"),
        ("/", "projects/building-a/"),
        ("", "projects/building-a/"),
    ])
    def test_a_folder_location_joins_the_path_under_it(self, path, expected):
        assert al.object_key_for(FOLDER_LOCATION, path) == expected
        assert al.names_own_object(FOLDER_LOCATION, path) is False

    def test_no_location_resolves_nothing(self):
        assert al.object_key_for("", "/model.glb") is None
        assert al.object_key_for(None, "/model.glb") is None

    @pytest.mark.parametrize("location,key", [
        (FILE_LOCATION, FILE_LOCATION),
        (FILE_LOCATION, "projects/model.glb/sub/x.obj"),
        (FILE_LOCATION, "projects/model.glb.previewFile.png"),
        (FOLDER_LOCATION, "projects/building-a/sub/m.glb"),
        (FOLDER_LOCATION, FOLDER_LOCATION),
    ])
    def test_object_key_for_inverts_relative_path_in(self, location, key):
        assert al.object_key_for(location, al.relative_path_in(location, key)) == key


@pytest.mark.unit
class TestListingPrefixes:
    def test_a_single_file_location_lists_its_object_previews_and_companion_folder(self):
        assert al.listing_prefixes(FILE_LOCATION) == [
            (FILE_LOCATION, 1), (FILE_LOCATION + ".previewFile.", None), (FILE_LOCATION + "/", None)]

    def test_every_key_the_location_holds_is_under_one_of_its_prefixes(self):
        held = (FILE_LOCATION, "projects/model.glb.previewFile.png", "projects/model.glb/sub/x.obj",
                "projects/model.glb/model.glb")
        for key in held:
            assert any(key.startswith(prefix) for prefix, _ in al.listing_prefixes(FILE_LOCATION)), key

    def test_a_neighbour_is_only_under_the_one_key_read(self):
        for key in ("projects/model.glb.bak", "projects/model.glb-old/n.txt", "projects/model.glbx/inner.txt"):
            wide = [prefix for prefix, cap in al.listing_prefixes(FILE_LOCATION) if cap != 1]
            assert not any(key.startswith(prefix) for prefix in wide), key

    def test_a_folder_location_is_its_own_prefix(self):
        assert al.listing_prefixes(FOLDER_LOCATION) == [(FOLDER_LOCATION, None)]
        assert al.listing_prefixes("/" + FOLDER_LOCATION) == [(FOLDER_LOCATION, None)]

    def test_no_location_lists_nothing(self):
        assert al.listing_prefixes("") == [] and al.listing_prefixes(None) == []


@pytest.mark.unit
class TestHeldObjects:
    @staticmethod
    def _lister(*keys):
        def list_objects(prefix, cap):
            listed = [{"Key": key} for key in sorted(keys) if key.startswith(prefix)]
            return listed[:cap] if cap else listed
        return list_objects

    def test_each_held_key_is_yielded_once_when_the_object_has_no_current_version(self):
        lister = self._lister("projects/model.glb.previewFile.png", "projects/model.glb/sub/x.obj")
        assert [o["Key"] for o in al.held_objects(FILE_LOCATION, lister)] == [
            "projects/model.glb.previewFile.png", "projects/model.glb/sub/x.obj"]
        lister = self._lister("projects/model.glb/sub/x.obj")
        assert [o["Key"] for o in al.held_objects(FILE_LOCATION, lister)] == ["projects/model.glb/sub/x.obj"]

    def test_the_object_and_its_keys_are_yielded_and_neighbours_are_not(self):
        lister = self._lister(FILE_LOCATION, "projects/model.glb-old/n.txt", "projects/model.glb.bak",
                              "projects/model.glb.previewFile.png", "projects/model.glb/sub/x.obj")
        assert [o["Key"] for o in al.held_objects(FILE_LOCATION, lister)] == [
            FILE_LOCATION, "projects/model.glb.previewFile.png", "projects/model.glb/sub/x.obj"]
        # A folder location lists its folder once
        lister = self._lister("projects/building-a/x.txt", "projects/building-a-other/y.txt")
        assert [o["Key"] for o in al.held_objects(FOLDER_LOCATION, lister)] == ["projects/building-a/x.txt"]


@pytest.mark.unit
class TestAFileNamedLikeAPreview:
    LOCATION = "projects/scan.previewFile.png"

    @pytest.mark.parametrize("key,path", [
        ("projects/scan.previewFile.png", "/scan.previewFile.png"),
        ("projects/scan.previewFile.png/scan.previewFile.png", "/scan.previewFile.png/scan.previewFile.png"),
        ("projects/scan.previewFile.png.previewFile.gif", "/scan.previewFile.png.previewFile.gif"),
        ("projects/scan.previewFile.png/sub/x.obj", "/sub/x.obj"),
    ])
    def test_its_keys_keep_their_own_paths(self, key, path):
        assert al.relative_path_in(self.LOCATION, key) == path
        assert al.object_key_for(self.LOCATION, path) == key
        assert al.names_own_object(self.LOCATION, path) is (key == self.LOCATION)


@pytest.mark.unit
class TestAuxScope:
    def test_the_object_keeps_to_its_preview_folder_and_not_a_companion_preview_folder(self):
        prefix, keep = al.aux_scope("db1", FILE_LOCATION, FILE_LOCATION)
        assert prefix == "db1/projects/model.glb/preview/"
        assert keep("db1/projects/model.glb/preview/PotreeViewer/r.bin")
        assert not keep("db1/projects/model.glb/preview/x.glb/preview/PotreeViewer/r.bin")

    def test_a_companion_entry_in_a_folder_named_preview_keeps_to_its_own_data(self):
        prefix, keep = al.aux_scope("db1", FILE_LOCATION, "projects/model.glb/preview/x.glb")
        assert prefix == "db1/projects/model.glb/preview/x.glb/"
        assert keep("db1/projects/model.glb/preview/x.glb/preview/a.bin")
        assert not keep("db1/projects/model.glb/preview/x.glb/a.bin")

    def test_other_files_need_no_filter(self):
        assert al.aux_scope("db1", FILE_LOCATION, "projects/model.glb/sub/x.obj") == (
            "db1/projects/model.glb/sub/x.obj/", None)
        assert al.aux_scope("db1", FOLDER_LOCATION, "projects/building-a/x.txt") == (
            "db1/projects/building-a/x.txt/", None)
