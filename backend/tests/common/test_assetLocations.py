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

import json
import sys
import threading
import types
from unittest.mock import MagicMock

import boto3
import botocore.session
import pytest

from backend.tests.pagingStub import Pager
from backend.tests.ssmParametersStub import (
    FakeClock, ParameterStoreStub, always, client_error, install_clock, load_ssm_parameters,
)
from tests.handlers.assets.test_createAsset_conditional_put import _load as _load_create_asset

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

    @pytest.mark.parametrize("path", ["/model.glb", "model.glb", FILE_LOCATION])
    def test_a_request_path_names_the_own_object_by_its_name_or_its_key(self, path):
        assert al.names_own_object(FILE_LOCATION, path) is True

    def test_a_request_path_that_spells_the_key_after_a_slash_is_a_companion_entry(self):
        """#1943: a leading '/' marks a path in the asset, so '/{file key}' is the companion entry
        '{file key}/{file key}', not the object."""
        assert al.names_own_object(FILE_LOCATION, "/" + FILE_LOCATION) is False

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

    @pytest.mark.parametrize("location,key", [
        (FILE_LOCATION, FILE_LOCATION),
        (FILE_LOCATION, "projects/model.glb/sub/x.obj"),
        (FILE_LOCATION, "projects/model.glb.previewFile.png"),
        (FILE_LOCATION, "projects/model.glb/model.glb"),
        (FILE_LOCATION, "projects/model.glb/model.glb.previewFile.png"),
        # A companion entry whose path spells one of the location's own keys
        (FILE_LOCATION, "projects/model.glb/projects/model.glb/x.obj"),
        (FILE_LOCATION, "projects/model.glb/projects/model.glb"),
        (FOLDER_LOCATION, "projects/building-a/sub/m.glb"),
        (FOLDER_LOCATION, "projects/building-a/projects/building-a/m.glb"),
        (FOLDER_LOCATION, FOLDER_LOCATION),
    ])
    def test_object_key_for_path_inverts_relative_path_in_for_every_held_key(self, location, key):
        """The path-only reading (#1855): every key a location holds is named by its own path, so a path
        that spells another key of the location is that companion entry, not the key it spells."""
        assert al.object_key_for_path(location, al.relative_path_in(location, key)) == key

    @pytest.mark.parametrize("location,path,expected", [
        (FILE_LOCATION, "/projects/model.glb/x.obj", "projects/model.glb/projects/model.glb/x.obj"),
        (FILE_LOCATION, "/projects/model.glb", "projects/model.glb/projects/model.glb"),
        (FILE_LOCATION, "/model.glb", FILE_LOCATION),
        (FILE_LOCATION, "/model.glb.previewFile.png", "projects/model.glb.previewFile.png"),
        (FILE_LOCATION, "/sub/x.obj", "projects/model.glb/sub/x.obj"),
        (FOLDER_LOCATION, "/projects/building-a/m.glb", "projects/building-a/projects/building-a/m.glb"),
        (FOLDER_LOCATION, "/sub/m.glb", "projects/building-a/sub/m.glb"),
    ])
    def test_object_key_for_path_never_takes_a_path_as_a_key(self, location, path, expected):
        assert al.object_key_for_path(location, path) == expected

    def test_object_key_for_path_names_nothing_for_the_whole_single_file_asset_or_no_location(self):
        assert al.object_key_for_path(FILE_LOCATION, "/") is None
        assert al.object_key_for_path("", "/model.glb") is None
        assert al.object_key_for_path(None, "/model.glb") is None


# Location shapes a request path is read against (#1943)
REQUEST_LOCATIONS = [
    "A1/",                          # {assetId}/
    "vams/B/",                      # a registration's base prefix
    "proj/site/",                   # a custom root on an existing folder
    "assets/preview/",              # a folder named preview
    FILE_LOCATION,                  # a single file
    "projects/preview/model.glb",   # a single file with a preview segment
    "model.glb",                    # a single file at the bucket root
]


def _request_values(location):
    """Every value form a request can send for a location."""
    return [
        "/x.las", "/sub/x.las", "x.las", "sub/x.las",
        location + "x.las", location + "/x.las", location + "/sub/x.las",
        "/" + location + "x.las", "/" + location + "/x.las", "/" + location,
        location, "/", "", None, "/../x.las", "../x.las", "sub/../../x.las",
        "A10/x.las", "/A10/x.las", location + ".bak", "/" + location + ".bak",
        "model.glb", "/model.glb", "/model.glb.previewFile.png", location + ".previewFile.png",
    ]


