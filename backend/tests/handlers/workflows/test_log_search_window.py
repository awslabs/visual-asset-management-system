# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A live log search is bounded by the execution's own time window.

The workflow log group is SHARED by every state machine in the deployment and retained for ten years.
`FilterLogEvents` spends a bounded scan budget across the group's streams, so an UNBOUNDED search on a
group that also holds older streams returns those streams' events and reports nothing for a run that
finished seconds ago — the events are present, the search never reaches them.

Verified against a live group holding 13 streams: a query scoped to the run's own stream returned 12
events, the identical query issued group-wide with no startTime returned 0, and adding a startTime
around the run's window returned all 12. Defaulting startTime to the execution's start fixes it.
"""

import pathlib
import re

import pytest

EXECUTION_SERVICE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "backend" / "handlers" / "workflows" / "executionService.py"
)
SOURCE = EXECUTION_SERVICE.read_text(encoding="utf-8")


def _load_module_pieces():
    """Compile just the helper under test.

    executionService builds AWS clients and resolves SSM names at import, so the whole module cannot be
    imported without the mocked-table bootstrap. The helper is pure, so it is compiled on its own
    against the same stdlib names the module imports.
    """
    from datetime import datetime, timezone

    match = re.search(
        r"^LOG_SEARCH_WINDOW_MARGIN_MS = .*$", SOURCE, re.MULTILINE)
    assert match, "LOG_SEARCH_WINDOW_MARGIN_MS is gone"
    helper = re.search(
        r"^def _log_search_window_start\(main_item\):.*?(?=^def |\Z)", SOURCE,
        re.MULTILINE | re.DOTALL)
    assert helper, "_log_search_window_start is gone"
    namespace = {"datetime": datetime, "timezone": timezone}
    exec(match.group(0), namespace)          # noqa: S102 - compiling one known constant
    exec(helper.group(0), namespace)         # noqa: S102 - compiling one known pure helper
    return namespace


@pytest.mark.unit
class TestWindowStart:
    # The expected epoch-ms is DERIVED from the same instant rather than hardcoded: a hand-written
    # constant here was simply wrong, and the test failed against correct code.
    RAW = "2026-08-10T22:14:56Z"

    @staticmethod
    def _exact_epoch_ms(raw):
        from datetime import datetime, timezone
        return int(datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)

    def test_a_recorded_start_becomes_an_epoch_ms_lower_bound(self):
        ns = _load_module_pieces()
        start = ns["_log_search_window_start"]({"executionStartDate": self.RAW})
        assert isinstance(start, int)
        assert start == self._exact_epoch_ms(self.RAW) - ns["LOG_SEARCH_WINDOW_MARGIN_MS"]

    def test_the_margin_precedes_the_recorded_start(self):
        """Clock skew between the recorded start and the first event must not clip the window."""
        ns = _load_module_pieces()
        start = ns["_log_search_window_start"]({"executionStartDate": self.RAW})
        exact = self._exact_epoch_ms(self.RAW)
        assert start < exact
        assert exact - start == ns["LOG_SEARCH_WINDOW_MARGIN_MS"]

    @pytest.mark.parametrize("row", [
        {},
        {"executionStartDate": ""},
        {"executionStartDate": "not-a-date"},
        {"executionStartDate": None},
    ])
    def test_an_unparseable_start_leaves_the_search_unbounded(self, row):
        """None means 'no default', preserving the prior behavior rather than guessing a window."""
        ns = _load_module_pieces()
        assert ns["_log_search_window_start"](row) is None


@pytest.mark.unit
class TestSearchUsesTheWindow:
    """The window is applied to every live read, and a caller's explicit startTime still wins."""

    def test_full_search_falls_back_to_the_default_window(self):
        block = re.search(
            r"if query_params\.get\('startTime'\):\n\s+kwargs\['startTime'\] = int\("
            r"query_params\['startTime'\]\)\n\s+elif default_start_time:", SOURCE)
        assert block, (
            "a live search no longer defaults startTime; an unbounded group-wide search silently "
            "returns another run's streams instead of this execution's events")

    def test_the_callers_explicit_start_time_takes_precedence(self):
        """`elif`, not `if` — an explicit startTime must not be overwritten by the default."""
        assert "elif default_start_time:" in SOURCE
        assert re.search(r"kwargs\['startTime'\] = int\(default_start_time\)", SOURCE)

    def test_every_live_read_in_full_mode_is_bounded(self):
        """The registered/sub-process reads hit shared log groups too, so they need the same bound."""
        assert SOURCE.count("default_start_time=window_start") >= 3, (
            "a registered sub-process log read is still unbounded")
        assert "window_start = _log_search_window_start(main_item)" in SOURCE
