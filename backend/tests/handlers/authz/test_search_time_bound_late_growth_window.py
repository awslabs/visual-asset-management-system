# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A larger window whose fetch ends past the time bound keeps the hits the earlier windows checked (#1773).

For a role whose rules are checked after the query, the search fetches larger windows until the page is full.
When such a fetch answered after the fill bound, the checks of the larger window stopped before its first hit,
including the hits the first window had already checked, so the page was cut to nothing: a readable record the
search had found was dropped and the answer was empty. A hit decided earlier in the request now passes the
deadline check (its decision is remembered), so the page holds the readable hits already checked, in order, and
stops at the first hit not yet checked: never an unchecked hit, the total a minimum, the ``truncated:time``
warning. Driven through both keyword handlers against the in-memory OpenSearch of ``opensearch_dsl_oracle``, on
the harness's fake clock.
"""

import json

import pytest

from backend.tests.handlers.authz.opensearch_dsl_oracle import FakeSearchManager
from backend.tests.handlers.authz.search_authz_harness import (
    CLAIMS, ROLE, install_fake_clock, load_search_module, policies,
)
from backend.tests.handlers.authz.test_search_counts_honour_permissions import _file, _install_rows, _manager
from backend.tests.handlers.authz.test_search_paging_reaches_readable_assets import (
    REGEX_ONLY, _ScoredFake, _browse, _sparse_assets,
)

TIME_WARNING = "truncated:time"
READABLE_AT = (0, 50, 120)


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_late_growth_window")


@pytest.fixture(autouse=True)
def fake_clock(search_module, monkeypatch):
    return install_fake_clock(search_module, monkeypatch)


def _late_windows(base, clock, late_seconds):
    """``base`` (a FakeSearchManager class) whose fetch number ``n`` (1-based) takes ``late_seconds[n]``
    seconds more than the clock's set cost."""

    class _LateWindows(base):
        def search_dual_index(self, asset_query, file_query, entity_types):
            response = super().search_dual_index(asset_query, file_query, entity_types)
            clock.now += late_seconds.get(len(self.queries), 0.0)
            return response

    return _LateWindows


def _run(module, monkeypatch, fake, body, simple=False):
    manager = _manager(module, monkeypatch)
    processor = module.DualIndexResponseProcessor(manager)
    event = {"body": json.dumps(body)}
    if simple:
        response = module.handle_simple_post_request(event, fake, module.SimpleSearchQueryBuilder(manager),
                                                     processor, CLAIMS)
    else:
        response = module.handle_post_request(event, fake, module.DualIndexQueryBuilder(manager), processor, CLAIMS)
    assert response["statusCode"] == 200, response["body"]
    return json.loads(response["body"]), processor


def _ids(result):
    return [h["_id"] for h in result["hits"]["hits"]]


def _expect_cut(result):
    assert result["hits"]["total"]["relation"] == "gte"
    assert result["hits"]["total"]["value"] >= len(result["hits"]["hits"])
    assert [w["code"] for w in result["warnings"]] == [TIME_WARNING]


