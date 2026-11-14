"""Tests that combining GLBs keeps each input GLB's node hierarchy (glbassetcombine, bomassemble)."""

import json
import math
import struct

from vamscli.commands.industry.engineering.bom.Dynamic_BOM import (
    build_complete_export_from_bom,
    build_node_tree,
)
from vamscli.utils.glb_combiner import (
    build_transform_tree_from_export,
    merge_glb_meshes_into_tree,
    read_glb_file,
    write_combined_glb,
    write_glb_file,
)

IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]

# Root node of a CAD export: millimetres to metres and Z-up to Y-up, column-major.
CAD_ROOT_MATRIX = [0.001, 0, 0, 0, 0, 0, -0.001, 0, 0, 0.001, 0, 0, 0, 0, 0, 1]
QUARTER_TURN_Z = [0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)]


def _write_test_glb(path, mesh_sizes, nodes=None, roots=None, extra=None):
    """Write a GLB whose meshes are axis-aligned point boxes from the origin to each size."""
    binary = b''
    accessors, views, meshes = [], [], []
    for index, size in enumerate(mesh_sizes):
        corners = [(x, y, z) for x in (0, size[0]) for y in (0, size[1]) for z in (0, size[2])]
        data = b''.join(struct.pack('<3f', *corner) for corner in corners)
        views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(data)})
        accessors.append({'bufferView': index, 'componentType': 5126, 'count': len(corners),
                          'type': 'VEC3', 'min': [0, 0, 0], 'max': list(size)})
        meshes.append({'name': f'mesh{index}',
                       'primitives': [{'attributes': {'POSITION': index}, 'mode': 0}]})
        binary += data
    gltf = {'asset': {'version': '2.0'}, 'meshes': meshes, 'accessors': accessors,
            'bufferViews': views, 'buffers': [{'byteLength': len(binary)}]}
    if nodes is not None:
        gltf['nodes'] = nodes
    if roots is not None:
        gltf['scene'] = 0
        gltf['scenes'] = [{'nodes': roots}]
    gltf.update(extra or {})
    write_glb_file(str(path), gltf, binary)
    return str(path)


def _write_cad_assembly_glb(path):
    """A CAD-style assembly: scaled and rotated root, nested sub-assembly, one mesh placed twice."""
    nodes = [
        {'name': 'Product', 'matrix': CAD_ROOT_MATRIX, 'children': [1, 4]},
        {'name': 'SubAssembly', 'translation': [1000, 0, 0], 'rotation': QUARTER_TURN_Z,
         'children': [2, 3]},
        {'name': 'Bolt.1', 'mesh': 0, 'extras': {'partNumber': 'B-10'}},
        {'name': 'Bolt.2', 'translation': [0, 500, 0], 'mesh': 0},
        {'name': 'Plate', 'scale': [2, 1, 1], 'mesh': 1, 'camera': 0},
    ]
    cameras = [{'type': 'perspective', 'perspective': {'yfov': 1.0, 'znear': 0.1}}]
    return _write_test_glb(path, [(10, 20, 30), (100, 50, 5)], nodes, [0], {'cameras': cameras})


def _matmul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _column_major(values):
    return [[float(values[c * 4 + r]) for c in range(4)] for r in range(4)]


