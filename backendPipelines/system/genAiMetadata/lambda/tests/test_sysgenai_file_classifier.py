#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The classifier's allow list is the viewer catalog, and every listed extension has a class and a
render branch.

The allow list is stored as an explicit literal (a Lambda cannot read the web catalog), so the rule
that derives it — every ``supportedExtensions`` entry of every ``enabled: true`` viewer, the preview
viewer's ``"*"`` excluded, minus ``EXCLUDED_EXTENSIONS`` — is re-derived here from
``web/src/visualizerPlugin/config/viewerConfig.json``. A viewer added without a classifier entry, or
an extension the pipeline lists that no viewer serves, fails here rather than at upload time.

The file-type phrases — the words a user types when asking for a kind of file — are embedded in every
file's source text and matched on the query side by the backend's ``fileClassIntent`` module; the two
dicts are pinned equal by reading the backend module by path, the way ``analysisCommon`` pins the
status file name to the backend constant."""

import importlib.util
import json
import os
import struct

import pytest

import sysgenai_harness as h

fc = h.load_local("fileClassifier")

_FILE_CLASS_INTENT = os.path.join(h.REPO_ROOT, "backend", "backend", "common", "vectorsearch", "fileClassIntent.py")

# Spec §6.6: the phrase per class, mirrored by the query-side intent vocabulary.
EXPECTED_PHRASES = {
    "mesh": "3D model (mesh)", "usd": "3D model (USD scene)", "cad": "CAD model", "splat": "3D Gaussian splat",
    "pointcloud": "point cloud (LiDAR scan)", "ifc": "BIM building model (IFC)", "tiles3d": "3D Tiles tileset",
    "image": "image (photo or picture)", "video": "video (footage)", "audio": "audio recording",
    "document": "document (PDF or office)", "text": "text file", "data": "data table (spreadsheet)", "other": "file",
}


def _backend_file_class_intent():
    spec = importlib.util.spec_from_file_location("sysgenai_backend_fileClassIntent", _FILE_CLASS_INTENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalog_extensions():
    """The spec §6.3 rule applied to the viewer catalog."""
    config = h.read_json_file(h.VIEWER_CONFIG)
    extensions = set()
    for viewer in config["viewers"]:
        if viewer.get("enabled") is not True:
            continue
        for extension in viewer.get("supportedExtensions", []):
            if extension == "*":
                continue
            extensions.add(extension.lower())
    return extensions


def _ply_header(*lines):
    return ("ply\nformat binary_little_endian 1.0\n" + "\n".join(lines) + "\nend_header\n").encode("ascii")


def _las_header(minor, legacy_count, count_1_4=0):
    header = bytearray(375)
    header[0:4] = b"LASF"
    header[24] = 1
    header[25] = minor
    struct.pack_into("<H", header, 94, 375)
    struct.pack_into("<I", header, 107, legacy_count)
    struct.pack_into("<Q", header, 247, count_1_4)
    return bytes(header)


def _no_sniff(_nbytes):
    raise AssertionError("classify() must not sniff an extension the table decides")


@pytest.mark.unit
class TestAllowList:
    def test_the_catalog_rule_is_live(self):
        # Control: a catalog read that returned nothing would make every set comparison below vacuous.
        catalog = catalog_extensions()
        assert len(catalog) >= 80, sorted(catalog)
        assert ".glb" in catalog and ".pdf" in catalog and ".las" in catalog

    def test_allow_list_equals_the_catalog_minus_the_exclusions(self):
        assert set(fc.ALLOW_LIST) == catalog_extensions() - fc.EXCLUDED_EXTENSIONS

    def test_exclusions_are_empty_at_release(self):
        assert fc.EXCLUDED_EXTENSIONS == set()

    def test_allow_list_is_sorted_finite_and_dotted(self):
        assert fc.ALLOW_LIST == sorted(fc.ALLOW_LIST)
        assert len(fc.ALLOW_LIST) == len(set(fc.ALLOW_LIST))
        for extension in fc.ALLOW_LIST:
            assert extension.startswith("."), extension
            assert extension == extension.lower(), extension
            assert "*" not in extension and "/" not in extension, extension

    @pytest.mark.temporary  # pins the drop of .fls/.fws relative to the thumbnail allow list
    def test_the_two_never_loading_faro_extensions_are_absent(self):
        # .fls/.fws were advertised by the thumbnail pipeline and never loaded; they are not viewer
        # extensions. test_allow_list_equals_the_catalog_minus_the_exclusions already forbids them
        # today; this pin exists only until the release that retires the thumbnail list.
        assert ".fls" not in fc.ALLOW_LIST and ".fws" not in fc.ALLOW_LIST

    def test_every_allow_listed_extension_has_a_class_and_branch(self):
        for extension in fc.ALLOW_LIST:
            if extension in fc.SNIFFED_EXTENSIONS:
                continue
            file_class, branch = fc.EXTENSION_CLASSES[extension]
            assert file_class in fc.FILE_CLASSES and file_class != fc.CLASS_OTHER, extension
            assert branch in fc.RENDER_BRANCHES and branch != fc.BRANCH_FARGATE, extension

    def test_the_table_and_the_sniffed_set_cover_exactly_the_allow_list(self):
        assert set(fc.EXTENSION_CLASSES) | set(fc.SNIFFED_EXTENSIONS) == set(fc.ALLOW_LIST)
        assert not (set(fc.EXTENSION_CLASSES) & set(fc.SNIFFED_EXTENSIONS))

    def test_only_proprietary_cad_routes_to_none_by_extension(self):
        """Every class whose attributes need a format library routes to a branch image; NONE is
        reserved for formats no open library reads (A7) and for the ``other`` fallback."""
        none_extensions = {ext for ext, (_cls, branch) in fc.EXTENSION_CLASSES.items()
                           if branch == fc.BRANCH_NONE}
        assert none_extensions == set(fc.PROPRIETARY_CAD_EXTENSIONS)
        for extension in fc.PROPRIETARY_CAD_EXTENSIONS:
            assert fc.EXTENSION_CLASSES[extension] == (fc.CLASS_CAD, fc.BRANCH_NONE)


@pytest.mark.unit
class TestClassifyMatrix:
    @pytest.mark.parametrize("extension,expected", [
        (".png", (fc.CLASS_IMAGE, fc.BRANCH_MEDIA)), (".svg", (fc.CLASS_IMAGE, fc.BRANCH_MEDIA)),
        (".mp4", (fc.CLASS_VIDEO, fc.BRANCH_MEDIA)), (".wav", (fc.CLASS_AUDIO, fc.BRANCH_MEDIA)),
        (".pdf", (fc.CLASS_DOCUMENT, fc.BRANCH_MEDIA)), (".md", (fc.CLASS_TEXT, fc.BRANCH_MEDIA)),
        (".html", (fc.CLASS_TEXT, fc.BRANCH_MEDIA)), (".csv", (fc.CLASS_DATA, fc.BRANCH_MEDIA)),
        (".fcs", (fc.CLASS_DATA, fc.BRANCH_MEDIA)),
        (".glb", (fc.CLASS_MESH, fc.BRANCH_BLENDER)), (".gltf", (fc.CLASS_MESH, fc.BRANCH_BLENDER)),
        (".fbx", (fc.CLASS_MESH, fc.BRANCH_BLENDER)), (".obj", (fc.CLASS_MESH, fc.BRANCH_BLENDER)),
        (".dae", (fc.CLASS_MESH, fc.BRANCH_BLENDER)), (".stl", (fc.CLASS_MESH, fc.BRANCH_BLENDER)),
        (".3ds", (fc.CLASS_MESH, fc.BRANCH_RENDER3D)), (".3mf", (fc.CLASS_MESH, fc.BRANCH_RENDER3D)),
        (".wrl", (fc.CLASS_MESH, fc.BRANCH_RENDER3D)), (".off", (fc.CLASS_MESH, fc.BRANCH_RENDER3D)),
        (".amf", (fc.CLASS_MESH, fc.BRANCH_RENDER3D)), (".3dm", (fc.CLASS_MESH, fc.BRANCH_RENDER3D)),
        (".bim", (fc.CLASS_MESH, fc.BRANCH_RENDER3D)),
        (".usd", (fc.CLASS_USD, fc.BRANCH_BLENDER)), (".usdz", (fc.CLASS_USD, fc.BRANCH_BLENDER)),
        (".stp", (fc.CLASS_CAD, fc.BRANCH_RENDER3D)), (".step", (fc.CLASS_CAD, fc.BRANCH_RENDER3D)),
        (".iges", (fc.CLASS_CAD, fc.BRANCH_RENDER3D)), (".brep", (fc.CLASS_CAD, fc.BRANCH_RENDER3D)),
        (".sldprt", (fc.CLASS_CAD, fc.BRANCH_NONE)), (".x_t", (fc.CLASS_CAD, fc.BRANCH_NONE)),
        (".las", (fc.CLASS_POINTCLOUD, fc.BRANCH_RENDER3D)), (".laz", (fc.CLASS_POINTCLOUD, fc.BRANCH_RENDER3D)),
        (".e57", (fc.CLASS_POINTCLOUD, fc.BRANCH_RENDER3D)),
        (".spz", (fc.CLASS_SPLAT, fc.BRANCH_RENDER3D)), (".sog", (fc.CLASS_SPLAT, fc.BRANCH_RENDER3D)),
        (".splat", (fc.CLASS_SPLAT, fc.BRANCH_RENDER3D)), (".lcc", (fc.CLASS_SPLAT, fc.BRANCH_RENDER3D)),
        (".ifc", (fc.CLASS_IFC, fc.BRANCH_RENDER3D)), (".ifczip", (fc.CLASS_IFC, fc.BRANCH_RENDER3D)),
    ])
    def test_table_decided_extensions(self, extension, expected):
        assert fc.classify(extension, _no_sniff) == expected

    def test_classification_is_case_insensitive(self):
        assert fc.classify(".GLB", _no_sniff) == (fc.CLASS_MESH, fc.BRANCH_BLENDER)

    def test_unknown_or_missing_extension_is_other_none(self):
        assert fc.classify(".zzz", _no_sniff) == (fc.CLASS_OTHER, fc.BRANCH_NONE)
        assert fc.classify("", _no_sniff) == (fc.CLASS_OTHER, fc.BRANCH_NONE)
        assert fc.classify(None, _no_sniff) == (fc.CLASS_OTHER, fc.BRANCH_NONE)


@pytest.mark.unit
class TestPlySniff:
    def test_faces_make_a_mesh(self):
        header = _ply_header("element vertex 8", "property float x", "element face 12",
                             "property list uchar int vertex_indices")
        assert fc.classify(".ply", lambda n: header) == (fc.CLASS_MESH, fc.BRANCH_BLENDER)

    def test_spherical_harmonics_make_a_splat(self):
        header = _ply_header("element vertex 5000", "property float x", "property float f_dc_0",
                             "property float opacity")
        assert fc.classify(".ply", lambda n: header) == (fc.CLASS_SPLAT, fc.BRANCH_RENDER3D)

    def test_vertices_alone_make_a_point_cloud(self):
        header = _ply_header("element vertex 5000", "property float x", "property uchar red")
        assert fc.classify(".ply", lambda n: header) == (fc.CLASS_POINTCLOUD, fc.BRANCH_RENDER3D)

    def test_zero_faces_is_not_a_mesh(self):
        header = _ply_header("element vertex 5000", "property float x", "element face 0")
        assert fc.classify(".ply", lambda n: header) == (fc.CLASS_POINTCLOUD, fc.BRANCH_RENDER3D)

    def test_an_unparsable_header_is_other(self):
        assert fc.classify(".ply", lambda n: b"\x00\x01not a ply") == (fc.CLASS_OTHER, fc.BRANCH_NONE)
        assert fc.classify(".ply", lambda n: b"") == (fc.CLASS_OTHER, fc.BRANCH_NONE)

    def test_the_sniff_is_asked_for_the_window_size(self):
        seen = []

        def sniff(nbytes):
            seen.append(nbytes)
            return _ply_header("element vertex 1", "element face 1")

        fc.classify(".ply", sniff)
        assert seen == [fc.SNIFF_BYTES]

    def test_vertex_count_is_read_for_the_point_cap(self):
        header = _ply_header("element vertex 123456", "property float x")
        assert fc.point_count_from_header(".ply", header) == 123456


@pytest.mark.unit
class TestJsonSniff:
    def test_a_tileset_root_is_tiles3d(self):
        head = json.dumps({"asset": {"version": "1.1"}, "geometricError": 500, "root": {}}).encode()
        assert fc.classify(".json", lambda n: head) == (fc.CLASS_TILES3D, fc.BRANCH_MEDIA)

    def test_a_truncated_tileset_is_still_tiles3d(self):
        head = (json.dumps({"asset": {"version": "1.1"}, "geometricError": 500, "root": {"children": []}})
                .encode()[:-12])
        assert fc.classify(".json", lambda n: head) == (fc.CLASS_TILES3D, fc.BRANCH_MEDIA)

    def test_ordinary_json_is_text(self):
        head = json.dumps({"name": "config", "values": [1, 2, 3]}).encode()
        assert fc.classify(".json", lambda n: head) == (fc.CLASS_TEXT, fc.BRANCH_MEDIA)

    def test_undecodable_bytes_are_other(self):
        assert fc.classify(".json", lambda n: b"\xff\xfe\x00\x00\x80") == (fc.CLASS_OTHER, fc.BRANCH_NONE)

    def test_an_array_root_is_text(self):
        assert fc.classify(".json", lambda n: b"[1, 2, 3]") == (fc.CLASS_TEXT, fc.BRANCH_MEDIA)

    def test_a_geojson_feature_collection_is_data(self):
        head = json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [8.5, 47.4]}, "properties": {}}]}).encode()
        assert fc.classify(".json", lambda n: head) == (fc.CLASS_DATA, fc.BRANCH_MEDIA)

    def test_a_bare_geojson_geometry_or_feature_is_data(self):
        polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
        assert fc.classify(".json", lambda n: json.dumps(polygon).encode()) == (fc.CLASS_DATA, fc.BRANCH_MEDIA)
        feature = {"type": "Feature", "geometry": polygon, "properties": {"name": "plot"}}
        assert fc.classify(".json", lambda n: json.dumps(feature).encode()) == (fc.CLASS_DATA, fc.BRANCH_MEDIA)

    def test_a_truncated_feature_collection_is_still_data(self):
        head = json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None}] * 40}).encode()[:-30]
        assert fc.classify(".json", lambda n: head) == (fc.CLASS_DATA, fc.BRANCH_MEDIA)

    def test_a_type_key_that_is_not_geojson_is_text(self):
        # "type" alone is not a GeoJSON marker; the value must be one of the closed set WP06c consults too.
        head = json.dumps({"type": "config", "values": [1]}).encode()
        assert fc.classify(".json", lambda n: head) == (fc.CLASS_TEXT, fc.BRANCH_MEDIA)
        assert fc.GEOJSON_ROOT_TYPES == frozenset({"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon",
                                                   "MultiPolygon", "GeometryCollection", "Feature", "FeatureCollection"})

    def test_a_geojson_type_without_its_member_key_is_text(self):
        # The media branch's geo.is_geojson applies the same member-key rule to the whole root.
        for root in ({"type": "Point"}, {"type": "Feature", "properties": {}}, {"type": "FeatureCollection"},
                     {"type": "GeometryCollection"}):
            assert fc.classify(".json", lambda n, r=root: json.dumps(r).encode()) == (fc.CLASS_TEXT, fc.BRANCH_MEDIA)

    def test_json_outcomes_are_exactly_the_media_branch_hand_off_set(self):
        """WP06c's MediaExtract handler reclassifies a text .json to tiles3d, data or other; this sniff
        decides the same four outcomes so state and manifest never disagree before the branch runs."""
        heads = [
            json.dumps({"asset": {"version": "1.1"}, "geometricError": 1, "root": {}}).encode(),
            json.dumps({"type": "FeatureCollection", "features": []}).encode(),
            json.dumps({"type": "MultiPolygon", "coordinates": []}).encode(),
            json.dumps({"type": "Point"}).encode(),  # a type word without its member key is text
            json.dumps({"hello": "world"}).encode(), b"[1]", b"not json at all", b"\xff\xfe",
        ]
        outcomes = {fc.classify(".json", lambda n, h=head: h) for head in heads}
        assert outcomes == {(fc.CLASS_TILES3D, fc.BRANCH_MEDIA), (fc.CLASS_DATA, fc.BRANCH_MEDIA),
                            (fc.CLASS_TEXT, fc.BRANCH_MEDIA), (fc.CLASS_OTHER, fc.BRANCH_NONE)}


@pytest.mark.unit
class TestLasHeader:
    def test_legacy_count_for_las_1_2(self):
        assert fc.las_point_count(_las_header(2, 4_000_000)) == 4_000_000

    def test_1_4_count_wins_when_present(self):
        assert fc.las_point_count(_las_header(4, 0, 25_000_000)) == 25_000_000

    def test_1_4_falls_back_to_legacy_when_the_extended_count_is_zero(self):
        assert fc.las_point_count(_las_header(4, 900, 0)) == 900

    def test_not_a_las_file_yields_none(self):
        assert fc.las_point_count(b"ply\nformat ascii 1.0\n") is None
        assert fc.las_point_count(b"LASF") is None

    def test_point_count_from_header_routes_by_extension(self):
        header = _las_header(2, 42)
        assert fc.point_count_from_header(".laz", header) == 42
        assert fc.point_count_from_header(".e57", header) is None
        assert fc.point_count_from_header(".glb", header) is None


@pytest.mark.unit
class TestFileClassPhrases:
    def test_every_class_has_a_phrase(self):
        """One phrase per class, ``other`` included, and the vocabulary is exactly the spec's — the embedding
        step indexes the dict by the manifest's class, so a class without a phrase would fail at run time."""
        assert set(fc.FILE_CLASS_PHRASES) == set(fc.FILE_CLASSES)
        assert fc.FILE_CLASS_PHRASES == EXPECTED_PHRASES

    def test_phrases_match_the_query_side_vocabulary(self):
        """Restated, not imported (pipeline code cannot import the backend package): the backend's
        fileClassIntent module carries the same dict for the search API's type-word matching, so a phrase
        changed on one side only would embed words the query side no longer recognises."""
        assert os.path.isfile(_FILE_CLASS_INTENT), (
            f"{_FILE_CLASS_INTENT} is missing: WP08 (the query-side file-type intent module) has not landed; "
            "the phrases this pipeline embeds must match the ones the search API recognises")
        backend = _backend_file_class_intent()
        assert hasattr(backend, "FILE_CLASS_PHRASES"), "fileClassIntent.py has no FILE_CLASS_PHRASES"
        assert fc.FILE_CLASS_PHRASES == backend.FILE_CLASS_PHRASES