@pytest.mark.unit
class TestObjectKeyForRequest:
    """object_key_for_request (#1943): a leading '/' marks the file's path in the asset; a value without one
    is the file's full key when the location holds it, else the path in the asset."""

    @pytest.mark.parametrize("value,expected", [
        # The subfolder named like the asset is a path, the root file's full key is its key
        ("/A1/cloud.las", "A1/A1/cloud.las"),
        ("/cloud.las", "A1/cloud.las"),
        ("A1/cloud.las", "A1/cloud.las"),
        ("A1/A1/cloud.las", "A1/A1/cloud.las"),
        ("cloud.las", "A1/cloud.las"),
        ("sub/cloud.las", "A1/sub/cloud.las"),
        # A neighbour that only shares characters is a path in the asset
        ("A10/x.las", "A1/A10/x.las"),
        ("/A10/x.las", "A1/A10/x.las"),
        # The whole asset
        ("/", "A1/"),
        ("", "A1/"),
        (None, "A1/"),
        ("A1/", "A1/"),
        ("/A1/", "A1/A1/"),
    ])
    def test_an_asset_id_folder(self, value, expected):
        assert al.object_key_for_request("A1/", value) == expected

    @pytest.mark.parametrize("location,value,expected", [
        ("vams/B/", "/vams/B/x.las", "vams/B/vams/B/x.las"),
        ("vams/B/", "vams/B/x.las", "vams/B/x.las"),
        ("vams/B/", "/x.las", "vams/B/x.las"),
        ("vams/B/", "vams/C/x.las", "vams/B/vams/C/x.las"),
        ("proj/site/", "/proj/site/m.glb", "proj/site/proj/site/m.glb"),
        ("proj/site/", "proj/site/m.glb", "proj/site/m.glb"),
        ("proj/site/", "proj/siteOTHER/m.glb", "proj/site/proj/siteOTHER/m.glb"),
        ("assets/preview/", "/assets/preview/x.glb", "assets/preview/assets/preview/x.glb"),
        ("assets/preview/", "/preview/x.glb", "assets/preview/preview/x.glb"),
    ])
    def test_a_prefixed_or_custom_folder(self, location, value, expected):
        assert al.object_key_for_request(location, value) == expected

    @pytest.mark.parametrize("value,expected", [
        ("/model.glb", FILE_LOCATION),
        ("model.glb", FILE_LOCATION),
        (FILE_LOCATION, FILE_LOCATION),
        # A leading '/' never names one of the location's keys: it is a companion entry
        ("/" + FILE_LOCATION, "projects/model.glb/projects/model.glb"),
        ("/" + FILE_LOCATION + "/x.obj", "projects/model.glb/projects/model.glb/x.obj"),
        ("/" + FILE_LOCATION + ".previewFile.png", "projects/model.glb/projects/model.glb.previewFile.png"),
        # Held keys without one are the keys
        ("projects/model.glb/x.obj", "projects/model.glb/x.obj"),
        ("projects/model.glb.previewFile.png", "projects/model.glb.previewFile.png"),
        # Paths
        ("/x.obj", "projects/model.glb/x.obj"),
        ("x.obj", "projects/model.glb/x.obj"),
        ("/model.glb.previewFile.png", "projects/model.glb.previewFile.png"),
        ("/model.glb/model.glb", "projects/model.glb/model.glb"),
        # A neighbour that only shares characters is a companion entry
        ("projects/model.glb.bak", "projects/model.glb/projects/model.glb.bak"),
        ("/model.glb.bak", "projects/model.glb/model.glb.bak"),
        # The whole single-file asset is no single key
        ("/", None),
        ("", None),
        (None, None),
    ])
    def test_a_single_file(self, value, expected):
        assert al.object_key_for_request(FILE_LOCATION, value) == expected

    @pytest.mark.parametrize("location,value,expected", [
        ("projects/preview/model.glb", "/projects/preview/model.glb", "projects/preview/model.glb/projects/preview/model.glb"),
        ("projects/preview/model.glb", "projects/preview/model.glb/preview/x.glb", "projects/preview/model.glb/preview/x.glb"),
        ("projects/preview/model.glb", "/preview/x.glb", "projects/preview/model.glb/preview/x.glb"),
        ("model.glb", "/model.glb", "model.glb"),
        ("model.glb", "model.glb", "model.glb"),
        ("model.glb", "model.glb/x.obj", "model.glb/x.obj"),
        ("model.glb", "/model.glb/x.obj", "model.glb/model.glb/x.obj"),
    ])
    def test_a_single_file_with_a_preview_segment_or_at_the_bucket_root(self, location, value, expected):
        assert al.object_key_for_request(location, value) == expected

    @pytest.mark.parametrize("location,value,expected", [
        # Exactly one leading '/' is the marker: '//x' is the path of the key with an empty segment (L4)
        ("A1/", "//cloud.las", "A1//cloud.las"),
        ("A1/", "///cloud.las", "A1///cloud.las"),
        ("A1/", "//", "A1//"),
        ("vams/B/", "//x.las", "vams/B//x.las"),
        (FILE_LOCATION, "//x.obj", "projects/model.glb//x.obj"),
        (FILE_LOCATION, "/model.glb", FILE_LOCATION),
        ("A1/", "/cloud.las", "A1/cloud.las"),
    ])
    def test_one_leading_slash_is_the_marker(self, location, value, expected):
        assert al.object_key_for_request(location, value) == expected
        assert al.relative_path_in(location, expected) == value

    @pytest.mark.parametrize("location", REQUEST_LOCATIONS)
    def test_every_listed_path_names_its_own_key(self, location):
        """Round trip: the path relative_path_in lists a key at names that key, empty segments included."""
        keys = [location + "x", location + "/x", location + "//x", location + "sub//x", location + "/sub/x"]
        for key in keys:
            path = al.relative_path_in(location, key)
            if path is not None:
                assert al.object_key_for_request(location, path) == key, (location, key, path)
                assert al.object_key_for_path(location, path) == key, (location, key, path)

    @pytest.mark.parametrize("location", REQUEST_LOCATIONS)
    @pytest.mark.parametrize("value", ["/../x.las", "../x.las", "sub/../../x.las", "/sub/.."])
    def test_a_parent_segment_names_nothing(self, location, value):
        assert al.object_key_for_request(location, value) is None

    @pytest.mark.parametrize("location", ["", None, "/"])
    def test_no_location_names_nothing(self, location):
        assert al.object_key_for_request(location, "/x.las") is None
        assert al.object_key_for_request(location, "x.las") is None

    @pytest.mark.parametrize("location", REQUEST_LOCATIONS)
    def test_every_key_it_names_is_held_by_the_location(self, location):
        """Confinement: whatever the value, the key stays inside the requesting asset's location."""
        for value in _request_values(location):
            key = al.object_key_for_request(location, value)
            if key is not None:
                assert al.contains(location, key) or key == location, (location, value, key)

    @pytest.mark.parametrize("location", REQUEST_LOCATIONS)
    def test_a_path_with_a_slash_is_the_path_of_its_key(self, location):
        """A '/'-path names the key whose listed path it is, the inverse of relative_path_in."""
        for value in _request_values(location):
            if not value or not value.startswith("/"):
                continue
            key = al.object_key_for_request(location, value)
            if key is not None:
                assert key == al.object_key_for_path(location, value)

    @pytest.mark.parametrize("location", REQUEST_LOCATIONS)
    def test_a_held_key_without_a_slash_is_that_key(self, location):
        for key in [location + "x.las", location + "/x.las", location + ".previewFile.png"]:
            if al.contains(location, key):
                assert al.object_key_for_request(location, key) == key


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


BUCKET = "vams-assets"


def _row(database_id, asset_id, location, bucket=BUCKET, bucket_id="b1", **extra):
    row = {
        "databaseId": database_id,
        "assetId": asset_id,
        "bucketId": bucket_id,
        "assetLocation": {"Key": location},
        al.LOCATION_BUCKET_ATTRIBUTE: bucket,
        al.LOCATION_KEY_ATTRIBUTE: al.normalize_location(location),
    }
    row.update(extra)
    return row


