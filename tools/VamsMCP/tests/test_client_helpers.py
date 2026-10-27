"""Tests for VamsClient helper logic (pagination + search trimming).

These avoid constructing a real VamsClient (which needs a vamscli profile) by
bypassing __init__ and setting only the attributes the helpers use.
"""

from vams_mcp.client import VamsClient
from vams_mcp.config import Config


def _client(max_pages=20, page_size=100) -> VamsClient:
    obj = object.__new__(VamsClient)
    obj.config = Config(max_pages=max_pages, page_size=page_size)
    return obj


def test_paginate_single_page():
    client = _client()
    pages = [{"Items": [1, 2, 3], "NextToken": None}]

    def fetch(_params):
        return pages.pop(0)

    result = client.paginate(fetch)
    assert result["Items"] == [1, 2, 3]
    assert result["count"] == 3
    assert result["pages"] == 1
    assert "truncated" not in result


def test_paginate_follows_next_token():
    client = _client()
    responses = [
        {"Items": [1, 2], "NextToken": "t1"},
        {"Items": [3, 4], "NextToken": "t2"},
        {"Items": [5], "NextToken": None},
    ]
    calls = []

    def fetch(params):
        calls.append(params.get("startingToken"))
        return responses[len(calls) - 1]

    result = client.paginate(fetch)
    assert result["Items"] == [1, 2, 3, 4, 5]
    assert result["pages"] == 3
    # first call has no token, subsequent calls carry the prior NextToken
    assert calls == [None, "t1", "t2"]


def test_paginate_respects_max_items():
    client = _client()

    def fetch(_params):
        return {"Items": [1, 2, 3, 4, 5], "NextToken": "more"}

    result = client.paginate(fetch, max_items=3)
    assert result["Items"] == [1, 2, 3]
    assert result["truncated"] is True
    assert "note" in result


def test_paginate_respects_max_pages():
    client = _client(max_pages=2, page_size=10)

    def fetch(_params):
        return {"Items": [1], "NextToken": "always-more"}

    result = client.paginate(fetch)
    assert result["pages"] == 2  # stopped at max_pages
    # max_pages cut the walk short while more items remain, so flag it.
    assert result["truncated"] is True


# --- Resumption: the outstanding token must come back ------------------------
#
# S6-TOOLS-002. `paginate()` built its result from Items/count/pages/truncated/note and never
# included the token it stopped holding, and no read tool accepted a starting token. With the
# defaults (max_pages=20, page_size=100) that made 2,000 items a WALL rather than a page size: on a
# real asset with 12,000 files, files 2,001-12,000 were unreachable through this server, and the
# agent could not answer "does file X exist in this asset?" at all. `max_items` above the ceiling was
# accepted and ignored, and the note ("limit 20000, max_pages 20") read as "try a bigger max_items".


def test_paginate_returns_the_outstanding_next_token_at_the_max_items_bound():
    client = _client()

    def fetch(_params):
        return {"Items": [1, 2, 3, 4, 5], "NextToken": "more"}

    result = client.paginate(fetch, max_items=3)
    assert result["NextToken"] == "more"
    assert result["truncated"] is True
    # The note has to say how to continue, not only that the result is short.
    assert "starting_token" in result["note"]


def test_paginate_returns_the_outstanding_next_token_at_the_max_pages_bound():
    """The ceiling the finding measured: max_pages fires regardless of max_items, so the token is the
    only way past it."""
    client = _client(max_pages=2, page_size=10)

    def fetch(_params):
        return {"Items": [1] * 10, "NextToken": "more"}

    result = client.paginate(fetch, max_items=1000)
    assert result["count"] == 20
    assert result["pages"] == 2
    assert result["truncated"] is True
    assert result["NextToken"] == "more"
    # The note must name WHICH bound fired, or the agent cannot tell "raise max_items" from
    # "resume with the token".
    assert "max_pages" in result["note"]


def test_paginate_sends_a_supplied_starting_token_on_the_first_page():
    client = _client()
    seen = []

    def fetch(params):
        seen.append(params.get("startingToken"))
        return {"Items": [1], "NextToken": None}

    client.paginate(fetch, starting_token="resume-here")
    assert seen == ["resume-here"]


def test_paginate_omits_next_token_on_a_complete_walk():
    """Negative control: a token present on a finished walk would make every result read as partial."""
    client = _client()

    def fetch(_params):
        return {"Items": [1, 2], "NextToken": None}

    result = client.paginate(fetch)
    assert "NextToken" not in result
    assert "truncated" not in result
    assert "note" not in result


# --- Resuming after a max_items cut, and naming the page bound ---------------
#
# The token returned at a max_items bound came from a page that was then cut to max_items, so it
# pointed past the rows dropped from that page: with the default page size of 100, `max_items=50`
# plus one resume on a 123-row route returned 73 rows, and the resumed walk ended with no
# `truncated` flag. And with no max_items the limit is max_pages * page_size, so a walk the page
# bound stopped after full pages also reached that limit and its note said "the requested limit".


def _offset_endpoint(rows):
    """A route that honours pageSize and issues a token resuming after the last RETURNED row."""
    sent = []

    def fetch(params):
        sent.append(dict(params))
        start = int(params.get("startingToken") or 0)
        page = rows[start : start + int(params["pageSize"])]
        end = start + len(page)
        return {"Items": page, "NextToken": str(end) if end < len(rows) else None}

    return fetch, sent


