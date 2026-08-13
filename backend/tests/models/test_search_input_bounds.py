# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input-bound and nested-model validation on the search request models.

Every field on a search request model ends up inside an OpenSearch query body, so an
unbounded string, an unbounded list, or an unvalidated nested shape lets one request
drive an arbitrarily large query or reach OpenSearch as a malformed clause.
"""

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError


@pytest.mark.unit
class TestGeoJsonFilterValidation:
    """geoJson is typed Dict[str, Any]; the GeoJSON sub-validators must still run."""

    def _polygon(self, ring):
        return {"type": "Polygon", "coordinates": [ring]}

    def test_accepts_valid_polygon(self):
        from models.search import GeoSearchModel
        model = GeoSearchModel(geoJson=self._polygon([[0, 0], [1, 0], [1, 1], [0, 0]]))
        assert model.geoJson["type"] == "Polygon"

    def test_accepts_valid_point(self):
        from models.search import GeoSearchModel
        model = GeoSearchModel(geoJson={"type": "Point", "coordinates": [-122.3, 47.6]})
        assert model.geoJson["coordinates"] == [-122.3, 47.6]

    def test_accepts_feature_collection(self):
        from models.search import GeoSearchModel
        model = GeoSearchModel(geoJson={
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [10, 20]},
            }],
        })
        assert model.geoJson["type"] == "FeatureCollection"

    def test_accepts_opensearch_envelope(self):
        from models.search import GeoSearchModel
        model = GeoSearchModel(geoJson={"type": "envelope", "coordinates": [[-10, 10], [10, -10]]})
        assert model.geoJson["type"] == "envelope"

    def test_accepts_opensearch_circle(self):
        from models.search import GeoSearchModel
        model = GeoSearchModel(geoJson={"type": "circle", "coordinates": [0, 0], "radius": "100m"})
        assert model.geoJson["type"] == "circle"

    def test_rejects_longitude_out_of_range(self):
        from models.search import GeoSearchModel
        with pytest.raises(ValueError):
            GeoSearchModel(geoJson={"type": "Point", "coordinates": [999, 0]})

    def test_rejects_latitude_out_of_range(self):
        from models.search import GeoSearchModel
        with pytest.raises(ValueError):
            GeoSearchModel(geoJson={"type": "Point", "coordinates": [0, 91]})

    def test_rejects_self_intersecting_ring(self):
        """OpenSearch rejects this with invalid_shape_exception at query time."""
        from models.search import GeoSearchModel
        with pytest.raises(ValueError):
            GeoSearchModel(geoJson=self._polygon([[0, 0], [1, 0], [0, 0], [1, 1], [0, 0]]))

    def test_rejects_unclosed_ring(self):
        from models.search import GeoSearchModel
        with pytest.raises(ValueError):
            GeoSearchModel(geoJson=self._polygon([[0, 0], [1, 0], [1, 1], [0, 1]]))

    def test_rejects_unknown_geometry_type(self):
        from models.search import GeoSearchModel
        with pytest.raises(ValueError):
            GeoSearchModel(geoJson={"type": "Hyperboloid", "coordinates": [0, 0]})

    def test_rejects_non_object_geojson(self):
        from models.search import GeoSearchModel
        with pytest.raises(ValueError):
            GeoSearchModel(geoJson="not-an-object")

    def test_rejects_circle_without_radius(self):
        from models.search import GeoSearchModel
        with pytest.raises(ValueError):
            GeoSearchModel(geoJson={"type": "circle", "coordinates": [0, 0]})

    def test_rejects_envelope_with_wrong_corner_count(self):
        from models.search import GeoSearchModel
        with pytest.raises(ValueError):
            GeoSearchModel(geoJson={"type": "envelope", "coordinates": [[0, 0]]})

    def test_rejects_geojson_over_position_ceiling(self):
        from models.search import GeoSearchModel, MAX_GEOJSON_POSITIONS
        oversized = [[float(i % 180) - 90, 0] for i in range(MAX_GEOJSON_POSITIONS + 2)]
        with pytest.raises(ValueError):
            GeoSearchModel(geoJson={"type": "MultiPoint", "coordinates": oversized})

    def test_geojson_reaches_search_request(self):
        """A malformed geoJson nested two models deep is still rejected."""
        from models.search import SearchRequestModel
        with pytest.raises(ValidationError):
            parse(
                {"geoSearch": {"geoJson": {"type": "Point", "coordinates": [200, 0]}}},
                model=SearchRequestModel,
            )

    def test_valid_geojson_reaches_search_request(self):
        from models.search import SearchRequestModel
        model = parse(
            {"geoSearch": {"geoJson": {"type": "Point", "coordinates": [-122.3, 47.6]}}},
            model=SearchRequestModel,
        )
        assert model.geoSearch.geoJson["type"] == "Point"


@pytest.mark.unit
class TestSearchFilterQueryString:
    """The query_string sub-object is forwarded to OpenSearch verbatim."""

    def test_accepts_query_option(self):
        from models.search import SearchFilterModel
        model = SearchFilterModel(query_string={"query": 'str_databaseid.keyword:"my-db"'})
        assert model.query_string["query"] == 'str_databaseid.keyword:"my-db"'

    def test_accepts_supported_options(self):
        from models.search import SearchFilterModel
        model = SearchFilterModel(query_string={
            "query": "str_assetname:thing",
            "default_operator": "OR",
            "default_field": "str_assetname",
        })
        assert model.query_string["default_operator"] == "OR"

    def test_rejects_unsupported_option(self):
        from models.search import SearchFilterModel
        with pytest.raises(ValueError):
            SearchFilterModel(query_string={"query": "x", "script": "evil"})

    def test_rejects_missing_query_option(self):
        from models.search import SearchFilterModel
        with pytest.raises(ValueError):
            SearchFilterModel(query_string={"default_operator": "OR"})

    def test_rejects_oversized_query_value(self):
        from models.search import SearchFilterModel, MAX_SEARCH_TEXT_LENGTH
        with pytest.raises(ValueError):
            SearchFilterModel(query_string={"query": "a" * (MAX_SEARCH_TEXT_LENGTH + 1)})

    def test_accepts_query_value_at_ceiling(self):
        from models.search import SearchFilterModel, MAX_SEARCH_TEXT_LENGTH
        model = SearchFilterModel(query_string={"query": "a" * MAX_SEARCH_TEXT_LENGTH})
        assert len(model.query_string["query"]) == MAX_SEARCH_TEXT_LENGTH

    def test_unsupported_option_rejected_through_search_request(self):
        from models.search import SearchRequestModel
        with pytest.raises(ValidationError):
            parse(
                {"filters": [{"query_string": {"query": "x", "script": "evil"}}]},
                model=SearchRequestModel,
            )


@pytest.mark.unit
class TestSearchListBounds:
    """Every repeated request element expands into its own OpenSearch clause."""

    def test_rejects_filters_over_ceiling(self):
        from models.search import SearchRequestModel, MAX_SEARCH_FILTERS
        body = {"filters": [{"query_string": {"query": "x"}}] * (MAX_SEARCH_FILTERS + 1)}
        with pytest.raises(ValidationError):
            parse(body, model=SearchRequestModel)

    def test_accepts_filters_at_ceiling(self):
        from models.search import SearchRequestModel, MAX_SEARCH_FILTERS
        body = {"filters": [{"query_string": {"query": "x"}}] * MAX_SEARCH_FILTERS}
        model = parse(body, model=SearchRequestModel)
        assert len(model.filters) == MAX_SEARCH_FILTERS

    def test_rejects_tokens_over_ceiling(self):
        from models.search import SearchRequestModel, MAX_SEARCH_TOKENS
        body = {"tokens": [{"value": "x"}] * (MAX_SEARCH_TOKENS + 1)}
        with pytest.raises(ValidationError):
            parse(body, model=SearchRequestModel)

    def test_accepts_tokens_at_ceiling(self):
        from models.search import SearchRequestModel, MAX_SEARCH_TOKENS
        body = {"tokens": [{"value": "x"}] * MAX_SEARCH_TOKENS}
        model = parse(body, model=SearchRequestModel)
        assert len(model.tokens) == MAX_SEARCH_TOKENS

    def test_rejects_sort_over_ceiling(self):
        from models.search import SearchRequestModel, MAX_SEARCH_SORT_ENTRIES
        body = {"sort": ["str_assetname"] * (MAX_SEARCH_SORT_ENTRIES + 1)}
        with pytest.raises(ValidationError):
            parse(body, model=SearchRequestModel)

    def test_accepts_typical_sort(self):
        from models.search import SearchRequestModel
        model = parse({"sort": ["str_assetname", {"field": "num_size", "order": "desc"}]},
                      model=SearchRequestModel)
        assert len(model.sort) == 2

    def test_rejects_simple_search_tags_over_ceiling(self):
        from models.search import SimpleSearchRequestModel, MAX_SEARCH_TAGS
        with pytest.raises(ValidationError):
            parse({"tags": ["t"] * (MAX_SEARCH_TAGS + 1)}, model=SimpleSearchRequestModel)

    def test_accepts_simple_search_tags_at_ceiling(self):
        from models.search import SimpleSearchRequestModel, MAX_SEARCH_TAGS
        model = parse({"tags": ["t"] * MAX_SEARCH_TAGS}, model=SimpleSearchRequestModel)
        assert len(model.tags) == MAX_SEARCH_TAGS

    def test_rejects_repeated_entity_types(self):
        from models.search import SearchRequestModel
        with pytest.raises(ValidationError):
            parse({"entityTypes": ["asset", "file", "asset"]}, model=SearchRequestModel)

    def test_accepts_both_entity_types(self):
        from models.search import SearchRequestModel
        model = parse({"entityTypes": ["asset", "file"]}, model=SearchRequestModel)
        assert model.entityTypes == ["asset", "file"]


@pytest.mark.unit
class TestSearchFieldNameBounds:
    def test_rejects_oversized_token_property_key(self):
        from models.search import SearchTokenModel, MAX_SEARCH_FIELD_LENGTH
        with pytest.raises(ValueError):
            SearchTokenModel(propertyKey="k" * (MAX_SEARCH_FIELD_LENGTH + 1), value="v")

    def test_accepts_realistic_token_property_key(self):
        from models.search import SearchTokenModel
        model = SearchTokenModel(propertyKey="MD_str_manufacturer", value="acme")
        assert model.propertyKey == "MD_str_manufacturer"

    def test_rejects_oversized_token_value(self):
        from models.search import SearchTokenModel, MAX_SEARCH_TEXT_LENGTH
        with pytest.raises(ValueError):
            SearchTokenModel(value="v" * (MAX_SEARCH_TEXT_LENGTH + 1))

    def test_rejects_oversized_sort_model_field(self):
        from models.search import SearchSortModel, MAX_SEARCH_FIELD_LENGTH
        with pytest.raises(ValueError):
            SearchSortModel(field="f" * (MAX_SEARCH_FIELD_LENGTH + 1))

    def test_rejects_oversized_bare_string_sort_field(self):
        """A bare string sort entry bypasses SearchSortModel's field bound."""
        from models.search import SearchRequestModel, MAX_SEARCH_FIELD_LENGTH
        with pytest.raises(ValidationError):
            parse({"sort": ["str_" + "f" * MAX_SEARCH_FIELD_LENGTH]}, model=SearchRequestModel)

    def test_accepts_realistic_bare_string_sort_field(self):
        from models.search import SearchRequestModel
        model = parse({"sort": ["str_assetname"]}, model=SearchRequestModel)
        assert model.sort == ["str_assetname"]
