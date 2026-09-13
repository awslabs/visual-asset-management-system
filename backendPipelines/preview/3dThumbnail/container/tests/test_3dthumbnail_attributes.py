#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The sys_* attribute extractors: each returns {sys_key: dict} of plain JSON values, so the metadata
step can JSON-stringify every key into a string-only file attribute and promote the contract keys to
typed ext_* metadata. Library-backed extractors are exercised against fakes of pxr / ezdxf / ifcopenshell
/ laspy installed per test; the promotion source contract (registry 3.6) is pinned as a literal here."""

import io
import json
import sys
import types
import zipfile
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy", reason="geometry is numpy arithmetic")

from preview_pipeline.analysis import attributes as attrs  # noqa: E402

# Registry 3.6 "Promotion source contract", copied verbatim: the keys WP06d's metadataCatalog.py reads.
CONTRACT = {
    "sys_geometry": {"boundsMin", "boundsMax", "dimensions", "extentMax", "volume", "surfaceArea", "units"},
    "sys_statistics": {"meshCount", "vertices", "faces", "triangles", "watertight"},
    "sys_visual": {"materialCount", "textureCount", "hasUv", "hasVertexColors"},
    "sys_scene": {"nodeCount", "hasAnimation", "hasArmature"},
    "sys_cad": {"solidCount", "faceCount", "edgeCount", "assemblyCount", "declaredUnits", "estimatedUnits"},
    "sys_pointcloud": {"pointCount", "hasColor", "boundsMin", "boundsMax", "crs"},
    "sys_ifc": {"schema", "projectName", "storeyCount", "elementCount", "site"},
}
DIMENSIONS_KEYS = {"width", "height", "depth"}
CRS_KEYS = {"epsg", "name", "geographic"}
SITE_KEYS = {"latitude", "longitude", "elevation"}
UNIT_TOKENS = {"m", "mm", "cm", "in", "ft"}


def _module(monkeypatch, name, **members):
    mod = types.ModuleType(name)
    for key, value in members.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def _polydata(points, n_cells, point_data=None, texture=None):
    data = SimpleNamespace(points=np.asarray(points, dtype=np.float64), n_points=len(points),
                           n_cells=n_cells, point_data=point_data or {})
    if texture is not None:
        data._preview_texture = texture
    return data


BOX = [[x, y, z] for x in (0.0, 2.0) for y in (0.0, 1.0) for z in (0.0, 4.0)]
BOX_GEOMETRY = {"boundsMin": [0.0, 0.0, 0.0], "boundsMax": [2.0, 1.0, 4.0],
                "dimensions": {"width": 2.0, "height": 1.0, "depth": 4.0}, "extentMax": 4.0,
                "volume": None, "surfaceArea": None, "units": None}


def assert_contract_shape(attributes):
    """Every contract group present carries exactly the contract key set, with the nested shapes the
    catalogue reads; sys_format carries extension, loader and a details dict."""
    for group, keys in CONTRACT.items():
        if group in attributes:
            assert set(attributes[group]) == keys, group
    geometry = attributes.get("sys_geometry")
    if geometry is not None:
        assert geometry["dimensions"] is None or set(geometry["dimensions"]) == DIMENSIONS_KEYS
        assert geometry["units"] is None or geometry["units"] in UNIT_TOKENS
    cloud = attributes.get("sys_pointcloud")
    if cloud is not None:
        assert cloud["crs"] is None or set(cloud["crs"]) == CRS_KEYS
    cad = attributes.get("sys_cad")
    if cad is not None:
        for key in ("declaredUnits", "estimatedUnits"):
            assert cad[key] is None or cad[key] in UNIT_TOKENS
    ifc = attributes.get("sys_ifc")
    if ifc is not None:
        assert ifc["site"] is None or set(ifc["site"]) == SITE_KEYS
    if "sys_format" in attributes:
        assert set(attributes["sys_format"]) == {"extension", "loader", "details"}
        assert isinstance(attributes["sys_format"]["details"], dict)


def _geo_key(key_id, value, location=0):
    return SimpleNamespace(id=key_id, tiff_tag_location=location, count=1, value_offset=value)


class GeoKeyDirectoryVlr:
    """Named like laspy's known VLR: the extractor matches VLR classes by name."""

    def __init__(self, geo_keys):
        self.geo_keys = geo_keys


class WktCoordinateSystemVlr:
    def __init__(self, string):
        self.string = string


UTM33_WKT = ('PROJCS["WGS 84 / UTM zone 33N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,'
             '298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
             'PROJECTION["Transverse_Mercator"],UNIT["metre",1]]')


