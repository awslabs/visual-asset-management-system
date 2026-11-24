# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.assetLocations: what an asset's stored location key covers.

A location ending in '/' is a folder and holds every key under it. Any other location is a single-file
asset: it holds its own object, everything under '{file key}/' (its companion folder) and its own
previews '{file key}.previewFile.{ext}'. A key that merely starts with the same characters (a sibling
such as 'model.glb.bak', or 'model.glbx/...') is not part of it. Two locations overlap when some key is
held by both, which ``locations_share_key`` decides. The module is the REAL one, registered by
tests/conftest.py.
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