def _local_matrix(node):
    if 'matrix' in node:
        return _column_major(node['matrix'])
    t = node.get('translation', [0, 0, 0])
    x, y, z, w = node.get('rotation', [0, 0, 0, 1])
    s = node.get('scale', [1, 1, 1])
    rot = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
           [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
           [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    rows = [[rot[r][0] * s[0], rot[r][1] * s[1], rot[r][2] * s[2], t[r]] for r in range(3)]
    return rows + [[0, 0, 0, 1]]


def _apply(matrix, point):
    return tuple(sum(matrix[r][c] * v for c, v in enumerate((*point, 1.0))) for r in range(3))


def _scene_summary(path):
    """World-space vertices, mesh placements and mesh count of a GLB's default scene."""
    data = read_glb_file(path)
    gltf, binary = data['json'], data['binary']
    points, placements = [], []

    def visit(index, parent):
        node = gltf['nodes'][index]
        world = _matmul(parent, _local_matrix(node))
        if 'mesh' in node:
            placements.append(node.get('name'))
            for primitive in gltf['meshes'][node['mesh']]['primitives']:
                accessor = gltf['accessors'][primitive['attributes']['POSITION']]
                view = gltf['bufferViews'][accessor['bufferView']]
                offset = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
                for k in range(accessor['count']):
                    vertex = struct.unpack_from('<3f', binary, offset + 12 * k)
                    points.append(_apply(world, vertex))
        for child in node.get('children', []):
            visit(child, world)

    for root in gltf['scenes'][gltf.get('scene', 0)]['nodes']:
        visit(root, IDENTITY)
    return {'gltf': gltf, 'points': points, 'placements': placements,
            'meshes': len(gltf['meshes'])}


def _bounds(points):
    return ([round(min(p[i] for p in points), 6) for i in range(3)],
            [round(max(p[i] for p in points), 6) for i in range(3)])


def _assert_references_valid(gltf):
    """Every index points at an existing entry and every node has at most one parent."""
    nodes = gltf['nodes']
    parents = {}
    for index, node in enumerate(nodes):
        if 'mesh' in node:
            assert 0 <= node['mesh'] < len(gltf['meshes'])
        for key, array in (('camera', 'cameras'), ('skin', 'skins')):
            if key in node:
                assert node[key] < len(gltf.get(array, []))
        for child in node.get('children', []):
            assert 0 <= child < len(nodes)
            assert child not in parents, f'node {child} has two parents'
            parents[child] = index
    for root in gltf['scenes'][gltf.get('scene', 0)]['nodes']:
        assert root not in parents


def _combine(export, glb_dir, out_path):
    tree = build_transform_tree_from_export(export)
    gltf, binary = merge_glb_meshes_into_tree(tree, str(glb_dir))
    write_combined_glb(str(out_path), gltf, binary)
    return _scene_summary(str(out_path))


def _link_export(links):
    """An export with a file-less root asset and one parentChild link per (asset, files, metadata)."""
    assets = [{'assetid': 'root', 'assetname': 'Root', 'is_root_lookup_asset': True, 'files': []}]
    relationships = []
    seen = set()
    for index, (asset_id, files, metadata) in enumerate(links):
        if asset_id not in seen:
            seen.add(asset_id)
            assets.append({'assetid': asset_id, 'assetname': asset_id,
                           'files': [{'fileName': f, 'key': f} for f in files]})
        relationships.append({'parentAssetId': 'root', 'childAssetId': asset_id,
                              'assetLinkAliasId': str(index + 1), 'metadata': metadata})
    return {'assets': assets, 'relationships': relationships}


class TestGlbHierarchyKept:
    """glbassetcombine places each input GLB's own node hierarchy under its asset node."""

    def test_input_node_transforms_and_instances_are_kept(self, tmp_path):
        source = _scene_summary(_write_cad_assembly_glb(tmp_path / 'part.glb'))
        # In metres, Y-up: the plate spans x 0..0.2, the rotated bolts reach x 1.0 and y 0.03.
        assert _bounds(source['points']) == ([0.0, 0.0, -0.05], [1.0, 0.03, 0.0])
        export = _link_export([('part', ['part.glb'], {})])

        result = _combine(export, tmp_path, tmp_path / 'out.glb')

        assert _bounds(result['points']) == _bounds(source['points'])
        assert sorted(result['placements']) == ['Bolt.1', 'Bolt.2', 'Plate']
        assert result['meshes'] == source['meshes'] == 2
        _assert_references_valid(result['gltf'])
        nodes = {node.get('name'): node for node in result['gltf']['nodes']}
        assert nodes['Bolt.1']['extras'] == {'partNumber': 'B-10'}
        assert nodes['Bolt.1']['mesh'] == nodes['Bolt.2']['mesh']
        assert 'camera' not in nodes['Plate']

    def test_link_transform_applies_on_top_of_input_hierarchy(self, tmp_path):
        source = _scene_summary(_write_cad_assembly_glb(tmp_path / 'part.glb'))
        # Quarter turn about Y, then 5 m along X, as a row-major 2D Matrix link value.
        link_rows = [[0, 0, 1, 0], [0, 1, 0, 0], [-1, 0, 0, 0], [5, 0, 0, 1]]
        metadata = {'Matrix': {'value': json.dumps(link_rows), 'valueType': 'matrix4x4'}}
        export = _link_export([('part', ['part.glb'], metadata)])

        result = _combine(export, tmp_path, tmp_path / 'out.glb')

        link = [[0, 0, -1, 5], [0, 1, 0, 0], [1, 0, 0, 0], [0, 0, 0, 1]]
        expected = [_apply(link, p) for p in source['points']]
        assert _bounds(result['points']) == _bounds(expected)
        assert len(result['placements']) == 3

    def test_each_glb_of_an_asset_and_each_asset_instance_is_placed(self, tmp_path):
        source = _scene_summary(_write_cad_assembly_glb(tmp_path / 'part.glb'))
        _write_test_glb(tmp_path / 'loose.glb', [(1, 1, 1)])
        _write_test_glb(tmp_path / 'unscened.glb', [(2, 2, 2)],
                        nodes=[{'name': 'Holder', 'translation': [0, 0, 7], 'children': [1]},
                               {'name': 'Held', 'mesh': 0}])
        files = ['part.glb', 'loose.glb', 'unscened.glb']
        offset = {'Translation': {'value': '{"x": 10, "y": 0, "z": 0}'}}
        export = _link_export([('part', files, {}), ('part', files, offset)])

        result = _combine(export, tmp_path, tmp_path / 'out.glb')

        # Per instance: three placed parts, the node-less mesh, and the mesh of the scene-less GLB.
        assert len(result['placements']) == 10
        assert result['placements'].count('Held') == 2
        instance = source['points'] + [(0, 0, 0), (1, 1, 1)] + [(0, 0, 7), (2, 2, 9)]
        shifted = [(x + 10, y, z) for x, y, z in instance]
        assert _bounds(result['points']) == _bounds(instance + shifted)
        _assert_references_valid(result['gltf'])


class TestBomHierarchyKept:
    """bomassemble applies each BOM node transform on top of the source GLB's own hierarchy."""

    def test_bom_transforms_apply_on_top_of_input_hierarchy(self, tmp_path):
        glb_path = _write_cad_assembly_glb(tmp_path / 'part.glb')
        source = _scene_summary(glb_path)
        lift = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 10, 1]
        shift = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 3, 0, 0, 1]
        double = [2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1]
        sources = [{'source': 'assembly', 'storage': 'no'}, {'source': 'part', 'storage': 'VAMS'}]
        node_tree = build_node_tree([
            {'node': '1', 'source': 'assembly'},
            {'node': '2', 'source': 'part', 'parent_node': '1', 'matrix': lift},
            {'node': '3', 'source': 'part', 'parent_node': '1', 'matrix': shift},
            {'node': '4', 'source': 'part', 'parent_node': '2', 'matrix': double},
        ])
        export = build_complete_export_from_bom(node_tree, '1', {'part': glb_path}, sources)

        result = _combine(export, tmp_path, tmp_path / 'out.glb')

        lift_m, shift_m, double_m = (_column_major(m) for m in (lift, shift, double))
        placements = (lift_m, shift_m, _matmul(lift_m, double_m))
        expected = [_apply(m, p) for m in placements for p in source['points']]
        assert _bounds(result['points']) == _bounds(expected)
        assert len(result['placements']) == 9
        _assert_references_valid(result['gltf'])
