#!/usr/bin/env python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Replay the pipeline's post-generation chain offline, against a mesh a run already produced.

Everything after generation is CPU work on geometry -- decimation, the repair ladder, the print gate,
texture carry-over, print features, the shape descriptors -- and almost every change made to this
pipeline has been to one of those. Verifying one cost a container rebuild and an AWS Batch GPU job,
around fifteen minutes for a result that takes seconds to compute. This runs the same code, in the same
order, on a stored mesh.

It calls ``postprocess`` in the container's own ``__main__.py`` rather than reproducing the order, for
a reason worth stating: an earlier hand-written reproduction did NOT match the live run, because the
pipeline decimates the TEXTURED mesh and welds its uv seams first, while the reproduction had loaded an
STL with no uv field at all. A replay that quietly diverges from the pipeline is worse than no replay.

Two limits are honest and worth knowing:

*   **Generation is not replayed.** The stored mesh is the input, so this cannot tell you anything about
    a model's own output -- only about what the pipeline does to it afterwards.
*   **The rungs are the ones installed HERE.** pymeshfix and pymeshlab are GPL-3.0 and present in the
    container image; a local environment without them walks a shorter ladder and will not match. The
    ladder actually walked is printed, so a mismatch is visible rather than silent.

Usage:

    # What the pipeline would do to a stored mesh, with the settings a run used
    python replay.py --mesh raw-stable3dgen/mug.glb --settings baseline-stable3dgen.json

    # Compare two settings without touching a GPU: does a deeper face budget still pass?
    python replay.py --mesh raw-triposr/mug.glb --set TARGET_FACE_COUNT=200000
    python replay.py --mesh raw-triposr/mug.glb --set TARGET_FACE_COUNT=150000

    # Regression: assert the gate verdict has not moved for a stored mesh
    python replay.py --mesh raw-stable3dgen/mug.glb --expect expected/stable3dgen.json

    # Colour a geometry-only mesh from the photograph it was reconstructed from
    python replay.py --mesh raw-stable3dgen/mug.glb --up-axis y --photo inputs/mug.png
        --set PROJECT_PHOTO_COLOUR=auto --write-mesh projected/
