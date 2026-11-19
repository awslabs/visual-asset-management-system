# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.assetLocations: what an asset's stored location key covers.

A location ending in '/' is a folder and holds every key under it. Any other location is a single-file
asset: it holds its own object, everything under '{file key}/' (its companion folder) and its own
previews '{file key}.previewFile.{ext}'. A key that merely starts with the same characters (a sibling
such as 'model.glb.bak', or 'model.glbx/...') is not part of it. The module is the REAL one, registered
by tests/conftest.py.
"""

import json
import sys
import threading
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.tests.pagingStub import Pager
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
        ("a/b/c.glb", ["a/b/c.glb", "a/b/", "a/"]),
        ("a/b/", ["a/b/", "a/"]),
        ("/a/b", ["a/b", "a/"]),
        ("x", ["x"]),
        ("", []),
    ])
    def test_candidate_locations_deepest_first(self, key, expected):
        assert al.candidate_locations(key) == expected

    @pytest.mark.parametrize("prefix", ["", "/", "assets", "assets/", "/assets/", "/assets", "a/b", "a/b/"])
    def test_base_prefix_normalizer_matches_the_create_path(self, prefix):
        create_asset = _load_create_asset(fresh=True)
        assert al.normalize_base_prefix(prefix) == create_asset.normalize_base_assets_prefix(prefix)

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
        assert asked == sorted(["projects/alpha/sub/model.glb", "projects/alpha/sub/",
                                "projects/alpha/", "projects/"])

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
        # The hit on the owning folder came from the cache; the new key and the expired miss on
        # `projects/` were read again.
        assert sorted(q["ExpressionAttributeValues"][":k"] for q in table.queries[first:]) == [
            "projects/", "projects/alpha/y.glb"]
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

    def test_matches_the_create_paths_ownership_rule(self):
        # find_conflicts is non-empty exactly when createAsset.keys_conflict flags a stored row, and
        # every ancestor or equal row it flags is returned.
        keys_conflict = _load_create_asset(fresh=True).keys_conflict
        stored = ["projects/", "projects/alpha/", "projects/alpha/deep/", "projects/model.glb",
                  "projects/beta", "shared/x/", "z"]
        targets = ["projects/alpha/", "projects/alpha/deep/more/", "projects/alphabet/",
                   "projects/model.glb", "projects/model.glb.bak", "projects/model.glb/",
                   "projects/beta/", "shared/", "shared/x/y.glb", "z/", "zz/", "new/"]
        for existing in stored:
            table = FakeLocationTable([_row("db1", "a1", existing)])
            for target in targets:
                expected = keys_conflict(existing, target)
                found = bool(al.find_conflicts(table, BUCKET, target))
                assert (existing, target, found) == (existing, target, expected)


@pytest.mark.unit
class TestIndexReady:
    PREFIX = "/vams-test/resourceNames"
    NAME = "/vams-test/resourceNames/dataMigrations/assetLocationIndex"
    TABLE = "vams-core-AssetStorageTable-ABC123"

    @pytest.fixture(autouse=True)
    def _prefix(self, monkeypatch):
        monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", self.PREFIX)

    @staticmethod
    def _ssm(value=None, error_code=None):
        ssm = MagicMock()
        if error_code:
            ssm.get_parameter.side_effect = ClientError({"Error": {"Code": error_code}}, "GetParameter")
        else:
            ssm.get_parameter.return_value = {"Parameter": {"Value": value}}
        return ssm

    def _complete(self, **extra):
        return json.dumps({"version": 1, "status": "complete", "assetTable": self.TABLE,
                           "completedAt": "t", **extra})

    def test_parameter_name(self):
        assert al.index_ready_parameter_name() == self.NAME
        assert al.index_ready_parameter_name("/other/resourceNames/") == (
            "/other/resourceNames/dataMigrations/assetLocationIndex")

    def test_complete_marker_for_this_table_is_ready(self):
        ssm = self._ssm(self._complete())
        assert al.index_ready(self.TABLE, ssm_client=ssm) is True
        ssm.get_parameter.assert_called_once_with(Name=self.NAME)

    def test_marker_of_another_table_is_not_ready(self):
        # A deployment removed and created again under the same names keeps the old parameter.
        assert al.index_ready("vams-core-AssetStorageTable-NEW999",
                              ssm_client=self._ssm(self._complete())) is False

    @pytest.mark.parametrize("value", [
        json.dumps({"version": 1, "status": "running", "assetTable": TABLE}),
        json.dumps({"version": 2, "status": "complete", "assetTable": TABLE}),
        json.dumps({"version": 1, "status": "complete"}),
        json.dumps(["complete"]),
        "complete",
        "",
    ])
    def test_anything_else_is_not_ready(self, value):
        assert al.index_ready(self.TABLE, ssm_client=self._ssm(value)) is False

    @pytest.mark.parametrize("code", ["ParameterNotFound", "AccessDeniedException", "ThrottlingException"])
    def test_an_unreadable_marker_is_not_ready(self, code):
        assert al.index_ready(self.TABLE, ssm_client=self._ssm(error_code=code)) is False

    def test_no_prefix_or_no_table_is_not_ready(self, monkeypatch):
        ssm = self._ssm(self._complete())
        assert al.index_ready("", ssm_client=ssm) is False
        monkeypatch.delenv("VAMS_RESOURCE_PARAM_PREFIX")
        assert al.index_ready(self.TABLE, ssm_client=ssm) is False
        ssm.get_parameter.assert_not_called()

    def test_answer_is_cached_both_ways(self):
        now = {"t": 100.0}
        missing = self._ssm(error_code="ParameterNotFound")
        assert al.index_ready(self.TABLE, ssm_client=missing, clock=lambda: now["t"]) is False
        complete = self._ssm(self._complete())
        now["t"] += al.INDEX_READY_CACHE_TTL_SECONDS - 1
        assert al.index_ready(self.TABLE, ssm_client=complete, clock=lambda: now["t"]) is False
        complete.get_parameter.assert_not_called()
        now["t"] += 2
        assert al.index_ready(self.TABLE, ssm_client=complete, clock=lambda: now["t"]) is True
        now["t"] += al.INDEX_READY_CACHE_TTL_SECONDS - 1
        assert al.index_ready(self.TABLE, ssm_client=missing, clock=lambda: now["t"]) is True