def _las_reader(monkeypatch, vlrs, point_count=42):
    """A laspy double whose header carries the given VLR objects."""
    header = SimpleNamespace(
        point_count=point_count, version=SimpleNamespace(major=1, minor=4),
        point_format=SimpleNamespace(id=7, dimension_names=["X", "Y", "Z", "red", "green", "blue", "gps_time"]),
        mins=np.array([0.0, 1.0, 2.0]), maxs=np.array([3.0, 4.0, 5.0]),
        scales=np.array([0.01, 0.01, 0.01]), offsets=np.array([0.0, 0.0, 0.0]),
        vlrs=vlrs, generating_software="las2las\x00", system_identifier="scanner")

    class _Reader:
        def __init__(self):
            self.header = header

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    _module(monkeypatch, "laspy", open=lambda path: _Reader())


@pytest.mark.unit
class TestJsonSafe:
    def test_converts_numpy_and_containers(self):
        value = {"a": np.float32(1.5), "b": np.int64(3), "c": np.array([1, 2]), "d": (np.bool_(True),),
                 "e": {np.str_("k"): None}}
        out = attrs.json_safe(value)
        assert out == {"a": 1.5, "b": 3, "c": [1, 2], "d": [True], "e": {"k": None}}
        json.dumps(out)


@pytest.mark.unit
class TestUnits:
    @pytest.mark.parametrize("name,token", [
        ("millimetre", "mm"), ("millimeters (estimated)", "mm"), ("metre", "m"), ("meters (estimated)", "m"),
        ("centimeters (estimated)", "cm"), ("inch", "in"), ("feet", "ft"), ("micrometre", None),
        ("unknown", None), (None, None)])
    def test_normalize_unit_maps_to_the_five_tokens_or_none(self, name, token):
        assert attrs.normalize_unit(name) == token


@pytest.mark.unit
class TestGeometry:
    def test_bounds_dimensions_and_extent(self):
        assert attrs.geometry_from_points(np.array(BOX)) == BOX_GEOMETRY

    def test_empty(self):
        assert attrs.geometry_from_points(np.zeros((0, 3))) == {key: None for key in CONTRACT["sys_geometry"]}

    def test_polydata_attributes_for_a_coloured_mesh(self):
        data = _polydata(BOX, n_cells=12, point_data={"RGB": np.zeros((8, 3), np.uint8)})
        out = attrs.polydata_attributes(data, ".obj", "trimesh")
        assert_contract_shape(out)
        assert out[attrs.SYS_GEOMETRY] == BOX_GEOMETRY
        assert out[attrs.SYS_STATISTICS] == {"meshCount": 1, "vertices": 8, "faces": 12, "triangles": 12,
                                             "watertight": None}
        assert out[attrs.SYS_VISUAL] == {"materialCount": None, "textureCount": 0, "hasUv": False,
                                         "hasVertexColors": True}
        assert out[attrs.SYS_FORMAT] == {"extension": ".obj", "loader": "trimesh", "details": {}}

    def test_polydata_attributes_for_a_textured_point_free_cloud(self):
        data = _polydata(BOX, n_cells=0, texture=object())
        out = attrs.polydata_attributes(data, ".ply", "trimesh")
        assert out[attrs.SYS_STATISTICS] == {"meshCount": 0, "vertices": 8, "faces": 0, "triangles": 0,
                                             "watertight": None}
        assert out[attrs.SYS_VISUAL] == {"materialCount": None, "textureCount": 1, "hasUv": False,
                                         "hasVertexColors": False}

    def test_mesh_measurements_come_from_the_polydata_when_it_offers_them(self):
        data = _polydata(BOX, n_cells=12)
        data.n_open_edges = 0
        data.volume = 8.0
        data.area = 28.0
        data.active_texture_coordinates = np.zeros((8, 2))
        out = attrs.polydata_attributes(data, ".glb", "trimesh")
        assert out[attrs.SYS_STATISTICS]["watertight"] is True
        assert out[attrs.SYS_GEOMETRY]["volume"] == 8.0 and out[attrs.SYS_GEOMETRY]["surfaceArea"] == 28.0
        assert out[attrs.SYS_GEOMETRY]["units"] == "m", "glTF is defined in metres"
        assert out[attrs.SYS_VISUAL]["hasUv"] is True

    def test_an_open_mesh_reports_no_volume(self):
        data = _polydata(BOX, n_cells=12)
        data.n_open_edges = 4
        data.volume = 8.0
        out = attrs.polydata_attributes(data, ".stl", "trimesh")
        assert out[attrs.SYS_STATISTICS]["watertight"] is False
        assert out[attrs.SYS_GEOMETRY]["volume"] is None and out[attrs.SYS_GEOMETRY]["units"] is None


