# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GeoJSON validation on the geo_MD_location write side (S2-BACKEND-078).

build_geo_location derives the geo_MD_location field the asset and file indexers write to
OpenSearch. The 'location' metadata key carries free-form caller text unless a GEOJSON-typed
metadata schema is attached, so the geometry reaching this module is not guaranteed to be one
OpenSearch's geo_shape will accept. A shape it rejects costs a mapper_parsing_exception, a
second index call with the geo field stripped, and an asset that is searchable but absent from
the geospatial map with only a warning line.

Two properties are pinned here:

  * A geometry the GEOJSON metadata value type would reject (out-of-range coordinates, a
    non-array coordinates member, an unclosed or degenerate linear ring) is not indexed, so
    the write side and the query-side validator in models/search.py agree on what a valid
    shape is. NaN and boolean positions are additionally rejected here: a range check reads
    them as in range (every comparison against NaN is False, and bool subclasses int) while
    OpenSearch does not, and json.loads parses the bare NaN literal out of a stored metadata
    value.
  * A GeometryCollection is indexed. It is accepted by the GEOJSON metadata value type but
    carries a 'geometries' member rather than 'coordinates', so it used to fall through every
    branch and drop out of geo_MD_location entirely.
  * A GeometryCollection nested past MAX_GEOJSON_NESTING_DEPTH is refused rather than
    walked. Validation, the finiteness walk and the member strip each recurse once per level,
    and a few kilobytes of stored metadata carry several hundred levels, so an unbounded walk
    raises RecursionError inside the indexer - which fails the whole document instead of
    leaving one field out of it.

The positive controls cover every shape that already indexed - a point, a closed polygon, a
point with altitude, the multi-geometry types, Feature / FeatureCollection unwrapping, and both
lat/lon fallback paths - because tightening validation here is otherwise indistinguishable from
losing the map.

The module is loaded through its `backend.backend.*` path: `tests/conftest.py` replaces
`common.indexing.geoLocation` in sys.modules with a stub that always returns None.
"""

import pytest

from backend.backend.common.indexing.geoLocation import (
    build_geo_location,
    _normalize_geojson_geometry,
)
from models.metadata import MAX_GEOJSON_NESTING_DEPTH, _validate_geometry


def _closed_ring():
    return [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]


def _nested_collection(levels):
    """A geometry whose nesting is `levels` deep in total, innermost Point included."""
    geometry = {"type": "Point", "coordinates": [1.0, 2.0]}
    for _ in range(levels - 1):
        geometry = {"type": "GeometryCollection", "geometries": [geometry]}
    return geometry


def _nested_collection_json(levels):
    """The same shape as a JSON string, built textually.

    json.dumps recurses per level too, so serializing the dict caps the depth a test can
    reach well below the depth a stored metadata value can hold.
    """
    text = '{"type":"Point","coordinates":[1.0,2.0]}'
    for _ in range(levels - 1):
        text = '{"type":"GeometryCollection","geometries":[' + text + "]}"
    return text


@pytest.mark.unit
class TestGeoJsonShapesRejected:
    """Shapes OpenSearch's geo_shape would refuse must not reach it."""

    def test_out_of_range_point_is_not_indexed(self):
        assert build_geo_location({"location": {"type": "Point", "coordinates": [999, 999]}}) is None

    def test_out_of_range_polygon_vertex_is_not_indexed(self):
        ring = [[0.0, 0.0], [200.0, 0.0], [1.0, 1.0], [0.0, 0.0]]
        assert build_geo_location({"location": {"type": "Polygon", "coordinates": [ring]}}) is None

    def test_non_array_coordinates_are_not_indexed(self):
        assert build_geo_location({"location": {"type": "Polygon", "coordinates": "garbage"}}) is None

    def test_unclosed_polygon_ring_is_not_indexed(self):
        ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
        assert build_geo_location({"location": {"type": "Polygon", "coordinates": [ring]}}) is None

    def test_degenerate_polygon_ring_is_not_indexed(self):
        # Closed, four positions, but only two unique vertices - a line, not an area.
        ring = [[0.0, 0.0], [1.0, 1.0], [0.0, 0.0], [0.0, 0.0]]
        assert build_geo_location({"location": {"type": "Polygon", "coordinates": [ring]}}) is None

    def test_nan_coordinate_from_a_stored_json_string_is_not_indexed(self):
        # json.loads accepts the bare NaN literal, and every range comparison against NaN is
        # False, so the coordinate would otherwise pass a pure range check.
        assert build_geo_location({"location": '{"type":"Point","coordinates":[NaN,0]}'}) is None

    def test_infinite_coordinate_from_a_stored_json_string_is_not_indexed(self):
        assert build_geo_location({"location": '{"type":"Point","coordinates":[Infinity,0]}'}) is None

    def test_boolean_coordinates_are_not_indexed(self):
        # bool subclasses int, so a range check alone reads True/False as 1/0.
        assert build_geo_location({"location": {"type": "Point", "coordinates": [True, False]}}) is None

    def test_single_position_point_is_not_indexed(self):
        assert build_geo_location({"location": {"type": "Point", "coordinates": [1.0]}}) is None

    def test_single_position_linestring_is_not_indexed(self):
        assert build_geo_location({"location": {"type": "LineString", "coordinates": [[1.0, 2.0]]}}) is None

    def test_malformed_geometry_inside_a_feature_is_not_indexed(self):
        location = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 91]}}
        assert build_geo_location({"location": location}) is None


