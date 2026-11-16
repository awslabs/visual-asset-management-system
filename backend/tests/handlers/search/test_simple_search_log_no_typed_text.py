# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Simple search never writes the text a caller typed to the function log (#1552).

The simple-search builder used to log the whole generated query clause at INFO, and that clause
holds every typed term (asset name, file key, metadata key and value, free text, tags). Each
test types a distinctive value in one field and asserts it is not in the captured log, and that
the clause the builder returns (and so the search results) still carries the term.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.search.test_simple_search_paging_and_merge import search_module  # noqa: F401

FIELDS = {
    "query": "zq-query-term-81f3",
    "assetName": "zq-asset-name-82f3",
    "assetId": "zq-asset-id-83f3",
    "fileKey": "zq-file-key-84f3",
    "metadataKey": "zq-meta-key-85f3",
    "metadataValue": "zq-meta-value-86f3",
    "tags": ["zq-tag-87f3"],
}


# Criteria that only exist on the other index: the builder drops them (a request holding only these
# matches nothing on this index).
FOREIGN_CRITERIA = {("fileKey", "asset")}


def _logged_text(logger):
    """Every message and keyword argument passed to the mocked logger, as one string."""
    return "\n".join(str(call) for call in logger.method_calls)


@pytest.mark.unit
@pytest.mark.parametrize("field", sorted(FIELDS))
@pytest.mark.parametrize("index_type", ["asset", "file"])
def test_a_typed_term_never_reaches_the_log(search_module, field, index_type):  # noqa: F811
    builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
    request = search_module.SimpleSearchRequestModel(**{field: FIELDS[field]})
    term = FIELDS[field][0] if isinstance(FIELDS[field], list) else FIELDS[field]

    search_module.logger = MagicMock()
    query = builder._build_simple_index_query(request, ["db1"], index_type)

    assert search_module.logger.info.called, "the query shape is still logged"
    assert term not in _logged_text(search_module.logger), f"typed {field} reached the function log"
    # Unchanged behaviour: the typed term still shapes the query that is sent to the index. The one
    # criterion foreign to an index (a file key, on the asset index) yields exactly match_none, and
    # every other field carries the term.
    if (field, index_type) in FOREIGN_CRITERIA:
        assert query["query"] == {"match_none": {}}
        assert term not in str(query)
    else:
        assert term in str(query), f"{field} no longer shapes the {index_type} query"
        assert "match_none" not in str(query)


@pytest.mark.unit
def test_an_index_foreign_field_leaves_the_query_clause_unchanged(search_module):  # noqa: F811
    """A file key alongside a name on the asset index: the clause is the same as without the file key."""
    builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
    with_foreign = search_module.SimpleSearchRequestModel(assetName=FIELDS["assetName"], fileKey=FIELDS["fileKey"])
    without = search_module.SimpleSearchRequestModel(assetName=FIELDS["assetName"])
    search_module.logger = MagicMock()
    clause_with = builder._build_simple_index_query(with_foreign, ["db1"], "asset")["query"]
    clause_without = builder._build_simple_index_query(without, ["db1"], "asset")["query"]
    assert clause_with == clause_without
    assert FIELDS["fileKey"] not in str(clause_with)
    assert FIELDS["fileKey"] not in _logged_text(search_module.logger)


@pytest.mark.unit
def test_the_log_still_describes_the_query_shape(search_module):  # noqa: F811
    builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
    request = search_module.SimpleSearchRequestModel(query=FIELDS["query"], metadataValue=FIELDS["metadataValue"])
    search_module.logger = MagicMock()
    builder._build_simple_index_query(request, ["db1"], "asset")
    messages = [c.args[0] for c in search_module.logger.info.call_args_list if "Generated" in c.args[0]]
    assert messages and "node(s)" in messages[0] and "bool=" in messages[0]


class _EngineError(Exception):
    """An OpenSearch error whose text quotes a fragment of the query, as a parse error does."""

    status_code = 400


@pytest.mark.unit
def test_an_engine_error_that_quotes_the_query_is_logged_by_class_and_status(search_module):  # noqa: F811
    manager = search_module.DualIndexSearchManager()
    manager.client = MagicMock()
    manager.client.search.side_effect = _EngineError(f"Failed to parse query [*{FIELDS['query']}*]")
    manager.is_available = lambda: True
    search_module.logger = MagicMock()

    with pytest.raises(search_module.VAMSGeneralErrorResponse, match="Error executing search query"):
        manager.search_dual_index({"query": {}}, {"query": {}}, ["asset"])

    logged = _logged_text(search_module.logger)
    assert FIELDS["query"] not in logged
    assert "_EngineError (HTTP 400)" in logged