@pytest.mark.unit
class TestPointcloud:
    def test_attributes_prefer_header_bounds_and_counts(self):
        header = {"points": 1_000_000, "boundsMin": [-1.0, -1.0, -1.0], "boundsMax": [9.0, 9.0, 9.0],
                  "crs": {"epsg": 32633, "name": "WGS 84 / UTM zone 33N", "geographic": False},
                  "details": {"version": "1.4", "pointFormat": 7}}
        out = attrs.pointcloud_attributes(".laz", np.array(BOX), np.zeros((8, 3), np.uint8), header, "laspy")
        assert_contract_shape(out)
        assert out[attrs.SYS_POINTCLOUD] == {
            "pointCount": 1_000_000, "hasColor": True, "boundsMin": [-1.0, -1.0, -1.0],
            "boundsMax": [9.0, 9.0, 9.0],
            "crs": {"epsg": 32633, "name": "WGS 84 / UTM zone 33N", "geographic": False}}
        assert out[attrs.SYS_FORMAT] == {"extension": ".laz", "loader": "laspy",
                                         "details": {"version": "1.4", "pointFormat": 7, "pointsLoaded": 8}}

    def test_attributes_fall_back_to_the_loaded_bounds(self):
        out = attrs.pointcloud_attributes(".xyz", np.array(BOX), None, {}, "text")
        assert out[attrs.SYS_POINTCLOUD] == {"pointCount": 8, "hasColor": False, "boundsMin": [0.0, 0.0, 0.0],
                                             "boundsMax": [2.0, 1.0, 4.0], "crs": None}
        assert out[attrs.SYS_FORMAT]["details"] == {"pointsLoaded": 8}

    def test_flat_header_keys_fold_into_the_details(self):
        out = attrs.pointcloud_attributes(".pcd", np.array(BOX), None, {"points": 2, "version": "0.7"},
                                          "pcd_reader")
        assert out[attrs.SYS_POINTCLOUD]["pointCount"] == 2
        assert out[attrs.SYS_FORMAT]["details"] == {"version": "0.7", "pointsLoaded": 8}

    def test_las_header_fields(self, monkeypatch):
        _las_reader(monkeypatch, [GeoKeyDirectoryVlr([_geo_key(1024, 1), _geo_key(3072, 32633)]),
                                  WktCoordinateSystemVlr(UTM33_WKT)])
        out = attrs.las_header("/w/cloud.laz")
        assert out == {
            "points": 42, "boundsMin": [0.0, 1.0, 2.0], "boundsMax": [3.0, 4.0, 5.0],
            "crs": {"epsg": 32633, "name": "WGS 84 / UTM zone 33N", "geographic": False},
            "details": {"version": "1.4", "pointFormat": 7, "scales": [0.01, 0.01, 0.01],
                        "offsets": [0.0, 0.0, 0.0], "hasColorDimensions": True, "hasGpsTime": True,
                        "vlrCount": 2, "vlrTypes": ["GeoKeyDirectoryVlr", "WktCoordinateSystemVlr"],
                        "generatingSoftware": "las2las", "systemIdentifier": "scanner"}}

    def test_las_header_without_a_crs_vlr(self, monkeypatch):
        _las_reader(monkeypatch, [SimpleNamespace()])
        out = attrs.las_header("/w/cloud.las")
        assert out["crs"] is None and out["details"]["vlrCount"] == 1

    def test_e57_header_fields(self, monkeypatch):
        class _E57:
            scan_count = 3

            def __init__(self, path):
                pass

            def get_header(self, index):
                return SimpleNamespace(point_count=500, point_fields=["cartesianX", "cartesianY", "cartesianZ"])

        _module(monkeypatch, "pye57", E57=_E57)
        assert attrs.e57_header("/w/scan.e57") == {
            "points": 500, "crs": None,
            "details": {"scanCount": 3, "scanFields": ["cartesianX", "cartesianY", "cartesianZ"]}}

    def test_pointcloud_header_dispatch(self, monkeypatch, tmp_path):
        ptx = tmp_path / "a.ptx"
        ptx.write_bytes(b"2\n2\n" + b"0 0 0\n" * 8)
        assert attrs.pointcloud_header(".ptx", str(ptx)) == {"points": 4, "details": {"columns": 2, "rows": 2}}
        pcd = tmp_path / "a.pcd"
        pcd.write_bytes(b"VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
                        b"WIDTH 2\nHEIGHT 1\nPOINTS 2\nDATA ascii\n0 0 0\n1 1 1\n")
        assert attrs.pointcloud_header(".pcd", str(pcd)) == {
            "points": 2,
            "details": {"version": "0.7", "fields": ["x", "y", "z"], "data": "ascii", "width": 2, "height": 1}}
        assert attrs.pointcloud_header(".xyz", "/w/a.xyz") == {}


