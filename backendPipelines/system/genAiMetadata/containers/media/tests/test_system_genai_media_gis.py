#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The GIS extractor: a GeoPackage read through sqlite3 and an ArcGIS Pro layer file read as a CIM document, both
written to the GeoJSON extractor's `sys_geo` shape plus `layers[]` and `crs`. A footprint is written for EPSG:4326
only; another spatial reference keeps its native extent and the catalog gets no `location`.

The GeoPackage read is bounded whatever the file holds: the metadata tables are read for the listed layers only, a
listed table gets a `PRAGMA table_info` and a LIMIT-bounded count, a listed view is described but not run, and a
progress handler interrupts any statement past the file's or the layer's time budget. No feature row or geometry
blob is read, and the connection is read-only. Names from either file are cut to DATA_CELL_MAX_CHARS and `sys_geo`
stays under GIS_MAX_SYS_GEO_CHARS. A layer file's connection string keeps only the pairs that say where the data
lives. A layer file over the document bound, a non-SQLite `.gpkg`, an SQLite file without `gpkg_contents` and a
JSON document that is not a CIMLayerDocument all degrade to `other`."""

import json
import sqlite3
import time

import pytest

from media_extractors import gis
from media_extractors.common import CLASS_DATA, CLASS_OTHER, DATA_CELL_MAX_CHARS
from system_genai_media_fixtures import geopackage_bytes, lyrx_dict, make_ctx, write

_SYDNEY = (151.1, -33.9, 151.3, -33.8)
_MERCATOR = (16800000.0, -4020000.0, 16830000.0, -4000000.0)
# A recursive query that never yields a row: `LIMIT` cannot end it, only the budget can.
_ENDLESS = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
            "SELECT 'a' AS table_name, 'features' AS data_type, 'a' AS identifier, '' AS description, "
            "0.0 AS min_x, 0.0 AS min_y, 0.0 AS max_x, 0.0 AS max_y, 4326 AS srs_id FROM c WHERE x < 0")


def _gpkg(tmp_path, layers=None, name="fixture.gpkg"):
    path = write(tmp_path, name, geopackage_bytes(layers))
    return path, make_ctx(tmp_path, name, extract_geo_location=True)


def _edit(path, *statements, parameters=()):
    connection = sqlite3.connect(str(path))
    for index, statement in enumerate(statements):
        connection.execute(statement, parameters[index] if index < len(parameters) else ())
    connection.commit()
    connection.close()


def _lyrx(tmp_path, document, name="parcels.lyrx"):
    path = write(tmp_path, name, json.dumps(document).encode("utf-8"))
    return path, make_ctx(tmp_path, name, content_type="application/json")


def _traced(monkeypatch):
    statements = []
    real_connect = sqlite3.connect

    def recording_connect(database, **kwargs):
        connection = real_connect(database, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(gis.sqlite3, "connect", recording_connect)
    return statements


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
        assert geo["footprint"] == {"type": "Polygon", "coordinates": [[
            [151.1, -33.9], [151.3, -33.9], [151.3, -33.8], [151.1, -33.8], [151.1, -33.9]]]}
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
            {"name": "notes", "data_type": "attributes", "geometry": None, "srs_id": 0, "extent": (None,) * 4,
             "rows": 4},
        ])
        result = gis.extract_geopackage(path, ctx)
        geo = result.attributes["sys_geo"]
        assert geo["layerCount"] == 3
        assert [layer["type"] for layer in geo["layers"]] == ["attributes", "features", "features"]
        assert geo["footprint"]["type"] == "Polygon" and geo["footprint"]["coordinates"][0][0] == [151.1, -33.9]
        assert geo["geometryTypes"] == ["LineString", "Point"] and geo["crs"] == ["EPSG:3857", "EPSG:4326"]
        # Feature counts sum the feature layers; the attributes table is counted on its own row and not in the total.
        assert geo["featureCount"] == 5
        notes = geo["layers"][0]
        assert notes["featureCount"] == 4 and "geometryType" not in notes and "crs" not in notes
        assert "nativeExtent" not in notes and notes["srsId"] == 0
        assert result.warnings == []

    def test_wgs84_is_decided_from_the_registered_reference_not_the_srs_id(self, tmp_path):
        """srs_id is a file-local key: EPSG:4326 registered under another id gets a footprint, and id 4326
        registered as another reference does not."""
        path, ctx = _gpkg(tmp_path, [{"name": "sites", "geometry": "POINT", "srs_id": 9999, "extent": _SYDNEY,
                                      "rows": 1}])
        _edit(path, "INSERT INTO gpkg_spatial_ref_sys VALUES ('WGS 84 alias', 9999, 'EPSG', 4326, 'x', NULL)")
        geo = gis.extract_geopackage(path, ctx).attributes["sys_geo"]
        assert geo["layers"][0]["crs"] == "EPSG:4326" and geo["footprint"]["type"] == "Polygon"

        other, ctx = _gpkg(tmp_path, name="other.gpkg")
        _edit(other, "UPDATE gpkg_spatial_ref_sys SET organization_coordsys_id = 32756 WHERE srs_id = 4326")
        result = gis.extract_geopackage(other, ctx)
        assert result.attributes["sys_geo"]["layers"][0]["crs"] == "EPSG:32756"
        assert result.attributes["sys_geo"]["footprint"] is None

    def test_an_out_of_range_wgs84_extent_writes_no_footprint_and_says_so(self, tmp_path):
        path, ctx = _gpkg(tmp_path, [{"name": "sites", "geometry": "POINT", "srs_id": 4326,
                                      "extent": (-500.0, -33.9, 900.0, -33.8), "rows": 1}])
        result = gis.extract_geopackage(path, ctx)
        assert result.attributes["sys_geo"]["footprint"] is None and "footprint" not in result.facts
        assert any("not a longitude/latitude box" in warning for warning in result.warnings)

    def test_the_feature_count_is_bounded_and_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gis, "DATA_MAX_ROWS_COUNTED", 2)
        path, ctx = _gpkg(tmp_path, [{"name": "sites", "geometry": "POINT", "srs_id": 4326, "extent": _SYDNEY,
                                      "rows": 5}])
        result = gis.extract_geopackage(path, ctx)
        layer = result.attributes["sys_geo"]["layers"][0]
        assert layer["featureCount"] == 2 and layer["featureCountExact"] is False
        assert result.attributes["sys_geo"]["featureCountExact"] is False
        assert result.facts["features"] == "2 features or more"

    def test_layers_beyond_the_cap_are_counted_but_not_listed_or_read(self, tmp_path, monkeypatch):
        """The layer total is a COUNT(*); only the listed layers' metadata rows are fetched, so a file whose
        gpkg_contents holds a million rows costs what one holding GIS_MAX_LAYERS does."""
        monkeypatch.setattr(gis, "GIS_MAX_LAYERS", 2)
        path, ctx = _gpkg(tmp_path, [{"name": f"layer{index}", "geometry": "POINT", "srs_id": 4326,
                                      "extent": _SYDNEY, "rows": 1} for index in range(4)])
        statements = _traced(monkeypatch)
        result = gis.extract_geopackage(path, ctx)
        geo = result.attributes["sys_geo"]
        assert geo["layerCount"] == 4 and len(geo["layers"]) == 2 and result.facts["layers"] == "4 layers"
        assert any("4 layers" in warning and "first 2" in warning for warning in result.warnings)
        reads = {table: [s for s in statements if f"FROM {table}" in s]
                 for table in ("gpkg_contents", "gpkg_geometry_columns", "gpkg_spatial_ref_sys")}
        assert any("COUNT(*)" in s for s in reads["gpkg_contents"])
        assert all("COUNT(*)" in s or "LIMIT" in s for s in reads["gpkg_contents"]), reads["gpkg_contents"]
        assert reads["gpkg_geometry_columns"] and all("IN (" in s for s in reads["gpkg_geometry_columns"])
        assert reads["gpkg_spatial_ref_sys"] and all("IN (" in s for s in reads["gpkg_spatial_ref_sys"])
        # layer2 and layer3 are beyond the cap: no statement names them.
        assert not any("layer2" in s or "layer3" in s for s in statements)

    def test_no_feature_row_or_geometry_blob_is_read_and_the_file_is_opened_read_only(self, tmp_path, monkeypatch):
        """Every statement that reads a layer table (the quoted identifier) is the LIMIT-bounded count or its PRAGMA,
        so the geometry blobs are never fetched, and the connection is read-only and immutable so SQLite neither
        writes a journal nor takes a lock on the download."""
        statements, uris = [], []
        real_connect = sqlite3.connect
        # Written before the patch: the fixture builder opens sqlite3 too, and only the extractor's open is measured.
        path, ctx = _gpkg(tmp_path, [{"name": "sites", "geometry": "POINT", "srs_id": 4326, "extent": _SYDNEY,
                                      "rows": 50}])

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
        layer_reads = [statement for statement in statements if '"sites"' in statement]
        assert layer_reads, statements
        for statement in layer_reads:
            upper = statement.upper()
            assert upper.startswith("PRAGMA TABLE_INFO") or ("SELECT COUNT(*)" in upper and "LIMIT" in upper), statement
            assert "GEOM" not in upper
        # Every other statement naming the layer is a lookup in the schema or a metadata table.
        for statement in statements:
            if "sites" in statement and '"sites"' not in statement:
                assert "FROM sqlite_master" in statement or "FROM gpkg_" in statement, statement
        mutating = ("INSERT", "UPDATE", "DELETE", "CREATE")
        assert not any(statement.upper().startswith(mutating) for statement in statements)

    def test_a_view_listed_as_a_layer_is_described_not_counted(self, tmp_path):
        path, ctx = _gpkg(tmp_path)
        _edit(path, f"CREATE VIEW endless AS {_ENDLESS}",
              "INSERT INTO gpkg_contents (table_name, data_type, identifier, srs_id) "
              "VALUES ('endless', 'features', 'endless', 4326)")
        started = time.monotonic()
        result = gis.extract_geopackage(path, ctx)
        assert time.monotonic() - started < 10
        geo = result.attributes["sys_geo"]
        view = next(layer for layer in geo["layers"] if layer["table"] == "endless")
        assert view["view"] is True and view["featureCountExact"] is False and "featureCount" not in view
        assert view["fields"] == ["table_name", "data_type", "identifier", "description", "min_x", "min_y", "max_x",
                                  "max_y", "srs_id"]
        assert geo["featureCount"] == 3 and geo["featureCountExact"] is False
        assert any("is a view" in warning for warning in result.warnings)

    def test_metadata_that_never_yields_is_interrupted_by_the_file_budget(self, tmp_path, monkeypatch):
        """A crafted gpkg_contents view whose query never yields would hold the read until the function timed out;
        the progress handler ends it at the file's budget and the file degrades to `other` with the reason."""
        monkeypatch.setattr(gis, "GPKG_SQL_BUDGET_SECONDS", 0.5)
        path, ctx = _gpkg(tmp_path)
        _edit(path, "ALTER TABLE gpkg_contents RENAME TO gpkg_contents_table",
              f"CREATE VIEW gpkg_contents AS {_ENDLESS}")
        started = time.monotonic()
        result = gis.extract_geopackage(path, ctx)
        assert time.monotonic() - started < 10
        assert result.file_class == CLASS_OTHER and "sys_geo" not in result.attributes
        assert "0.5-second budget" in result.warnings[0]

    def test_a_layer_count_past_its_budget_is_a_warning_and_the_next_layer_is_still_read(self, tmp_path, monkeypatch):
        path, ctx = _gpkg(tmp_path, [
            {"name": "aaa", "geometry": "POINT", "srs_id": 4326, "extent": _SYDNEY, "rows": 3},
            {"name": "bbb", "data_type": "attributes", "geometry": None, "srs_id": 0, "extent": (None,) * 4,
             "rows": 2},
        ])
        real_count = gis._bounded_count

        def slow_first_count(connection, table):
            if table == "aaa":
                # A statement that runs until the layer budget interrupts it.
                connection.execute(f"SELECT COUNT(*) FROM ({_ENDLESS})").fetchone()
            return real_count(connection, table)

        monkeypatch.setattr(gis, "GPKG_LAYER_BUDGET_SECONDS", 0.2)
        monkeypatch.setattr(gis, "_bounded_count", slow_first_count)
        result = gis.extract_geopackage(path, ctx)
        layers = {layer["table"]: layer for layer in result.attributes["sys_geo"]["layers"]}
        assert "featureCount" not in layers["aaa"] and layers["aaa"]["featureCountExact"] is False
        assert layers["bbb"]["featureCount"] == 2
        assert any("'aaa' was not counted" in warning and "0.2-second budget" in warning
                   for warning in result.warnings)

    def test_names_from_the_file_are_cut_and_sys_geo_stays_under_the_value_limit(self, tmp_path):
        path, ctx = _gpkg(tmp_path, [{"name": "sites", "identifier": "a" * 450_000, "geometry": "POINT",
                                      "srs_id": 4326, "extent": _SYDNEY, "rows": 1,
                                      "description": "d" * 10_000}])
        geo = gis.extract_geopackage(path, ctx).attributes["sys_geo"]
        layer = geo["layers"][0]
        assert len(layer["name"]) == DATA_CELL_MAX_CHARS and len(layer["description"]) <= 500
        assert gis._serialized_size(geo) <= gis.GIS_MAX_SYS_GEO_CHARS

    def test_a_sys_geo_over_its_budget_drops_field_lists_with_a_warning(self, tmp_path, monkeypatch):
        path, ctx = _gpkg(tmp_path, [{"name": f"layer{index}", "geometry": "POINT", "srs_id": 4326,
                                      "extent": _SYDNEY, "rows": 1} for index in range(3)])
        full = gis.extract_geopackage(path, ctx).attributes["sys_geo"]
        monkeypatch.setattr(gis, "GIS_MAX_SYS_GEO_CHARS", gis._serialized_size(full) - 10)
        result = gis.extract_geopackage(path, ctx)
        geo = result.attributes["sys_geo"]
        assert gis._serialized_size(geo) <= gis.GIS_MAX_SYS_GEO_CHARS
        assert "fields" not in geo["layers"][-1] and "fields" in geo["layers"][0] and len(geo["layers"]) == 3
        assert any("field names of 1 layer(s)" in warning for warning in result.warnings)

    def test_a_table_the_contents_name_but_the_file_lacks_is_a_warning_not_a_failure(self, tmp_path):
        data = geopackage_bytes([{"name": "sites", "geometry": "POINT", "srs_id": 4326, "extent": _SYDNEY, "rows": 1}])
        path = write(tmp_path, "fixture.gpkg", data)
        _edit(path, "DROP TABLE sites")
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
        _edit(path, "CREATE TABLE t (x INTEGER)")
        result = gis.extract_geopackage(str(path), make_ctx(tmp_path, "plain.gpkg"))
        assert result.file_class == CLASS_OTHER and "gpkg_contents" in result.warnings[0]


