# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /search/nlp: the window signal of a natural-language answer does not depend on records the caller
cannot read.

A database the caller's access covers only in part holds records the caller may not open, and access is
checked after the closest matches are chosen. Such a database is searched with the largest window
whatever ``size``, and its window is always reported as possibly incomplete: ``nlp.truncated`` true,
``hits.total.relation`` ``gte`` and the ``truncated:window`` warning, never ``segments:window_full``.
Only the windows of databases the caller reads in full are checked for filling, as before. So for a
caller of partial access the whole answer, signals, counts, totals and aggregations included, is the same
for two stores that differ only in records the caller cannot read, at every ``size``.

Uses the behaviour suite's fixture and fakes, so the handler under test is the real module loaded by path.
"""

import pytest

from tests.handlers.osVectorSearch import test_vectorSearchService as behaviour
from tests.handlers.osVectorSearch.test_vectorSearchService import FakeStore, call, chunk, item, rows

svc = behaviour.svc

SIZES = (1, 2, 3, 5, 25, 100)


def _line(effect, *criteria):
    return {"effect": effect, "criteriaAnd": [{"field": f, "operator": o, "value": v} for f, o, v in criteria],
            "criteriaOr": []}


# A role that may open the assets of db1 whose name starts with "pub" (and nothing else)
PUBLIC_ASSETS_OF_DB1 = [_line("allow", ("databaseId", "equals", "db1"), ("assetName", "starts_with", "pub"))]
# A role that may open every live asset of db1 (a database-scoped role)
EVERY_ASSET_OF_DB1 = [_line("allow", ("databaseId", "equals", "db1"))]

READABLE = ("pub1", "pub2")
UNREADABLE = tuple(f"sec{i}" for i in range(1, 10))


class RecordingStore(FakeStore):
    """A FakeStore that also records the window (``top_k``) of each call."""

    def __init__(self, items):
        super().__init__(items)
        self.windows = []

    def search(self, vector, *, top_k, filters):
        self.windows.append((filters.get("databaseId"), filters.get("segmentKind"), top_k))
        return super().search(vector, top_k=top_k, filters=filters)


def _readable_items(db="db1", segments=False):
    items = [item(db=db, asset="pub1", path="a.glb", distance=0.30),
             item(db=db, asset="pub2", path="b.glb", distance=0.50)]
    if segments:
        items += [chunk("c000001", 0.31, db=db, asset="pub1", path="a.glb"),
                  chunk("c000002", 0.32, db=db, asset="pub1", path="a.glb")]
    return items


def _unreadable_items(db="db1", segments=False):
    # Some rank closer to the query than the readable files, some farther
    items = [item(db=db, asset=name, path=f"{name}.glb", distance=0.05 * i) for i, name in enumerate(UNREADABLE, 1)]
    if segments:
        items += [chunk(f"c00000{i}", 0.06 * i, db=db, asset=name, path=f"{name}.glb")
                  for i, name in enumerate(UNREADABLE, 1)]
    return items


def _role(svc, lines, opens, read_in_full=None):
    """The caller's policy lines, its per-record decision, and its decision on a whole database."""
    svc.enforcer.effective_object_policies.return_value = lines
    svc.enforcer.enforce.side_effect = lambda obj, action: obj["object__type"] == "asset" and opens(obj)
    if read_in_full is not None:
        svc.enforcer.service_object.enforce.side_effect = lambda obj, action: read_in_full(obj)


def _public_role(svc):
    _role(svc, PUBLIC_ASSETS_OF_DB1, lambda obj: obj["databaseId"] == "db1" and obj["assetName"].startswith("pub"),
          read_in_full=lambda obj: False)


def _all_rows(svc, db="db1"):
    rows(svc, *[(db, name) for name in READABLE + UNREADABLE])


def _answer(body):
    """The whole response body but its timing."""
    return {key: value for key, value in body.items() if key != "took"}