class FakeLocationTable:
    """An asset table holding `rows` with AssetLocationGSI over them.

    `query` (on `meta.client`) serves the two key conditions the module issues, in the plain values
    a resource table's client takes and returns,
    `page_size` rows a page with a LastEvaluatedKey after each full page. `get_item` serves the
    base table by primary key. Every index query is recorded in `queries`."""

    def __init__(self, rows, page_size=None, get_item_override=None):
        self.name = "test-asset-table"
        self.rows = list(rows)
        self.page_size = page_size
        self.queries = []
        self.get_item_calls = []
        self._lock = threading.Lock()
        self._get_item_override = get_item_override
        self.meta = MagicMock()
        self.meta.client.query.side_effect = self._query

    def _query(self, **kwargs):
        assert kwargs["TableName"] == self.name
        assert kwargs["IndexName"] == al.LOCATION_INDEX_NAME
        values = kwargs["ExpressionAttributeValues"]
        bucket = values[":b"]
        condition = kwargs["KeyConditionExpression"]
        with self._lock:
            self.queries.append(kwargs)
        indexed = [r for r in self.rows
                   if r.get(al.LOCATION_BUCKET_ATTRIBUTE) == bucket and r.get(al.LOCATION_KEY_ATTRIBUTE)]
        if "begins_with" in condition:
            prefix = values[":p"]
            matches = [r for r in indexed if r[al.LOCATION_KEY_ATTRIBUTE].startswith(prefix)]
        else:
            key = values[":k"]
            matches = [r for r in indexed if r[al.LOCATION_KEY_ATTRIBUTE] == key]
        matches.sort(key=lambda r: (r[al.LOCATION_KEY_ATTRIBUTE], r["databaseId"], r["assetId"]))
        start = int(kwargs.get("ExclusiveStartKey", {}).get("offset", 0))
        size = kwargs.get("Limit") or self.page_size or len(matches) or 1
        page = matches[start:start + size]
        response = {"Items": [({k: r[k] for k in ("databaseId", "assetId", "bucketId",
                                                       al.LOCATION_BUCKET_ATTRIBUTE,
                                                       al.LOCATION_KEY_ATTRIBUTE)}) for r in page]}
        if start + size < len(matches) or (self.page_size and len(page) == size and start + size == len(matches)):
            response["LastEvaluatedKey"] = {"offset": start + size}
        return response

    def get_item(self, Key, **kwargs):
        self.get_item_calls.append({"Key": Key, **kwargs})
        if self._get_item_override is not None:
            return self._get_item_override(Key, **kwargs)
        for r in self.rows:
            if r["databaseId"] == Key["databaseId"] and r["assetId"] == Key["assetId"]:
                return {"Item": dict(r)}
        return {}


@pytest.fixture(autouse=True)
def _fresh_caches():
    al.clear_location_cache()
    al.reset_index_ready_cache()
    yield
    al.clear_location_cache()
    al.reset_index_ready_cache()


@pytest.mark.unit
class TestIndexAttributes:
    @pytest.mark.parametrize("key,expected", [
        # each ancestor folder X/ and the single file X whose companion folder it is
        ("a/b/c.glb", ["a/b/c.glb", "a/b/", "a/b", "a/", "a"]),
        # a folder marker is also a single file's companion folder marker
        ("a/b/", ["a/b/", "a/b", "a/", "a"]),
        ("/a/b", ["a/b", "a/", "a"]),
        ("a//b", ["a//b", "a//", "a/", "a"]),
        ("x", ["x"]),
        ("", []),
        # an own preview names its base file
        ("p/m.glb.previewFile.png", ["p/m.glb.previewFile.png", "p/m.glb", "p/", "p"]),
        ("a.previewFile.b.previewFile.c", ["a.previewFile.b.previewFile.c", "a.previewFile.b", "a"]),
        # an extension with a slash is no preview
        ("p/m.glb.previewFile.x/y", ["p/m.glb.previewFile.x/y", "p/m.glb.previewFile.x/",
                                     "p/m.glb.previewFile.x", "p/", "p"]),
        ("p/m.glb/sub/x.obj", ["p/m.glb/sub/x.obj", "p/m.glb/sub/", "p/m.glb/sub", "p/m.glb/",
                               "p/m.glb", "p/", "p"]),
    ])
    def test_candidate_locations_deepest_first(self, key, expected):
        assert al.candidate_locations(key) == expected

    @pytest.mark.parametrize("key", ["a/b/c.glb", "p/m.glb.previewFile.png", "p/m.glb/sub/x.obj", "a/b/"])
    def test_every_candidate_that_could_hold_the_key_is_listed(self, key):
        # Every prefix of the key that holds it as a location is a candidate (the candidates are
        # complete), and every candidate is a prefix of the key.
        candidates = al.candidate_locations(key)
        holders = [key[:i] for i in range(1, len(key) + 1) if al.contains(key[:i], key)]
        assert set(holders) <= set(candidates)
        assert all(key.startswith(c) for c in candidates)

    @pytest.mark.parametrize("prefix,expected", [
        ("", ""), (None, ""), ("/", ""), ("assets", "assets/"), ("assets/", "assets/"), ("/assets/", "assets/"),
        ("/assets", "assets/"), ("a/b", "a/b/"), ("a/b/", "a/b/"),
    ])
    def test_base_prefix_normalizer(self, prefix, expected):
        assert al.normalize_base_prefix(prefix) == expected

    def test_the_create_path_uses_the_one_base_prefix_normalizer(self):
        create_asset = _load_create_asset(fresh=True)
        assert create_asset.normalize_base_prefix is al.normalize_base_prefix
        assert not hasattr(create_asset, "normalize_base_assets_prefix")

    @pytest.mark.parametrize("prefix,expected", [
        ("/", "x1/"), ("", "x1/"), ("assets", "assets/x1/"), ("/assets/", "assets/x1/"),
    ])
    def test_derive_location_is_the_folder_under_the_prefix(self, prefix, expected):
        assert al.derive_location({"baseAssetsPrefix": prefix}, "db1", "x1") == expected

    def test_location_attributes(self):
        assert al.location_attributes(BUCKET, "/a/b/") == {
            al.LOCATION_BUCKET_ATTRIBUTE: BUCKET, al.LOCATION_KEY_ATTRIBUTE: "a/b/"}
        assert al.location_attributes(BUCKET, "p/model.glb") == {
            al.LOCATION_BUCKET_ATTRIBUTE: BUCKET, al.LOCATION_KEY_ATTRIBUTE: "p/model.glb"}
        # An index key cannot be empty: a bucket-root location, or no bucket name, is not indexed.
        assert al.location_attributes(BUCKET, "/") == {}
        assert al.location_attributes(BUCKET, "") == {}
        assert al.location_attributes("", "a/") == {}
        assert al.location_attributes(None, "a/") == {}

    def test_fill_location_attributes_sets_them_on_a_row_without_them(self):
        row = {"databaseId": "db1", "assetId": "a1", "assetLocation": {"Key": "/a1/"}}
        assert al.has_location_attributes(row) is False
        assert al.fill_location_attributes(row, BUCKET) is row
        assert (row[al.LOCATION_BUCKET_ATTRIBUTE], row[al.LOCATION_KEY_ATTRIBUTE]) == (BUCKET, "a1/")
        assert al.has_location_attributes(row) is True

    def test_fill_location_attributes_keeps_attributes_already_on_the_row(self):
        row = _row("db1", "a1", "a1/")
        before = dict(row)
        assert al.fill_location_attributes(row, "another-bucket") == before

    def test_fill_location_attributes_completes_a_row_with_one_of_them(self):
        row = {"assetLocation": {"Key": "a1/"}, al.LOCATION_BUCKET_ATTRIBUTE: BUCKET}
        al.fill_location_attributes(row, BUCKET)
        assert row[al.LOCATION_KEY_ATTRIBUTE] == "a1/"

    @pytest.mark.parametrize("row,bucket_name", [
        ({"assetLocation": {"Key": "/"}}, BUCKET),      # the bucket root holds no key
        ({"assetLocation": {}}, BUCKET),
        ({}, BUCKET),
        ({"assetLocation": "a1/"}, BUCKET),             # not a location map
        ({"assetLocation": {"Key": "a1/"}}, None),      # no registration bucket name
        ({"assetLocation": {"Key": "a1/"}}, ""),
    ])
    def test_fill_location_attributes_invents_nothing(self, row, bucket_name):
        before = dict(row)
        assert al.fill_location_attributes(row, bucket_name) == before