"""

import argparse
import importlib.util
import json
import os
import sys

CONTAINER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "container")


def load_container_main():
    """The container's entry module, loaded by path because its name is ``__main__.py``.

    Its directory goes on ``sys.path`` first so its sibling imports (export, meshcleanup, texturing,
    printfeatures) resolve to the container's copies rather than to anything of the same name here.
    """
    if CONTAINER not in sys.path:
        sys.path.insert(0, CONTAINER)
    spec = importlib.util.spec_from_file_location(
        "imageto3d_container_main", os.path.join(CONTAINER, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_settings(main_module, model, overrides=None, settings_file=None):
    """The full settings a run would resolve, so a replay is not missing a default the pipeline has.

    Built from ``config.json``'s own allow-list, which is the same source the container resolves from --
    a replay with a hand-written settings dict silently omits every default and measures something the
    pipeline never does.
    """
    allowed = main_module.load_allowed_settings()
    settings = dict(main_module.allowed_for_model(allowed, model))
    settings["MODEL"] = model
    if settings_file:
        with open(settings_file, encoding="utf-8") as handle:
            document = json.load(handle)
        # Accept either a flat settings map or the pipeline-parameters shape the CLI takes.
        for block in document.values() if "genai-image-to-3d" in document else [document]:
            if isinstance(block, dict) and "templateTags" in block:
                for tag in block["templateTags"]:
                    settings[tag["key"]] = str(tag["value"])
            elif isinstance(block, dict):
                settings.update({k: str(v) for k, v in block.items()})
    for override in overrides or []:
        key, _, value = override.partition("=")
        settings[key.strip()] = value.strip()
    return settings


def gate_summary(gate_report):
    """The gate verdict as a plain dict, or ``None`` when the cleanup did not run."""
    return None if gate_report is None else gate_report.as_dict()


def declared_input_view(model):
    """The azimuth that model's output frame puts the input photograph at, or None.

    Read from the adapter, because the live pipeline reads it from the adapter: a replay that hardcoded
    it would project colour from a pose the container does not use. Generation is not replayed, so the
    adapter is only being asked for a class attribute.
    """
    if CONTAINER not in sys.path:
        sys.path.insert(0, CONTAINER)
    try:
        import generate  # noqa: E402  - resolved from the container directory

        adapter = generate.MODEL_ADAPTERS.get(model)
        return getattr(adapter, "INPUT_VIEW_AZIMUTH", None) if adapter else None
    except Exception as e:
        print(f"warning: could not read {model}'s declared input view ({e}); the pose will be "
              f"recovered from the silhouette instead, which is not what the container does")
        return None


def replay(mesh_path, settings, main_module=None, up_axis=None, photo_path="", out_dir="",
           input_view_azimuth=None):
    """Run the post-generation chain over a stored mesh. Returns a summary dict."""
    import trimesh

    main_module = main_module or load_container_main()
    sys.path.insert(0, CONTAINER) if CONTAINER not in sys.path else None
    import export  # noqa: E402  - resolved from the container directory

    mesh = trimesh.load(mesh_path, force="mesh")
    loaded = {"faces": len(mesh.faces), "vertices": len(mesh.vertices)}
    if up_axis:
        # A raw mesh straight from a model is in that model's own space; a stored EXPORT has already
        # been normalised, so rotating it again would leave it on its side.
        mesh = export.normalize_up_axis(mesh, up_axis)
    elif os.path.splitext(mesh_path)[1].lower() in (".glb", ".gltf"):
        # glTF is Y-up by specification, so a GLB written from this pipeline's z-up space comes BACK
        # rotated -- including the pipeline's own exports. Everything measured from a mesh's extents
        # then describes the wrong axis: measured on the Stable3DGen mug, the proportion descriptor
        # read 1.32 against the 0.76 the live run recorded, and the colour projection sampled the
        # photograph's vertical gradient around the subject's circumference instead of down it.
        print(f"warning: {os.path.basename(mesh_path)} is a glTF container, which is Y-up, so this "
              f"replay is measuring a mesh lying on its side. Pass --up-axis y to bring it into the "
              f"z-up space the pipeline works in.")

    run_cleanup = str(settings.get("MESH_CLEANUP", "true")).strip().lower() != "false"
    mesh, gate_report, texture_status, feature_report, projection_report = main_module.postprocess(
        mesh, settings, run_cleanup=run_cleanup, photo_path=photo_path,
        input_view_azimuth=input_view_azimuth)

    if out_dir:
        # The point of replaying a colour stage is to LOOK at the result (requirement 6.4), which needs
        # the mesh written out rather than only measured.
        os.makedirs(out_dir, exist_ok=True)
        written = os.path.join(out_dir, os.path.basename(mesh_path))
        mesh.export(written)
        print(f"wrote {written}")

    return {
        "mesh": os.path.basename(mesh_path),
        "loaded": loaded,
        "shipped": {"faces": len(mesh.faces), "vertices": len(mesh.vertices)},
        "textureStatus": texture_status,
        "gate": gate_summary(gate_report),
        "printFeatures": feature_report or {},
        "projectedColour": projection_report or {},
        "shape": export.shape_descriptors(mesh),
    }


def compare(summary, expected):
    """Differences between a replay's gate verdict and a stored one. Empty means they agree."""
    differences = []
    got, want = summary.get("gate") or {}, expected.get("gate") or {}
    for key in sorted(set(got) | set(want)):
        if key in ("failures", "backendsApplied", "repairsRejected"):
            # Prose and provenance move with unrelated wording changes; the verdict is the contract.
            continue
        if got.get(key) != want.get(key):
            differences.append(f"gate.{key}: {want.get(key)!r} -> {got.get(key)!r}")
    for key in ("textureStatus",):
        if summary.get(key) != expected.get(key):
            differences.append(f"{key}: {expected.get(key)!r} -> {summary.get(key)!r}")
    return differences


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mesh", required=True, help="a mesh a run produced (glb keeps its texture)")
    parser.add_argument("--model", default="stable3dgen",
                        help="which model's settings block to resolve defaults from")
    parser.add_argument("--settings", help="a settings JSON, flat or in --pipeline-parameters shape")
    parser.add_argument("--set", action="append", dest="overrides", metavar="KEY=VALUE",
                        help="override one setting; repeatable")
    parser.add_argument("--up-axis", choices=("y", "z"),
                        help="normalise a RAW model mesh; omit for an already-exported one")
    parser.add_argument("--photo", default="",
                        help="the photograph the mesh was reconstructed from, for PROJECT_PHOTO_COLOUR")
    parser.add_argument("--write-mesh", default="", metavar="DIR",
                        help="also write the finished mesh here, so the result can be looked at")
    parser.add_argument("--expect", help="a stored summary JSON to compare the gate verdict against")
    parser.add_argument("--out", help="write the summary here")
    args = parser.parse_args(argv)

    main_module = load_container_main()
    settings = resolve_settings(main_module, args.model, args.overrides, args.settings)
    summary = replay(args.mesh, settings, main_module=main_module, up_axis=args.up_axis,
                     photo_path=args.photo, out_dir=args.write_mesh,
                     input_view_azimuth=declared_input_view(args.model))

    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)

    if args.expect:
        with open(args.expect, encoding="utf-8") as handle:
            expected = json.load(handle)
        differences = compare(summary, expected)
        if differences:
            print("\nthe gate verdict MOVED against " + args.expect)
            for difference in differences:
                print(f"  {difference}")
            return 1
        print("\nthe gate verdict matches " + args.expect)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