@pytest.mark.unit
class TestAnswersOfPartialAccessDoNotDependOnUnreadableRecords:
    @pytest.mark.parametrize("segments", [False, True])
    @pytest.mark.parametrize("entity_type", ["file", "asset"])
    @pytest.mark.parametrize("size", SIZES)
    def test_two_stores_that_differ_in_unreadable_records_give_the_same_answer(
            self, svc, size, entity_type, segments):
        _public_role(svc)
        _all_rows(svc)
        request = {"query": "a file", "size": size, "entityTypes": [entity_type], "includeSegments": segments}
        status_a, readable_only = call(svc, FakeStore(_readable_items(segments=segments)), request)
        status_b, with_unreadable = call(
            svc, FakeStore(_readable_items(segments=segments) + _unreadable_items(segments=segments)), request)
        assert status_a == status_b == 200
        assert _answer(with_unreadable) == _answer(readable_only)
        assert {h["_source"]["str_assetid"] for h in with_unreadable["hits"]["hits"]} <= set(READABLE)

    @pytest.mark.parametrize("segments", [False, True])
    def test_the_signals_and_counts_are_the_same_at_every_size(self, svc, segments):
        _public_role(svc)
        _all_rows(svc)
        store_items = _readable_items(segments=segments) + _unreadable_items(segments=segments)
        answers = []
        for size in SIZES:
            _, body = call(svc, FakeStore(store_items),
                           {"query": "a file", "size": size, "includeSegments": segments})
            answers.append({"nlp": body["nlp"], "warnings": body["warnings"], "total": body["hits"]["total"],
                            "aggregations": body["aggregations"], "aggregationTotal": body["aggregationTotal"]})
        assert all(answer == answers[0] for answer in answers)

    @pytest.mark.parametrize("segments", [False, True])
    def test_the_window_is_reported_as_possibly_incomplete(self, svc, segments):
        _public_role(svc)
        _all_rows(svc)
        _, body = call(svc, FakeStore(_readable_items(segments=segments)),
                       {"query": "a file", "size": 100, "includeSegments": segments})
        assert body["nlp"]["truncated"] is True
        assert body["hits"]["total"] == {"value": 2, "relation": "gte"}
        assert [w["code"] for w in body["warnings"]] == ["truncated:window"]
        assert body["warnings"][0]["message"] == svc.PARTIAL_ACCESS_WINDOW_MESSAGE

    def test_an_answer_with_no_readable_record_is_a_minimum_of_zero(self, svc):
        _public_role(svc)
        _all_rows(svc)
        for store_items in ([], _unreadable_items()):
            _, body = call(svc, FakeStore(store_items), {"query": "a file", "size": 1})
            assert body["hits"]["hits"] == []
            assert body["hits"]["total"] == {"value": 0, "relation": "gte"}
            assert body["nlp"]["truncated"] is True
            assert body["nlp"]["candidatesEvaluated"] == 0 and body["nlp"]["itemsCollapsed"] == 0
            assert [w["code"] for w in body["warnings"]] == ["truncated:window"]

    @pytest.mark.parametrize("size", SIZES)
    def test_a_database_of_partial_access_is_searched_with_the_largest_window(self, svc, size):
        _public_role(svc)
        _all_rows(svc)
        store = RecordingStore(_readable_items())
        call(svc, store, {"query": "a file", "size": size})
        assert store.windows and all(window == svc.MAX_TOP_K for _, _, window in store.windows)