@pytest.mark.unit
class TestResolveOwner:
    def test_folder_location_owns_keys_under_it(self):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")])
        result = al.resolve_owner(table, BUCKET, "projects/alpha/sub/model.glb")
        assert result.status == al.RESOLVED_FOUND
        assert (result.entry.database_id, result.entry.asset_id) == ("db1", "a1")
        assert result.row["assetId"] == "a1"

    def test_one_query_per_candidate(self):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")])
        al.resolve_owner(table, BUCKET, "projects/alpha/sub/model.glb")
        asked = sorted(q["ExpressionAttributeValues"][":k"] for q in table.queries)
        assert asked == sorted(["projects/alpha/sub/model.glb", "projects/alpha/sub/", "projects/alpha/sub",
                                "projects/alpha/", "projects/alpha", "projects/", "projects"])

    @pytest.mark.parametrize("key", [
        "projects/model.glb",                        # the object
        "projects/model.glb/",                       # the companion folder marker
        "projects/model.glb/textures/a.png",         # a companion entry
        "projects/model.glb.previewFile.png",        # an own preview
        "projects/model.glb/sub/n.obj.previewFile.png",  # a companion entry's preview
    ])
    def test_single_file_location_owns_its_object_companion_and_previews(self, key):
        table = FakeLocationTable([_row("db1", "f1", "projects/model.glb")])
        result = al.resolve_owner(table, BUCKET, key)
        assert result.status == al.RESOLVED_FOUND and result.entry.asset_id == "f1"

    @pytest.mark.parametrize("key", [
        "projects/model.glb.bak",
        "projects/model.glbx/a.png",
        "projects/model.glb.previewFile.x/y.png",    # an extension with a slash is no preview
        "projects/other.glb.previewFile.png",
    ])
    def test_single_file_location_owns_nothing_else(self, key):
        table = FakeLocationTable([_row("db1", "f1", "projects/model.glb")])
        assert al.resolve_owner(table, BUCKET, key).status == al.RESOLVED_NOT_FOUND

    def test_a_single_file_inside_a_folder_wins_for_its_companion(self):
        table = FakeLocationTable([
            _row("db1", "outer", "projects/"),
            _row("db2", "file", "projects/model.glb"),
            _row("db3", "inner", "projects/model.glb/sub/n.glb"),
        ])
        assert al.resolve_owner(table, BUCKET, "projects/model.glb/textures/a.png").entry.asset_id == "file"
        assert al.resolve_owner(table, BUCKET, "projects/model.glb.previewFile.png").entry.asset_id == "file"
        assert al.resolve_owner(table, BUCKET,
                                "projects/model.glb/sub/n.glb.previewFile.png").entry.asset_id == "inner"
        assert al.resolve_owner(table, BUCKET, "projects/other.glb").entry.asset_id == "outer"

    def test_a_key_with_too_many_candidates_is_not_resolved(self):
        key = "/".join(f"d{i}" for i in range(al.MAX_CANDIDATE_LOCATIONS)) + "/x.glb"
        table = FakeLocationTable([_row("db1", "a1", "d0/")])
        assert al.resolve_owner(table, BUCKET, key).status == al.RESOLVED_TOO_DEEP
        assert table.queries == []

    def test_path_boundary(self):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")])
        assert al.resolve_owner(table, BUCKET, "projects/alphabet/model.glb").status == al.RESOLVED_NOT_FOUND
        assert table.get_item_calls == []

    def test_single_object_location_owns_exactly_its_key(self):
        table = FakeLocationTable([_row("db1", "f1", "projects/model.glb")])
        hit = al.resolve_owner(table, BUCKET, "projects/model.glb")
        assert hit.status == al.RESOLVED_FOUND and hit.entry.asset_id == "f1"
        assert al.resolve_owner(table, BUCKET, "projects/model.glb.bak").status == al.RESOLVED_NOT_FOUND

    def test_longest_match_wins(self):
        table = FakeLocationTable([
            _row("db1", "outer", "projects/"),
            _row("db2", "inner", "projects/alpha/"),
        ])
        assert al.resolve_owner(table, BUCKET, "projects/alpha/x.glb").entry.asset_id == "inner"
        assert al.resolve_owner(table, BUCKET, "projects/beta/x.glb").entry.asset_id == "outer"

    def test_other_bucket_is_never_matched(self):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/", bucket="other-bucket")])
        assert al.resolve_owner(table, BUCKET, "projects/alpha/x.glb").status == al.RESOLVED_NOT_FOUND

    def test_two_live_rows_on_one_location_are_ambiguous(self):
        table = FakeLocationTable([
            _row("db1", "a1", "projects/shared/"),
            _row("db2", "a2", "projects/shared/"),
        ])
        result = al.resolve_owner(table, BUCKET, "projects/shared/x.glb")
        assert result.status == al.RESOLVED_AMBIGUOUS
        assert result.entry is None and result.row is None
        assert {e.asset_id for e in result.candidates} == {"a1", "a2"}
        assert table.get_item_calls == []

    def test_archived_rows_count_only_when_asked(self):
        table = FakeLocationTable([_row("db1#deleted", "a1", "projects/old/", status="archived")])
        assert al.resolve_owner(table, BUCKET, "projects/old/x.glb").status == al.RESOLVED_NOT_FOUND
        result = al.resolve_owner(table, BUCKET, "projects/old/x.glb", include_archived=True)
        assert result.status == al.RESOLVED_FOUND
        assert result.entry.archived is True

    def test_an_archived_row_does_not_shadow_a_live_ancestor(self):
        table = FakeLocationTable([
            _row("db1", "outer", "projects/"),
            _row("db1#deleted", "inner", "projects/old/", status="archived"),
        ])
        assert al.resolve_owner(table, BUCKET, "projects/old/x.glb").entry.asset_id == "outer"
        assert al.resolve_owner(table, BUCKET, "projects/old/x.glb",
                                include_archived=True).entry.asset_id == "inner"

    def test_live_row_wins_over_archived_copy_on_the_same_location(self):
        # An archive writes the #deleted row before it deletes the live one.
        table = FakeLocationTable([
            _row("db1", "a1", "projects/alpha/"),
            _row("db1#deleted", "a1", "projects/alpha/", status="archived"),
        ])
        result = al.resolve_owner(table, BUCKET, "projects/alpha/x.glb", include_archived=True)
        assert result.status == al.RESOLVED_FOUND
        assert result.entry.database_id == "db1"

    def test_hit_is_confirmed_strongly_consistent(self):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")])
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        assert table.get_item_calls == [
            {"Key": {"databaseId": "db1", "assetId": "a1"}, "ConsistentRead": True}]

    def test_unconfirmed_hit_is_not_found_and_leaves_the_cache(self):
        rows = [_row("db1", "a1", "projects/alpha/")]
        table = FakeLocationTable(rows, get_item_override=lambda Key, **_: {})
        assert al.resolve_owner(table, BUCKET, "projects/alpha/x.glb").status == al.RESOLVED_NOT_FOUND
        queries_before = len(table.queries)
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        # The deepest-hit lookup was dropped from the cache, so it is read again.
        asked_again = [q["ExpressionAttributeValues"][":k"] for q in table.queries[queries_before:]]
        assert asked_again == ["projects/alpha/"]

    def test_row_that_moved_location_is_not_confirmed(self):
        moved = _row("db1", "a1", "projects/alpha/")
        moved[al.LOCATION_KEY_ATTRIBUTE] = "projects/other/"
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")],
                                  get_item_override=lambda Key, **_: {"Item": moved})
        assert al.resolve_owner(table, BUCKET, "projects/alpha/x.glb").status == al.RESOLVED_NOT_FOUND

    def test_archived_status_on_the_confirmed_row_is_honoured(self):
        live_key_but_archived = _row("db1", "a1", "projects/alpha/", status="archived")
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")],
                                  get_item_override=lambda Key, **_: {"Item": live_key_but_archived})
        assert al.resolve_owner(table, BUCKET, "projects/alpha/x.glb").status == al.RESOLVED_NOT_FOUND
        assert al.resolve_owner(table, BUCKET, "projects/alpha/x.glb",
                                include_archived=True).status == al.RESOLVED_FOUND

    def test_exact_lookup_pages_on_key_presence(self):
        # Three rows on one location, one per page: the lookup reads every page before it decides.
        rows = [_row("db1", "a1", "projects/alpha/"),
                _row("db1#deleted", "a2", "projects/alpha/", status="archived"),
                _row("db2", "a3", "projects/alpha/")]
        table = FakeLocationTable(rows, page_size=1)
        result = al.resolve_owner(table, BUCKET, "projects/alpha/", include_archived=True)
        assert result.status == al.RESOLVED_AMBIGUOUS
        assert {e.asset_id for e in result.candidates} == {"a1", "a3"}

    def test_empty_bucket_or_key_reads_nothing(self):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")])
        assert al.resolve_owner(table, "", "projects/alpha/x").status == al.RESOLVED_NOT_FOUND
        assert al.resolve_owner(table, BUCKET, "").status == al.RESOLVED_NOT_FOUND
        assert table.queries == []


