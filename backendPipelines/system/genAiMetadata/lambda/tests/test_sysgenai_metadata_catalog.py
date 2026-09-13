#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The ext_* promotion is deterministic, typed, class-gated and reads only the registry's source keys;
every value it emits is accepted by the backend's own type validation, and every location it derives
passes the backend's GeoJSON rules.

The backend module is loaded by path with its pydantic layer inert (sysgenai_harness); a positive and a
negative control run through it first, so a load that produced an inert validator cannot pass."""

import json
import math

import pytest

import sysgenai_harness as h

mc = h.load_local("metadataCatalog")
fc = h.load_local("fileClassifier")
common = h.load_local("analysisCommon")
backend = h.load_backend_metadata_validators()

SYS_FILE = {"name": "f", "ext": ".x", "sizeBytes": 1, "contentType": "", "etag": "e", "versionId": "v1"}

MESH = {
    "sys_file": SYS_FILE,
    "sys_geometry": {"boundsMin": [-0.5, -1.0, -1.5], "boundsMax": [0.5, 1.0, 1.5],
                     "dimensions": {"width": 1.0, "height": 2.0, "depth": 3.0}, "extentMax": 3.0,
                     "volume": 6.0, "surfaceArea": 22.0, "units": "m"},
    "sys_statistics": {"meshCount": 1, "vertices": 8, "faces": 12, "triangles": 12, "watertight": True},
    "sys_visual": {"materialCount": 1, "textureCount": 0, "hasUv": True, "hasVertexColors": False},
    "sys_scene": {"nodeCount": 1, "hasAnimation": False, "hasArmature": False},
}
CAD = {
    "sys_file": SYS_FILE,
    "sys_geometry": {"boundsMin": [0.0, 0.0, 0.0], "boundsMax": [1500.0, 400.0, 100.0],
                     "dimensions": [1500.0, 400.0, 100.0], "extentMax": 1500.0, "volume": 1.0, "surfaceArea": 2.0},
    "sys_cad": {"solidCount": 3, "faceCount": 30, "edgeCount": 60, "assemblyCount": 2,
                "declaredUnits": "mm", "estimatedUnits": "cm"},
}
POINTCLOUD = {
    "sys_file": SYS_FILE,
    "sys_pointcloud": {"pointCount": 1234567, "hasColor": True, "boundsMin": [8.5, 47.3, 400.0],
                       "boundsMax": [8.6, 47.4, 900.0], "crs": {"epsg": 4326, "name": "WGS 84", "geographic": True}},
}
IMAGE = {
    "sys_file": SYS_FILE,
    "sys_image": {"width": 6000, "height": 4000, "mode": "RGB",
                  "exif": {"make": "Canon", "model": "EOS R5", "dateTimeOriginal": "2026:01:02 03:04:05",
                           "gps": {"latitude": 47.37, "longitude": 8.54, "altitude": 408.0}}},
}
IFC = {
    "sys_file": SYS_FILE,
    "sys_ifc": {"schema": "IFC4", "projectName": "Clinic", "storeyCount": 2, "elementCount": 40,
                "site": {"latitude": 48.2082, "longitude": 16.3738, "elevation": 171.0}},
}
TILES3D = {
    "sys_file": SYS_FILE,
    "sys_tiles3d": {"geometricError": 500, "tileCount": 3, "region": [-1.3197, 0.6988, -1.3196, 0.6989, 0, 88]},
}
GEO_POLYGON = {"type": "Polygon", "coordinates": [[[8.5, 47.3], [8.6, 47.3], [8.6, 47.4], [8.5, 47.4], [8.5, 47.3]]]}
GEO = {"sys_file": SYS_FILE, "sys_geo": {"featureCount": 12, "geometryTypes": ["Point", "Polygon"], "footprint": GEO_POLYGON}}
VIDEO = {
    "sys_file": SYS_FILE,
    "sys_media": {"kind": "video", "durationSeconds": 92.48, "width": 1920, "height": 1080, "frameRate": 29.97,
                  "videoCodec": "h264", "audioCodec": "aac", "bitrateKbps": 4628, "channels": 2, "sampleRate": 48000,
                  "tags": {"title": "Site walk", "artist": "Survey team", "album": "Q3", "year": "2019"}},
}
AUDIO = {"sys_file": SYS_FILE, "sys_media": {"kind": "audio", "durationSeconds": 12.5, "channels": 1,
                                             "sampleRate": 44100, "bitrateKbps": 128, "audioCodec": "mp3",
                                             "tags": {"title": "Note", "year": 2021}}}
DOCUMENT = {"sys_file": SYS_FILE, "sys_document": {"pageCount": 7, "title": "Spec", "author": "A. Author",
                                                   "createdAt": "2024-05-06T07:08:09Z", "hasText": True}}
TEXT = {"sys_file": SYS_FILE, "sys_text": {"encoding": "utf-8", "lineCount": 120, "wordCount": 900, "language": "en"}}
DATA = {"sys_file": SYS_FILE, "sys_data": {"columnCount": 3, "rowCount": 41, "columns": ["id", "name", "qty"]}}

FIXTURES = [(MESH, "mesh"), (MESH, "usd"), (CAD, "cad"), (POINTCLOUD, "pointcloud"), (POINTCLOUD, "splat"),
            (IMAGE, "image"), (IFC, "ifc"), (TILES3D, "tiles3d"), (GEO, "data"), (VIDEO, "video"),
            (AUDIO, "audio"), (DOCUMENT, "document"), (TEXT, "text"), (DATA, "data")]

# Registry §3.6 "Metadata keys — promoted", by type.
REGISTRY_EXT_KEYS = {
    "xyz": {"ext_dimensions", "ext_bounds_min", "ext_bounds_max"},
    "string": {"ext_units", "ext_size_category", "ext_crs", "ext_ifc_schema", "ext_project_name", "ext_color_mode",
               "ext_camera", "ext_resolution", "ext_video_codec", "ext_audio_codec", "ext_title", "ext_artist",
               "ext_album", "ext_author", "ext_language", "ext_encoding", "ext_columns", "ext_geometry_types"},
    "number": {"ext_extent_max", "ext_volume", "ext_surface_area", "ext_vertex_count", "ext_face_count",
               "ext_triangle_count", "ext_mesh_count", "ext_material_count", "ext_texture_count", "ext_object_count",
               "ext_solid_count", "ext_edge_count", "ext_assembly_count", "ext_point_count", "ext_storey_count",
               "ext_element_count", "ext_geometric_error", "ext_tile_count", "ext_width", "ext_height",
               "ext_duration_seconds", "ext_frame_rate", "ext_bitrate_kbps", "ext_channels", "ext_sample_rate",
               "ext_year", "ext_page_count", "ext_line_count", "ext_word_count", "ext_row_count", "ext_column_count",
               "ext_feature_count"},
    "boolean": {"ext_watertight", "ext_has_textures", "ext_has_uv", "ext_has_vertex_colors", "ext_has_animation",
                "ext_has_armature", "ext_has_color", "ext_has_text"},
    "date": {"ext_captured_at", "ext_created_at"},
}


def _items(attributes, file_class):
    return {item["metadataKey"]: item for item in mc.promote(attributes, file_class)}


def _value(attributes, file_class, key):
    return _items(attributes, file_class)[key]["metadataValue"]


@pytest.mark.unit
class TestBackendLoaderControls:
    def test_the_loaded_backend_validator_is_live(self):
        # Positive and negative controls: an inert stub would accept everything or nothing.
        assert backend.validate_metadata_value_common('{"x":1,"y":2,"z":3}', "xyz") == '{"x":1,"y":2,"z":3}'
        with pytest.raises(ValueError):
            backend.validate_metadata_value_common('{"x":1,"y":2}', "xyz")
        with pytest.raises(ValueError):
            backend.validate_metadata_value_common("2026:01:02 03:04:05", "date")
        with pytest.raises(ValueError):
            backend._validate_geojson_value({"type": "Point", "coordinates": [200, 0]})

    def test_catalogue_types_are_types_the_backend_knows(self):
        known = {member.value for member in backend.MetadataValueType}
        assert {field.value_type for field in mc.PROMOTED_FIELDS} <= known
        assert mc.TYPE_GEOJSON in known


@pytest.mark.unit
class TestCatalogueShape:
    def test_keys_match_the_registry_exactly(self):
        by_type = {}
        for field in mc.PROMOTED_FIELDS:
            by_type.setdefault(field.value_type, set()).add(field.key)
        assert by_type == REGISTRY_EXT_KEYS
        assert len(mc.PROMOTED_FIELDS) == 63 == sum(len(keys) for keys in REGISTRY_EXT_KEYS.values())

    def test_every_key_is_prefixed_and_unique(self):
        keys = [field.key for field in mc.PROMOTED_FIELDS]
        assert len(keys) == len(set(keys))
        assert all(key.startswith(mc.PROMOTED_PREFIX) for key in keys)
        assert mc.PIPELINE_OWNED_PREFIXES == ("ext_", "genai_")
        assert mc.LOCATION_KEY == "location"

    def test_the_owned_prefixes_are_excluded_from_the_existing_metadata_the_analysis_re_reads(self):
        """analysisCommon.existing_metadata_lines skips every key this pipeline writes when it re-reads the
        execution envelope for the prompt and the embedding; its exclusion list must cover the ownership
        prefixes here (plus sys_, the attribute groups) or an earlier run's ext_*/genai_* rows would be fed
        back into the next run."""
        assert set(mc.PIPELINE_OWNED_PREFIXES) <= set(common.EXISTING_METADATA_EXCLUDED_PREFIXES)
        assert "sys_" in common.EXISTING_METADATA_EXCLUDED_PREFIXES

    def test_every_source_path_names_a_registry_group(self):
        groups = {"sys_geometry", "sys_statistics", "sys_visual", "sys_scene", "sys_cad", "sys_pointcloud", "sys_ifc",
                  "sys_image", "sys_media", "sys_document", "sys_text", "sys_data", "sys_tiles3d", "sys_geo"}
        for field in mc.PROMOTED_FIELDS:
            assert field.source and all(path.split(".")[0] in groups for path in field.source), field.key
            assert field.classes and field.classes <= set(fc.FILE_CLASSES), field.key

    def test_thresholds_and_units(self):
        assert mc.SIZE_CATEGORY_THRESHOLDS_M == ((0.3, "tiny"), (1.0, "small"), (2.0, "medium"), (5.0, "large"))
        assert mc.SIZE_CATEGORY_LARGEST == "huge"
        assert mc.UNIT_TO_METRES == {"m": 1, "mm": 0.001, "cm": 0.01, "in": 0.0254, "ft": 0.3048}


@pytest.mark.unit
class TestMesh:
    def test_mesh_with_units_promotes_dimensions_units_and_size(self):
        rows = _items(MESH, "mesh")
        assert json.loads(rows["ext_dimensions"]["metadataValue"]) == {"x": 1.0, "y": 2.0, "z": 3.0}
        assert rows["ext_dimensions"]["metadataValueType"] == "xyz"
        assert json.loads(rows["ext_bounds_min"]["metadataValue"]) == {"x": -0.5, "y": -1.0, "z": -1.5}
        assert json.loads(rows["ext_bounds_max"]["metadataValue"]) == {"x": 0.5, "y": 1.0, "z": 1.5}
        assert rows["ext_units"] == {"metadataKey": "ext_units", "metadataValue": "m", "metadataValueType": "string"}
        assert rows["ext_size_category"]["metadataValue"] == "large"  # 3.0 m: >= 2, < 5
        assert (rows["ext_extent_max"]["metadataValue"], rows["ext_volume"]["metadataValue"],
                rows["ext_surface_area"]["metadataValue"]) == ("3", "6", "22")
        assert rows["ext_extent_max"]["metadataValueType"] == "number"
        assert (rows["ext_vertex_count"]["metadataValue"], rows["ext_face_count"]["metadataValue"],
                rows["ext_triangle_count"]["metadataValue"], rows["ext_mesh_count"]["metadataValue"]) == ("8", "12", "12", "1")
        assert rows["ext_watertight"] == {"metadataKey": "ext_watertight", "metadataValue": "true",
                                          "metadataValueType": "boolean"}
        assert (rows["ext_material_count"]["metadataValue"], rows["ext_texture_count"]["metadataValue"]) == ("1", "0")
        assert rows["ext_has_textures"]["metadataValue"] == "false"
        assert (rows["ext_has_uv"]["metadataValue"], rows["ext_has_vertex_colors"]["metadataValue"]) == ("true", "false")
        assert rows["ext_object_count"]["metadataValue"] == "1"
        assert (rows["ext_has_animation"]["metadataValue"], rows["ext_has_armature"]["metadataValue"]) == ("false", "false")
        # Catalogue order is preserved and cad-only rows are absent for a mesh.
        assert list(rows)[:4] == ["ext_dimensions", "ext_bounds_min", "ext_bounds_max", "ext_units"]
        assert "ext_solid_count" not in rows and "ext_point_count" not in rows

    @pytest.mark.parametrize("extent,units,expected", [
        (0.2, "m", "tiny"), (0.5, "m", "small"), (1500, "mm", "medium"), (100, "in", "large"), (20, "ft", "huge"),
        (299, "mm", "tiny"), (0.3, "m", "small"), (5.0, "m", "huge"),
    ])
    def test_size_category_thresholds_in_metres(self, extent, units, expected):
        attributes = {"sys_geometry": {"extentMax": extent, "units": units}}
        assert _value(attributes, "mesh", "ext_size_category") == expected

    def test_mesh_without_units_has_no_size_category_and_no_units(self):
        attributes = {"sys_geometry": dict(MESH["sys_geometry"])}
        del attributes["sys_geometry"]["units"]
        rows = _items(attributes, "mesh")
        assert "ext_units" not in rows and "ext_size_category" not in rows
        assert "ext_dimensions" in rows and rows["ext_extent_max"]["metadataValue"] == "3"

    def test_a_units_string_outside_the_contract_is_omitted(self):
        rows = _items({"sys_geometry": {"extentMax": 3.0, "units": "millimetre"}}, "mesh")
        assert "ext_units" not in rows and "ext_size_category" not in rows
        assert _value({"sys_geometry": {"extentMax": 3.0, "units": " MM "}}, "usd", "ext_units") == "mm"

    def test_usd_uses_the_same_rows(self):
        assert set(_items(MESH, "usd")) == set(_items(MESH, "mesh"))


@pytest.mark.unit
class TestCad:
    def test_cad_promotes_counts_and_declared_units(self):
        rows = _items(CAD, "cad")
        assert rows["ext_units"]["metadataValue"] == "mm"  # declaredUnits wins over estimatedUnits
        assert rows["ext_size_category"]["metadataValue"] == "medium"  # 1500 mm = 1.5 m
        assert json.loads(rows["ext_dimensions"]["metadataValue"]) == {"x": 1500.0, "y": 400.0, "z": 100.0}
        assert (rows["ext_solid_count"]["metadataValue"], rows["ext_face_count"]["metadataValue"],
                rows["ext_edge_count"]["metadataValue"], rows["ext_assembly_count"]["metadataValue"]) == ("3", "30", "60", "2")
        assert "ext_vertex_count" not in rows  # no sys_statistics in this fixture

    def test_estimated_units_are_the_fallback(self):
        attributes = {"sys_geometry": {"extentMax": 250.0}, "sys_cad": {"declaredUnits": None, "estimatedUnits": "cm"}}
        rows = _items(attributes, "cad")
        assert rows["ext_units"]["metadataValue"] == "cm" and rows["ext_size_category"]["metadataValue"] == "large"

    def test_an_unrecognised_geometry_unit_does_not_hide_a_declared_one(self):
        attributes = {"sys_geometry": {"extentMax": 250.0, "units": "unknown"}, "sys_cad": {"declaredUnits": "cm"}}
        rows = _items(attributes, "cad")
        assert rows["ext_units"]["metadataValue"] == "cm" and rows["ext_size_category"]["metadataValue"] == "large"

    def test_statistics_faces_win_over_cad_faces_when_both_exist(self):
        attributes = {"sys_statistics": {"faces": 7}, "sys_cad": {"faceCount": 30}}
        assert _value(attributes, "cad", "ext_face_count") == "7"


@pytest.mark.unit
class TestPointCloudAndSplat:
    def test_pointcloud_promotes_counts_color_crs_and_bounds(self):
        rows = _items(POINTCLOUD, "pointcloud")
        assert rows["ext_point_count"] == {"metadataKey": "ext_point_count", "metadataValue": "1234567",
                                           "metadataValueType": "number"}
        assert rows["ext_has_color"]["metadataValue"] == "true"
        assert rows["ext_crs"]["metadataValue"] == "EPSG:4326"
        assert json.loads(rows["ext_bounds_min"]["metadataValue"]) == {"x": 8.5, "y": 47.3, "z": 400.0}
        assert "ext_dimensions" not in rows

    def test_crs_without_epsg_uses_the_name(self):
        attributes = {"sys_pointcloud": {"crs": {"epsg": None, "name": "Local grid", "geographic": False}}}
        assert _value(attributes, "pointcloud", "ext_crs") == "Local grid"
        assert "ext_crs" not in _items({"sys_pointcloud": {"crs": {"epsg": None, "name": None, "geographic": False}}},
                                       "pointcloud")

    def test_geographic_bounds_make_a_polygon_location(self):
        location = mc.location_geojson(POINTCLOUD, "pointcloud")
        assert location == {"type": "Polygon", "coordinates": [[[8.5, 47.3], [8.6, 47.3], [8.6, 47.4], [8.5, 47.4],
                                                                [8.5, 47.3]]]}

    def test_a_projected_cloud_has_crs_but_no_location(self):
        attributes = {"sys_pointcloud": dict(POINTCLOUD["sys_pointcloud"],
                                             crs={"epsg": 32632, "name": "WGS 84 / UTM 32N", "geographic": False},
                                             boundsMin=[465000.0, 5247000.0, 400.0], boundsMax=[466000.0, 5248000.0, 900.0])}
        assert _value(attributes, "pointcloud", "ext_crs") == "EPSG:32632"
        assert mc.location_geojson(attributes, "pointcloud") is None

    def test_splat_uses_the_pointcloud_rows_and_location(self):
        assert _value(POINTCLOUD, "splat", "ext_point_count") == "1234567"
        assert mc.location_geojson(POINTCLOUD, "splat")["type"] == "Polygon"

    def test_a_zero_extent_box_is_a_point_and_a_line_box_is_dropped(self):
        flat = {"sys_pointcloud": {"boundsMin": [8.5, 47.3, 0], "boundsMax": [8.5, 47.3, 9], "crs": {"geographic": True}}}
        assert mc.location_geojson(flat, "pointcloud") == {"type": "Point", "coordinates": [8.5, 47.3]}
        line = {"sys_pointcloud": {"boundsMin": [8.5, 47.3, 0], "boundsMax": [8.6, 47.3, 9], "crs": {"geographic": True}}}
        assert mc.location_geojson(line, "pointcloud") is None


@pytest.mark.unit
class TestImage:
    def test_image_promotes_dimensions_camera_and_capture_date(self):
        rows = _items(IMAGE, "image")
        assert (rows["ext_width"]["metadataValue"], rows["ext_height"]["metadataValue"]) == ("6000", "4000")
        assert rows["ext_color_mode"]["metadataValue"] == "RGB"
        assert rows["ext_camera"]["metadataValue"] == "Canon EOS R5"
        assert rows["ext_captured_at"] == {"metadataKey": "ext_captured_at", "metadataValue": "2026-01-02T03:04:05",
                                           "metadataValueType": "date"}
        assert "ext_resolution" not in rows  # video-only

    def test_exif_gps_makes_a_point_location_with_altitude(self):
        assert mc.location_geojson(IMAGE, "image") == {"type": "Point", "coordinates": [8.54, 47.37, 408.0]}

    def test_gps_without_altitude_is_a_two_d_point(self):
        attributes = {"sys_image": {"exif": {"gps": {"latitude": 47.37, "longitude": 8.54}}}}
        assert mc.location_geojson(attributes, "image") == {"type": "Point", "coordinates": [8.54, 47.37]}

    def test_out_of_range_gps_is_dropped(self):
        attributes = {"sys_image": {"exif": {"gps": {"latitude": 95.0, "longitude": 8.54}}}}
        assert mc.location_geojson(attributes, "image") is None

    def test_camera_with_only_a_make_and_an_iso_capture_date(self):
        attributes = {"sys_image": {"exif": {"make": " Canon ", "dateTimeOriginal": "2026-01-02T03:04:05Z"}}}
        rows = _items(attributes, "image")
        assert rows["ext_camera"]["metadataValue"] == "Canon"
        assert rows["ext_captured_at"]["metadataValue"] == "2026-01-02T03:04:05Z"
        assert "ext_captured_at" not in _items({"sys_image": {"exif": {"dateTimeOriginal": "yesterday"}}}, "image")


@pytest.mark.unit
class TestIfc:
    def test_ifc_promotes_schema_project_counts_and_site_point(self):
        rows = _items(IFC, "ifc")
        assert (rows["ext_ifc_schema"]["metadataValue"], rows["ext_project_name"]["metadataValue"]) == ("IFC4", "Clinic")
        assert (rows["ext_storey_count"]["metadataValue"], rows["ext_element_count"]["metadataValue"]) == ("2", "40")
        assert mc.location_geojson(IFC, "ifc") == {"type": "Point", "coordinates": [16.3738, 48.2082, 171.0]}

    def test_a_null_site_has_no_location(self):
        assert mc.location_geojson({"sys_ifc": {"schema": "IFC2X3", "site": None}}, "ifc") is None


@pytest.mark.unit
class TestTiles3d:
    def test_region_radians_become_a_degrees_polygon(self):
        rows = _items(TILES3D, "tiles3d")
        assert (rows["ext_geometric_error"]["metadataValue"], rows["ext_tile_count"]["metadataValue"]) == ("500", "3")
        west, south, east, north = (round(math.degrees(v), 7) for v in TILES3D["sys_tiles3d"]["region"][:4])
        assert mc.location_geojson(TILES3D, "tiles3d") == {
            "type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]]}
        assert -76 < west < -75 and 40 < south < 41  # sanity: radians were converted, not passed through

    def test_a_null_region_has_no_location(self):
        assert mc.location_geojson({"sys_tiles3d": {"geometricError": 1, "tileCount": 1, "region": None}}, "tiles3d") is None


@pytest.mark.unit
class TestGeo:
    def test_geo_promotes_counts_and_types_and_passes_a_polygon_footprint_through(self):
        rows = _items(GEO, "data")
        assert rows["ext_feature_count"]["metadataValue"] == "12"
        assert rows["ext_geometry_types"] == {"metadataKey": "ext_geometry_types", "metadataValue": "Point, Polygon",
                                              "metadataValueType": "string"}
        assert mc.location_geojson(GEO, "data") == GEO_POLYGON

    def test_a_point_footprint_passes_through(self):
        point = {"type": "Point", "coordinates": [8.55, 47.35]}
        assert mc.location_geojson({"sys_geo": {"footprint": point}}, "data") == point

    def test_other_geometries_reduce_to_the_bbox_polygon(self):
        multi = {"type": "MultiPolygon", "coordinates": [
            [[[8.5, 47.3], [8.55, 47.3], [8.55, 47.35], [8.5, 47.35], [8.5, 47.3]]],
            [[[8.58, 47.38], [8.6, 47.38], [8.6, 47.4], [8.58, 47.4], [8.58, 47.38]]]]}
        assert mc.location_geojson({"sys_geo": {"footprint": multi}}, "data") == {
            "type": "Polygon", "coordinates": [[[8.5, 47.3], [8.6, 47.3], [8.6, 47.4], [8.5, 47.4], [8.5, 47.3]]]}
        collection = {"type": "GeometryCollection", "geometries": [
            {"type": "Point", "coordinates": [8.5, 47.3]}, {"type": "LineString", "coordinates": [[8.6, 47.3], [8.6, 47.4]]}]}
        assert mc.location_geojson({"sys_geo": {"footprint": collection}}, "data")["type"] == "Polygon"

    def test_an_invalid_footprint_is_dropped(self):
        bad = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 0]]]}
        assert mc.location_geojson({"sys_geo": {"footprint": bad}}, "data") is None
        assert mc.location_geojson({"sys_geo": {"footprint": {"type": "Point", "coordinates": [181, 0]}}}, "data") is None


@pytest.mark.unit
class TestMediaDocumentTextData:
    def test_video(self):
        rows = _items(VIDEO, "video")
        assert (rows["ext_duration_seconds"]["metadataValue"], rows["ext_frame_rate"]["metadataValue"]) == ("92.48", "29.97")
        assert (rows["ext_width"]["metadataValue"], rows["ext_height"]["metadataValue"]) == ("1920", "1080")
        assert rows["ext_resolution"]["metadataValue"] == "1920x1080"
        assert (rows["ext_video_codec"]["metadataValue"], rows["ext_audio_codec"]["metadataValue"]) == ("h264", "aac")
        assert (rows["ext_bitrate_kbps"]["metadataValue"], rows["ext_channels"]["metadataValue"],
                rows["ext_sample_rate"]["metadataValue"]) == ("4628", "2", "48000")
        assert (rows["ext_title"]["metadataValue"], rows["ext_artist"]["metadataValue"],
                rows["ext_album"]["metadataValue"]) == ("Site walk", "Survey team", "Q3")
        assert rows["ext_year"] == {"metadataKey": "ext_year", "metadataValue": "2019", "metadataValueType": "number"}
        assert mc.location_geojson(VIDEO, "video") is None

    def test_audio(self):
        rows = _items(AUDIO, "audio")
        assert rows["ext_duration_seconds"]["metadataValue"] == "12.5"
        assert (rows["ext_channels"]["metadataValue"], rows["ext_sample_rate"]["metadataValue"]) == ("1", "44100")
        assert rows["ext_year"]["metadataValue"] == "2021" and rows["ext_title"]["metadataValue"] == "Note"
        assert "ext_resolution" not in rows and "ext_width" not in rows and "ext_video_codec" not in rows

    def test_document(self):
        rows = _items(DOCUMENT, "document")
        assert (rows["ext_page_count"]["metadataValue"], rows["ext_title"]["metadataValue"],
                rows["ext_author"]["metadataValue"]) == ("7", "Spec", "A. Author")
        assert rows["ext_created_at"] == {"metadataKey": "ext_created_at", "metadataValue": "2024-05-06T07:08:09Z",
                                          "metadataValueType": "date"}
        assert rows["ext_has_text"]["metadataValue"] == "true"
        assert "ext_created_at" not in _items({"sys_document": {"createdAt": "D:20240506070809"}}, "document")

    def test_text(self):
        rows = _items(TEXT, "text")
        assert (rows["ext_language"]["metadataValue"], rows["ext_encoding"]["metadataValue"]) == ("en", "utf-8")
        assert (rows["ext_line_count"]["metadataValue"], rows["ext_word_count"]["metadataValue"]) == ("120", "900")

    def test_data(self):
        rows = _items(DATA, "data")
        assert (rows["ext_row_count"]["metadataValue"], rows["ext_column_count"]["metadataValue"]) == ("41", "3")
        assert rows["ext_columns"]["metadataValue"] == "id, name, qty"
        assert "ext_feature_count" not in rows


@pytest.mark.unit
class TestGeneralRules:
    @pytest.mark.parametrize("file_class", fc.FILE_CLASSES)
    def test_absent_sources_produce_no_item(self, file_class):
        assert mc.promote({"sys_file": SYS_FILE}, file_class) == []
        assert mc.location_geojson({"sys_file": SYS_FILE}, file_class) is None
        assert mc.promote({}, file_class) == [] and mc.promote(None, file_class) == []

    def test_null_and_unusable_values_produce_no_item(self):
        attributes = {"sys_geometry": {"volume": None, "units": None, "extentMax": "lots", "surfaceArea": float("nan")},
                      "sys_statistics": {"watertight": "maybe", "faces": True}}
        rows = _items(attributes, "mesh")
        assert rows == {}

    def test_the_class_gates_the_catalogue(self):
        assert "ext_dimensions" not in _items(MESH, "image")
        assert "ext_width" not in _items(IMAGE, "mesh")
        assert mc.promote(MESH, "other") == []

    @pytest.mark.parametrize("attributes,file_class", FIXTURES)
    def test_every_emitted_key_is_prefixed_or_location(self, attributes, file_class):
        items = mc.promote(attributes, file_class)
        assert items, (file_class, "fixture produced nothing — the assertions below would be vacuous")
        assert all(item["metadataKey"].startswith("ext_") for item in items)
        assert all(set(item) == {"metadataKey", "metadataValue", "metadataValueType"} for item in items)
        assert all(isinstance(item["metadataValue"], str) and item["metadataValue"] for item in items)
        location = mc.location_geojson(attributes, file_class)
        assert location is None or location["type"] in ("Point", "Polygon")

    def test_number_rendering(self):
        assert mc.render_value(12, "number") == "12" and mc.render_value(12.0, "number") == "12"
        assert mc.render_value(3.14159265, "number") == "3.141593"
        assert mc.render_value("2019", "number") == "2019"
        assert mc.render_value(True, "number") is None and mc.render_value("x", "number") is None
        assert mc.render_value(float("inf"), "number") is None
        assert mc.xyz_text([1, 2]) is None and mc.xyz_text({"x": 1, "y": 2, "z": "z"}) is None


@pytest.mark.unit
class TestBackendCompatibility:
    @pytest.mark.parametrize("attributes,file_class", FIXTURES)
    def test_every_promoted_value_validates_under_its_backend_type(self, attributes, file_class):
        items = mc.promote(attributes, file_class)
        assert items
        for item in items:
            assert backend.validate_metadata_value_common(item["metadataValue"], item["metadataValueType"]) == \
                item["metadataValue"], item
        location = mc.location_geojson(attributes, file_class)
        if location is not None:
            encoded = json.dumps(location, separators=(",", ":"))
            assert backend.validate_metadata_value_common(encoded, "geojson") == encoded

    @pytest.mark.parametrize("candidate", [
        {"type": "Point", "coordinates": [8.5, 47.3]},
        {"type": "Point", "coordinates": [8.5, 47.3, 400.0]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}},
        {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}}]},
        {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [1, 2]}]},
        {"type": "Point", "coordinates": [181, 0]},
        {"type": "Point", "coordinates": [0, 91]},
        {"type": "Point", "coordinates": ["8.5", 47.3]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 0]]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [1, 1]]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 0], [1, 1], [0, 0]]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 0], [1, 1], [0, 0]]]},
        {"type": "Polygon", "coordinates": []},
        {"type": "LineString", "coordinates": [[0, 0]]},
        {"type": "Circle", "coordinates": [0, 0]},
        {"type": "Feature", "geometry": None},
        {"type": "FeatureCollection", "features": []},
        {"type": "GeometryCollection", "geometries": []},
        [1, 2],
        "not an object",
    ])
    def test_location_rules_match_the_backend_validator(self, candidate):
        """Same verdict on both sides for every fixture — valid and invalid — so the restated rules
        cannot drift from backend/backend/models/metadata.py without this failing."""
        def verdict(validate):
            try:
                validate(candidate)
                return None
            except ValueError as e:
                return str(e).replace("GeoJSON", "").strip()

        assert verdict(mc.validate_geojson_value) == verdict(backend._validate_geojson_value)

    def test_nesting_depth_limit_matches(self):
        assert mc.MAX_GEOJSON_NESTING_DEPTH == backend.MAX_GEOJSON_NESTING_DEPTH == 32
        nested = {"type": "Point", "coordinates": [1, 2]}
        for _ in range(mc.MAX_GEOJSON_NESTING_DEPTH):
            nested = {"type": "GeometryCollection", "geometries": [nested]}
        with pytest.raises(ValueError, match="nested more than"):
            mc.validate_geojson_value(nested)
        with pytest.raises(ValueError, match="nested more than"):
            backend._validate_geojson_value(nested)
