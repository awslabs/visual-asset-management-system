#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""GeoJSON `.json`: the root type set decides detection (a type word alone does not), the file keeps the
`data` class, sys_geo carries featureCount / geometryTypes / footprint, the footprint is the file's own
geometry for a single Point or closed Polygon and the bounding-box Polygon otherwise, and invalid coordinates
leave footprint null with a warning rather than failing the task."""

import json

import pytest

from media_extractors import geo
from system_genai_media_fixtures import geojson_collection_dict, geojson_point_dict, make_ctx, tileset_dict


@pytest.mark.unit
class TestIsGeojson:
    def test_root_types_with_their_member_key(self):
        assert geo.is_geojson(geojson_point_dict()) is True
        assert geo.is_geojson(geojson_collection_dict()) is True
        assert geo.is_geojson({"type": "Polygon", "coordinates": []}) is True
        assert geo.is_geojson({"type": "GeometryCollection", "geometries": []}) is True

    def test_type_word_alone_is_not_geojson(self):
        assert geo.is_geojson({"type": "Point"}) is False
        assert geo.is_geojson({"type": "Feature"}) is False
        assert geo.is_geojson({"type": "FeatureCollection"}) is False
        assert geo.is_geojson({"type": "Unknown", "coordinates": [1, 2]}) is False
        assert geo.is_geojson(tileset_dict()) is False
        assert geo.is_geojson([{"type": "Point", "coordinates": [1, 2]}]) is False


@pytest.mark.unit
class TestExtractGeo:
    def test_point_file_footprint_is_the_point_itself(self, tmp_path):
        result = geo.extract_geo(geojson_point_dict(), make_ctx(tmp_path, "sites.json"))
        sys_geo = result.attributes["sys_geo"]
        assert result.file_class == "data"
        assert sys_geo["format"] == "GeoJSON" and sys_geo["type"] == "Feature"
        assert sys_geo["featureCount"] == 1 and sys_geo["geometryTypes"] == ["Point"]
        assert sys_geo["footprint"] == {"type": "Point", "coordinates": [151.2, -33.9]}
        assert sys_geo["positionCount"] == 1 and sys_geo["propertyKeys"] == ["kind", "name"]
        assert result.facts == {"features": "1 feature", "geometryTypes": "Point", "footprint": "Point"}
        assert json.loads(result.text_excerpt)["footprint"]["type"] == "Point"
        assert result.render_images == [] and result.render_skipped is None and result.warnings == []

    def test_single_polygon_feature_footprint_is_the_polygon(self, tmp_path):
        ring = [[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]
        document = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {}}
        sys_geo = geo.extract_geo(document, make_ctx(tmp_path, "area.json")).attributes["sys_geo"]
        assert sys_geo["footprint"] == {"type": "Polygon", "coordinates": [ring]}
        assert sys_geo["positionCount"] == 5 and "propertyKeys" not in sys_geo

    def test_feature_collection_of_mixed_geometries_gets_a_bbox_polygon(self, tmp_path):
        result = geo.extract_geo(geojson_collection_dict(), make_ctx(tmp_path, "mixed.json"))
        sys_geo = result.attributes["sys_geo"]
        assert sys_geo["type"] == "FeatureCollection" and sys_geo["featureCount"] == 3
        assert sys_geo["geometryTypes"] == ["GeometryCollection", "Point", "Polygon"]
        assert sys_geo["footprint"] == {"type": "Polygon", "coordinates": [
            [[0.0, -33.9], [151.2, -33.9], [151.2, 20.0], [0.0, 20.0], [0.0, -33.9]]]}
        assert sys_geo["positionCount"] == 9
        assert sys_geo["propertyKeys"] == ["kind", "name", "note"]
        assert result.facts["footprint"] == "Polygon bounding box" and result.facts["features"] == "3 features"
        assert result.facts["geometryTypes"] == "GeometryCollection, Point, Polygon"
        assert result.warnings == []

    def test_invalid_coordinates_reject_the_footprint_with_a_warning(self, tmp_path):
        document = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [151.2, -33.9]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10, 95]}, "properties": {}},
        ]}
        result = geo.extract_geo(document, make_ctx(tmp_path, "bad.json"))
        sys_geo = result.attributes["sys_geo"]
        assert sys_geo["footprint"] is None and "positionCount" not in sys_geo
        assert sys_geo["featureCount"] == 2 and sys_geo["geometryTypes"] == ["Point"]
        assert len(result.warnings) == 1 and "rejected" in result.warnings[0] and "[10, 95]" in result.warnings[0]
        assert "footprint" not in result.facts
        for bad in ([151.2, "x"], [151.2], ["a", "b"], [float("nan"), 0], [200, 0], "not an array"):
            with pytest.raises(ValueError):
                list(geo.iter_positions(bad))

    def test_unclosed_single_polygon_falls_back_to_its_bbox(self, tmp_path):
        document = {"type": "Polygon", "coordinates": [[[10, 10], [20, 10], [20, 20], [10, 20]]]}
        result = geo.extract_geo(document, make_ctx(tmp_path, "open.json"))
        assert result.attributes["sys_geo"]["footprint"] == {"type": "Polygon", "coordinates": [
            [[10.0, 10.0], [20.0, 10.0], [20.0, 20.0], [10.0, 20.0], [10.0, 10.0]]]}
        assert len(result.warnings) == 1 and "not closed" in result.warnings[0]
        assert result.facts["footprint"] == "Polygon bounding box"

    def test_degenerate_boxes_collapse(self, tmp_path):
        same = {"type": "MultiPoint", "coordinates": [[5, 5], [5, 5], [5, 5]]}
        assert geo.extract_geo(same, make_ctx(tmp_path, "same.json")).attributes["sys_geo"]["footprint"] == {
            "type": "Point", "coordinates": [5.0, 5.0]}
        line = {"type": "MultiPoint", "coordinates": [[5, 5], [5, 9]]}
        assert geo.extract_geo(line, make_ctx(tmp_path, "line.json")).attributes["sys_geo"]["footprint"] == {
            "type": "LineString", "coordinates": [[5.0, 5.0], [5.0, 9.0]]}

    def test_null_geometry_feature_is_counted_without_positions(self, tmp_path):
        document = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": None, "properties": {"a": 1}}]}
        result = geo.extract_geo(document, make_ctx(tmp_path, "empty.json"))
        sys_geo = result.attributes["sys_geo"]
        assert sys_geo["featureCount"] == 1 and sys_geo["geometryTypes"] == [] and sys_geo["footprint"] is None
        assert sys_geo["positionCount"] == 0 and "no positions" in result.warnings[0]
        assert "geometryTypes" not in result.facts and "footprint" not in result.facts