@pytest.mark.unit
class TestResolveOwnerCache:
    @pytest.fixture
    def clock(self, monkeypatch):
        now = {"t": 1000.0}
        monkeypatch.setattr(al.time, "monotonic", lambda: now["t"])
        return now

    def test_hits_are_cached_for_sixty_seconds(self, clock):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")])
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        first = len(table.queries)
        clock["t"] += al.LOOKUP_HIT_TTL_SECONDS - 1
        al.resolve_owner(table, BUCKET, "projects/alpha/y.glb")
        # The hit on the owning folder came from the cache; the new key and the expired misses were
        # read again.
        assert sorted(q["ExpressionAttributeValues"][":k"] for q in table.queries[first:]) == [
            "projects", "projects/", "projects/alpha", "projects/alpha/y.glb"]
        # The confirmation runs on every call.
        assert len(table.get_item_calls) == 2

    def test_misses_are_cached_for_ten_seconds(self, clock):
        table = FakeLocationTable([])
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        first = len(table.queries)
        clock["t"] += al.LOOKUP_MISS_TTL_SECONDS - 1
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        assert len(table.queries) == first
        clock["t"] += 2
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        assert len(table.queries) == 2 * first

    def test_a_miss_expires_before_a_hit(self, clock):
        table = FakeLocationTable([])
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        table.rows.append(_row("db1", "a1", "projects/alpha/"))
        clock["t"] += al.LOOKUP_MISS_TTL_SECONDS + 1
        assert al.resolve_owner(table, BUCKET, "projects/alpha/x.glb").status == al.RESOLVED_FOUND

    def test_hit_expires_after_sixty_seconds(self, clock):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")])
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        first = len(table.queries)
        clock["t"] += al.LOOKUP_HIT_TTL_SECONDS + 1
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        assert len(table.queries) == 2 * first

    def test_uncached_resolution_reads_every_candidate(self, clock):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/")])
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb")
        first = len(table.queries)
        al.resolve_owner(table, BUCKET, "projects/alpha/x.glb", use_cache=False)
        assert len(table.queries) == 2 * first

    def test_cache_is_bounded(self, clock, monkeypatch):
        monkeypatch.setattr(al, "LOOKUP_CACHE_MAX_ENTRIES", 5)
        table = FakeLocationTable([])
        for i in range(20):
            al.resolve_owner(table, BUCKET, f"f{i}/x")
        assert len(al._lookup_cache) <= 5