@pytest.mark.unit
class TestGeometryCollection:
    """A GeometryCollection carries 'geometries', not 'coordinates'."""

    def test_geometry_collection_is_indexed(self):
        collection = {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Point", "coordinates": [1.0, 2.0]},
                {"type": "Polygon", "coordinates": [_closed_ring()]},
            ],
        }
        assert build_geo_location({"location": collection}) == collection

    def test_nested_geometry_collection_is_indexed(self):
        collection = {
            "type": "GeometryCollection",
            "geometries": [
                {
                    "type": "GeometryCollection",
                    "geometries": [{"type": "Point", "coordinates": [1.0, 2.0]}],
                }
            ],
        }
        assert build_geo_location({"location": collection}) == collection

    def test_geometry_collection_sub_geometries_are_stripped_to_indexable_members(self):
        collection = {
            "type": "GeometryCollection",
            "bbox": [1.0, 2.0, 1.0, 2.0],
            "geometries": [{"type": "Point", "coordinates": [1.0, 2.0], "bbox": [1.0, 2.0, 1.0, 2.0]}],
        }
        assert build_geo_location({"location": collection}) == {
            "type": "GeometryCollection",
            "geometries": [{"type": "Point", "coordinates": [1.0, 2.0]}],
        }

    def test_empty_geometry_collection_is_not_indexed(self):
        location = {"type": "GeometryCollection", "geometries": []}
        assert build_geo_location({"location": location}) is None

    def test_geometry_collection_with_an_invalid_member_is_not_indexed(self):
        location = {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Point", "coordinates": [1.0, 2.0]},
                {"type": "Point", "coordinates": [999, 0]},
            ],
        }
        assert build_geo_location({"location": location}) is None


@pytest.mark.unit
class TestNestingDepthBound:
    """Nesting deep enough to exhaust the stack is refused, not walked.

    Every assertion here also asserts "no exception": an uncaught RecursionError fails the
    whole index operation, which loses the document rather than just the geo field.
    """

    def test_nesting_at_the_bound_is_still_indexed(self):
        geometry = _nested_collection(MAX_GEOJSON_NESTING_DEPTH)
        assert build_geo_location({"location": geometry}) == geometry

    def test_nesting_one_level_past_the_bound_is_not_indexed(self):
        geometry = _nested_collection(MAX_GEOJSON_NESTING_DEPTH + 1)
        assert build_geo_location({"location": geometry}) is None

    def test_deeply_nested_collection_object_is_refused(self):
        assert build_geo_location({"location": _nested_collection(400)}) is None

    def test_deeply_nested_collection_json_string_is_refused(self):
        # Parses cleanly - the depth that used to be reached and walked.
        location = _nested_collection_json(400)
        assert len(location) < 400000  # inside the metadata value length limit
        assert build_geo_location({"location": location}) is None

    def test_json_string_too_deep_for_the_parser_is_refused(self):
        # Deeper than json.loads itself will go, which it reports as RecursionError rather
        # than as a decode error.
        assert build_geo_location({"location": _nested_collection_json(2000)}) is None


