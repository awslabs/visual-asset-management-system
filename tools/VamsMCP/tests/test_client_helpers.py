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
