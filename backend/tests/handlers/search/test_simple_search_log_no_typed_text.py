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
    # Unchanged behaviour: the typed term still shapes the query that is sent to the index. A
    # criterion foreign to the index type yields match_none, which carries no term by design.
    assert term in str(query) or "match_none" in str(query), f"{field} no longer shapes the query"


@pytest.mark.unit
def test_the_log_still_describes_the_query_shape(search_module):  # noqa: F811
    builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
    request = search_module.SimpleSearchRequestModel(query=FIELDS["query"], metadataValue=FIELDS["metadataValue"])
    search_module.logger = MagicMock()
    builder._build_simple_index_query(request, ["db1"], "asset")
    messages = [c.args[0] for c in search_module.logger.info.call_args_list if "Generated" in c.args[0]]
    assert messages and "node(s)" in messages[0] and "bool=" in messages[0]