@pytest.mark.unit
class TestALateLargerWindowKeepsTheCheckedHits:
    @pytest.mark.parametrize("late", [12.0, 25.0])  # past the 10 s fill bound; past the 20 s request cap too
    @pytest.mark.parametrize("simple", [False, True])
    def test_the_first_windows_readable_hit_is_returned(self, search_module, monkeypatch, fake_clock, late,
                                                        simple):
        """The first window (20) finds a0 readable; the larger window (80) answers past the bound. The page
        holds a0, its total is a minimum, and no hit past the first window's was checked."""
        assets = _sparse_assets(count=300, readable_at=READABLE_AT)
        _install_rows(search_module, monkeypatch, assets)
        fake = _late_windows(FakeSearchManager, fake_clock, {2: late})(search_module, assets, [])
        body = {"entityTypes": ["asset"], "from": 0, "size": 10} if simple else _browse(10)
        with policies(REGEX_ONLY, (ROLE,)):
            result, processor = _run(search_module, monkeypatch, fake, body, simple=simple)

        assert [q[0]["size"] for q in fake.queries] == [20, 80]
        assert _ids(result) == ["a0"]
        assert processor.decision_count == 20
        _expect_cut(result)

    def test_file_hits_keep_their_checks_and_no_parent_row_is_read_after_the_bound(self, search_module,
                                                                                   monkeypatch, fake_clock):
        assets = _sparse_assets(count=300, readable_at=READABLE_AT)
        files = [_file(n, a) for n, a in enumerate(assets)]
        _install_rows(search_module, monkeypatch, assets)
        reads_at = []
        read_rows = search_module.batch_get_items

        def recording(resource, table, keys, **kwargs):
            reads_at.append(fake_clock.now)
            return read_rows(resource, table, keys, **kwargs)

        monkeypatch.setattr(search_module, "batch_get_items", recording)
        fake = _late_windows(FakeSearchManager, fake_clock, {2: 12.0})(search_module, assets, files)
        started = fake_clock.now
        with policies(REGEX_ONLY, (ROLE,)):
            result, _processor = _run(search_module, monkeypatch, fake, _browse(10, entity_types=("file",)))

        assert _ids(result) == ["f0"]
        _expect_cut(result)
        assert reads_at and all(at - started < search_module.SEARCH_FILL_MAX_SECONDS for at in reads_at)

    def test_a_search_over_both_indexes_keeps_the_checked_prefix_in_order(self, search_module, monkeypatch,
                                                                          fake_clock):
        """Assets and files interleaved: the late page is the start of the same search's page when every
        window is fast, so it neither skips nor reorders a record."""
        assets = _sparse_assets(count=300, readable_at=READABLE_AT)
        files = [_file(n, a) for n, a in enumerate(assets)]
        _install_rows(search_module, monkeypatch, assets)
        body = _browse(10, entity_types=("asset", "file"))
        with policies(REGEX_ONLY, (ROLE,)):
            fast, _ = _run(search_module, monkeypatch, _ScoredFake(search_module, assets, files, "interleaved"), body)
            late_fake = _late_windows(_ScoredFake, fake_clock, {2: 12.0})(search_module, assets, files, "interleaved")
            late, _ = _run(search_module, monkeypatch, late_fake, body)

        assert _ids(fast) == ["a0", "f0", "a50", "f50", "a120", "f120"]
        assert _ids(late) == ["a0", "f0"]
        assert _ids(late) == _ids(fast)[: len(_ids(late))]
        _expect_cut(late)


@pytest.mark.unit
class TestALateLargerWindowNeverReturnsAnUncheckedHit:
    def test_a_record_new_in_the_larger_window_ahead_of_the_checked_ones_ends_the_page(self, search_module,
                                                                                       monkeypatch, fake_clock):
        """A readable record indexed between the two fetches ranks first in the late larger window. It was
        never checked, so the page stops before it, and the checked a0 after it is not listed either: the
        page would otherwise skip a record that ranks above it."""
        assets = _sparse_assets(count=300, readable_at=READABLE_AT)
        newcomer = dict(assets[0], _id="a999", str_assetid="a999")
        _install_rows(search_module, monkeypatch, assets + [newcomer])

        class _NewRecordInTheLateWindow(FakeSearchManager):
            def search_dual_index(self, asset_query, file_query, entity_types):
                if len(self.queries) == 1:
                    self.indexes["asset"] = [newcomer] + self.indexes["asset"]
                response = super().search_dual_index(asset_query, file_query, entity_types)
                if len(self.queries) == 2:
                    fake_clock.now += 12.0
                return response

        fake = _NewRecordInTheLateWindow(search_module, assets, [])
        with policies(REGEX_ONLY, (ROLE,)):
            result, processor = _run(search_module, monkeypatch, fake, _browse(10))

        assert [q[0]["size"] for q in fake.queries] == [20, 80]
        assert result["hits"]["hits"] == []
        assert ("asset", "a999") not in processor._decisions
        _expect_cut(result)

    def test_the_checks_stop_at_the_first_hit_not_yet_checked(self, search_module, monkeypatch, fake_clock):
        """Past the deadline, remembered decisions are counted and the first hit without one ends the
        checks: it is not decided."""
        assets = _sparse_assets(count=30, readable_at=(0, 3, 25))
        _install_rows(search_module, monkeypatch, assets)
        processor = search_module.DualIndexResponseProcessor(_manager(search_module, monkeypatch))
        hits = [{"_id": s["_id"], "_score": 1.0, "_index_type": "asset",
                 "_source": {k: v for k, v in s.items() if k != "_id"}} for s in assets]
        with policies(REGEX_ONLY, (ROLE,)):
            first = processor.decide_in_order(hits[:5], CLAIMS, fake_clock.now + 60.0)
            fake_clock.now += 100.0  # every deadline below is spent
            again = processor.decide_in_order(hits, CLAIMS, fake_clock.now - 1.0)

        assert first == (5, 2)  # a0 and a3
        assert again == (5, 2)
        assert processor.decision_count == 5
