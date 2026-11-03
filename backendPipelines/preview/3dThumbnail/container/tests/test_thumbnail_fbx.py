#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""FBX scenes of quads and n-gons, their colours, and how far their instances expand (#499).

* An FBX is read with Open3D's ``read_triangle_model`` (Assimp), which triangulates quads and n-gons and
  applies node transforms. ``read_triangle_mesh`` skipped every non-triangle primitive, so a model
  exported from a DCC tool with quad faces loaded as empty and the run failed.
* A mesh keeps its vertex colours, or takes its material's base colour; a scene with neither stays
  uncoloured (the renderer's default colour), as before.
* Assimp expands every model placement and copies instanced geometry before it returns, so the scene is
  counted from the file first (``fbx_scene.scene_size``) and refused over the ``SceneLimit`` bounds
  (ARCC BSC1).

The FBX files are generated here, as ASCII 7.4 and as binary 7.4 (raw and zlib-deflated arrays). Tests
that load geometry need the real Open3D and PyVista (pipeline image).
"""

import re
import struct
import time
import zlib

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import core

CUBE_VERTICES = [-0.5, -0.5, 0.5, 0.5, -0.5, 0.5, -0.5, 0.5, 0.5, 0.5, 0.5, 0.5,
                 -0.5, 0.5, -0.5, 0.5, 0.5, -0.5, -0.5, -0.5, -0.5, 0.5, -0.5, -0.5]
CUBE_QUADS = [[0, 1, 3, 2], [2, 3, 5, 4], [4, 5, 7, 6], [6, 7, 1, 0], [1, 7, 5, 3], [6, 0, 2, 4]]

# A hexagonal prism: two hexagons (4 triangles each) and six quads (2 each), 20 triangles.
_HEX = [(np.cos(a), np.sin(a)) for a in np.linspace(0, 2 * np.pi, 7)[:-1]]
PRISM_VERTICES = [c for x, y in _HEX for c in (x, y, 0.0)] + [c for x, y in _HEX for c in (x, y, 1.0)]
PRISM_POLYGONS = [list(range(6)), list(range(11, 5, -1))] + [
    [i, (i + 1) % 6, (i + 1) % 6 + 6, i + 6] for i in range(6)]

# A tetrahedron of triangles with one colour per corner.
TETRA_VERTICES = [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]
TETRA_TRIANGLES = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
TETRA_RGBA = [1.0, 0.5, 0.0, 1.0]


def _index_array(polygons):
    """FBX ``PolygonVertexIndex``: a polygon's last index is written as ``-(i + 1)``."""
    out = []
    for polygon in polygons:
        out += list(polygon[:-1]) + [-polygon[-1] - 1]
    return out


def _ascii_array(name, values, indent="\t\t"):
    return f"{indent}{name}: *{len(values)} {{\n{indent}\ta: {','.join(str(v) for v in values)}\n{indent}}}\n"


def ascii_fbx(geometries=(), models=(), materials=(), connections=(), connection_type="OO"):
    """An ASCII FBX 7.4 document.

    ``geometries``: (id, vertices, polygons, rgba per polygon corner or None). ``models``: (id,
    (tx, ty, tz)). ``materials``: (id, (r, g, b)). ``connections``: (child id, parent id), 0 the root,
    of type ``connection_type``; an entry (child, parent, type) or (child, parent, type, property)
    sets its own. Each geometry names material 0 of its model, so a model's first connected material
    colours it.
    """
    out = ["; FBX 7.4.0 project file\n",
           "FBXHeaderExtension:  {\n\tFBXHeaderVersion: 1003\n\tFBXVersion: 7400\n}\n",
           "Objects:  {\n"]
    for gid, vertices, polygons, rgba in geometries:
        out.append(f'\tGeometry: {gid}, "Geometry::g{gid}", "Mesh" {{\n')
        out.append(_ascii_array("Vertices", vertices))
        out.append(_ascii_array("PolygonVertexIndex", _index_array(polygons)))
        out.append("\t\tGeometryVersion: 124\n")
        layers = ["LayerElementMaterial"]
        out.append('\t\tLayerElementMaterial: 0 {\n\t\t\tVersion: 101\n\t\t\tName: ""\n'
                   '\t\t\tMappingInformationType: "AllSame"\n\t\t\tReferenceInformationType: "IndexToDirect"\n'
                   + _ascii_array("Materials", [0], "\t\t\t") + "\t\t}\n")
        if rgba is not None:
            corners = sum(len(polygon) for polygon in polygons)
            layers.append("LayerElementColor")
            out.append('\t\tLayerElementColor: 0 {\n\t\t\tVersion: 101\n\t\t\tName: "colorSet1"\n'
                       '\t\t\tMappingInformationType: "ByPolygonVertex"\n\t\t\tReferenceInformationType: "Direct"\n'
                       + _ascii_array("Colors", list(rgba) * corners, "\t\t\t") + "\t\t}\n")
        out.append("\t\tLayer: 0 {\n\t\t\tVersion: 100\n" + "".join(
            f'\t\t\tLayerElement:  {{\n\t\t\t\tType: "{layer}"\n\t\t\t\tTypedIndex: 0\n\t\t\t}}\n' for layer in layers)
            + "\t\t}\n\t}\n")
    for mid, (tx, ty, tz) in models:
        out.append(f'\tModel: {mid}, "Model::m{mid}", "Mesh" {{\n\t\tVersion: 232\n\t\tProperties70:  {{\n'
                   f'\t\t\tP: "Lcl Translation", "Lcl Translation", "", "A",{tx},{ty},{tz}\n\t\t}}\n\t}}\n')
    for mat_id, (r, g, b) in materials:
        out.append(f'\tMaterial: {mat_id}, "Material::mat{mat_id}", "" {{\n\t\tVersion: 102\n'
                   f'\t\tShadingModel: "lambert"\n\t\tMultiLayer: 0\n\t\tProperties70:  {{\n'
                   f'\t\t\tP: "DiffuseColor", "Color", "", "A",{r},{g},{b}\n\t\t}}\n\t}}\n')
    out.append("}\nConnections:  {\n")
    for child, parent, kind, prop in _connections(connections, connection_type):
        out.append(f'\tC: "{kind}",{child},{parent}' + ("" if prop is None else f', "{prop}"') + "\n")
    out.append("}\n")
    return "".join(out)


def _connections(connections, connection_type):
    """(child, parent, type, property or None) of each connection entry."""
    for child, parent, *rest in connections:
        yield child, parent, rest[0] if rest else connection_type, rest[1] if len(rest) > 1 else None


# --- binary FBX: 7.4 (32-bit record offsets) and 7.5 and later (64-bit) ----------------------------

def _binary_property(kind, value, compress=False):
    if kind == "L":
        return b"L" + struct.pack("<q", value)
    if kind == "I":
        return b"I" + struct.pack("<i", value)
    if kind == "D":
        return b"D" + struct.pack("<d", value)
    if kind == "S":
        return b"S" + struct.pack("<I", len(value)) + value
    payload = np.asarray(value, dtype="<f8" if kind == "d" else "<i4").tobytes()
    if compress:
        payload = zlib.compress(payload)
    return kind.encode() + struct.pack("<III", len(value), 1 if compress else 0, len(payload)) + payload


def _binary_node(name, properties, children, start, wide, zero_words):
    header, layout = (25, "<QQQB") if wide else (13, "<IIIB")
    props = b"".join(properties)
    body_start = start + header + len(name) + len(props)
    nested = b"\x00" * (8 if wide else 4) if children and zero_words else b""
    for child in children:
        nested += child(body_start + len(nested), wide, zero_words)
    if children:
        nested += b"\x00" * header
    end = body_start + len(nested)
    return struct.pack(layout, end, len(properties), len(props), len(name)) + name + props + nested


def _node(name, properties=(), children=()):
    return lambda start, wide, zero_words: _binary_node(name, list(properties), list(children), start, wide,
                                                        zero_words)


def _p70(name, kind, values):
    """The properties of one ``Properties70`` ``P`` record: name, type, label, flags, three doubles."""
    return [_binary_property("S", s) for s in (name, kind, b"", b"A")] + [_binary_property("D", v) for v in values]


def binary_fbx(geometries=(), models=(), materials=(), connections=(), compress=False, version=7400,
               connection_type="OO", zero_words=False):
    """The binary FBX equivalent of ``ascii_fbx`` (vertex colours are not written); ``version`` 7500 and
    later use 64-bit record headers. Assimp needs a nested scope on every Model and Material, so each
    gets its Version and Properties70. ``zero_words`` starts every nested record list with a zero
    word, which Assimp's binary tokenizer skips."""
    objects = []
    for gid, vertices, polygons, _ in geometries:
        objects.append(_node(b"Geometry", [_binary_property("L", gid), _binary_property("S", b"g\x00\x01Geometry"),
                                           _binary_property("S", b"Mesh")], [
            _node(b"Vertices", [_binary_property("d", vertices, compress)]),
            _node(b"PolygonVertexIndex", [_binary_property("i", _index_array(polygons), compress)]),
            _node(b"LayerElementMaterial", [_binary_property("I", 0)], [
                _node(b"MappingInformationType", [_binary_property("S", b"AllSame")]),
                _node(b"ReferenceInformationType", [_binary_property("S", b"IndexToDirect")]),
                _node(b"Materials", [_binary_property("i", [0])])]),
            _node(b"Layer", [_binary_property("I", 0)], [
                _node(b"LayerElement", [], [_node(b"Type", [_binary_property("S", b"LayerElementMaterial")]),
                                            _node(b"TypedIndex", [_binary_property("I", 0)])])])]))
    for mid, (tx, ty, tz) in models:
        objects.append(_node(b"Model", [_binary_property("L", mid), _binary_property("S", b"m\x00\x01Model"),
                                        _binary_property("S", b"Mesh")], [
            _node(b"Version", [_binary_property("I", 232)]),
            _node(b"Properties70", [], [_node(b"P", _p70(b"Lcl Translation", b"Lcl Translation", (tx, ty, tz)))])]))
    for mat_id, (r, g, b) in materials:
        objects.append(_node(b"Material", [_binary_property("L", mat_id), _binary_property("S", b"m\x00\x01Material"),
                                           _binary_property("S", b"")], [
            _node(b"Version", [_binary_property("I", 102)]),
            _node(b"ShadingModel", [_binary_property("S", b"lambert")]),
            _node(b"MultiLayer", [_binary_property("I", 0)]),
            _node(b"Properties70", [], [_node(b"P", _p70(b"DiffuseColor", b"Color", (r, g, b)))])]))
    top = [
        _node(b"FBXHeaderExtension", [], [_node(b"FBXHeaderVersion", [_binary_property("I", 1003)]),
                                          _node(b"FBXVersion", [_binary_property("I", version)])]),
        _node(b"Objects", [], objects),
        _node(b"Connections", [], [
            _node(b"C", [_binary_property("S", kind.encode()), _binary_property("L", child),
                         _binary_property("L", parent)]
                  + ([] if prop is None else [_binary_property("S", prop.encode())]))
            for child, parent, kind, prop in _connections(connections, connection_type)]),
    ]
    wide = version >= 7500
    data = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", version)
    for node in top:
        data += node(len(data), wide, zero_words)
    return data + b"\x00" * (25 if wide else 13)


# --- scenes ----------------------------------------------------------------------------------------

def two_quad_cubes(rgba=None):
    """Two quad cubes 3 apart along X: red at x = -1.5, green at x = +1.5."""
    return dict(geometries=[(10, CUBE_VERTICES, CUBE_QUADS, rgba), (11, CUBE_VERTICES, CUBE_QUADS, rgba)],
                models=[(20, (-1.5, 0, 0)), (21, (1.5, 0, 0))],
                materials=[(30, (1, 0, 0)), (31, (0, 1, 0))],
                connections=[(20, 0), (21, 0), (10, 20), (11, 21), (30, 20), (31, 21)])


def one_cube_placed(count, spacing=2.0):
    """One quad cube geometry under ``count`` models in a row: ``count`` placements."""
    models = [(100 + i, (spacing * i, 0, 0)) for i in range(count)]
    connections = [(m, 0) for m, _ in models] + [(10, m) for m, _ in models] + [(30, m) for m, _ in models]
    return dict(geometries=[(10, CUBE_VERTICES, CUBE_QUADS, None)], models=models,
                materials=[(30, (1, 0, 0))], connections=connections)


def doubling(depth):
    """Level k holds two models, each a child of both models of level k - 1; the last level's two
    models carry the cube: 2^(depth + 1) cube placements from a file of a few kilobytes."""
    models, connections = [], []
    for k in range(depth + 1):
        a, b = 1000 + 2 * k, 1001 + 2 * k
        models += [(a, (0.5, 0, 0)), (b, (0, 0.5, 0))]
        parents = [0] if k == 0 else [998 + 2 * k, 999 + 2 * k]
        connections += [(child, parent) for child in (a, b) for parent in parents]
    connections += [(10, 1000 + 2 * depth), (10, 1001 + 2 * depth)]
    return dict(geometries=[(10, CUBE_VERTICES, CUBE_QUADS, None)], models=models, connections=connections)


def property_links(scene):
    """``scene`` with every connection repeated as an "OP" link naming a property and as a "PP" link.
    Assimp reads neither as a placement."""
    pairs = [connection[:2] for connection in scene["connections"]]
    return dict(scene, connections=list(scene["connections"]) + [(c, p, "OP", "Lcl Translation") for c, p in pairs]
                + [(c, p, "PP") for c, p in pairs])


def nested_ascii(depth):
    """An ASCII FBX whose scopes nest ``depth`` levels deep, and nothing else. The parser descends
    into each ``{`` as it opens, so this is ``depth`` levels of nesting."""
    return "; FBX 7.4.0 project file\n" + "".join(f"N{i}: {{\n" for i in range(depth)) + "}\n" * depth


def nested_binary(depth, version=7400):
    """A binary FBX of ``depth`` nested node records (one chain). ``depth`` must stay within the
    interpreter's recursion limit, since ``_node`` builds the chain recursively; the reader under
    test walks it iteratively."""
    node = _node(b"N")
    for _ in range(depth - 1):
        node = _node(b"N", children=[node])
    wide = version >= 7500
    data = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", version)
    data += node(len(data), wide, False)
    return data + b"\x00" * (25 if wide else 13)


def _nested(depth, encoding):
    return nested_ascii(depth) if encoding == "ascii" else nested_binary(
        depth, version=7500 if encoding == "binary-7500" else 7400)


def _write(path, text_or_bytes):
    if isinstance(text_or_bytes, str):
        path.write_text(text_or_bytes)
    else:
        path.write_bytes(text_or_bytes)
    return str(path)


def _budgets(triangles=1_000_000):
    from preview_pipeline import resources

    return resources.Budgets(memory_bytes=16 * 1024 ** 3, cpus=4, points=1_000_000, triangles=triangles,
                             splats=250_000)


def _colours(mesh):
    return {tuple(int(c) for c in row) for row in np.asarray(mesh.point_data["RGB"])}


def _never_read(*args, **kwargs):
    raise AssertionError("Open3D was asked to read a scene that should have been refused first")


def _block_open3d(monkeypatch):
    """Fail any Open3D mesh read, so a scene that is not refused first fails at once instead of
    expanding (the pre-#499 loader called ``read_triangle_mesh``)."""
    import open3d as o3d

    monkeypatch.setattr(o3d.io, "read_triangle_model", _never_read)
    monkeypatch.setattr(o3d.io, "read_triangle_mesh", _never_read)


def _fbx_file(path, scene, encoding):
    """``scene`` written as ``ascii``, ``binary`` (7.4), ``binary-zlib`` (7.4, arrays deflated) or
    ``binary-7500`` (64-bit record headers, arrays deflated)."""
    if encoding == "ascii":
        return _write(path, ascii_fbx(**scene))
    return _write(path, binary_fbx(**scene, compress=encoding != "binary",
                                   version=7500 if encoding == "binary-7500" else 7400))


ENCODINGS = ["ascii", "binary", "binary-zlib", "binary-7500"]

# scene_size of doubling(4): models, vertices, triangles, stored vertices, stored triangles.
DOUBLING_4 = (62, 768, 384, 24, 12)


def _ascii_doubling(edit=lambda text: text, **options):
    return lambda depth: edit(ascii_fbx(**doubling(depth), **options))


def _binary_doubling(edit=lambda data: data, **options):
    return lambda depth: edit(binary_fbx(**doubling(depth), **options))


def _entries_on_separate_lines(text):
    return re.sub(r"(PolygonVertexIndex: \*\d+ \{\n\t\t\ta: )([^\n]*)",
                  lambda m: m.group(1) + m.group(2).replace(",", "\n\t\t\t"), text)


# A doubling written the ways Assimp 5.4.2 (Open3D 0.19) reads it: (id, file for a depth, scene_size at
# depth 4). At depth 4 Assimp builds the third number's triangles from each file.
READINGS = [
    # Document::ReadConnections reads every connection type but "PP".
    ("ascii-type-ZZ", _ascii_doubling(connection_type="ZZ"), DOUBLING_4),
    ("ascii-type-empty", _ascii_doubling(connection_type=""), DOUBLING_4),
    ("binary-type-ZZ", _binary_doubling(connection_type="ZZ"), DOUBLING_4),
    ("binary-7500-type-empty", _binary_doubling(connection_type="", compress=True, version=7500), DOUBLING_4),
    ("ascii-type-PP", _ascii_doubling(connection_type="PP"), (0, 0, 0, 24, 12)),
    ("binary-type-PP", _binary_doubling(connection_type="PP"), (0, 0, 0, 24, 12)),
    # The node and geometry walks skip an "OP" link that names a property, and only that one.
    ("ascii-property-links", lambda depth: ascii_fbx(**property_links(doubling(depth))), DOUBLING_4),
    ("binary-property-links", lambda depth: binary_fbx(**property_links(doubling(depth))), DOUBLING_4),
    ("ascii-op-without-property", lambda depth: ascii_fbx(**dict(doubling(depth), connections=[
        (c, p, "OP", "") for c, p in doubling(depth)["connections"]])), DOUBLING_4),
    # ParseTokenAsID reads an id's leading digits, and an id that overflows 64 bits as 0, the root.
    ("ascii-ids-with-trailing-characters",
     _ascii_doubling(lambda t: re.sub(r'(C: "OO",\d+),(\d+)', r"\1x,\2y", t)), DOUBLING_4),
    ("ascii-overflowing-root-id",
     _ascii_doubling(lambda t: re.sub(r'(C: "OO",\d+),0\n', r"\1,99999999999999999999\n", t)), DOUBLING_4),
    # The ASCII tokenizer: comments and strings hold no structure, the text ends at a NUL, and entries
    # may be split by line breaks instead of commas.
    ("ascii-comment-holds-a-header",
     _ascii_doubling(lambda t: t.replace("Connections:  {", '; Model: 10, "x", "y"\nConnections:  {', 1)),
     DOUBLING_4),
    ("ascii-comment-in-a-geometry",
     _ascii_doubling(lambda t: t.replace("\t\tPolygonVertexIndex:", "\t\t; Model: 77,\n\t\tPolygonVertexIndex:", 1)),
     DOUBLING_4),
    ("ascii-string-holds-a-header",
     _ascii_doubling(lambda t: t.replace("\t\tVersion: 232\n", '\t\tVersion: 232\n\t\tName: "Model: 10, x"\n', 1)),
     DOUBLING_4),
    ("ascii-text-after-a-nul",
     _ascii_doubling(lambda t: t + '\x00Objects: {\n\tModel: 10, "x", "y" {\n\t}\n}\n'), DOUBLING_4),
    ("ascii-entries-on-separate-lines", _ascii_doubling(_entries_on_separate_lines), DOUBLING_4),
    # Assimp keeps one object per id and reads the first Objects section; both count as all of them.
    ("ascii-id-reused-by-a-geometry", _ascii_doubling(lambda t: t.replace(
        "}\nConnections:", '\tGeometry: 1000, "Geometry::reused", "Mesh" {\n\t}\n}\nConnections:', 1)), DOUBLING_4),
    ("ascii-second-objects-section", _ascii_doubling(
        lambda t: t + 'Objects:  {\n\tModel: 10, "Model::x", "Mesh" {\n\t\tVersion: 232\n\t}\n}\n'),
     (94, 768, 384, 24, 12)),
    # MeshGeometry: a one-corner polygon adds a vertex and no triangle.
    ("ascii-one-corner-polygons", _ascii_doubling(lambda t: t.replace(
        "PolygonVertexIndex: *24 {\n\t\t\ta: ", "PolygonVertexIndex: *48 {\n\t\t\ta: " + "-1," * 24, 1)),
     (62, 1536, 384, 48, 12)),
    # The binary tokenizer: any file starting "Kaydara FBX Binary" is binary, and a zero word inside a
    # nested record list is skipped.
    ("binary-magic-tail", _binary_doubling(lambda d: d[:18] + b"XY\x00" + d[21:]), DOUBLING_4),
    ("binary-zero-words", _binary_doubling(zero_words=True), DOUBLING_4),
    ("binary-7500-zero-words", _binary_doubling(zero_words=True, compress=True, version=7500), DOUBLING_4),
]
READING_IDS = [reading[0] for reading in READINGS]
EXPANDING_READINGS = [reading for reading in READINGS if not reading[0].endswith("-PP")]


# --------------------------------------------------------------------------------------------------
# Loading quads, n-gons and colours
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
@requires_real("open3d", "pyvista")
class TestFbxPolygons:
    @pytest.mark.parametrize("encoding", ENCODINGS)
    def test_quad_cubes_load_triangulated_apart_and_in_their_material_colours(self, tmp_path, encoding):
        mesh = core._load_file(_fbx_file(tmp_path / "quads.fbx", two_quad_cubes(), encoding), ".fbx",
                               budgets=_budgets())
        assert mesh.n_cells == 24, "six quads per cube, two triangles each"
        assert mesh.is_all_triangles
        rgb = np.asarray(mesh.point_data["RGB"])
        points = np.asarray(mesh.points)
        red = points[(rgb == (255, 0, 0)).all(axis=1)]
        green = points[(rgb == (0, 255, 0)).all(axis=1)]
        assert len(red) and len(green) and len(red) + len(green) == mesh.n_points
        np.testing.assert_allclose([red[:, 0].min(), red[:, 0].max()], [-2.0, -1.0], atol=1e-6)
        np.testing.assert_allclose([green[:, 0].min(), green[:, 0].max()], [1.0, 2.0], atol=1e-6)

    def test_n_gons_are_triangulated(self, tmp_path):
        scene = dict(geometries=[(10, PRISM_VERTICES, PRISM_POLYGONS, None)], models=[(20, (0, 0, 0))],
                     connections=[(20, 0), (10, 20)])
        mesh = core._load_file(_write(tmp_path / "prism.fbx", ascii_fbx(**scene)), ".fbx", budgets=_budgets())
        assert mesh.n_cells == 20
        np.testing.assert_allclose(mesh.bounds[4:], [0.0, 1.0], atol=1e-6)

    def test_a_triangle_mesh_keeps_its_vertex_colours(self, tmp_path):
        scene = dict(geometries=[(10, TETRA_VERTICES, TETRA_TRIANGLES, TETRA_RGBA)], models=[(20, (0, 0, 0))],
                     materials=[(30, (0, 0, 1))], connections=[(20, 0), (10, 20), (30, 20)])
        mesh = core._load_file(_write(tmp_path / "tetra.fbx", ascii_fbx(**scene)), ".fbx", budgets=_budgets())
        assert mesh.n_cells == 4
        assert _colours(mesh) == {(255, 127, 0)}, "vertex colours win over the material's blue"

    def test_a_scene_with_no_colour_stays_uncoloured(self, tmp_path):
        scene = dict(geometries=[(10, TETRA_VERTICES, TETRA_TRIANGLES, None)], models=[(20, (0, 0, 0))],
                     connections=[(20, 0), (10, 20)])
        mesh = core._load_file(_write(tmp_path / "plain.fbx", ascii_fbx(**scene)), ".fbx", budgets=_budgets())
        assert mesh.n_cells == 4
        assert "RGB" not in mesh.point_data, "the renderer's default colour, as for any uncoloured mesh"

    def test_an_uncoloured_part_beside_a_coloured_one_gets_the_default_mesh_colour(self, tmp_path):
        scene = two_quad_cubes()
        scene["materials"] = [(30, (1, 0, 0))]
        scene["connections"] = [c for c in scene["connections"] if c != (31, 21)]
        mesh = core._load_file(_write(tmp_path / "half.fbx", ascii_fbx(**scene)), ".fbx", budgets=_budgets())
        assert _colours(mesh) == {(255, 0, 0), (173, 216, 230)}

    @pytest.mark.parametrize("content", [b"", ascii_fbx().encode()], ids=["zero-bytes", "no-objects"])
    def test_a_file_with_no_geometry_fails_with_a_message(self, tmp_path, content):
        path = _write(tmp_path / "empty.fbx", content)
        with pytest.raises(ValueError, match="No triangle geometry read from the FBX file"):
            core._load_file(path, ".fbx", budgets=_budgets())

    def test_the_declared_up_axis_still_travels_with_the_mesh(self, tmp_path):
        text = ascii_fbx(**two_quad_cubes()).replace(
            "Objects:  {", 'GlobalSettings:  {\n\tProperties70:  {\n\t\tP: "UpAxis", "int", "Integer", "",2\n'
                           '\t}\n}\nObjects:  {', 1)
        mesh = core._load_file(_write(tmp_path / "z-up.fbx", text), ".fbx", budgets=_budgets())
        assert mesh.n_cells == 24 and mesh._preview_up_axis == "Z"


# --------------------------------------------------------------------------------------------------
# Expansion bounds
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
@requires_real("open3d", "pyvista")
class TestFbxExpansion:
    def test_a_30_level_doubling_is_refused_before_open3d_reads_it(self, tmp_path, monkeypatch, caplog):
        path = _write(tmp_path / "doubling.fbx", ascii_fbx(**doubling(30)))
        _block_open3d(monkeypatch)
        with caplog.at_level("WARNING"), pytest.raises(ValueError, match="places more than 1,000,000 models"):
            core._load_file(path, ".fbx", budgets=_budgets())
        assert "1,000,000 nodes" in caplog.text

    def test_a_model_cycle_is_refused_before_open3d_reads_it(self, tmp_path, monkeypatch):
        scene = one_cube_placed(2)
        scene["connections"] += [(100, 101), (101, 100)]
        path = _write(tmp_path / "cycle.fbx", ascii_fbx(**scene))
        _block_open3d(monkeypatch)
        with pytest.raises(ValueError, match="places more than 1,000,000 models"):
            core._load_file(path, ".fbx", budgets=_budgets())

    def test_a_deeply_nested_fbx_is_refused_before_it_is_read(self, tmp_path):
        """A file nesting tens of thousands of levels must be refused by the pre-read, before the
        reader descends into it. The real load path runs in a subprocess so that, without the
        pre-read, the child does not return cleanly (it is read by descending into every level and
        the process ends abnormally) and this test fails on the child's exit status instead of
        taking pytest down with it. With the pre-read the child refuses the file and exits 0."""
        import os
        import subprocess  # nosec B404 - fixed interpreter, no shell
        import sys

        import conftest

        path = _write(tmp_path / "deep.fbx", nested_ascii(30_000))
        child = (
            "import sys\n"
            "from preview_pipeline import core\n"
            "try:\n"
            "    core._load_file(sys.argv[1], '.fbx', budgets=None)\n"
            "    print('LOADED')\n"
            "except ValueError as e:\n"
            "    print('REFUSED' if 'nests deeper' in str(e) else 'VALUEERROR:' + str(e))\n"
        )
        env = dict(os.environ,
                   PYTHONPATH=conftest._CONTAINER_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""),
                   AWS_DEFAULT_REGION="us-east-1", AWS_REGION="us-east-1")
        result = subprocess.run([sys.executable, "-c", child, path],  # nosec B603 - fixed args
                                capture_output=True, text=True, timeout=600, env=env)
        assert result.returncode == 0, (
            f"the load did not return cleanly (returncode {result.returncode}). The file reached the "
            f"reader instead of being refused first. stdout={result.stdout!r} "
            f"stderr={result.stderr[-500:]!r}")
        assert "REFUSED" in result.stdout, f"stdout={result.stdout!r}"

    def test_instances_past_the_triangle_budget_are_refused(self, tmp_path, monkeypatch):
        path = _write(tmp_path / "row.fbx", ascii_fbx(**one_cube_placed(100)))
        _block_open3d(monkeypatch)
        with pytest.raises(ValueError, match="places more than 1,000 triangles"):
            core._load_file(path, ".fbx", budgets=_budgets(triangles=1_000))

    def test_instances_within_the_budget_all_load_in_place(self, tmp_path):
        mesh = core._load_file(_write(tmp_path / "row.fbx", ascii_fbx(**one_cube_placed(50))), ".fbx",
                               budgets=_budgets(triangles=1_000))
        assert mesh.n_cells == 50 * 12
        np.testing.assert_allclose([mesh.bounds[0], mesh.bounds[1]], [-0.5, 98.5], atol=1e-6)

    def test_a_single_mesh_larger_than_the_budget_still_loads(self, tmp_path):
        """Without instancing nothing expands: the file's own geometry loads and the budget decimates it."""
        mesh = core._load_file(_write(tmp_path / "prism.fbx", ascii_fbx(
            geometries=[(10, PRISM_VERTICES, PRISM_POLYGONS, None)], models=[(20, (0, 0, 0))],
            connections=[(20, 0), (10, 20)])), ".fbx", budgets=_budgets(triangles=10))
        assert mesh.n_cells == 20

    @pytest.mark.parametrize("encoding", ENCODINGS)
    @pytest.mark.parametrize("connection_type", ["ZZ", ""], ids=["ZZ", "empty"])
    def test_a_doubling_linked_by_any_connection_type_is_refused_before_open3d_reads_it(
            self, tmp_path, monkeypatch, encoding, connection_type):
        """Assimp links a connection's child to its parent whatever its type string except "PP"."""
        path = _fbx_file(tmp_path / "doubling.fbx", dict(doubling(30), connection_type=connection_type), encoding)
        _block_open3d(monkeypatch)
        with pytest.raises(ValueError, match="places more than 1,000,000 models"):
            core._load_file(path, ".fbx", budgets=_budgets())

    @pytest.mark.parametrize("name, build, expected", EXPANDING_READINGS,
                             ids=[reading[0] for reading in EXPANDING_READINGS])
    def test_a_30_level_doubling_is_refused_however_assimp_would_read_it(
            self, tmp_path, monkeypatch, name, build, expected):
        path = _write(tmp_path / "doubling.fbx", build(30))
        _block_open3d(monkeypatch)
        with pytest.raises(ValueError, match="places more than 1,000,000 models"):
            core._load_file(path, ".fbx", budgets=_budgets())

    @pytest.mark.parametrize("name, build, expected", EXPANDING_READINGS,
                             ids=[reading[0] for reading in EXPANDING_READINGS])
    def test_instances_past_the_triangle_budget_are_refused_however_assimp_would_read_them(
            self, tmp_path, monkeypatch, name, build, expected):
        """An 8-level doubling: 1,022 models placing 512 cubes, 6,144 triangles."""
        path = _write(tmp_path / "doubling.fbx", build(8))
        _block_open3d(monkeypatch)
        with pytest.raises(ValueError, match="places more than 1,000 triangles"):
            core._load_file(path, ".fbx", budgets=_budgets(triangles=1_000))

    @pytest.mark.parametrize("encoding", ENCODINGS)
    @pytest.mark.parametrize("scene", [two_quad_cubes(), one_cube_placed(3), doubling(4),
                                       dict(doubling(4), connection_type="ZZ"), property_links(two_quad_cubes())],
                             ids=["two-cubes", "shared-geometry", "doubling-4", "doubling-4-type-ZZ",
                                  "two-cubes-property-links"])
    def test_the_counted_triangles_are_the_triangles_open3d_reads(self, tmp_path, scene, encoding):
        import open3d as o3d
        from preview_pipeline.format_handlers import fbx_scene

        path = _fbx_file(tmp_path / "scene.fbx", scene, encoding)
        model = o3d.io.read_triangle_model(path)
        read = sum(len(m.mesh.triangles) for m in model.meshes)
        assert read and fbx_scene.scene_size(path, 1_000_000).triangles == read

    @pytest.mark.parametrize("name, build, expected", READINGS, ids=READING_IDS)
    def test_open3d_reads_the_counted_triangles_however_the_file_is_written(self, tmp_path, name, build, expected):
        import open3d as o3d
        from preview_pipeline.format_handlers import fbx_scene

        path = _write(tmp_path / "doubling.fbx", build(4))
        model = o3d.io.read_triangle_model(path)
        read = sum(len(m.mesh.triangles) for m in model.meshes)
        assert read == expected[2] == fbx_scene.scene_size(path, 1_000_000).triangles


