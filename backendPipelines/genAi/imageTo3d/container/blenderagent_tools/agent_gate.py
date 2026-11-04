# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The pipeline's OWN print gate, shape descriptors and silhouette match, run on a model in the workspace.

    python tools/agent_gate.py --mesh output/model.glb --reference refs/<front>_cutout.png [--json renders/gate.json]

Run it with the pipeline's Python (the one on PATH in the container), not Blender's. It imports the very
modules the pipeline applies after the agent hands over -- ``meshcleanup.evaluate_gate``,
``export.shape_descriptors``, ``texturing``'s silhouette search -- so the numbers it prints are the numbers
the run will be judged by, not a proxy for them. Six builds in a row scored themselves 7 for silhouette and
proportions where a blind judge gave 3 and 2; this is the instrument that was missing.

What it reports, and what to do about each:

- **Gate**: passed or not, bodies, watertight, non-manifold counts. Anything but one closed body fails.
- **Shape**: fill ratio, height/width, genus -- and the SAME numbers for the reference photograph where they
  can be read off it, so a figure 4.8 times taller than wide beside a photograph that is 3.9 is visible.
- **Silhouette IoU**: the model's front outline against the reference cutout's, both cropped and scaled
  to the same height. 1.0 is identical. Below 0.75 the outline is wrong; above 0.9 it is right.
- **Bands**: the outline compared in ten horizontal bands from top to bottom -- where the model is too
  narrow or too wide, by how much. This is the list of edits.
- **Facing**: whether the front view (-Y, which the pipeline sees from azimuth 180) is where the model's
  outline matches the photograph. If another side matches better, either the model faces the wrong way or
  the photograph is not a front view; ``matchIoU`` is the better of the two and is the number to finish on.
- **Colour** per band, model beside reference, as words.
- **Colour noise**: how far neighbouring vertices' colours step. A reconstruction's own colour steps 4 to 5;
  a surface stepping 10 or more is what a blind judge calls speckled or patchy and marks down. Beside it,
  the share of isolated speckles -- vertices far from their neighbours -- which the mean step barely sees
  and a judge calls pitted from about half a percent.
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.environ.get("PIPELINE_CODE_DIR", "/opt/ml/code"))

BANDS = 10
BAND_TOLERANCE = 0.15
# The pipeline's azimuth 0 looks ALONG -Y, from the +Y side: at the back of a model whose front faces -Y,
# which is the agent's convention. The photograph is therefore matched at azimuth 180.
FRONT_AZIMUTH = 180.0