@pytest.mark.unit
class TestFindConflicts:
    def test_no_rows_no_conflict(self):
        assert al.find_conflicts(FakeLocationTable([]), BUCKET, "projects/alpha/") == []

    @pytest.mark.parametrize("existing,target", [
        ("projects/alpha/", "projects/alpha/"),         # equal
        ("projects/", "projects/alpha/"),               # ancestor
        ("projects/alpha/deep/", "projects/alpha/"),    # descendant
        ("projects/alpha/model.glb", "projects/alpha/"),  # a single object under the folder
        ("projects/alpha", "projects/alpha/"),          # the same name as an object
        ("projects/model.glb/", "projects/model.glb"),  # a folder named like the object
        ("projects", "projects/alpha/"),                # an object named like an ancestor folder
        # a new single-file location
        ("projects/", "projects/model.glb"),                  # a folder above it
        ("projects/model.glb", "projects/model.glb"),          # the same single file
        ("projects/model.glb/sub/", "projects/model.glb"),     # a folder in its companion folder
        ("projects/model.glb/sub/n.glb", "projects/model.glb"),  # a single file in its companion folder
        ("projects/model.glb.previewFile.png", "projects/model.glb"),  # a single file at its own preview
        ("projects/model.glb", "projects/model.glb.previewFile.png"),  # at the existing file's preview
        ("projects/model.glb", "projects/model.glb/sub/n.glb"),  # in the existing file's companion
        # a new folder location
        ("projects/model.glb", "projects/"),                  # a single file under it
        ("projects/model.glb", "projects/model.glb/sub/"),     # inside a single file's companion
        ("projects/sub/model.glb", "projects/sub/"),           # a single file whose companion is under it
        # two single files whose preview stems share keys that neither location names (L2, D8)
        ("a/x", "a/x.previewFile"),
        ("a/x.previewFile", "a/x"),
        ("a/x", "a/x.previewFile."),
        ("a/x.previewFile.", "a/x"),
        ("a/x.previewFile", "a/x.previewFile.previewFile"),
    ])
    def test_conflict(self, existing, target):
        table = FakeLocationTable([_row("db1", "a1", existing)])
        conflicts = al.find_conflicts(table, BUCKET, target)
        assert [c.asset_id for c in conflicts] == ["a1"]

    @pytest.mark.parametrize("existing,target", [
        ("projects/alphabet/", "projects/alpha/"),
        ("projects/alpha/", "projects/alphabet/"),
        ("projects/model.glb.bak", "projects/model.glb"),
        ("projects/model.glb", "projects/model.glb.bak"),
        ("other/alpha/", "projects/alpha/"),
        ("projects/model.glbx/", "projects/model.glb"),
        ("projects/model.glb", "projects/model.glbx/"),
        ("projects/model.glb.previewFile.x/", "projects/model.glb"),  # a slash: no preview
        ("projects/model.glb", "projects/model.glb.previewFile.x/"),
        ("projects/model.glb.previewFile.png", "projects/other.glb"),
    ])
    def test_no_conflict(self, existing, target):
        table = FakeLocationTable([_row("db1", "a1", existing)])
        assert al.find_conflicts(table, BUCKET, target) == []

    def test_archived_rows_conflict_too(self):
        table = FakeLocationTable([_row("db1#deleted", "a1", "projects/alpha/", status="archived")])
        assert [c.archived for c in al.find_conflicts(table, BUCKET, "projects/alpha/sub/")] == [True]

    def test_other_bucket_does_not_conflict(self):
        table = FakeLocationTable([_row("db1", "a1", "projects/alpha/", bucket="other")])
        assert al.find_conflicts(table, BUCKET, "projects/alpha/") == []

    def test_descendant_lookup_reads_one_row(self):
        table = FakeLocationTable([_row("db1", f"a{i}", f"projects/alpha/{i}/") for i in range(5)])
        conflicts = al.find_conflicts(table, BUCKET, "projects/alpha/")
        assert len(conflicts) == 1
        descendant = [q for q in table.queries if "begins_with" in q["KeyConditionExpression"]]
        assert len(descendant) == 1 and descendant[0]["Limit"] == 1

    def test_descendant_lookup_pages_past_an_empty_page(self):
        # A page may come back empty and still carry a key; the lookup continues from it.
        row = ({"databaseId": "db1", "assetId": "a1", "bucketId": "b1",
                     al.LOCATION_BUCKET_ATTRIBUTE: BUCKET,
                     al.LOCATION_KEY_ATTRIBUTE: "projects/alpha/deep/"})
        descendants = Pager({"Items": [], "LastEvaluatedKey": {"k": "c1"}},
                            {"Items": [row]}, name="descendant lookup")
        exact = FakeLocationTable([])

        def query(**kwargs):
            if "begins_with" in kwargs["KeyConditionExpression"]:
                return descendants(**kwargs)
            return exact._query(**kwargs)

        table = FakeLocationTable([])
        table.meta.client.query.side_effect = query
        conflicts = al.find_conflicts(table, BUCKET, "projects/alpha/")
        assert [c.asset_id for c in conflicts] == ["a1"]
        descendants.assert_paged_to_exhaustion()

    def test_descendant_lookup_that_never_ends_is_an_error(self):
        endless = Pager(*[{"Items": [], "LastEvaluatedKey": {"k": f"c{i}"}} for i in range(10)],
                        name="endless descendant lookup", max_reads=20)
        exact = FakeLocationTable([])

        def query(**kwargs):
            if "begins_with" in kwargs["KeyConditionExpression"]:
                return endless(**kwargs)
            return exact._query(**kwargs)

        table = FakeLocationTable([])
        table.meta.client.query.side_effect = query
        with pytest.raises(RuntimeError):
            al.find_conflicts(table, BUCKET, "projects/alpha/", max_descendant_pages=3)

    def test_preview_lookup_pages_past_locations_that_are_no_previews(self):
        # Folder locations named like previews sort before the real preview and are not previews.
        rows = [_row("db1", f"n{i:02d}", f"projects/model.glb.previewFile.a{i:02d}/") for i in range(30)]
        rows.append(_row("db2", "preview", "projects/model.glb.previewFile.png"))
        table = FakeLocationTable(rows)
        conflicts = al.find_conflicts(table, BUCKET, "projects/model.glb")
        assert [c.asset_id for c in conflicts] == ["preview"]
        preview_reads = [q for q in table.queries
                         if q["ExpressionAttributeValues"].get(":p") == "projects/model.glb.previewFile."]
        assert len(preview_reads) >= 2
        assert {q["Limit"] for q in preview_reads} == {al.PREVIEW_LOOKUP_PAGE_SIZE}

    def test_a_location_with_too_many_candidates_is_refused(self):
        location = "/".join(f"d{i}" for i in range(al.MAX_CANDIDATE_LOCATIONS)) + "/"
        with pytest.raises(al.LocationTooDeepError):
            al.find_conflicts(FakeLocationTable([]), BUCKET, location)

    GRID = ["projects/", "projects/alpha/", "projects/alpha/deep/", "projects/alphabet/",
            "projects/model.glb", "projects/model.glb/", "projects/model.glb/sub/",
            "projects/model.glb/sub/n.glb", "projects/model.glb/model.glb", "projects/model.glb.previewFile.png",
            "projects/model.glb.previewFile.x/", "projects/model.glb.bak", "projects/model.glbx/",
            "projects/beta", "projects/beta/", "projects", "shared/x/", "shared/x/y.glb", "z", "zz/",
            # names ending in `.previewFile`: they share a preview key with `a/x` without holding it
            "a/x", "a/x.previewFile", "a/x.previewFile.", "projects/model.glb.previewFile"]

    def test_the_grid_holds_pairs_that_share_a_key_without_either_containing_the_other(self):
        # The shared-key rule is not "one contains the other": `a/x` and `a/x.previewFile` both hold
        # `a/x.previewFile.previewFile.z`. Brute force over keys built from both locations.
        not_containing = []
        for a in self.GRID:
            for b in self.GRID:
                assert (a, b, _brute_force_share(a, b)) == (a, b, al.locations_share_key(a, b))
                if al.locations_share_key(a, b) and not (al.contains(a, b) or al.contains(b, a)):
                    not_containing.append((a, b))
        assert {("a/x", "a/x.previewFile"), ("a/x", "a/x.previewFile."),
                ("projects/model.glb", "projects/model.glb.previewFile")} <= set(not_containing)

    def test_matches_the_shared_key_rule_on_every_pair(self):
        for existing in self.GRID:
            table = FakeLocationTable([_row("db1", "a1", existing)])
            for target in self.GRID:
                found = bool(al.find_conflicts(table, BUCKET, target))
                assert (existing, target, found) == (existing, target, al.locations_share_key(existing, target))

    @pytest.mark.parametrize("existing", GRID_LOCATIONS)
    def test_matches_the_shared_key_rule_on_the_overlap_grid(self, existing):
        # The grid locations_share_key is checked on (names ending in or containing `.previewFile`).
        table = FakeLocationTable([_row("db1", "a1", existing)])
        wrong = [target for target in GRID_LOCATIONS
                 if bool(al.find_conflicts(table, BUCKET, target)) is not al.locations_share_key(existing, target)]
        assert wrong == []

    def test_agrees_with_the_create_paths_ownership_rule_on_every_pair(self):
        # createAsset.keys_conflict decides the same shared-key rule on the stored rows it walks; the
        # index lookup must answer the same for every pair, the single-file preview pairs included.
        keys_conflict = _load_create_asset(fresh=True).keys_conflict
        compared = 0
        for existing in self.GRID:
            table = FakeLocationTable([_row("db1", "a1", existing)])
            for target in self.GRID:
                found = bool(al.find_conflicts(table, BUCKET, target))
                assert (existing, target, found) == (existing, target, keys_conflict(existing, target))
                compared += 1
        assert compared == len(self.GRID) ** 2
        assert keys_conflict("projects/model.glb", "projects/model.glb.previewFile.png")


