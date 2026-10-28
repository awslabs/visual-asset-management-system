# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render the scene from fixed named views so the agent can compare it against the references.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_render.py -- \
        --out renders/v003 [--views front,left,back,right,top,threequarter] [--size 768] \
        [--engine CYCLES|BLENDER_WORKBENCH|BLENDER_EEVEE_NEXT] [--samples 32]

Writes ``<out>_<view>.png`` per view and ``<out>_report.json`` with the mesh report. The views are the
same fixed orthographic angles every time, so a v003 front render lines up with a v002 front render and
with the pipeline's own silhouette checks; the agent should compare like with like.
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import bl_common  # noqa: E402
from view_fill import VIEWS  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="output prefix, e.g. renders/v003")
    parser.add_argument("--views", default="front,left,back,right,threequarter")
    parser.add_argument("--size", type=int, default=768)
    parser.add_argument("--engine", default=os.environ.get("AGENT_RENDER_ENGINE", "CYCLES"))
    parser.add_argument("--samples", type=int, default=int(os.environ.get("AGENT_RENDER_SAMPLES", "32")))
    args = parser.parse_args(bl_common.args_after_double_dash())

    bl_common.require_inside_workspace(args.out, "render")
    report_path = bl_common.require_inside_workspace(f"{args.out}_report.json", "write")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    camera = bl_common.ensure_camera_and_light()
    engine = bl_common.configure_render(args.engine, args.samples, args.size)
    written = []
    for name in [v.strip() for v in args.views.split(",") if v.strip()]:
        if name not in VIEWS:
            raise SystemExit(f"Unknown view '{name}'; choose from {', '.join(VIEWS)}")
        az, el = VIEWS[name]
        bl_common.frame_camera(camera, az, el)
        path = bl_common.require_inside_workspace(f"{args.out}_{name}.png", "render")
        # Absolute: Blender resolves a bare relative render path against its own idea of the current
        # directory, which is not always the workspace, and then reports success for a file written
        # somewhere else. Check the file too; the operator does not fail when the write does.
        bpy.context.scene.render.filepath = os.path.abspath(path)
        bpy.ops.render.render(write_still=True)
        if not os.path.isfile(path):
            raise SystemExit(f"Render reported success but {path} was not written")
        written.append(path)
        print(f"rendered {name:12} -> {path}")

    report = bl_common.mesh_report()
    report["engine"] = engine
    report["renders"] = written
    # The colour of what was rendered, and of each reference cutout, as a hex value and a word. A render
    # the agent describes as "warm ginger" while the tool says blue is caught here, in numbers.
    report["colours"] = {os.path.basename(path).rsplit("_", 1)[-1][:-4]: bl_common.image_colour_summary(path)
                         for path in written}
    references = sorted(glob.glob(os.path.join(bl_common.WORKSPACE, "refs", "*_cutout.*")))
    report["referenceColours"] = {os.path.basename(path): bl_common.image_colour_summary(path)
                                  for path in references}
    bl_common.write_json(report_path, report)
    print(f"report -> {report_path}")
    print(f"scene: {report.get('meshCount')} meshes, dimensions {report.get('sceneDimensions')}")
    for view, summary in report["colours"].items():
        print(f"render {view:12} mean colour {summary['meanHex']} ({summary['colour']}), "
              f"{summary['coverage']:.0%} of the frame")
    for name, summary in report["referenceColours"].items():
        print(f"reference {name}: mean colour {summary['meanHex']} ({summary['colour']})")
    render_words = {s["colour"] for s in report["colours"].values()}
    reference_words = {s["colour"] for s in report["referenceColours"].values()}
    if reference_words and not (render_words & reference_words):
        print(f"COLOUR MISMATCH: the renders read as {', '.join(sorted(render_words))} and the references as "
              f"{', '.join(sorted(reference_words))}. Whatever the material was meant to be, this is what it is.")


main()
