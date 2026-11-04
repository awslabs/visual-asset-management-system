# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Report what is in the scene: dimensions, counts, UVs, materials, and whether each mesh is closed.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_inspect.py [-- --json out.json]

The closedness and body counts are what the pipeline's print gate will apply later, so the agent can see
the verdict before it exports rather than after the run. A mesh with boundary edges is not watertight; a
mesh with edges shared by more than two faces is not manifold; and a mesh in several connected pieces is
several parts however closed each one is -- ``printable`` is true only for ONE mesh, in ONE body, closed.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmesh  # noqa: E402
import bpy  # noqa: E402

import bl_common  # noqa: E402


def topology(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary = sum(1 for e in bm.edges if e.is_boundary)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    loose_verts = sum(1 for v in bm.verts if not v.link_edges)
    bm.free()
    return {"boundaryEdges": boundary, "nonManifoldEdges": non_manifold, "looseVertices": loose_verts,
            "watertight": boundary == 0 and non_manifold == 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    args = parser.parse_args(bl_common.args_after_double_dash())

    report = bl_common.mesh_report()
    total_bodies = 0
    for entry, obj in zip(report["objects"], bl_common.mesh_objects()):
        entry.update(topology(obj))
        # Closed is not the same as one piece: a closed shell that touches nothing is a loose part.
        bodies = bl_common.connected_bodies(obj)
        total_bodies += len(bodies)
        entry["bodies"] = len(bodies)
        entry["oneBody"] = len(bodies) == 1
        if len(bodies) > 1:
            entry["detachedBodies"] = bodies[1:6]
        entry["hasImageTexture"] = any(
            m and m.use_nodes and any(n.type == "TEX_IMAGE" and n.image for n in m.node_tree.nodes)
            for m in obj.data.materials)
    report["bodies"] = total_bodies
    report["printable"] = (report["meshCount"] == 1 and total_bodies == 1
                           and all(e["watertight"] for e in report["objects"]))
    print(json.dumps(report, indent=2))
    if args.json:
        bl_common.write_json(args.json, report)


main()
