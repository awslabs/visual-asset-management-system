# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /search/nlp is a search-category route in the master registry."""

from common.apiRoutes import ALL_API_ROUTES, API_SEARCH_NLP, API_SEARCH_SIMPLE, POST, SEARCH_ROUTES


def test_route_shape():
    assert API_SEARCH_NLP.path == "/search/nlp"
    assert API_SEARCH_NLP.methods == (POST,)
    assert API_SEARCH_NLP.category == API_SEARCH_SIMPLE.category == "search"
    assert API_SEARCH_NLP.internal is False and API_SEARCH_NLP.unauthenticated is False


def test_registered():
    assert API_SEARCH_NLP in SEARCH_ROUTES and API_SEARCH_NLP in ALL_API_ROUTES
    assert API_SEARCH_NLP.matches("/search/nlp") and not API_SEARCH_NLP.matches("/search/simple")
    assert not API_SEARCH_NLP.matches("/search/nlp/extra")
