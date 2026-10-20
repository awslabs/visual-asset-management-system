#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The GIS extractor: a GeoPackage read through sqlite3 and an ArcGIS Pro layer file read as a CIM document, both
written to the GeoJSON extractor's `sys_geo` shape (`format`, `featureCount`, `geometryTypes`, `footprint`) plus
`layers[]` and `crs`. A footprint is written for EPSG:4326 only; another spatial reference keeps its native extent
and the catalog gets no `location`. The GeoPackage read is bounded whatever the file's size: metadata tables, a
`PRAGMA table_info` per layer and a LIMIT-bounded count, never a feature row or a geometry blob, through a read-only
connection. A layer file over the document bound, a non-SQLite `.gpkg`, an SQLite file without `gpkg_contents` and
a JSON document that is not a CIMLayerDocument all degrade to `other`."""

import json
import sqlite3

import pytest

from media_extractors import gis
from media_extractors.common import CLASS_DATA, CLASS_OTHER
from system_genai_media_fixtures import geopackage_bytes, lyrx_dict, make_ctx, write

_SYDNEY = (151.1, -33.9, 151.3, -33.8)
_MERCATOR = (16800000.0, -4020000.0, 16830000.0, -4000000.0)


def _gpkg(tmp_path, layers=None, name="fixture.gpkg"):
    path = write(tmp_path, name, geopackage_bytes(layers))
    return path, make_ctx(tmp_path, name, extract_geo_location=True)


def _lyrx(tmp_path, document, name="parcels.lyrx"):
    path = write(tmp_path, name, json.dumps(document).encode("utf-8"))
    return path, make_ctx(tmp_path, name, content_type="application/json")


@pytest.mark.unit
class TestGeoPackage:
    def test_a_wgs84_layer_writes_the_geojson_contract_with_layers_and_a_footprint(self, tmp_path):
        path, ctx = _gpkg(tmp_path)
        result = gis.extract_geopackage(path, ctx)
        assert result.file_class == CLASS_DATA and result.warnings == []
        geo = result.attributes["sys_geo"]
        assert geo["format"] == "GeoPackage" and geo["type"] == "GeoPackage"
        assert geo["layerCount"] == 1 and len(geo["layers"]) == 1
        layer = geo["layers"][0]
        assert layer["name"] == "sites" and layer["table"] == "sites" and layer["type"] == "features"
        assert layer["geometryType"] == "Point" and layer["crs"] == "EPSG:4326" and layer["srsId"] == 4326
        assert layer["featureCount"] == 3 and layer["featureCountExact"] is True
        # The geometry column is not a field; the attribute columns are, in table order.
        assert layer["fields"] == ["fid", "name", "height"]
        assert layer["nativeExtent"] == list(_SYDNEY)
        assert geo["featureCount"] == 3 and geo["featureCountExact"] is True
        assert geo["geometryTypes"] == ["Point"] and geo["crs"] == "EPSG:4326"
        # The footprint is the WGS84 extent as a closed ring, the shape the catalog promotes to `location`.
        assert geo["footprint"] == {"type": "Polygon", "coordinates": [[[151.1, -33.9], [151.3, -33.9], [151.3, -33.8],
                                                                          [151.1, -33.8], [151.1, -33.9]]]}
        assert result.facts["layers"] == "1 layer" and result.facts["features"] == "3 features"
        assert result.facts["geometryTypes"] == "Point" and result.facts["crs"] == "EPSG:4326"
        assert result.facts["footprint"] == "Polygon bounding box"
        excerpt = json.loads(result.text_excerpt)
        assert excerpt["format"] == "GeoPackage" and excerpt["layers"][0]["name"] == "sites"
        assert "nativeExtent" not in excerpt["layers"][0]

    def test_a_degenerate_extent_is_a_point_footprint(self, tmp_path):
        path, ctx = _gpkg(tmp_path, [{"name": "hq", "geometry": "POINT", "srs_id": 4326,
                                      "extent": (151.2, -33.85, 151.2, -33.85), "rows": 1}])
        geo = gis.extract_geopackage(path, ctx).attributes["sys_geo"]
        assert geo["footprint"] == {"type": "Point", "coordinates": [151.2, -33.85]}

    def test_a_projected_layer_keeps_its_extent_and_writes_no_footprint(self, tmp_path):
        path, ctx = _gpkg(tmp_path, [{"name": "roads", "geometry": "MULTILINESTRING", "srs_id": 3857,
                                      "extent": _MERCATOR, "rows": 2}])
        result = gis.extract_geopackage(path, ctx)
        geo = result.attributes["sys_geo"]
        assert geo["footprint"] is None and "footprint" not in result.facts
        assert geo["layers"][0]["crs"] == "EPSG:3857" and geo["layers"][0]["nativeExtent"] == list(_MERCATOR)
        assert geo["layers"][0]["geometryType"] == "MultiLineString" and geo["crs"] == "EPSG:3857"
        assert any("EPSG:4326" in warning and "no footprint" in warning for warning in result.warnings)

    def test_mixed_spatial_references_footprint_the_wgs84_layers_only(self, tmp_path):
        path, ctx = _gpkg(tmp_path, [
            {"name": "sites", "geometry": "POINT", "srs_id": 4326, "extent": _SYDNEY, "rows": 3},
            {"name": "roads", "geometry": "LINESTRING", "srs_id": 3857, "extent": _MERCATOR, "rows": 2},
            {"name": "notes", "data_type": "attributes", "geometry": None, "srs_id": 0, "extent": (None,) * 4, "rows": 4},
        ])
        result = gis.extract_geopackage(path, ctx)
        geo = result.attributes["sys_geo"]
        assert geo["layerCount"] == 3 and [layer["type"] for layer in geo["layers"]] == ["attributes", "features", "features"]
        assert geo["footprint"]["type"] == "Polygon" and geo["footprint"]["coordinates"][0][0] == [151.1, -33.9]
        assert geo["geometryTypes"] == ["LineString", "Point"] and geo["crs"] == ["EPSG:3857", "EPSG:4326"]
        # Feature counts sum the feature layers; the attributes table is counted on its own row and not in the total.
        assert geo["featureCount"] == 5
        notes = geo["layers"][0]
        assert notes["featureCount"] == 4 and "geometryType" not in notes and "crs" not in notes
        assert "nativeExtent" not in notes and notes["srsId"] == 0
        assert result.warnings == []

    def test_the_feature_count_is_bounded_and_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gis, "DATA_MAX_ROWS_COUNTED", 2)
        path, ctx = _gpkg(tmp_path, [{"name": "sites", "geometry": "POINT", "srs_id": 4326, "extent": _SYDNEY, "rows": 5}])
        result = gis.extract_geopackage(path, ctx)
        layer = result.attributes["sys_geo"]["layers"][0]
        assert layer["featureCount"] == 2 and layer["featureCountExact"] is False
        assert result.attributes["sys_geo"]["featureCountExact"] is False
        assert result.facts["features"] == "2 features or more"

    def test_layers_beyond_the_cap_are_counted_but_not_listed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gis, "GIS_MAX_LAYERS", 2)
        path, ctx = _gpkg(tmp_path, [{"name": f"layer{index}", "geometry": "POINT", "srs_id": 4326,
                                      "extent": _SYDNEY, "rows": 1} for index in range(4)])
        result = gis.extract_geopackage(path, ctx)
        geo = result.attributes["sys_geo"]
        assert geo["layerCount"] == 4 and len(geo["layers"]) == 2 and result.facts["layers"] == "4 layers"
        assert any("4 layers" in warning and "first 2" in warning for warning in result.warnings)

    def test_no_feature_row_or_geometry_blob_is_read_and_the_file_is_opened_read_only(self, tmp_path, monkeypatch):
        """The cost of a GeoPackage read does not grow with the file: every statement that names a layer table is
        the LIMIT-bounded count or its PRAGMA, so the geometry blobs are never fetched, and the connection is
        read-only and immutable so SQLite neither writes a journal nor takes a lock on the download."""
        statements, uris = [], []
        real_connect = sqlite3.connect
        # Written before the patch: the fixture builder opens sqlite3 too, and only the extractor's open is measured.
        path, ctx = _gpkg(tmp_path, [{"name": "sites", "geometry": "POINT", "srs_id": 4326, "extent": _SYDNEY, "rows": 50}])

        def recording_connect(database, **kwargs):
            uris.append((database, kwargs))
            connection = real_connect(database, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        monkeypatch.setattr(gis.sqlite3, "connect", recording_connect)
        result = gis.extract_geopackage(path, ctx)
        assert result.attributes["sys_geo"]["layers"][0]["featureCount"] == 50
        assert len(uris) == 1 and uris[0][1] == {"uri": True}
        assert uris[0][0].startswith("file:") and "mode=ro" in uris[0][0] and "immutable=1" in uris[0][0]
        layer_statements = [statement for statement in statements if "sites" in statement]
        assert layer_statements, statements
        for statement in layer_statements:
            upper = statement.upper()
            assert upper.startswith("PRAGMA TABLE_INFO") or ("SELECT COUNT(*)" in upper and "LIMIT" in upper), statement
            assert "GEOM" not in upper.replace("GEOMETRY_COLUMNS", "")
        assert not any(statement.upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for statement in statements)

    def test_a_table_the_contents_name_but_the_file_lacks_is_a_warning_not_a_failure(self, tmp_path):
        data = geopackage_bytes([{"name": "sites", "geometry": "POINT", "srs_id": 4326, "extent": _SYDNEY, "rows": 1}])
        path = write(tmp_path, "fixture.gpkg", data)
        connection = sqlite3.connect(str(path))
        connection.execute("DROP TABLE sites")
        connection.commit()
        connection.close()
        result = gis.extract_geopackage(str(path), make_ctx(tmp_path, "fixture.gpkg"))
        layer = result.attributes["sys_geo"]["layers"][0]
        assert "featureCount" not in layer and layer["geometryType"] == "Point" and result.file_class == CLASS_DATA

    def test_a_file_that_is_not_sqlite_is_other(self, tmp_path):
        path = write(tmp_path, "fake.gpkg", b"PK\x03\x04 not a database at all")
        result = gis.extract_geopackage(str(path), make_ctx(tmp_path, "fake.gpkg"))
        assert result.file_class == CLASS_OTHER and "sys_geo" not in result.attributes
        assert result.warnings == ["fake.gpkg: GeoPackage is not an SQLite database; sys_file attributes only"]

    def test_an_sqlite_file_without_gpkg_contents_is_other(self, tmp_path):
        path = tmp_path / "plain.gpkg"
        connection = sqlite3.connect(str(path))
        connection.execute("CREATE TABLE t (x INTEGER)")
        connection.commit()
        connection.close()
        result = gis.extract_geopackage(str(path), make_ctx(tmp_path, "plain.gpkg"))
        assert result.file_class == CLASS_OTHER and "gpkg_contents" in result.warnings[0]


@pytest.mark.unit
class TestLayerFile:
    def test_a_feature_layer_records_its_source_fields_and_renderer(self, tmp_path):
        path, ctx = _lyrx(tmp_path, lyrx_dict())
        result = gis.extract_lyrx(path, ctx)
        assert result.file_class == CLASS_DATA
        geo = result.attributes["sys_geo"]
        assert geo["format"] == "ArcGIS Layer File" and geo["type"] == "CIMLayerDocument" and geo["version"] == "3.3.0"
        assert geo["layerCount"] == 1 and geo["layerKinds"] == ["feature"] and geo["datasets"] == ["Parcels"]
        layer = geo["layers"][0]
        assert layer["name"] == "Parcels" and layer["type"] == "feature" and layer["cimType"] == "CIMFeatureLayer"
        assert layer["visible"] is True and layer["renderer"] == "CIMSimpleRenderer"
        assert layer["fields"] == ["OBJECTID", "APN", "ZONING"]
        assert layer["source"] == {"type": "CIMStandardDataConnection", "dataset": "Parcels",
                                   "datasetType": "esriDTFeatureClass", "workspaceFactory": "FileGDB",
                                   "workspace": "DATABASE=C:\\\\data\\\\city.gdb"}
        # No data travels with a layer file; the contract fields it cannot fill are empty, not absent.
        assert geo["featureCount"] == 0 and geo["geometryTypes"] == [] and geo["footprint"] is None
        assert geo["crs"] == []
        assert result.facts["layers"] == "1 layer" and result.facts["layerKinds"] == "feature"
        assert result.facts["datasets"] == "Parcels"
        assert result.warnings == ["a layer file references its data by name; the dataset itself is not in this file"]
        assert json.loads(result.text_excerpt)["layers"][0]["source"]["dataset"] == "Parcels"

    def test_a_connection_string_loses_its_credential_pairs(self, tmp_path):
        workspace = ("ENCRYPTED_PASSWORD=00022e68...;SERVER=gis-db;INSTANCE=sde:postgresql:gis-db;DBCLIENT=postgresql;"
                     "DB_CONNECTION_PROPERTIES=gis-db;DATABASE=city;USER=gis_reader;PASSWORD=plain;VERSION=sde.DEFAULT;"
                     "AUTHENTICATION_MODE=DBMS;TOKEN=abc")
        path, ctx = _lyrx(tmp_path, lyrx_dict(workspace=workspace, workspace_factory="SDE"))
        source = gis.extract_lyrx(path, ctx).attributes["sys_geo"]["layers"][0]["source"]
        assert source["workspace"] == ("SERVER=gis-db;INSTANCE=sde:postgresql:gis-db;DBCLIENT=postgresql;"
                                       "DB_CONNECTION_PROPERTIES=gis-db;DATABASE=city;USER=gis_reader;VERSION=sde.DEFAULT")
        serialized = json.dumps(source)
        assert "PASSWORD" not in serialized and "TOKEN" not in serialized and "00022e68" not in serialized

    def test_a_layer_extent_in_wgs84_is_the_footprint(self, tmp_path):
        path, ctx = _lyrx(tmp_path, lyrx_dict(extent=(151.1, -33.9, 151.3, -33.8, 4326)))
        geo = gis.extract_lyrx(path, ctx).attributes["sys_geo"]
        assert geo["layers"][0]["crs"] == "EPSG:4326" and geo["crs"] == "EPSG:4326"
        assert geo["footprint"]["type"] == "Polygon" and geo["layers"][0]["nativeExtent"] == [151.1, -33.9, 151.3, -33.8]

    def test_a_layer_extent_in_another_reference_is_recorded_without_a_footprint(self, tmp_path):
        path, ctx = _lyrx(tmp_path, lyrx_dict(extent=(*_MERCATOR, 3857)))
        geo = gis.extract_lyrx(path, ctx).attributes["sys_geo"]
        assert geo["layers"][0]["crs"] == "EPSG:3857" and geo["footprint"] is None

    def test_a_group_layer_records_its_members(self, tmp_path):
        document = lyrx_dict()
        document["layerDefinitions"].append({"type": "CIMGroupLayer", "name": "Base", "layers": ["a", "b"], "visibility": False})
        path, ctx = _lyrx(tmp_path, document)
        geo = gis.extract_lyrx(path, ctx).attributes["sys_geo"]
        assert geo["layerCount"] == 2 and geo["layerKinds"] == ["feature", "group"]
        assert geo["layers"][1] == {"name": "Base", "type": "group", "cimType": "CIMGroupLayer", "visible": False,
                                    "memberCount": 2}

    def test_a_document_without_layers_is_data_with_a_warning(self, tmp_path):
        document = lyrx_dict()
        document["layerDefinitions"] = []
        path, ctx = _lyrx(tmp_path, document)
        result = gis.extract_lyrx(path, ctx)
        assert result.file_class == CLASS_DATA and result.attributes["sys_geo"]["layers"] == []
        assert result.warnings[0] == "layer file carries no layer definitions"

    def test_a_file_over_the_document_bound_is_other_before_it_is_parsed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gis, "TEXT_HEAD_BYTES", 64)
        path, ctx = _lyrx(tmp_path, lyrx_dict())
        result = gis.extract_lyrx(path, ctx)
        assert result.file_class == CLASS_OTHER and "64-byte bound" in result.warnings[0]

    def test_a_non_json_file_is_other(self, tmp_path):
        path = write(tmp_path, "broken.lyrx", b"\xff\xfe not json")
        result = gis.extract_lyrx(str(path), make_ctx(tmp_path, "broken.lyrx"))
        assert result.file_class == CLASS_OTHER and "not a JSON document" in result.warnings[0]

    def test_a_json_document_that_is_not_a_cim_layer_document_is_other(self, tmp_path):
        path, ctx = _lyrx(tmp_path, {"type": "CIMMapDocument", "layerDefinitions": []})
        result = gis.extract_lyrx(path, ctx)
        assert result.file_class == CLASS_OTHER and "CIMLayerDocument" in result.warnings[0]


@pytest.mark.unit
class TestDispatch:
    def test_extract_gis_routes_on_the_context_extension(self, tmp_path):
        path, ctx = _gpkg(tmp_path, name="Sites.GPKG")
        assert gis.extract_gis(path, ctx).attributes["sys_geo"]["format"] == "GeoPackage"
        path, ctx = _lyrx(tmp_path, lyrx_dict(), name="parcels.lyrx")
        assert gis.extract_gis(path, ctx).attributes["sys_geo"]["format"] == "ArcGIS Layer File"

    def test_an_extension_the_module_does_not_read_is_other(self, tmp_path):
        path = write(tmp_path, "roads.shp", b"\x00\x00\x27\x0a")
        result = gis.extract_gis(str(path), make_ctx(tmp_path, "roads.shp"))
        assert result.file_class == CLASS_OTHER and ".shp" in result.warnings[0]