@pytest.mark.unit
class TestLasCrs:
    """CRS from the header VLRs alone: GeoTIFF keys for the EPSG code, the WKT record for the name, no
    reprojection library in the image."""

    def test_projected_geokey_and_wkt_name(self):
        crs = attrs.crs_from_las_vlrs([GeoKeyDirectoryVlr([_geo_key(1024, 1), _geo_key(3072, 32633)]),
                                       WktCoordinateSystemVlr(UTM33_WKT)])
        assert crs == {"epsg": 32633, "name": "WGS 84 / UTM zone 33N", "geographic": False}

    def test_geographic_geokey(self):
        crs = attrs.crs_from_las_vlrs([GeoKeyDirectoryVlr([_geo_key(2048, 4326)])])
        assert crs == {"epsg": 4326, "name": None, "geographic": True}

    def test_projected_wins_over_a_redundant_geographic_key(self):
        crs = attrs.crs_from_las_vlrs([GeoKeyDirectoryVlr([_geo_key(2048, 4326), _geo_key(3072, 32633)])])
        assert crs == {"epsg": 32633, "name": None, "geographic": False}

    def test_wkt_only_yields_the_name_and_the_geographic_flag(self):
        crs = attrs.crs_from_las_vlrs(
            [WktCoordinateSystemVlr('GEOGCRS["WGS 84",DATUM["World Geodetic System 1984"]]')])
        assert crs == {"epsg": None, "name": "WGS 84", "geographic": True}
        assert attrs.wkt_root('PROJCS["NAD83 ""HARN"" / Foo",GEOGCS["x"]]') == ("PROJCS", 'NAD83 "HARN" / Foo')
        assert attrs.wkt_root("") == (None, None)

    def test_keys_stored_in_another_vlr_or_out_of_range_are_ignored(self):
        crs = attrs.crs_from_las_vlrs([GeoKeyDirectoryVlr([_geo_key(3072, 32633, location=34736),
                                                           _geo_key(2048, 32767)])])
        assert crs == {"epsg": None, "name": None, "geographic": False}

    def test_no_crs_vlr_is_null(self):
        assert attrs.crs_from_las_vlrs([SimpleNamespace(), SimpleNamespace()]) is None
        assert attrs.crs_from_las_vlrs([]) is None

    def test_no_reprojection_library(self):
        with open(attrs.__file__, encoding="utf-8") as handle:
            assert "pyproj" not in handle.read().lower()


@pytest.mark.unit
class TestUsdScene:
    def test_prim_counts_units_and_stage_metadata(self, monkeypatch):
        prims = [SimpleNamespace(GetTypeName=lambda t=t: t) for t in ("Xform", "Mesh", "Mesh", "")]
        layer = SimpleNamespace(subLayerPaths=["a.usda"])
        default_prim = SimpleNamespace(GetName=lambda: "World", IsValid=lambda: True)
        stage = SimpleNamespace(
            Traverse=lambda: iter(prims), GetDefaultPrim=lambda: default_prim, GetRootLayer=lambda: layer,
            GetStartTimeCode=lambda: 1.0, GetEndTimeCode=lambda: 24.0, HasAuthoredTimeCodeRange=lambda: True)
        _module(monkeypatch, "pxr",
                Usd=SimpleNamespace(Stage=SimpleNamespace(Open=lambda path: stage)),
                UsdGeom=SimpleNamespace(GetStageUpAxis=lambda s: "Z", GetStageMetersPerUnit=lambda s: 0.01))
        out = attrs.usd_scene_attributes("/w/scene.usdz")
        assert out == {
            attrs.SYS_SCENE: {"nodeCount": 4, "hasAnimation": True, "hasArmature": False},
            attrs.SYS_STATISTICS: {"meshCount": 2},
            attrs.SYS_GEOMETRY: {"units": "cm"},
            attrs.SYS_FORMAT: {"details": {
                "primTypeCounts": {"Xform": 1, "Mesh": 2, "untyped": 1}, "upAxis": "Z", "metersPerUnit": 0.01,
                "defaultPrim": "World", "subLayerCount": 1, "startTimeCode": 1.0, "endTimeCode": 24.0,
                "hasAuthoredTimeCodes": True}}}

    def test_a_skeleton_prim_is_an_armature_and_merges_into_the_mesh_groups(self, monkeypatch):
        prims = [SimpleNamespace(GetTypeName=lambda t=t: t) for t in ("SkelRoot", "Skeleton", "Mesh")]
        layer = SimpleNamespace(subLayerPaths=[])
        stage = SimpleNamespace(
            Traverse=lambda: iter(prims), GetDefaultPrim=lambda: None, GetRootLayer=lambda: layer,
            GetStartTimeCode=lambda: 0.0, GetEndTimeCode=lambda: 0.0, HasAuthoredTimeCodeRange=lambda: False)
        _module(monkeypatch, "pxr",
                Usd=SimpleNamespace(Stage=SimpleNamespace(Open=lambda path: stage)),
                UsdGeom=SimpleNamespace(GetStageUpAxis=lambda s: "Y", GetStageMetersPerUnit=lambda s: 1.0))
        merged = attrs.merge_groups(attrs.polydata_attributes(_polydata(BOX, n_cells=12), ".usd", "usd-core"),
                                    attrs.usd_scene_attributes("/w/rig.usd"))
        assert_contract_shape(merged)
        assert merged[attrs.SYS_SCENE] == {"nodeCount": 3, "hasAnimation": False, "hasArmature": True}
        assert merged[attrs.SYS_STATISTICS]["meshCount"] == 1 and merged[attrs.SYS_STATISTICS]["vertices"] == 8
        assert merged[attrs.SYS_GEOMETRY]["units"] == "m"
        assert merged[attrs.SYS_FORMAT]["loader"] == "usd-core"
        assert merged[attrs.SYS_FORMAT]["details"]["upAxis"] == "Y"

    def test_an_unopenable_stage_raises(self, monkeypatch):
        _module(monkeypatch, "pxr", Usd=SimpleNamespace(Stage=SimpleNamespace(Open=lambda path: None)),
                UsdGeom=SimpleNamespace())
        with pytest.raises(ValueError, match="USD stage"):
            attrs.usd_scene_attributes("/w/broken.usd")