class RecordingLogger:
    """Records (level, message) for every call, at every level safeLogger exposes."""

    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        if level in ("debug", "info", "warning", "warn", "error", "critical", "exception"):
            return lambda message, *args, **kwargs: self.lines.append((level, str(message)))
        raise AttributeError(level)

    def at(self, *levels):
        return [message for level, message in self.lines if level in levels]


@pytest.mark.unit
class TestIndexReady:
    PREFIX = "/vams-test/resourceNames"
    NAME = "/vams-test/resourceNames/dataMigrations/assetLocationIndex"
    TABLE = "vams-core-AssetStorageTable-ABC123"

    @pytest.fixture(autouse=True)
    def _prefix(self, monkeypatch):
        monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", self.PREFIX)

    def _ssm(self, value=None, error_code=None):
        """A reader for index_ready: a private copy of common.ssmParameters' uncached read on a fake
        clock, over a stub Parameter Store that answers `value` for the marker or raises `error_code`
        on every call. The stub is the reader's `stub` attribute."""
        stub = ParameterStoreStub(
            {} if value is None else {self.NAME: value},
            fault=always(lambda: client_error(error_code)) if error_code else None)
        helper = load_ssm_parameters(stub)
        install_clock(helper, FakeClock())
        read = helper.read_parameter_value
        return types.SimpleNamespace(read=read, stub=stub)

    def _complete(self, **extra):
        return json.dumps({"version": 1, "status": "complete", "assetTable": self.TABLE,
                           "completedAt": "t", **extra})

    def test_parameter_name(self):
        assert al.index_ready_parameter_name() == self.NAME
        assert al.index_ready_parameter_name("/other/resourceNames/") == (
            "/other/resourceNames/dataMigrations/assetLocationIndex")

    def test_complete_marker_for_this_table_is_ready(self):
        ssm = self._ssm(self._complete())
        assert al.index_ready(self.TABLE, read_parameter=ssm.read) is True
        assert set(ssm.stub.calls) == {self.NAME}

    def test_marker_of_another_table_is_not_ready(self):
        # A deployment removed and created again under the same names keeps the old parameter.
        assert al.index_ready("vams-core-AssetStorageTable-NEW999",
                              read_parameter=self._ssm(self._complete()).read) is False

    @pytest.mark.parametrize("value", [
        json.dumps({"version": 1, "status": "running", "assetTable": TABLE}),
        json.dumps({"version": 2, "status": "complete", "assetTable": TABLE}),
        json.dumps({"version": 1, "status": "complete"}),
        json.dumps(["complete"]),
        "complete",
        "",
    ])
    def test_anything_else_is_not_ready(self, value):
        assert al.index_ready(self.TABLE, read_parameter=self._ssm(value).read) is False

    @pytest.mark.parametrize("code", ["ParameterNotFound", "AccessDeniedException", "ThrottlingException"])
    def test_an_unreadable_marker_is_not_ready(self, code, monkeypatch):
        logged = RecordingLogger()
        monkeypatch.setattr(al, "logger", logged)
        assert al.index_ready(self.TABLE, read_parameter=self._ssm(error_code=code).read) is False
        expected = [] if code == "ParameterNotFound" else [
            f"Location index marker {self.NAME} could not be read ({code})" "; the index is treated as not ready"]
        assert logged.at("warning") == expected

    def test_no_prefix_or_no_table_is_not_ready(self, monkeypatch):
        ssm = self._ssm(self._complete())
        assert al.index_ready("", read_parameter=ssm.read) is False
        monkeypatch.delenv("VAMS_RESOURCE_PARAM_PREFIX")
        assert al.index_ready(self.TABLE, read_parameter=ssm.read) is False
        assert ssm.stub.calls == []

    def test_answer_is_cached_both_ways(self):
        now = {"t": 100.0}
        missing = self._ssm(error_code="ParameterNotFound")
        assert al.index_ready(self.TABLE, read_parameter=missing.read, clock=lambda: now["t"]) is False
        complete = self._ssm(self._complete())
        now["t"] += al.INDEX_READY_CACHE_TTL_SECONDS - 1
        assert al.index_ready(self.TABLE, read_parameter=complete.read, clock=lambda: now["t"]) is False
        assert complete.stub.calls == []
        now["t"] += 2
        assert al.index_ready(self.TABLE, read_parameter=complete.read, clock=lambda: now["t"]) is True
        now["t"] += al.INDEX_READY_CACHE_TTL_SECONDS - 1
        assert al.index_ready(self.TABLE, read_parameter=missing.read, clock=lambda: now["t"]) is True