def name_colour(rgb):
    import colorsys

    r, g, b = (max(0.0, min(1.0, float(c))) for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < 0.12:
        return "black"
    if s < 0.12:
        return "white" if v > 0.85 else "grey"
    if 0.03 <= h <= 0.13 and v < 0.55:
        return "brown"
    for start, label in ((0.95, "red"), (0.83, "pink"), (0.72, "purple"), (0.53, "blue"), (0.45, "cyan"),
                         (0.19, "green"), (0.11, "yellow"), (0.04, "orange")):
        if h >= start:
            return label
    return "red"


def hexcode(rgb):
    return "#%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, float(c))) * 255)) for c in rgb)


def keep_vertex_colour(geometry):
    """A vertex colour beside a material with no image is the geometry's colour. trimesh parks it in the
    texture visual's attributes, where merging the scene drops it, so it becomes the visual first."""
    import trimesh

    visual = getattr(geometry, "visual", None)
    if getattr(visual, "kind", None) != "texture" or getattr(visual, "uv", None) is not None:
        return
    colour = getattr(visual, "vertex_attributes", {}).get("color")
    if colour is not None and len(colour) == len(geometry.vertices):
        geometry.visual = trimesh.visual.ColorVisuals(mesh=geometry, vertex_colors=colour)


def load_mesh(path):
    import trimesh

    import export

    scene = trimesh.load(path, force="scene")
    for geometry in getattr(scene, "geometry", {}).values():
        keep_vertex_colour(geometry)
    mesh = scene.to_geometry() if hasattr(scene, "to_geometry") else scene
    if not isinstance(mesh, trimesh.Trimesh):
        raise SystemExit(f"{path} holds no mesh")
    # glTF is Y-up; the pipeline turns the agent's export into its Z-up space before measuring anything.
    return export.normalize_up_axis(mesh, "y")


def band_widths(mask, bands=BANDS):
    """Mean filled width of each horizontal band of the subject, top to bottom, as fractions of the canvas."""
    import numpy as np

    rows = np.where(mask.any(axis=1))[0]
    if not len(rows):
        return [0.0] * bands
    edges = np.linspace(rows.min(), rows.max() + 1, bands + 1).astype(int)
    return [float(mask[a:b].sum(axis=1).mean() / mask.shape[1]) if b > a else 0.0
            for a, b in zip(edges[:-1], edges[1:])]


def mask_aspect(mask):
    import numpy as np

    rows, cols = np.where(mask)
    if not len(rows):
        return None, None
    height = rows.max() - rows.min() + 1
    width = cols.max() - cols.min() + 1
    return round(height / max(width, 1), 3), round(float(mask.sum() / (height * width)), 4)


def band_colours(colours, heights, bands=BANDS):
    """Mean colour per band from top to bottom, given per-sample colours (0..1) and heights."""
    import numpy as np

    if colours is None or len(colours) == 0:
        return [None] * bands
    lo, hi = float(heights.min()), float(heights.max())
    edges = np.linspace(hi, lo, bands + 1)  # top to bottom, to match the mask bands
    out = []
    for top, bottom in zip(edges[:-1], edges[1:]):
        picked = colours[(heights <= top) & (heights >= bottom)]
        out.append(None if len(picked) == 0 else picked.mean(axis=0))
    return out


def model_colours(mesh):
    """One colour per vertex in 0..1, from the texture or the vertex colours, or None.

    An atlas is read the way the pipeline delivers it -- a texel and a half inside each of a vertex's faces,
    averaged, samples on the cleared background left out -- so the numbers here describe the surface that
    ships, not the island borders the vertices sit on."""
    import numpy as np

    import texturing

    visual = getattr(mesh, "visual", None)
    kind = getattr(visual, "kind", None)
    try:
        if kind == "texture" and getattr(visual, "uv", None) is not None:
            if texturing.material_image(visual.material) is not None:
                sampled, _ = texturing.sample_atlas_inside_faces(mesh, np.asarray(visual.uv), visual.material)
                return np.asarray(sampled, dtype=float)[:, :3] / 255.0
        if kind == "vertex":
            return np.asarray(visual.vertex_colors, dtype=float)[:, :3] / 255.0
    except Exception as e:  # a colour read that fails must not end the measurement
        print(f"(could not read the model's colour: {e})")
    return None


def reference_colours(image, mask):
    import numpy as np

    rgb = np.asarray(image.convert("RGB"), dtype=float) / 255.0
    rows, cols = np.where(mask)
    return rgb[rows, cols], (mask.shape[0] - rows).astype(float)


NOISE_WORDS = (
    (7.0, "smooth", "a reconstruction's own colour steps 4 to 5 between neighbouring vertices"),
    (10.0, "grainy", "between 7 and 10; a blind judge begins to mark the surface down"),
    (math.inf, "speckled", "10 or more between neighbouring vertices: a blind judge marks the surface down as "
                           "speckled or patchy; the texture step that raised it is the cause, go back to the "
                           "version before it"),
)


def colour_noise(mesh, colours):
    """How far neighbouring vertices' colours step, on 0..255, and the word a blind judge's surface score
    tracks; None for a mesh without colour. Every refine that stepped 10 or more was marked down, every one
    under 7 was not, and a reconstruction's own colour steps 4 to 5."""
    import numpy as np
    import trimesh

    if colours is None:
        return None
    # Measured on the welded surface. An atlas export splits every vertex per island, so on the file as
    # exported a vertex's only neighbours are its own triangle's corners, which sampled the same island, and
    # the steps between islands -- what a viewer shows once the pipeline welds the positions -- never register.
    unique, inverse = trimesh.grouping.unique_rows(np.asarray(mesh.vertices))
    rgb = np.zeros((len(unique), 3))
    count = np.zeros(len(unique))
    np.add.at(rgb, inverse, np.asarray(colours, dtype=float)[:, :3] * 255.0)
    np.add.at(count, inverse, 1.0)
    rgb /= np.maximum(count, 1.0)[:, None]
    faces = inverse[np.asarray(mesh.faces, dtype=np.int64).reshape(-1, 3)]
    pairs = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    edges = np.unique(pairs[pairs[:, 0] != pairs[:, 1]], axis=0)
    step = np.abs(rgb[edges[:, 0]] - rgb[edges[:, 1]]).mean(axis=1) if len(edges) else np.zeros(0)
    mean = float(step.mean()) if len(step) else 0.0
    luminance = rgb @ np.array([0.299, 0.587, 0.114])
    word, advice = next((w, a) for limit, w, a in NOISE_WORDS if mean < limit)
    # An isolated speckle: a vertex far from the mean of its neighbours. The mean step barely registers a
    # sprinkling of dots over a smooth coat, and a blind judge calls them pitted from about half a percent.
    total = np.zeros_like(rgb)
    count = np.zeros(len(rgb))
    for a, b in ((edges[:, 0], edges[:, 1]), (edges[:, 1], edges[:, 0])):
        np.add.at(total, a, rgb[b])
        np.add.at(count, a, 1.0)
    neighbours = total / np.maximum(count, 1.0)[:, None]
    speckle = (count > 0) & (np.abs(rgb - neighbours).mean(axis=1) > 40)
    return {"meanStep": round(mean, 2), "speckledShare": float((step > 30).mean()) if len(step) else 0.0,
            "speckleVertexShare": float(speckle.mean()), "nearBlackShare": float((luminance < 20).mean()),
            "word": word, "advice": advice}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--reference", default="", help="the FRONT reference cutout")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    import numpy as np

    import meshcleanup
    import export
    import texturing

    mesh = load_mesh(args.mesh)
    report = {"mesh": args.mesh, "faces": int(len(mesh.faces)), "vertices": int(len(mesh.vertices))}

    gate = meshcleanup.evaluate_gate(mesh)
    report["gate"] = {"passed": bool(gate.passed), "bodies": int(gate.bodies), "isWatertight": bool(gate.is_watertight),
                      "nonManifoldEdges": int(gate.non_manifold_edges),
                      "nonManifoldVertices": int(gate.non_manifold_vertices), "failures": list(gate.failures)}
    print(f"GATE: {'PASS' if gate.passed else 'FAIL'} -- bodies {gate.bodies} (1 expected), "
          f"watertight {gate.is_watertight}, non-manifold edges {gate.non_manifold_edges}, "
          f"vertices {gate.non_manifold_vertices}")
    for failure in gate.failures:
        print(f"  gate: {failure}")

    shape = export.shape_descriptors(mesh)
    report["shape"] = shape
    print(f"SHAPE: height/width {shape.get('heightOverWidth')}, fill {shape.get('fillRatio')}, "
          f"footprint aspect {shape.get('footprintAspect')}, genus {shape.get('genus')}")

    status = texturing.generated_texture_status(mesh)
    report["texture"] = status
    print(f"TEXTURE: {status}")

    colours = model_colours(mesh)
    noise = colour_noise(mesh, colours)
    if noise is not None:
        report["colourNoise"] = noise
        dark = f"; {noise['nearBlackShare']:.0%} of the vertices are near-black" if noise["nearBlackShare"] > 0.02 else ""
        dots = (f"; isolated speckles on {noise['speckleVertexShare']:.1%} of the vertices (a reconstruction's own "
                f"colour: about 0.1%; a judge sees dots from about 0.5%)" if noise["speckleVertexShare"] > 0.003 else "")
        print(f"COLOUR NOISE: {noise['meanStep']:.1f} -- {noise['word']} ({noise['advice']}){dark}{dots}")

    if args.reference:
        from PIL import Image

        image = Image.open(args.reference)
        reference = texturing.prune_detached_debris(texturing.subject_mask(image))
        points = texturing.surface_points(mesh)
        front = texturing._normalised(texturing._silhouette(points, texturing._view_frame(FRONT_AZIMUTH, 0)))
        ref = texturing._normalised(reference)
        iou = texturing._mask_iou(front, ref)
        ref_aspect, ref_fill = mask_aspect(reference)
        model_aspect, model_fill = mask_aspect(front)
        pose = texturing.recover_photo_pose(mesh, reference, points=points)
        best_azimuth = pose.get("azimuth")
        # Facing the front if the front view is the best match or as good as it (a symmetric subject
        # matches its mirror image equally well, and the sweep may name that side first).
        best_iou = float(pose.get("iou") or 0.0)
        facing_front = (best_azimuth is None or iou >= best_iou - 0.02
                        or abs(((float(best_azimuth) - FRONT_AZIMUTH) + 180) % 360 - 180) <= 30)
        match_iou = max(iou, best_iou)
        report["silhouette"] = {"frontIoU": round(iou, 3), "bestIoU": pose.get("iou"), "matchIoU": round(match_iou, 3),
                                "bestAzimuth": best_azimuth, "bestElevation": pose.get("elevation"),
                                "facingFront": facing_front,
                                "referenceHeightOverWidth": ref_aspect, "modelHeightOverWidth": model_aspect,
                                "referenceFill": ref_fill, "modelFill": model_fill}
        verdict = "right" if match_iou >= 0.9 else "close" if match_iou >= 0.8 else "off" if match_iou >= 0.7 else "wrong"
        print(f"SILHOUETTE: match IoU {match_iou:.3f} ({verdict}) -- front view {iou:.3f}, best view "
              f"{pose.get('iou')} from azimuth {best_azimuth} elevation {pose.get('elevation')} "
              f"(the front is {FRONT_AZIMUTH:.0f})"
              + ("" if facing_front else
                 ". The photograph matches another side better than the front: either the model faces the wrong "
                 "way, or the photograph was not taken from the front -- a three-quarter photograph never "
                 "matches a front view, and then the best view is the one to judge by"))
        print(f"  height/width: model {model_aspect} vs photograph {ref_aspect}; fill: model {model_fill} vs "
              f"photograph {ref_fill}")

        model_bands = band_widths(front)
        ref_bands = band_widths(ref)
        bands = []
        for index, (m, r) in enumerate(zip(model_bands, ref_bands)):
            top, bottom = index * 100 // BANDS, (index + 1) * 100 // BANDS
            if r <= 0 and m <= 0:
                continue
            ratio = (m / r) if r > 0 else math.inf
            entry = {"band": f"{top}-{bottom}% from top", "modelWidth": round(m, 4), "referenceWidth": round(r, 4),
                     "ratio": None if ratio == math.inf else round(ratio, 3)}
            bands.append(entry)
            if ratio == math.inf:
                print(f"  band {top:2d}-{bottom:3d}%: the model has material where the photograph has none")
            elif ratio < 1 - BAND_TOLERANCE:
                print(f"  band {top:2d}-{bottom:3d}%: model {1 - ratio:.0%} TOO NARROW")
            elif ratio > 1 + BAND_TOLERANCE:
                print(f"  band {top:2d}-{bottom:3d}%: model {ratio - 1:.0%} TOO WIDE")
        report["bands"] = bands
        if all(1 - BAND_TOLERANCE <= (b["ratio"] or 0) <= 1 + BAND_TOLERANCE for b in bands if b["ratio"]):
            print("  every band within 15% of the photograph")

        heights = np.asarray(mesh.vertices)[:, 2]
        ref_rgb, ref_heights = reference_colours(image, reference)
        model_band_colours = band_colours(colours, heights) if colours is not None else [None] * BANDS
        ref_band_colours = band_colours(ref_rgb, ref_heights)
        colour_rows = []
        for index, (m, r) in enumerate(zip(model_band_colours, ref_band_colours)):
            top, bottom = index * 100 // BANDS, (index + 1) * 100 // BANDS
            row = {"band": f"{top}-{bottom}% from top",
                   "model": None if m is None else {"hex": hexcode(m), "colour": name_colour(m)},
                   "reference": None if r is None else {"hex": hexcode(r), "colour": name_colour(r)}}
            colour_rows.append(row)
            if m is not None and r is not None:
                same = name_colour(m) == name_colour(r)
                print(f"  colour {top:2d}-{bottom:3d}%: model {hexcode(m)} ({name_colour(m)}) vs photograph "
                      f"{hexcode(r)} ({name_colour(r)}){'' if same else '  <- DIFFERENT'}")
        report["colours"] = colour_rows
        if colours is None:
            print("  colour: the model carries no texture or vertex colour")
        else:
            # The join between what the camera saw and what was inferred: colour of the camera-facing
            # surface against the colour of the rest, band by band. A projected texture whose back band
            # was a row MEAN showed a seam at the crown where dark hair met a mixed colour; this is the
            # number that sees it.
            normals = np.asarray(mesh.vertex_normals)
            facing = normals[:, 1] < -0.25
            away = normals[:, 1] > 0.25
            front_bands = band_colours(colours[facing], heights[facing])
            back_bands = band_colours(colours[away], heights[away])
            seams = []
            for index, (f, b) in enumerate(zip(front_bands, back_bands)):
                if f is None or b is None:
                    continue
                top, bottom = index * 100 // BANDS, (index + 1) * 100 // BANDS
                gap = float(np.abs(f - b).mean())
                entry = {"band": f"{top}-{bottom}% from top", "front": hexcode(f), "back": hexcode(b),
                         "difference": round(gap, 3), "seam": bool(gap > 0.16 or name_colour(f) != name_colour(b))}
                seams.append(entry)
                if entry["seam"]:
                    print(f"  SEAM {top:2d}-{bottom:3d}%: camera-facing {hexcode(f)} ({name_colour(f)}) vs the rest "
                          f"{hexcode(b)} ({name_colour(b)}), difference {gap:.2f}")
            report["seams"] = seams
            if seams and not any(s["seam"] for s in seams):
                print("  no colour seam between the camera-facing surface and the rest in any band")

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"report -> {args.json}")


if __name__ == "__main__":
    main()
