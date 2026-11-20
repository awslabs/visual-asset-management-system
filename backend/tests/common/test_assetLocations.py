# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.assetLocations: what an asset's stored location key covers.

A location ending in '/' is a folder and holds every key under it. Any other location is a single-file
asset: it holds its own object, everything under '{file key}/' (its companion folder) and its own
previews '{file key}.previewFile.{ext}'. A key that merely starts with the same characters (a sibling
such as 'model.glb.bak', or 'model.glbx/...') is not part of it. The module is the REAL one, registered
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
class TestIsShadowed:
    @pytest.mark.parametrize("key", ["projects/model.glb/model.glb",
                                     "projects/model.glb/model.glb.previewFile.png"])
    def test_a_companion_entry_at_the_object_or_preview_path_is_shadowed(self, key):
        assert al.is_shadowed(FILE_LOCATION, key) is True
        assert al.contains(FILE_LOCATION, key) is True
        assert al.relative_path_in(FILE_LOCATION, key) == al.relative_path_in(
            FILE_LOCATION, FILE_LOCATION + key[len(FILE_LOCATION + "/model.glb"):])

    @pytest.mark.parametrize("key", [FILE_LOCATION, "projects/model.glb.previewFile.png",
                                     "projects/model.glb/sub/model.glb", "projects/model.glb/model.glb.bak",
                                     "projects/model.glb.bak"])
    def test_nothing_else_is_shadowed(self, key):
        assert al.is_shadowed(FILE_LOCATION, key) is False

    def test_a_folder_location_shadows_nothing(self):
        assert al.is_shadowed(FOLDER_LOCATION, "projects/building-a/building-a") is False


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