# --------------------------------------------------------------------------------------------------
# Counting the scene from the file (no Open3D)
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestFbxSceneSize:
    @pytest.mark.parametrize("compress, version", [(False, 7400), (True, 7400), (True, 7500)],
                             ids=["raw", "zlib", "7500"])
    def test_ascii_and_binary_files_count_alike(self, tmp_path, compress, version):
        from preview_pipeline.format_handlers import fbx_scene

        for name, scene, expected in [
            ("cubes", two_quad_cubes(), (2, 48, 24, 48, 24)),
            ("row", one_cube_placed(3), (3, 72, 36, 24, 12)),
            ("doubling", doubling(4), (2 * (2 ** 5 - 1), 24 * 2 ** 5, 12 * 2 ** 5, 24, 12)),
            ("prism", dict(geometries=[(10, PRISM_VERTICES, PRISM_POLYGONS, None)], models=[(20, (0, 0, 0))],
                           connections=[(20, 0), (10, 20)]), (1, 36, 20, 36, 20)),
        ]:
            text = _write(tmp_path / f"{name}.fbx", ascii_fbx(**scene))
            binary = _write(tmp_path / f"{name}-bin.fbx", binary_fbx(**scene, compress=compress, version=version))
            assert tuple(fbx_scene.scene_size(text, 1_000_000)) == expected, name
            assert tuple(fbx_scene.scene_size(binary, 1_000_000)) == expected, name

    @pytest.mark.parametrize("name, build, expected", READINGS, ids=READING_IDS)
    def test_the_file_is_counted_as_assimp_reads_it(self, tmp_path, name, build, expected):
        from preview_pipeline.format_handlers import fbx_scene

        assert tuple(fbx_scene.scene_size(_write(tmp_path / "doubling.fbx", build(4)), 1_000_000)) == expected

    @pytest.mark.parametrize("line, count, refused", [
        ("PolygonVertexIndex: *1 {\n", 20_000, True),
        ("PolygonVertexIndex: *1 {}\n", 20_000, False),
        (";PolygonVertexIndex: *1 {\n", 4_000, False),
    ], ids=["open-index-arrays", "closed-index-arrays", "commented-index-arrays"])
    def test_the_ascii_scan_reads_the_file_once(self, tmp_path, line, count, refused):
        """A geometry block packed with index-array headers, left open, closed or commented out. A
        scan that re-reads the rest of the block from each header takes more than 10 s on these files;
        one pass takes milliseconds, so the bound is generous. The open headers each open a scope and
        never close it, so that file nests one level per header and is refused by the depth bound;
        the closed and commented headers stay shallow and are counted."""
        from preview_pipeline.format_handlers import fbx_scene

        anchor = '"Geometry::g10", "Mesh" {\n'
        text = ascii_fbx(**one_cube_placed(1)).replace(anchor, anchor + line * count, 1)
        path = _write(tmp_path / "packed.fbx", text)
        started = time.perf_counter()
        if refused:
            with pytest.raises(ValueError, match="nests deeper"):
                fbx_scene.scene_size(path, 1_000_000)
        else:
            assert tuple(fbx_scene.scene_size(path, 1_000_000)) == (1, 24, 12, 24, 12)
        assert time.perf_counter() - started < 2.0

    def test_a_deep_doubling_stops_counting_at_the_bound(self, tmp_path):
        from preview_pipeline.format_handlers import fbx_scene

        for data in (ascii_fbx(**doubling(200)), binary_fbx(**doubling(200))):
            assert fbx_scene.scene_size(_write(tmp_path / "deep.fbx", data), 1_000).models == 1_001

    def test_a_chain_deeper_than_the_recursion_limit_is_counted(self, tmp_path):
        from preview_pipeline.format_handlers import fbx_scene

        depth = 5_000
        models = [(100 + i, (0, 0, 0)) for i in range(depth)]
        connections = [(100, 0)] + [(101 + i, 100 + i) for i in range(depth - 1)] + [(10, 100 + depth - 1)]
        scene = dict(geometries=[(10, CUBE_VERTICES, CUBE_QUADS, None)], models=models, connections=connections)
        path = _write(tmp_path / "chain.fbx", ascii_fbx(**scene))
        assert tuple(fbx_scene.scene_size(path, 1_000_000))[:3] == (depth, 24, 12)

    def test_a_cycle_counts_as_over_the_bound(self, tmp_path):
        from preview_pipeline.format_handlers import fbx_scene

        scene = one_cube_placed(2)
        scene["connections"] += [(100, 101), (101, 100)]
        assert fbx_scene.scene_size(_write(tmp_path / "c.fbx", ascii_fbx(**scene)), 1_000).models == 1_001

    @pytest.mark.parametrize("edit", [
        lambda t: t.replace('\n\tC: "OO"', ' C: "OO"'),
        lambda t: t.replace('\n\tC: "OO"', '\n\t; a note\n\tC: "OO"'),
        lambda t: t.replace('\n\tC: "OO"', '\n\tNote: 1, "x"\n\tC: "OO"'),
        lambda t: t.replace('\n\tC: "OO",10,', '\n\tC: "OO",10,\n'),
        lambda t: t.replace("\tModel: 10", "\tNote: 1, 2 Model: 10"),
        lambda t: t.replace("\t\tVersion: 232\n", '\t\tVersion: 232\n\t\tX: "}", {\n\t\t\tY: { }\n\t\t}\n'),
    ], ids=["connections-on-one-line", "comments-between-connections", "other-elements-between-connections",
            "connection-split-over-lines", "other-element-runs-into-a-model", "nested-scopes-in-a-model"])
    def test_the_ascii_layout_does_not_change_the_count(self, tmp_path, edit):
        from preview_pipeline.format_handlers import fbx_scene

        text = edit(ascii_fbx(**one_cube_placed(3)))
        assert text != ascii_fbx(**one_cube_placed(3))
        assert tuple(fbx_scene.scene_size(_write(tmp_path / "laid-out.fbx", text), 100)) == (3, 72, 36, 24, 12)

    def test_the_ascii_scan_needs_no_line_breaks_and_skips_comments_inside_arrays(self, tmp_path):
        from preview_pipeline.format_handlers import fbx_scene

        # Assimp would read the whole one-line file as a comment if it started with one.
        one_line = " ".join(ascii_fbx(**one_cube_placed(3)).split("\n", 1)[1].split())
        assert tuple(fbx_scene.scene_size(_write(tmp_path / "a.fbx", one_line), 100))[:3] == (3, 72, 36)
        # The '*N' header understates the array, and a comment inside it holds a '}'.
        text = ascii_fbx(**one_cube_placed(3)).replace("PolygonVertexIndex: *24 {\n\t\t\ta: 0,",
                                                       "PolygonVertexIndex: *1 {\n\t\t\ta: 0, ; }\n")
        assert tuple(fbx_scene.scene_size(_write(tmp_path / "b.fbx", text), 100))[:3] == (3, 72, 36)

    def test_geometry_or_models_outside_the_scene_place_nothing(self, tmp_path):
        from preview_pipeline.format_handlers import fbx_scene

        scene = one_cube_placed(2)
        scene["connections"] = [(10, 100), (10, 101), (100, 999)]  # no model under the root
        size = fbx_scene.scene_size(_write(tmp_path / "u.fbx", ascii_fbx(**scene)), 100)
        assert tuple(size) == (0, 0, 0, 24, 12)

    def test_a_truncated_or_inflating_binary_file_is_refused(self, tmp_path):
        from preview_pipeline.format_handlers import fbx_scene

        data = binary_fbx(**one_cube_placed(2))
        with pytest.raises(ValueError, match="FBX binary"):
            fbx_scene.scene_size(_write(tmp_path / "t.fbx", data[:200]), 100)
        bomb = binary_fbx(**one_cube_placed(2), compress=True)
        declared = struct.pack("<I", 24)  # PolygonVertexIndex length 24 -> claim 1
        start = bomb.index(b"PolygonVertexIndex") + len(b"PolygonVertexIndex") + 1
        assert bomb[start:start + 4] == declared
        bomb = bomb[:start] + struct.pack("<I", 1) + bomb[start + 4:]
        with pytest.raises(ValueError, match="inflates past its declared length"):
            fbx_scene.scene_size(_write(tmp_path / "z.fbx", bomb), 100)

    def test_an_empty_file_places_nothing(self, tmp_path):
        from preview_pipeline.format_handlers import fbx_scene

        assert tuple(fbx_scene.scene_size(_write(tmp_path / "e.fbx", b""), 100)) == (0, 0, 0, 0, 0)

    @pytest.mark.parametrize("encoding", ["ascii", "binary", "binary-7500"])
    def test_a_file_nesting_past_the_bound_is_refused(self, tmp_path, encoding):
        """At and just below the bound the file is counted; one level over is refused, naming the
        bound. ASCII scopes and binary node records alike."""
        from preview_pipeline.format_handlers import fbx_scene

        limit = fbx_scene.MAX_FBX_NESTING_DEPTH
        for depth in (limit - 1, limit):
            path = _write(tmp_path / f"ok-{depth}.fbx", _nested(depth, encoding))
            fbx_scene.scene_size(path, 1_000_000)  # counted, not refused
        over = _write(tmp_path / "over.fbx", _nested(limit + 1, encoding))
        with pytest.raises(ValueError, match=f"nests deeper than {limit} levels"):
            fbx_scene.scene_size(over, 1_000_000)

    def test_a_deeply_nested_file_is_refused_without_python_recursion(self, tmp_path):
        """Tens of thousands of levels are read iteratively: the scan refuses the file with the
        bound message rather than raising RecursionError or hanging."""
        from preview_pipeline.format_handlers import fbx_scene

        limit = fbx_scene.MAX_FBX_NESTING_DEPTH
        path = _write(tmp_path / "deep.fbx", nested_ascii(30_000))
        with pytest.raises(ValueError, match=f"nests deeper than {limit} levels"):
            fbx_scene.scene_size(path, 1_000_000)

    def test_quoted_and_comment_braces_do_not_count_toward_depth(self, tmp_path):
        """Braces inside a quoted string or a ``;`` comment are not scope openings, so a shallow
        file carrying many of them is counted normally, not refused."""
        from preview_pipeline.format_handlers import fbx_scene

        braces = "{" * (fbx_scene.MAX_FBX_NESTING_DEPTH * 4)
        comment_and_string = "; " + braces + '\nObjects:  {\n\tNote: "' + braces + '"'
        text = ascii_fbx(**one_cube_placed(1)).replace("Objects:  {", comment_and_string, 1)
        size = fbx_scene.scene_size(_write(tmp_path / "braces.fbx", text), 1_000_000)
        assert tuple(size)[:3] == (1, 24, 12)