def test_resume_after_a_max_items_cut_reaches_every_row():
    rows = list(range(123))
    fetch, _ = _offset_endpoint(rows)
    client = _client()

    first = client.paginate(fetch, max_items=50)
    rest = client.paginate(fetch, starting_token=first["NextToken"])

    assert first["Items"] == rows[:50]
    assert first["truncated"] is True
    assert first["Items"] + rest["Items"] == rows  # nothing skipped, nothing repeated
    assert "truncated" not in rest


def test_last_page_requests_only_the_rows_still_wanted():
    fetch, sent = _offset_endpoint(list(range(500)))
    _client(page_size=100).paginate(fetch, max_items=250)
    assert [p["pageSize"] for p in sent] == [100, 100, 50]


def test_max_pages_note_without_max_items_names_max_pages():
    """With no max_items the default limit is max_pages * page_size, so a walk the page bound
    stopped must not be reported as the caller's 'requested limit'."""
    client = _client(max_pages=2, page_size=25)

    def fetch(_params):
        return {"Items": [0] * 25, "NextToken": "more"}

    result = client.paginate(fetch)
    assert result["truncated"] is True
    assert "max_pages" in result["note"]
    assert "requested limit" not in result["note"]


def test_an_explicit_max_items_still_names_the_requested_limit():
    """Control: a caller-supplied max_items reached exactly keeps the 'requested limit' wording."""
    client = _client(max_pages=20, page_size=25)

    def fetch(_params):
        return {"Items": [0] * 25, "NextToken": "more"}

    result = client.paginate(fetch, max_items=50)
    assert "requested limit of 50" in result["note"]
    assert "max_pages" not in result["note"]


# --- Overflow on a final page with no token ---------------------------------
#
# S6-TOOLS-021. `items[:limit]` dropped the surplus but `truncated` was set only `if next_token`, so
# a single page holding more rows than max_items and carrying no NextToken reported a clean count.
# `list_databases(max_items=5)` against 60 databases answered "the deployment has 5 databases".


def test_paginate_flags_truncation_when_max_items_cuts_a_final_page():
    client = _client()

    def fetch(_params):
        return {"Items": [1, 2, 3, 4, 5], "NextToken": None}

    result = client.paginate(fetch, max_items=3)
    assert result["Items"] == [1, 2, 3]
    assert result["truncated"] is True
    assert "note" in result
    # No token exists, so the note must point at max_items rather than at a resumption that is
    # impossible here — the two causes need distinguishable wording.
    assert "starting_token" not in result["note"]
    assert "max_items" in result["note"]


def test_paginate_does_not_flag_a_page_that_exactly_fills_max_items():
    """Boundary control: equal is complete, not truncated."""
    client = _client()

    def fetch(_params):
        return {"Items": [1, 2, 3], "NextToken": None}

    result = client.paginate(fetch, max_items=3)
    assert result["Items"] == [1, 2, 3]
    assert "truncated" not in result


def test_paginate_custom_items_key():
    client = _client()

    def fetch(_params):
        return {"versions": [{"assetVersionId": "1"}], "NextToken": None}

    result = client.paginate(fetch, items_key="versions")
    # Normalized onto Items regardless of the source key.
    assert result["Items"] == [{"assetVersionId": "1"}]
    assert result["count"] == 1


def test_paginate_unwraps_message_envelope():
    client = _client()
    responses = [
        {"message": {"Items": [1, 2], "NextToken": "t1"}},
        {"message": {"Items": [3], "NextToken": None}},
    ]
    calls = []

    def fetch(params):
        calls.append(params.get("startingToken"))
        return responses[len(calls) - 1]

    result = client.paginate(fetch)
    assert result["Items"] == [1, 2, 3]
    assert calls == [None, "t1"]


def test_paginate_page_size_override():
    client = _client(page_size=100)
    seen = []

    def fetch(params):
        seen.append(params["pageSize"])
        return {"Items": [1], "NextToken": None}

    client.paginate(fetch, page_size=50)
    assert seen == [50]


def test_trim_search_results():
    raw = {
        "hits": {
            "total": {"value": 2},
            "hits": [
                {"_id": "a1", "_score": 1.5, "_source": {"name": "Asset One"}},
                {"_id": "a2", "_score": 1.0, "_source": {"name": "Asset Two"}},
            ],
        }
    }
    trimmed = VamsClient.trim_search_results(raw)
    assert trimmed["total"] == 2
    assert trimmed["returned"] == 2
    assert trimmed["results"][0] == {"id": "a1", "score": 1.5, "source": {"name": "Asset One"}}


def test_trim_search_results_max_hits():
    hits = [{"_id": str(i), "_score": 1, "_source": {}} for i in range(10)]
    raw = {"hits": {"total": {"value": 10}, "hits": hits}}
    trimmed = VamsClient.trim_search_results(raw, max_hits=3)
    assert trimmed["returned"] == 3
    assert trimmed["total"] == 10


def test_trim_search_results_empty():
    trimmed = VamsClient.trim_search_results({})
    assert trimmed == {"total": None, "returned": 0, "results": []}


def test_search_enabled_follows_the_noopensearch_feature_switch():
    from unittest.mock import MagicMock

    client = _client()
    client._profile = MagicMock()

    client._profile.has_feature_switch.return_value = True
    assert client.search_enabled() is False
    client._profile.has_feature_switch.assert_called_with("NOOPENSEARCH")

    client._profile.has_feature_switch.return_value = False
    assert client.search_enabled() is True