@pytest.mark.unit
class TestWhichDatabasesAreReadInFull:
    def test_a_database_scoped_role_keeps_the_computed_signal(self, svc):
        _role(svc, EVERY_ASSET_OF_DB1, lambda obj: obj["databaseId"] == "db1",
              read_in_full=lambda obj: obj["databaseId"] == "db1")
        rows(svc, ("db1", "pub1"), ("db1", "pub2"))
        store = RecordingStore(_readable_items())
        _, filled = call(svc, store, {"query": "a file", "size": 1, "includeSegments": False})
        assert filled["nlp"]["truncated"] is True and [w["code"] for w in filled["warnings"]] == ["truncated:window"]
        assert filled["warnings"][0]["message"] == "A result window of 2 filled; more files may match"
        assert store.windows == [("db1", "none", 2)]
        _, open_window = call(svc, FakeStore(_readable_items()),
                              {"query": "a file", "size": 5, "includeSegments": False})
        assert open_window["nlp"]["truncated"] is False and open_window["warnings"] == []
        assert open_window["hits"]["total"] == {"value": 2, "relation": "gte"}

    def test_archived_records_count_only_when_both_forms_are_allowed(self, svc):
        # The database-scoped role allows db1 but not db1#deleted, so its archived assets are not all readable
        _role(svc, EVERY_ASSET_OF_DB1, lambda obj: obj["databaseId"] == "db1",
              read_in_full=lambda obj: obj["databaseId"] == "db1")
        rows(svc, ("db1", "pub1"), ("db1", "pub2"))
        _, live = call(svc, FakeStore(_readable_items()), {"query": "a file", "size": 5, "includeSegments": False})
        _, archived = call(svc, FakeStore(_readable_items()),
                           {"query": "a file", "size": 5, "includeSegments": False, "includeArchived": True})
        assert live["nlp"]["truncated"] is False
        assert archived["nlp"]["truncated"] is True
        assert [w["code"] for w in archived["warnings"]] == ["truncated:window"]

    @pytest.mark.parametrize("lines", [
        PUBLIC_ASSETS_OF_DB1,
        [_line("allow", ("databaseId", "contains", ".*")), _line("deny", ("assetName", "equals", "sec1"))],
        [_line("allow", ("databaseId", "contains", ".*"), ("tags", "is_one_of", ["public"]))],
        [_line("allow", ("assetType", "equals", ".glb"))],
        None,
    ])
    def test_a_line_on_another_field_or_missing_lines_make_access_partial(self, svc, lines):
        _role(svc, lines, lambda obj: True, read_in_full=lambda obj: True)
        rows(svc, ("db1", "pub1"), ("db1", "pub2"))
        store = RecordingStore(_readable_items())
        _, body = call(svc, store, {"query": "a file", "size": 5, "includeSegments": False})
        assert body["nlp"]["truncated"] is True
        assert store.windows == [("db1", "none", svc.MAX_TOP_K)]

    def test_a_failure_to_decide_makes_access_partial(self, svc):
        svc.enforcer.service_object.enforce.side_effect = RuntimeError("policy unavailable")
        rows(svc, ("db1", "pub1"), ("db1", "pub2"))
        _, body = call(svc, FakeStore(_readable_items()), {"query": "a file", "size": 5, "includeSegments": False})
        assert body["nlp"]["truncated"] is True
        assert [w["code"] for w in body["warnings"]] == ["truncated:window"]

    def test_each_target_gets_its_own_window(self, svc):
        # db1 is read in full, db2 only in part
        svc.access.get_accessible_databases_with_count.return_value = (["db1", "db2"], 3)
        _role(svc, [_line("allow", ("databaseId", "equals", "db1")),
                    _line("allow", ("databaseId", "equals", "db2"))],
              lambda obj: obj["databaseId"] == "db1" or obj["assetName"].startswith("pub"),
              read_in_full=lambda obj: obj["databaseId"] == "db1")
        rows(svc, ("db1", "pub1"), ("db1", "pub2"), ("db2", "pub1"), ("db2", "pub2"))
        store = RecordingStore(_readable_items("db1") + _readable_items("db2"))
        _, body = call(svc, store, {"query": "a file", "size": 2, "includeSegments": False})
        assert sorted(store.windows) == [("db1", "none", 4), ("db2", "none", svc.MAX_TOP_K)]
        assert body["nlp"]["truncated"] is True
        assert body["warnings"][0]["message"] == svc.PARTIAL_ACCESS_WINDOW_MESSAGE

    @pytest.mark.parametrize("db2_in_full, truncated", [(True, False), (False, True)])
    def test_the_unpartitioned_call_needs_every_database_read_in_full(self, svc, db2_in_full, truncated):
        svc.access.get_accessible_databases_with_count.return_value = (["db1", "db2"], 2)
        _role(svc, EVERY_ASSET_OF_DB1 + [_line("allow", ("databaseId", "equals", "db2"))], lambda obj: True,
              read_in_full=lambda obj: obj["databaseId"] == "db1" or (db2_in_full and obj["databaseId"] == "db2"))
        rows(svc, ("db1", "pub1"), ("db1", "pub2"))
        store = RecordingStore(_readable_items())
        _, body = call(svc, store, {"query": "a file", "size": 5, "includeSegments": False})
        assert [target for target, _, _ in store.windows] == [None]
        assert body["nlp"]["truncated"] is truncated
        assert store.windows[0][2] == (10 if db2_in_full else svc.MAX_TOP_K)

    @pytest.mark.parametrize("database_scoped", [False, True])
    def test_the_whole_database_decision_writes_no_denial_audit_entry(self, svc, database_scoped):
        # The question about a whole database goes to the user's enforcer directly; only real records reach
        # the audited per-record check
        if database_scoped:
            _role(svc, EVERY_ASSET_OF_DB1, lambda obj: obj["databaseId"] == "db1",
                  read_in_full=lambda obj: obj["databaseId"] == "db1")
        else:
            _public_role(svc)
        _all_rows(svc)
        call(svc, FakeStore(_readable_items() + _unreadable_items()),
             {"query": "a file", "size": 5, "includeArchived": True})
        checked = [args[0] for args, _ in svc.enforcer.enforce.call_args_list]
        assert len(checked) == len(READABLE + UNREADABLE)
        assert sorted(obj["assetName"] for obj in checked) == sorted(READABLE + UNREADABLE)
        decided = [args[0] for args, _ in svc.enforcer.service_object.enforce.call_args_list]
        if database_scoped:
            # Both forms of the one database, each an asset object with no name, type or tags
            assert decided == [
                {"databaseId": form, "assetName": "", "tags": [], "assetType": "", "object__type": "asset"}
                for form in ("db1", "db1#deleted")
            ]
        else:
            # A line on the asset name settles it from the policy lines alone
            assert decided == []
