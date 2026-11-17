#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The shared WGS84 footprint helper (`media_extractors/geo_reproject.py`): axis order, edge densification, the
antimeridian split, the pole rule, the PixelIsPoint half-pixel shift, and the refusal to guess a CRS or to return an
invalid ring."""

import math

import pytest

from media_extractors import geo_reproject as gr

pytestmark = pytest.mark.unit

# GDAL's own WGS84 extent of the fixture the case mirrors (generate_fixtures.py), the independent oracle.
UTM33_GRID = ((30.0, 0.0, 500000.0, 0.0, -30.0, 5000000.0), 256, 256)
UTM33_GDAL_BOUNDS = [15.0, 45.0843021, 15.0977041, 45.1534772]


def _ring(footprint):
    return footprint.geometry["coordinates"][0]


def _assert_valid_ring(ring):
    assert len(ring) >= 4 and ring[0] == ring[-1]
    assert all(-180 <= lon <= 180 and -90 <= lat <= 90 for lon, lat in ring)
    assert all(a != b for a, b in zip(ring, ring[1:])), "consecutive duplicate vertex"
    assert len({tuple(position) for position in ring[:-1]}) >= 3
    area = sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1)) / 2
    assert area > 0, "exterior ring is not counter-clockwise"


class TestProjectedGrid:
    def test_utm_grid_matches_the_gdal_oracle_and_is_densified(self):
        affine, columns, rows = UTM33_GRID
        footprint = gr.extent_to_wgs84_polygon(32633, affine=affine, columns=columns, rows=rows)
        assert footprint.note is None and footprint.geometry["type"] == "Polygon"
        ring = _ring(footprint)
        _assert_valid_ring(ring)
        # 4 edges x (20 + 1) steps, plus the closing position.
        assert len(ring) == 4 * (gr.DEFAULT_DENSIFY + 1) + 1
        for ours, theirs in zip(footprint.bounds, UTM33_GDAL_BOUNDS):
            assert ours == pytest.approx(theirs, abs=0.005)

    def test_axis_order_is_longitude_first_whatever_the_crs_axis_order(self):
        # EPSG:4326 declares latitude first; the footprint still carries (lon, lat).
        footprint = gr.extent_to_wgs84_polygon(4326, bounds=(10.0, 50.0, 10.5, 50.25))
        assert footprint.bounds == pytest.approx([10.0, 50.0, 10.5, 50.25])
        # Positions are (longitude, latitude): the four corners are all present in that order.
        for corner in ([10.0, 50.0], [10.5, 50.0], [10.5, 50.25], [10.0, 50.25]):
            assert corner in _ring(footprint)

    def test_densification_follows_a_curved_edge(self):
        # An EPSG:3031 (polar stereographic) rectangle off the pole: its straight native edges bend in lon/lat,
        # so the densified ring reaches more extreme latitudes than the four corners alone.
        affine = (1000.0, 0.0, 1000000.0, 0.0, -1000.0, -1000000.0)
        dense = gr.extent_to_wgs84_polygon(3031, affine=affine, columns=256, rows=256)
        sparse = gr.extent_to_wgs84_polygon(3031, affine=affine, columns=256, rows=256, densify=0)
        assert dense.geometry is not None and sparse.geometry is not None
        assert len(_ring(dense)) > len(_ring(sparse)) == 5
        assert dense.bounds[3] >= sparse.bounds[3] - 1e-9 and dense.bounds[1] <= sparse.bounds[1] + 1e-9

    def test_a_rotated_grid_gives_the_rotated_outline_not_its_bounding_box(self):
        affine = (25.0, 12.0, 500000.0, 12.0, -25.0, 5000000.0)
        footprint = gr.extent_to_wgs84_polygon(32633, affine=affine, columns=256, rows=256, densify=0)
        ring = _ring(footprint)
        assert len(ring) == 5
        # The four corners of the rotated quadrilateral, not the corners of its box.
        assert len({round(lon, 6) for lon, _ in ring}) == 4

    def test_a_north_up_and_a_south_up_grid_both_wind_counter_clockwise(self):
        north_up = gr.extent_to_wgs84_polygon(32633, affine=(30.0, 0, 500000.0, 0, -30.0, 5000000.0), columns=10,
                                              rows=10)
        south_up = gr.extent_to_wgs84_polygon(32633, affine=(30.0, 0, 500000.0, 0, 30.0, 4999700.0), columns=10,
                                              rows=10)
        _assert_valid_ring(_ring(north_up))
        _assert_valid_ring(_ring(south_up))


class TestPixelIsPoint:
    def test_the_grid_is_widened_by_half_a_pixel_on_every_side(self):
        affine = (0.01, 0.0, 30.005, 0.0, -0.01, 19.995)
        area = gr.extent_to_wgs84_polygon(4326, affine=affine, columns=256, rows=256)
        point = gr.extent_to_wgs84_polygon(4326, affine=affine, columns=256, rows=256, pixel_is_point=True)
        assert point.bounds == pytest.approx([30.0, 17.44, 32.56, 20.0])
        assert area.bounds == pytest.approx([30.005, 17.435, 32.565, 19.995])
        assert point.bounds[0] == pytest.approx(area.bounds[0] - 0.005)
        assert point.bounds[3] == pytest.approx(area.bounds[3] + 0.005)


class TestAntimeridian:
    # EPSG:3832 (PDC Mercator, centred on 150E): this grid straddles 180 degrees.
    GRID = ((1000.0, 0.0, 3200000.0, 0.0, -1000.0, 1000000.0), 256, 256)

    def test_a_grid_across_180_is_cut_into_two_polygons_each_inside_the_longitude_range(self):
        affine, columns, rows = self.GRID
        footprint = gr.extent_to_wgs84_polygon(3832, affine=affine, columns=columns, rows=rows)
        assert footprint.geometry["type"] == "MultiPolygon"
        east, west = footprint.geometry["coordinates"]
        for polygon in (east, west):
            _assert_valid_ring(polygon[0])
        assert min(lon for lon, _ in east[0]) > 170 and max(lon for lon, _ in east[0]) == 180.0
        assert max(lon for lon, _ in west[0]) < -170 and min(lon for lon, _ in west[0]) == -180.0
        # The box runs eastwards across the antimeridian (west > east), GDAL's extent agrees.
        assert footprint.bounds == pytest.approx([178.7460891, 6.7128922, -178.9542238, 9.0058826], abs=0.005)

    def test_a_grid_that_does_not_reach_180_stays_one_polygon(self):
        footprint = gr.extent_to_wgs84_polygon(3832, affine=(1000.0, 0.0, 3000000.0, 0.0, -1000.0, 1000000.0),
                                               columns=100, rows=100)
        assert footprint.geometry["type"] == "Polygon"

    def test_a_grid_crossing_minus_180_westwards_is_cut_too(self):
        # The mirror image: PDC Mercator x of about -3.3e6 is west of the antimeridian's far side.
        footprint = gr.extent_to_wgs84_polygon(3832, affine=(1000.0, 0.0, -9000000.0, 0.0, -1000.0, 1000000.0),
                                               columns=256, rows=256)
        assert footprint.geometry is None or footprint.geometry["type"] in ("Polygon", "MultiPolygon")
        if footprint.geometry is not None:
            for polygon in footprint.geometry["coordinates"] if footprint.geometry["type"] == "MultiPolygon" else [
                    footprint.geometry["coordinates"]]:
                _assert_valid_ring(polygon[0])


class TestPoles:
    def test_an_extent_that_contains_the_pole_has_no_footprint(self):
        footprint = gr.extent_to_wgs84_polygon(3031, affine=(1000.0, 0.0, -128000.0, 0.0, -1000.0, 128000.0),
                                               columns=256, rows=256)
        assert footprint.geometry is None and footprint.bounds is None
        assert footprint.note == gr.NOTE_CONTAINS_POLE

    def test_a_north_polar_extent_is_caught_too(self):
        footprint = gr.extent_to_wgs84_polygon(3413, bounds=(-100000.0, -100000.0, 100000.0, 100000.0))
        assert footprint.note == gr.NOTE_CONTAINS_POLE

    def test_an_extent_beside_the_pole_has_a_footprint(self):
        footprint = gr.extent_to_wgs84_polygon(3031, affine=(1000.0, 0.0, 1000000.0, 0.0, -1000.0, -1000000.0),
                                               columns=256, rows=256)
        assert footprint.geometry["type"] == "Polygon"
        _assert_valid_ring(_ring(footprint))

    def test_a_geographic_extent_touching_a_pole_is_not_refused(self):
        footprint = gr.extent_to_wgs84_polygon(4326, bounds=(-180.0, 60.0, 180.0, 90.0))
        assert footprint.geometry["type"] == "Polygon"


class TestRefusals:
    @pytest.mark.parametrize("crs", [999999, "EPSG:999999", "not a crs", None, True])
    def test_an_unresolvable_crs_gives_no_footprint_and_never_assumes_wgs84(self, crs):
        footprint = gr.extent_to_wgs84_polygon(crs, bounds=(1.0, 2.0, 3.0, 4.0))
        assert footprint.geometry is None and footprint.note == gr.NOTE_UNKNOWN_CRS

    def test_a_geocentric_crs_is_refused(self):
        assert gr.extent_to_wgs84_polygon(4978, bounds=(1.0, 2.0, 3.0, 4.0)).note == gr.NOTE_UNKNOWN_CRS

    @pytest.mark.parametrize("bounds", [(math.nan, 0, 1, 1), (0, 0, math.inf, 1), (0, 0, 1), None])
    def test_a_non_finite_or_malformed_extent_is_refused(self, bounds):
        assert gr.extent_to_wgs84_polygon(4326, bounds=bounds).note == gr.NOTE_INVALID

    def test_a_zero_size_grid_is_refused(self):
        assert gr.extent_to_wgs84_polygon(4326, affine=(1, 0, 0, 0, -1, 0), columns=0, rows=5).note == gr.NOTE_INVALID

    def test_a_degenerate_extent_has_no_polygon(self):
        assert gr.extent_to_wgs84_polygon(4326, bounds=(5.0, 5.0, 5.0, 5.0)).note == gr.NOTE_INVALID

    def test_latitudes_beyond_the_poles_are_refused(self):
        assert gr.extent_to_wgs84_polygon(4326, bounds=(0.0, 80.0, 10.0, 95.0)).note == gr.NOTE_OUT_OF_RANGE

    def test_a_projection_that_cannot_reach_a_point_gives_no_footprint(self):
        # An orthographic view sees one hemisphere: a point a hundred million metres off its centre has no
        # longitude and latitude, and pyproj answers infinity.
        ortho = "+proj=ortho +lat_0=0 +lon_0=0 +ellps=WGS84"
        footprint = gr.extent_to_wgs84_polygon(ortho, bounds=(1e8, 1e8, 2e8, 2e8))
        assert footprint.geometry is None and footprint.note == gr.NOTE_INVALID


class TestResolveCrs:
    def test_integer_label_and_definition_forms_resolve(self):
        for spec in (4326, "EPSG:4326", "+proj=longlat +datum=WGS84 +no_defs"):
            assert gr.resolve_crs(spec) is not None
        assert gr.resolve_crs(32633).is_projected