@pytest.mark.unit
class TestDxf:
    def test_entity_counts_units_and_2d_bounds(self, monkeypatch):
        entities = [SimpleNamespace(dxftype=lambda: "LINE"), SimpleNamespace(dxftype=lambda: "LINE"),
                    SimpleNamespace(dxftype=lambda: "CIRCLE"), SimpleNamespace(dxftype=lambda: "INSERT")]
        doc = SimpleNamespace(
            dxfversion="AC1027", modelspace=lambda: entities, header={"$INSUNITS": 4},
            layers=["0", "walls", "text"], blocks=["*Model_Space", "*Paper_Space", "door"])
        extents = SimpleNamespace(has_data=True, extmin=SimpleNamespace(x=0.0, y=0.0),
                                  extmax=SimpleNamespace(x=4.0, y=3.0))
        _module(monkeypatch, "ezdxf", readfile=lambda path: doc,
                bbox=SimpleNamespace(extents=lambda ents, fast=True: extents))
        _module(monkeypatch, "ezdxf.bbox", extents=lambda ents, fast=True: extents)
        out = attrs.dxf_attributes("/w/plan.dxf")
        assert_contract_shape(out)
        assert out[attrs.SYS_GEOMETRY] == {"boundsMin": [0.0, 0.0, 0.0], "boundsMax": [4.0, 3.0, 0.0],
                                           "dimensions": {"width": 4.0, "height": 3.0, "depth": 0.0},
                                           "extentMax": 4.0, "volume": None, "surfaceArea": None, "units": "mm"}
        assert out[attrs.SYS_CAD] == {"solidCount": 0, "faceCount": 0, "edgeCount": 3, "assemblyCount": 1,
                                      "declaredUnits": "mm", "estimatedUnits": None}
        assert out[attrs.SYS_FORMAT] == {"extension": ".dxf", "loader": "ezdxf", "details": {
            "is2d": True, "dxfVersion": "AC1027", "insunitsCode": 4, "insunitsName": "millimeters",
            "layers": 3, "blocks": 3, "entityCounts": {"LINE": 2, "CIRCLE": 1, "INSERT": 1}, "entityTotal": 4,
            "area": 12.0}}

    def test_an_empty_drawing_has_no_bounds(self, monkeypatch):
        doc = SimpleNamespace(dxfversion="AC1015", modelspace=lambda: [], header={}, layers=[], blocks=[])
        extents = SimpleNamespace(has_data=False)
        _module(monkeypatch, "ezdxf", readfile=lambda path: doc)
        _module(monkeypatch, "ezdxf.bbox", extents=lambda ents, fast=True: extents)
        out = attrs.dxf_attributes("/w/empty.dxf")
        assert out[attrs.SYS_GEOMETRY] == {key: None for key in CONTRACT["sys_geometry"]}
        assert out[attrs.SYS_CAD]["declaredUnits"] is None
        details = out[attrs.SYS_FORMAT]["details"]
        assert details["insunitsName"] == "unitless" and details["entityTotal"] == 0 and "area" not in details