_SECRETS = ("encVALUE", "plainVALUE", "colonVALUE", "pwdVALUE", "apikeyVALUE", "apikey2VALUE", "keyVALUE",
            "akVALUE", "credVALUE", "authVALUE", "sigVALUE", "tokVALUE", "userVALUE", "urlUSER", "urlPASS",
            "urlTOKEN", "fieldUSER", "fieldPASS", "fieldTOKEN")


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
        # A layer file names its data without carrying it: no feature count or geometry types to promote.
        assert "featureCount" not in geo and "geometryTypes" not in geo
        assert geo["footprint"] is None and geo["crs"] == []
        assert result.facts["layers"] == "1 layer" and result.facts["layerKinds"] == "feature"
        assert result.facts["datasets"] == "Parcels"
        assert result.warnings == ["a layer file references its data by name; the dataset itself is not in this file"]
        assert json.loads(result.text_excerpt)["layers"][0]["source"]["dataset"] == "Parcels"

    def test_a_connection_keeps_only_the_pairs_that_say_where_the_data_lives(self, tmp_path):
        workspace = ("ENCRYPTED_PASSWORD=encVALUE;SERVER=gis-db;INSTANCE=sde:postgresql:gis-db;DBCLIENT=postgresql;"
                     "DB_CONNECTION_PROPERTIES=gis-db;DATABASE=city;USER=userVALUE;PASSWORD=plainVALUE;"
                     "PASSWORD=semi;colonVALUE;PWD=pwdVALUE;API_KEY=apikeyVALUE;APIKEY=apikey2VALUE;KEY=keyVALUE;"
                     "ACCESS_KEY=akVALUE;CREDENTIAL=credVALUE;AUTH=authVALUE;SIG=sigVALUE;TOKEN=tokVALUE;"
                     "VERSION=sde.DEFAULT;AUTHENTICATION_MODE=DBMS;"
                     "URL=https://urlUSER:urlPASS@maps.example.com:6443/arcgis/rest?token=urlTOKEN#frag")
        document = lyrx_dict(workspace=workspace, workspace_factory="SDE")
        document["layerDefinitions"][0]["featureTable"]["dataConnection"]["url"] = (
            "https://fieldUSER:fieldPASS@services.example.com/arcgis/rest/services/Parcels/FeatureServer/0"
            "?token=fieldTOKEN&f=json")
        path, ctx = _lyrx(tmp_path, document)
        result = gis.extract_lyrx(path, ctx)
        source = result.attributes["sys_geo"]["layers"][0]["source"]
        assert source["workspace"] == ("SERVER=gis-db;INSTANCE=sde:postgresql:gis-db;DBCLIENT=postgresql;"
                                       "DB_CONNECTION_PROPERTIES=gis-db;DATABASE=city;VERSION=sde.DEFAULT;"
                                       "URL=https://maps.example.com:6443/arcgis/rest")
        assert source["url"] == "https://services.example.com/arcgis/rest/services/Parcels/FeatureServer/0"
        recorded = json.dumps(result.attributes) + result.text_excerpt + json.dumps(result.facts)
        leaked = [secret for secret in _SECRETS if secret in recorded]
        assert leaked == []

    def test_a_url_that_does_not_parse_is_dropped(self, tmp_path):
        document = lyrx_dict(workspace="URL=https://maps.example.com:notaport/arcgis;DATABASE=city")
        path, ctx = _lyrx(tmp_path, document)
        source = gis.extract_lyrx(path, ctx).attributes["sys_geo"]["layers"][0]["source"]
        assert source["workspace"] == "DATABASE=city"

    def test_a_layer_extent_in_wgs84_is_the_footprint(self, tmp_path):
        path, ctx = _lyrx(tmp_path, lyrx_dict(extent=(151.1, -33.9, 151.3, -33.8, 4326)))
        geo = gis.extract_lyrx(path, ctx).attributes["sys_geo"]
        assert geo["layers"][0]["crs"] == "EPSG:4326" and geo["crs"] == "EPSG:4326"
        assert geo["footprint"]["type"] == "Polygon"
        assert geo["layers"][0]["nativeExtent"] == [151.1, -33.9, 151.3, -33.8]

    def test_a_layer_extent_in_another_reference_is_recorded_without_a_footprint(self, tmp_path):
        path, ctx = _lyrx(tmp_path, lyrx_dict(extent=(*_MERCATOR, 3857)))
        geo = gis.extract_lyrx(path, ctx).attributes["sys_geo"]
        assert geo["layers"][0]["crs"] == "EPSG:3857" and geo["footprint"] is None

    @pytest.mark.parametrize("extent", [(-500.0, -33.9, 900.0, -33.8), (151.3, -33.9, 151.1, -33.8)])
    def test_an_out_of_range_or_inverted_wgs84_extent_writes_no_footprint(self, tmp_path, extent):
        path, ctx = _lyrx(tmp_path, lyrx_dict(extent=(*extent, 4326)))
        result = gis.extract_lyrx(path, ctx)
        assert result.attributes["sys_geo"]["footprint"] is None and "footprint" not in result.facts
        assert any("not a longitude/latitude box" in warning for warning in result.warnings)

    def test_an_infinite_extent_is_not_recorded(self, tmp_path):
        path, ctx = _lyrx(tmp_path, lyrx_dict(extent=(float("-inf"), -33.9, 151.3, -33.8, 4326)))
        geo = gis.extract_lyrx(path, ctx).attributes["sys_geo"]
        assert "nativeExtent" not in geo["layers"][0] and geo["footprint"] is None

    def test_an_extent_number_too_large_for_a_float_is_not_recorded(self, tmp_path):
        document = json.dumps(lyrx_dict(extent=(0, -33.9, 151.3, -33.8, 4326))).replace(
            '"xmin": 0,', '"xmin": ' + "9" * 4000 + ",")
        path = write(tmp_path, "huge.lyrx", document.encode("utf-8"))
        result = gis.extract_lyrx(path, make_ctx(tmp_path, "huge.lyrx"))
        assert result.file_class == CLASS_DATA and "nativeExtent" not in result.attributes["sys_geo"]["layers"][0]

    @pytest.mark.parametrize("wkid, label", [(True, None), (102100, "ESRI:102100"), (4326, "EPSG:4326"),
                                             ("4326", None), (10 ** 12, None)])
    def test_wkid_labels(self, tmp_path, wkid, label):
        document = lyrx_dict(extent=(*_MERCATOR, 0))
        document["layerDefinitions"][0]["extent"]["spatialReference"] = {"wkid": wkid}
        path, ctx = _lyrx(tmp_path, document)
        layer = gis.extract_lyrx(path, ctx).attributes["sys_geo"]["layers"][0]
        assert layer.get("crs") == label

    def test_hidden_fields_are_not_recorded(self, tmp_path):
        document = lyrx_dict()
        document["layerDefinitions"][0]["featureTable"]["fieldDescriptions"][1]["visible"] = False
        path, ctx = _lyrx(tmp_path, document)
        assert gis.extract_lyrx(path, ctx).attributes["sys_geo"]["layers"][0]["fields"] == ["OBJECTID", "ZONING"]

    def test_names_from_the_file_are_cut(self, tmp_path):
        document = lyrx_dict(name="n" * 450_000)
        path, ctx = _lyrx(tmp_path, document)
        result = gis.extract_lyrx(path, ctx)
        assert len(result.attributes["sys_geo"]["layers"][0]["name"]) == DATA_CELL_MAX_CHARS
        assert gis._serialized_size(result.attributes["sys_geo"]) <= gis.GIS_MAX_SYS_GEO_CHARS

    def test_a_group_layer_records_its_members(self, tmp_path):
        document = lyrx_dict()
        document["layerDefinitions"].append(
            {"type": "CIMGroupLayer", "name": "Base", "layers": ["a", "b"], "visibility": False})
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

    def test_a_document_nested_deeper_than_the_parser_reads_is_other(self, tmp_path):
        depth = 200_000
        data = b'{"type": "CIMLayerDocument", "deep": ' + b"[" * depth + b"]" * depth + b"}"
        path = write(tmp_path, "deep.lyrx", data)
        result = gis.extract_lyrx(str(path), make_ctx(tmp_path, "deep.lyrx"))
        assert result.file_class == CLASS_OTHER and "nests deeper" in result.warnings[0]

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