@pytest.mark.unit
class TestWriteSideAgreesWithTheMetadataValidator:
    """Anything the GEOJSON metadata value type rejects must not be indexed."""

    @pytest.mark.parametrize(
        "geometry",
        [
            {"type": "Point", "coordinates": [999, 999]},
            {"type": "Point", "coordinates": [0, 91]},
            {"type": "Polygon", "coordinates": "garbage"},
            {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]]},
            {"type": "LineString", "coordinates": [[1.0, 2.0]]},
            {"type": "MultiPoint", "coordinates": []},
            {"type": "GeometryCollection", "geometries": []},
        ],
    )
    def test_rejected_by_metadata_validator_means_not_indexed(self, geometry):
        with pytest.raises(ValueError):
            _validate_geometry(geometry)
        assert _normalize_geojson_geometry(geometry) is None

    @pytest.mark.parametrize(
        "geometry",
        [
            {"type": "Point", "coordinates": [-73.9, 40.7]},
            {"type": "Point", "coordinates": [-73.9, 40.7, 12.5]},
            {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
            {"type": "MultiPoint", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
            {"type": "MultiLineString", "coordinates": [[[0.0, 0.0], [1.0, 1.0]]]},
            {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]},
            {"type": "MultiPolygon", "coordinates": [[[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]]},
            {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [1.0, 2.0]}]},
        ],
    )
    def test_accepted_by_metadata_validator_means_indexed(self, geometry):
        _validate_geometry(geometry)
        assert _normalize_geojson_geometry(geometry) == geometry


@pytest.mark.unit
class TestPositiveControls:
    """Every shape that already produced a geo_MD_location still does."""

    def test_valid_point_is_indexed(self):
        location = {"type": "Point", "coordinates": [-73.9, 40.7]}
        assert build_geo_location({"location": location}) == location

    def test_point_with_altitude_is_indexed(self):
        location = {"type": "Point", "coordinates": [-73.9, 40.7, 12.5]}
        assert build_geo_location({"location": location}) == location

    def test_closed_polygon_is_indexed(self):
        location = {"type": "Polygon", "coordinates": [_closed_ring()]}
        assert build_geo_location({"location": location}) == location

    def test_multi_geometries_are_indexed(self):
        for location in (
            {"type": "MultiPoint", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
            {"type": "MultiLineString", "coordinates": [[[0.0, 0.0], [1.0, 1.0]]]},
            {"type": "MultiPolygon", "coordinates": [[_closed_ring()]]},
        ):
            assert build_geo_location({"location": location}) == location

    def test_feature_unwraps_to_its_geometry(self):
        geometry = {"type": "Point", "coordinates": [1.0, 2.0]}
        assert build_geo_location({"location": {"type": "Feature", "geometry": geometry}}) == geometry

    def test_feature_collection_unwraps_to_the_first_geometry(self):
        geometry = {"type": "Point", "coordinates": [3.0, 4.0]}
        location = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": geometry}],
        }
        assert build_geo_location({"location": location}) == geometry

    def test_geojson_supplied_as_a_json_string_is_indexed(self):
        location = '{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}'
        assert build_geo_location({"location": location}) == {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        }

    def test_extra_geojson_members_are_stripped(self):
        location = {"type": "Point", "coordinates": [1.0, 2.0], "bbox": [1.0, 2.0, 1.0, 2.0]}
        assert build_geo_location({"location": location}) == {
            "type": "Point",
            "coordinates": [1.0, 2.0],
        }

    def test_comma_delimited_location_string_still_works(self):
        assert build_geo_location({"location": "40.7,-73.9"}) == {
            "type": "Point",
            "coordinates": [-73.9, 40.7],
        }

    def test_location_object_with_latitude_longitude_still_works(self):
        assert build_geo_location({"location": {"latitude": 40.7, "longitude": -73.9}}) == {
            "type": "Point",
            "coordinates": [-73.9, 40.7],
        }

    def test_lat_lon_metadata_fields_still_work(self):
        assert build_geo_location({"latitude": "40.7", "longitude": "-73.9"}) == {
            "type": "Point",
            "coordinates": [-73.9, 40.7],
        }

    def test_unsupported_geojson_type_falls_back_to_lat_lon_fields(self):
        # An unknown 'type' is not a geometry, so the lat/lon members on the same object are
        # still the usable location.
        location = {"type": "Circle", "coordinates": [1.0, 2.0], "latitude": 5.0, "longitude": 6.0}
        assert build_geo_location({"location": location}) == {
            "type": "Point",
            "coordinates": [6.0, 5.0],
        }

    def test_absent_metadata_yields_no_geo_location(self):
        assert build_geo_location(None) is None
        assert build_geo_location({}) is None
        assert build_geo_location({"assetName": "no location here"}) is None