@pytest.mark.unit
class TestIfc:
    def _model(self, counts, site=None):
        class _Model:
            schema = "IFC4"

            def by_type(self, name):
                n = counts.get(name, 0)
                if name == "IfcProject":
                    return [SimpleNamespace(Name="Clinic")] * n
                if name == "IfcSite":
                    blank = SimpleNamespace(RefLatitude=None, RefLongitude=None, RefElevation=None)
                    return [site if site is not None else blank] * n
                return [object()] * n
        return _Model()

    def test_schema_project_counts_and_site(self, monkeypatch):
        counts = {"IfcProject": 1, "IfcSite": 1, "IfcBuilding": 1, "IfcBuildingStorey": 2, "IfcProduct": 40,
                  "IfcElement": 31, "IfcWall": 12, "IfcDoor": 3}
        site = SimpleNamespace(RefLatitude=(48, 12, 30, 500000), RefLongitude=(16, 22, 12), RefElevation=171.0)
        opened = []

        def _open(path):
            opened.append(path)
            return self._model(counts, site)

        _module(monkeypatch, "ifcopenshell", open=_open)
        out = attrs.ifc_attributes("/w/model.ifc", "/w")
        assert opened == ["/w/model.ifc"]
        assert_contract_shape(out)
        ifc = out[attrs.SYS_IFC]
        assert ifc["schema"] == "IFC4" and ifc["projectName"] == "Clinic"
        assert ifc["storeyCount"] == 2 and ifc["elementCount"] == 31
        assert ifc["site"]["latitude"] == pytest.approx(48.2084722222)
        assert ifc["site"]["longitude"] == pytest.approx(16.37)
        assert ifc["site"]["elevation"] == 171.0
        assert out[attrs.SYS_FORMAT] == {"extension": ".ifc", "loader": "ifcopenshell", "details": {
            "sites": 1, "buildings": 1, "products": 40,
            "entityCounts": {"IfcWall": 12, "IfcDoor": 3, "IfcBuildingStorey": 2}}}

    @pytest.mark.parametrize("components,degrees", [
        ((48, 12, 30, 500000), 48.2084722222), ((-16, -22, -30), -16.375), ((0, -30, 0), -0.5),
        (None, None), ((48,), None)])
    def test_dms_to_decimal(self, components, degrees):
        if degrees is None:
            assert attrs.dms_to_decimal(components) is None
        else:
            assert attrs.dms_to_decimal(components) == pytest.approx(degrees)

    def test_a_site_without_coordinates_is_null(self, monkeypatch):
        site = SimpleNamespace(RefLatitude=None, RefLongitude=None, RefElevation=12.0)
        _module(monkeypatch, "ifcopenshell", open=lambda path: self._model({"IfcSite": 1}, site))
        out = attrs.ifc_attributes("/w/m.ifc", "/w")
        assert out[attrs.SYS_IFC]["site"] is None and out[attrs.SYS_IFC]["projectName"] is None

    def test_ifczip_is_unpacked_first(self, monkeypatch, tmp_path):
        archive = tmp_path / "model.ifczip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("sub/model.ifc", "ISO-10303-21;")
        opened = []

        def _open(path):
            opened.append(path)
            return self._model({"IfcProject": 0})

        _module(monkeypatch, "ifcopenshell", open=_open)
        out = attrs.ifc_attributes(str(archive), str(tmp_path))
        assert opened == [str(tmp_path / "model.ifc")]
        assert (tmp_path / "model.ifc").read_text() == "ISO-10303-21;"
        assert out[attrs.SYS_IFC]["projectName"] is None and out[attrs.SYS_IFC]["site"] is None
        assert out[attrs.SYS_FORMAT]["extension"] == ".ifczip"

    def test_an_ifczip_without_an_ifc_member_is_refused(self, tmp_path, monkeypatch):
        archive = tmp_path / "model.ifczip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("readme.txt", "x")
        _module(monkeypatch, "ifcopenshell", open=lambda path: None)
        with pytest.raises(ValueError, match="no .ifc member"):
            attrs.ifc_attributes(str(archive), str(tmp_path))