@pytest.mark.unit
class TestIndexReadyReadsThroughTheBoundedLookup:
    """#1820: the marker is read with common.ssmParameters.read_parameter_value(), the bounded,
    uncached read, not a client of this module's own with five adaptive attempts (about 30 s under
    throttling). Driven through the default reader, with boto3.client recorded, so the client the
    read actually used is what is checked."""

    NAME = TestIndexReady.NAME
    TABLE = TestIndexReady.TABLE

    @pytest.fixture
    def parameter_store(self, monkeypatch):
        """Records every SSM client built (with its config) and serves `stub`; the helper this test's
        index_ready reads through runs on a fake clock and logs to a recorder."""
        monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", TestIndexReady.PREFIX)
        # A client a previous index_ready of this module copy may have cached (the code before #1820).
        monkeypatch.setattr(al, "_ssm_client", None, raising=False)
        helper = sys.modules["common.ssmParameters"]
        clock = install_clock(helper, FakeClock(), monkeypatch)
        state = types.SimpleNamespace(stub=ParameterStoreStub(), configs=[], clock=clock, helper=helper,
                                      al_logger=RecordingLogger(), helper_logger=RecordingLogger())

        def client(service, *args, **kwargs):
            if service != "ssm":
                return MagicMock()
            state.configs.append(kwargs.get("config"))
            return state.stub

        monkeypatch.setattr(boto3, "client", client)
        monkeypatch.setattr(al, "logger", state.al_logger)
        monkeypatch.setattr(helper, "logger", state.helper_logger)
        return state

    def test_a_throttled_marker_read_answers_false_within_the_read_budget(self, parameter_store):
        state = parameter_store
        state.stub.fault = always(lambda: client_error("ThrottlingException"))
        started = state.clock.now

        assert al.index_ready(self.TABLE) is False

        assert len(state.configs) == 1, f"{len(state.configs)} SSM clients built for one read"
        resolved = botocore.session.get_session().create_client(
            "ssm", region_name="us-east-1", config=state.configs[0]).meta.config
        assert (resolved.connect_timeout, resolved.read_timeout, resolved.retries.get("total_max_attempts")) == (
            1, 2, 1), "the marker was read with a client of its own (five adaptive attempts, 60 s timeouts)"
        assert len(state.stub.calls) > 1, "the throttled read was never retried"
        assert state.clock.now - started <= state.helper.PARAMETER_READ_BUDGET_SECONDS
        assert state.al_logger.at("warning") == [
            f"Location index marker {self.NAME} could not be read (ThrottlingException)"
            "; the index is treated as not ready"]

        # The container now backs off: another table's check reads nothing and answers False.
        calls = len(state.stub.calls)
        assert al.index_ready("vams-core-AssetStorageTable-OTHER") is False
        assert len(state.stub.calls) <= calls, "a check inside the back-off window read Parameter Store"

    def test_a_missing_marker_answers_false_with_no_warning(self, parameter_store):
        state = parameter_store

        assert al.index_ready(self.TABLE) is False

        assert set(state.stub.calls) == {self.NAME}
        assert len(state.stub.calls) <= 1, "a missing marker was read more than once"
        assert state.al_logger.at("warning", "error") == []
        assert state.helper_logger.at("warning", "error") == [], "a marker not written yet is logged as a problem"
        # Answered from this module's 300 s cache: no second read.
        assert al.index_ready(self.TABLE) is False
        assert len(state.stub.calls) <= 1, "the cached answer was read again"

    def test_the_marker_is_read_uncached_once_the_answer_expires(self, parameter_store):
        state = parameter_store
        state.stub.values[self.NAME] = json.dumps({"version": 1, "status": "running", "assetTable": self.TABLE})
        now = {"t": 100.0}
        assert al.index_ready(self.TABLE, clock=lambda: now["t"]) is False
        state.stub.values[self.NAME] = json.dumps({"version": 1, "status": "complete", "assetTable": self.TABLE})
        now["t"] += al.INDEX_READY_CACHE_TTL_SECONDS + 1
        assert al.index_ready(self.TABLE, clock=lambda: now["t"]) is True
        assert set(state.stub.calls) == {self.NAME}
        assert 1 < len(state.stub.calls) <= 2, "the expired answer was not read again exactly once"


@pytest.mark.unit
class TestIndexForAFileNamedLikeAPreview:
    """A single-file location named like a preview (`scan.previewFile.png`) owns its own key, its companion
    folder and its own previews, also when a single file `scan` beside it would claim the key as its preview:
    the deeper location wins."""

    LOCATION = "projects/scan.previewFile.png"

    @pytest.mark.parametrize("key", [
        "projects/scan.previewFile.png",
        "projects/scan.previewFile.png/sub/x.obj",
        "projects/scan.previewFile.png.previewFile.gif",
    ])
    def test_its_keys_resolve_to_it(self, key):
        table = FakeLocationTable([_row("db1", "base", "projects/scan"), _row("db2", "named", self.LOCATION)])
        assert al.resolve_owner(table, BUCKET, key).entry.asset_id == "named"

    def test_conflicts_with_the_file_whose_preview_it_is_named_like(self):
        table = FakeLocationTable([_row("db1", "base", "projects/scan")])
        assert [c.asset_id for c in al.find_conflicts(table, BUCKET, self.LOCATION)] == ["base"]


@pytest.mark.unit
class TestARegistrationRootAsset:
    """An asset whose location is its bucket registration's own prefix (created before v2.6.1, which
    refuses it) is indexed like any other folder location: it holds every key under the prefix."""

    ROOT = "assets/"

    def test_its_row_carries_the_index_attributes(self):
        assert al.location_attributes(BUCKET, self.ROOT) == {
            al.LOCATION_BUCKET_ATTRIBUTE: BUCKET, al.LOCATION_KEY_ATTRIBUTE: self.ROOT}
        assert al.location_attributes(BUCKET, "/assets/") == al.location_attributes(BUCKET, self.ROOT)

    def test_it_owns_a_key_no_deeper_asset_holds(self):
        table = FakeLocationTable([_row("db1", "legacy-root", self.ROOT), _row("db1", "x1", "assets/x1/")])
        assert al.resolve_owner(table, BUCKET, "assets/x2/model.glb").entry.asset_id == "legacy-root"
        assert al.resolve_owner(table, BUCKET, "assets/x1/model.glb").entry.asset_id == "x1"

    def test_a_new_location_under_it_conflicts(self):
        table = FakeLocationTable([_row("db1", "legacy-root", self.ROOT)])
        assert [c.asset_id for c in al.find_conflicts(table, BUCKET, "assets/new/")] == ["legacy-root"]
        assert [c.asset_id for c in al.find_conflicts(table, BUCKET, "assets/new/model.glb")] == ["legacy-root"]
