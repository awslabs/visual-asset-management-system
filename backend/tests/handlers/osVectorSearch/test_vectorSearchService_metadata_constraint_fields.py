# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The metadataQuery constraint of POST /search/nlp finds its files on a provisioned domain too (#1285).

``_opensearch_constraints`` builds the metadataQuery clause with the /search query builder, so it names
the same flat_object fields: the root or the key for an exact or prefix value, and the ``._value`` /
``._valueAndPath`` subfields for any other pattern, on both engines. The clause is read with the model
of how OpenSearch answers it (``query_string_oracle``), over the records a provisioned domain was probed
with.
"""

import pytest

from tests.handlers.authz import query_string_oracle as oracle
from tests.handlers.authz.search_authz_harness import load_search_module
from tests.handlers.osVectorSearch import test_vectorSearchService as behaviour

svc = behaviour.svc  # the behaviour module's fixture: auth surface, embedding and database access replaced

PDF = "pump-manual/centrifugal_pump_manual.pdf"
TXT = "operator-notes/operator_notes.txt"


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_nlp_metadata_constraint")


@pytest.fixture(params=oracle.ENGINES)
def engine(request, search_module, monkeypatch):
    monkeypatch.setattr(search_module, "opensearch_type", request.param)
    return request.param


def _metadata_clauses(svc, search_module, metadata_query, mode):
    from models.vectorsearch import NlpSearchRequestModel

    request = NlpSearchRequestModel(query="pump", metadataQuery=metadata_query, metadataSearchMode=mode)
    (clause,) = svc._opensearch_constraints(request, search_module, ["fz-a"])
    return clause["bool"]["must"]


@pytest.mark.unit
@pytest.mark.parametrize("metadata_query, mode, expected", [
    ("*log", "both", {PDF}),
    ("*ator No*", "both", {TXT}),
    ("docType:*Notes", "both", {TXT}),
    ("maintenance", "value", {PDF, TXT}),
    ("maint*", "both", {PDF}),
    ("category:maintenance log", "both", {PDF}),
])
def test_the_constraint_keeps_the_files_holding_the_value(svc, search_module, engine, metadata_query, mode,
                                                          expected):
    must = _metadata_clauses(svc, search_module, metadata_query, mode)
    found = {name for name, document in oracle.PROBE_FILES.items()
             if all(oracle.clause_matches(clause, document, engine) for clause in must)}
    assert found == expected