@pytest.mark.unit
class TestSplat:
    def test_splat_container(self, tmp_path):
        path = tmp_path / "a.splat"
        path.write_bytes(b"\0" * 96)
        out = attrs.splat_attributes(".splat", str(path))
        assert_contract_shape(out)
        assert out == {
            attrs.SYS_POINTCLOUD: {"pointCount": 3, "hasColor": True, "boundsMin": None, "boundsMax": None,
                                   "crs": None},
            attrs.SYS_FORMAT: {"extension": ".splat", "loader": "header",
                               "details": {"container": "splat", "isGaussianSplat": True, "recordBytes": 32}}}

    def test_ply_splat_from_its_header(self, tmp_path):
        path = tmp_path / "a.ply"
        path.write_bytes(b"ply\nformat binary_little_endian 1.0\nelement vertex 10\n"
                         b"property float x\nproperty float y\nproperty float z\nproperty float f_dc_0\n"
                         + b"".join(b"property float f_rest_%d\n" % i for i in range(24)) + b"end_header\n")
        out = attrs.splat_attributes(".ply", str(path))
        assert out[attrs.SYS_POINTCLOUD]["pointCount"] == 10
        assert out[attrs.SYS_FORMAT]["details"] == {"container": "ply", "isGaussianSplat": True,
                                                    "plyFormat": "binary_little_endian", "properties": 28,
                                                    "shDegree": 2}

    def test_sog_bundle(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("meta.json", json.dumps({"version": 2, "count": 9}))
        path = tmp_path / "a.sog"
        path.write_bytes(buf.getvalue())
        out = attrs.splat_attributes(".sog", str(path))
        assert out[attrs.SYS_POINTCLOUD]["pointCount"] == 9
        assert out[attrs.SYS_FORMAT]["details"]["container"] == "sog"

    def test_unknown_container(self, tmp_path):
        with pytest.raises(ValueError, match="splat container"):
            attrs.splat_attributes(".lcc", str(tmp_path / "a.lcc"))


@pytest.mark.unit
class TestCadShape:
    def test_combines_statistics_geometry_and_units(self, monkeypatch, tmp_path):
        from preview_pipeline.format_handlers import cad_handler
        path = tmp_path / "part.step"
        path.write_text("#1=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));\n")
        monkeypatch.setattr(cad_handler, "shape_statistics", lambda shape: {
            "solids": 3, "shells": 3, "faces": 30, "wires": 30, "edges": 60, "vertices": 40})
        monkeypatch.setattr(cad_handler, "shape_geometry", lambda shape: {
            "boundsMin": [0.0, 0.0, 0.0], "boundsMax": [120.0, 40.0, 10.0], "dimensions": [120.0, 40.0, 10.0],
            "volume": 4800.0, "surfaceArea": 1000.0, "centerOfMass": [60.0, 20.0, 5.0]})
        out = attrs.cad_shape_attributes("shape", ".step", str(path), _polydata(BOX, n_cells=12))
        assert_contract_shape(out)
        assert out[attrs.SYS_CAD] == {"solidCount": 3, "faceCount": 30, "edgeCount": 60, "assemblyCount": 3,
                                      "declaredUnits": "mm", "estimatedUnits": "mm"}
        assert out[attrs.SYS_GEOMETRY] == {"boundsMin": [0.0, 0.0, 0.0], "boundsMax": [120.0, 40.0, 10.0],
                                           "dimensions": {"width": 120.0, "height": 40.0, "depth": 10.0},
                                           "extentMax": 120.0, "volume": 4800.0, "surfaceArea": 1000.0,
                                           "units": "mm"}
        assert out[attrs.SYS_STATISTICS] == {"meshCount": 3, "vertices": 40, "faces": 30, "triangles": 12,
                                             "watertight": None}
        assert out[attrs.SYS_FORMAT] == {"extension": ".step", "loader": "OCP", "details": {
            "source": "step", "isAssembly": True, "shells": 3, "wires": 30, "centerOfMass": [60.0, 20.0, 5.0],
            "declaredUnitName": "millimetre", "estimatedUnitName": "millimeters (estimated)",
            "tessellatedVertices": 8, "tessellatedFaces": 12}}

    def test_iges_has_no_declared_units(self, monkeypatch, tmp_path):
        from preview_pipeline.format_handlers import cad_handler
        monkeypatch.setattr(cad_handler, "shape_statistics", lambda shape: {
            "solids": 1, "shells": 1, "faces": 6, "wires": 6, "edges": 12, "vertices": 8})
        monkeypatch.setattr(cad_handler, "shape_geometry", lambda shape: {
            "boundsMin": [0.0, 0.0, 0.0], "boundsMax": [0.5, 0.5, 0.5], "dimensions": [0.5, 0.5, 0.5],
            "volume": 0.125, "surfaceArea": 1.5, "centerOfMass": [0.25, 0.25, 0.25]})
        out = attrs.cad_shape_attributes("shape", ".igs", str(tmp_path / "p.igs"))
        assert out[attrs.SYS_CAD]["declaredUnits"] is None and out[attrs.SYS_CAD]["estimatedUnits"] == "m"
        assert out[attrs.SYS_GEOMETRY]["units"] == "m", "the estimate stands in when nothing is declared"
        assert out[attrs.SYS_STATISTICS]["triangles"] is None
        details = out[attrs.SYS_FORMAT]["details"]
        assert details["isAssembly"] is False and details["declaredUnitName"] is None
        assert "tessellatedVertices" not in details


@pytest.mark.unit
class TestFacts:
    def test_sentences_per_topic(self):
        facts = attrs.facts_for({
            attrs.SYS_GEOMETRY: {**BOX_GEOMETRY, "units": "mm"},
            attrs.SYS_STATISTICS: {"meshCount": 3, "vertices": 40, "faces": 30, "triangles": 12,
                                   "watertight": None},
            attrs.SYS_POINTCLOUD: {"pointCount": 1234567, "hasColor": True, "boundsMin": None, "boundsMax": None,
                                   "crs": {"epsg": 32633, "name": "WGS 84 / UTM zone 33N", "geographic": False}},
            attrs.SYS_CAD: {"solidCount": 3, "faceCount": 30, "edgeCount": 60, "assemblyCount": 3,
                            "declaredUnits": "mm", "estimatedUnits": "mm"},
            attrs.SYS_SCENE: {"nodeCount": 40, "hasAnimation": False, "hasArmature": False},
            attrs.SYS_IFC: {"schema": "IFC4", "projectName": "Clinic", "storeyCount": 2, "elementCount": 31,
                            "site": {"latitude": 48.2085, "longitude": 16.37, "elevation": 171.0}},
            attrs.SYS_FORMAT: {"extension": ".step", "loader": "OCP",
                               "details": {"upAxis": "Y", "metersPerUnit": 1.0}},
        })
        assert facts["geometry"] == "bounding box 2.000 x 1.000 x 4.000 mm; 40 vertices, 30 faces"
        assert facts["pointcloud"] == "1,234,567 points, with colour, CRS EPSG:32633 (WGS 84 / UTM zone 33N)"
        assert facts["cad"] == "B-rep model with 3 solids, 30 faces, 60 edges; units mm"
        assert facts["scene"] == "USD stage with 40 prims (3 meshes), Y-up, 1.0 m per unit"
        assert facts["ifc"] == "IFC4 model 'Clinic' with 31 elements across 2 storeys, sited at 48.2085, 16.3700"

    def test_splat_and_dxf_sentences(self):
        facts = attrs.facts_for({
            attrs.SYS_POINTCLOUD: {"pointCount": 5000, "hasColor": True, "boundsMin": None, "boundsMax": None,
                                   "crs": None},
            attrs.SYS_CAD: {"solidCount": 0, "faceCount": 0, "edgeCount": 3, "assemblyCount": 1,
                            "declaredUnits": "mm", "estimatedUnits": None},
            attrs.SYS_FORMAT: {"extension": ".dxf", "loader": "ezdxf", "details": {
                "container": "spz", "isGaussianSplat": True, "is2d": True, "dxfVersion": "AC1027",
                "entityTotal": 3, "layers": 3}}})
        assert facts["pointcloud"] == "Gaussian splat with 5,000 splats in a spz container"
        assert facts["cad"] == "2D DXF drawing (AC1027), units mm, 3 entities on 3 layers"

    def test_nothing_in_nothing_out(self):
        assert attrs.facts_for({}) == {}


@pytest.mark.unit
class TestMergeGroups:
    def test_groups_merge_key_by_key_and_details_one_level_deep(self):
        base = {"sys_geometry": {"units": None, "extentMax": 1.0},
                "sys_format": {"extension": ".usd", "loader": "usd-core", "details": {"a": 1}}}
        extra = {"sys_geometry": {"units": "cm"}, "sys_scene": {"nodeCount": 2}, "sys_format": {"details": {"b": 2}}}
        merged = attrs.merge_groups(base, extra)
        assert merged is base
        assert merged == {"sys_geometry": {"units": "cm", "extentMax": 1.0}, "sys_scene": {"nodeCount": 2},
                          "sys_format": {"extension": ".usd", "loader": "usd-core", "details": {"a": 1, "b": 2}}}


@pytest.mark.unit
class TestPromotionSourceContract:
    """Registry 3.6 "Promotion source contract": WP06d's metadataCatalog.py reads these exact key names, so a
    renamed key would otherwise surface only as a silently absent ext_* field. CONTRACT above is the
    registry copied verbatim; the module constant and every extractor are held to it (the dxf, ifc, splat
    and usd extractors through assert_contract_shape in their own classes)."""

    def test_the_module_constant_is_the_registry_contract(self):
        assert {group: set(keys) for group, keys in attrs.PROMOTION_SOURCE_KEYS.items()} == CONTRACT
        assert set(attrs.UNIT_TOKENS) == UNIT_TOKENS

    def test_mesh_groups(self):
        out = attrs.polydata_attributes(_polydata(BOX, n_cells=12), ".obj", "trimesh")
        assert set(out) == {attrs.SYS_GEOMETRY, attrs.SYS_STATISTICS, attrs.SYS_VISUAL, attrs.SYS_FORMAT}
        assert_contract_shape(out)

    def test_pointcloud_groups(self):
        out = attrs.pointcloud_attributes(".las", np.array(BOX), None,
                                          {"points": 8, "crs": {"epsg": 4326, "name": None, "geographic": True}},
                                          "laspy")
        assert set(out) == {attrs.SYS_POINTCLOUD, attrs.SYS_FORMAT}
        assert_contract_shape(out)

    def test_cad_groups(self, monkeypatch, tmp_path):
        from preview_pipeline.format_handlers import cad_handler
        monkeypatch.setattr(cad_handler, "shape_statistics", lambda shape: {
            "solids": 1, "shells": 1, "faces": 6, "wires": 6, "edges": 12, "vertices": 8})
        monkeypatch.setattr(cad_handler, "shape_geometry", lambda shape: {
            "boundsMin": [0.0, 0.0, 0.0], "boundsMax": [1.0, 1.0, 1.0], "dimensions": [1.0, 1.0, 1.0],
            "volume": 1.0, "surfaceArea": 6.0, "centerOfMass": [0.5, 0.5, 0.5]})
        out = attrs.cad_shape_attributes("shape", ".brep", str(tmp_path / "p.brep"), _polydata(BOX, n_cells=12))
        assert set(out) == {attrs.SYS_GEOMETRY, attrs.SYS_STATISTICS, attrs.SYS_CAD, attrs.SYS_FORMAT}
        assert_contract_shape(out)
