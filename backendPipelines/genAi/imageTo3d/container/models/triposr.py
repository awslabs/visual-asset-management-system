# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""VAST-AI TripoSR adapter.

TripoSR is a feed-forward single-image reconstructor: one transformer pass produces a triplane NeRF
that is marching-cubed into a mesh, so a run is sub-second on an A100 and fits in 6-8 GB of GPU
memory. It is the fastest of the models and the cheapest to run, at the cost of a coarser
surface and a baked-from-one-view texture.
"""

import os
from pathlib import Path

from vams_utils.logging import log

from .base import (
    GenerationResult,
    ModelAdapter,
    collect_sidecars,
    find_generated_mesh,
    run_upstream,
    setting,
)

logger = log.get_logger()


class TripoSRAdapter(ModelAdapter):
    KEY = "triposr"
    DISPLAY_NAME = "VAST-AI TripoSR"
    LICENSE = "MIT"
    MIN_VRAM_GB = 8
    HF_REPOS = ("stabilityai/TripoSR",)
    REPO_DIR = "/opt/triposr"

    # TripoSR emits an OBJ by default and a GLB when asked for one; GLB keeps the mesh and its
    # baked texture in a single container, so it is preferred when the run bakes a texture.
    OBJ_FORMAT = "obj"
    GLB_FORMAT = "glb"

    # Upstream applies its framing and its mid-grey composite inside the background-removal branch
    # that --no-remove-bg skips, and offers no flag to keep a supplied mask while still framing, so
    # the pipeline frames the subject itself to the values upstream would have used.
    FRAMING_RATIO = 0.85
    FRAMING_BACKGROUND = "gray"

    def generate(self, image_path: str, work_dir: str, params: dict,
                 extra_images=(), source_images=(), starting_mesh=None) -> GenerationResult:
        out_dir = self.output_dir(work_dir)
        # Upstream writes each input's results to a per-image subdirectory indexed from zero and
        # exports straight into it, without creating it first, so a single-image run needs "0" to
        # exist or trimesh's export raises FileNotFoundError after the mesh has been extracted.
        Path(out_dir, "0").mkdir(parents=True, exist_ok=True)

        # The background is already removed by preprocess.py when BACKGROUND_REMOVAL is on, so
        # TripoSR's own rembg pass is skipped to avoid segmenting an alpha-composited image twice.
        skip_background_removal = str(
            setting(params, "BACKGROUND_REMOVAL", "rembg")
        ).lower() != "none"
        # The TRIPOSR_* keys are this model's own block in config.json, so a triposr run accepts them
        # and a run on another model does not. The defaults below match that block.
        mc_resolution = str(setting(params, "TRIPOSR_MC_RESOLUTION", "384"))
        bake_texture = str(setting(params, "TRIPOSR_BAKE_TEXTURE", "true")).lower() == "true"
        texture_resolution = str(setting(params, "TRIPOSR_TEXTURE_RESOLUTION", "2048"))
        # Always OBJ. On the baking path upstream exports through xatlas, which writes OBJ bytes
        # whatever extension the filename carries, so asking for a GLB there produces a file named
        # .glb that no glTF reader will open. The baked atlas arrives as a separate texture.png with
        # no material file referencing it, and the pipeline's own export stage is what assembles the
        # textured GLB.
        save_format = self.OBJ_FORMAT

        # Upstream entrypoint: TripoSR's run.py (VAST-AI-Research/TripoSR). Confirm the flag names
        # against the commit pinned by TRIPOSR_COMMIT in Dockerfile.triposr before deploying.
        # --foreground-ratio is deliberately absent: upstream reads it only in the branch that
        # --no-remove-bg skips, so passing it here would configure nothing. The pipeline's framing
        # stage applies the ratio instead.
        cmd = [
            "python", "-u", "run.py", image_path,
            "--output-dir", str(out_dir),
            "--model-save-format", save_format,
            "--mc-resolution", mc_resolution,
            "--device", "cuda:0",
        ]
        if skip_background_removal:
            cmd.append("--no-remove-bg")
        if bake_texture:
            cmd += ["--bake-texture", "--texture-resolution", texture_resolution]

        # The bake runs after the mesh is already extracted, so a fault there would otherwise lose a
        # completed reconstruction. It needs an offscreen GL context to rasterize the UV atlas, which
        # the image backs with EGL and a virtual framebuffer, and a host whose driver refuses one
        # still fails here. Retrying without the bake keeps the geometry and gives up only the baked
        # atlas, which the texture stage can still recover from the model's vertex colours.
        try:
            run_upstream(
                cmd,
                cwd=self.REPO_DIR,
                hf_home=os.environ["HF_HOME"],
                extra_env={"TRIPOSR_OUTPUT_DIR": str(out_dir)},
                timeout=1800,
            )
        except RuntimeError:
            if not bake_texture:
                raise
            logger.warning(
                "TripoSR failed with the texture bake enabled; retrying without it so the run "
                "yields geometry. The mesh reached this point, so the fault is in the bake itself: "
                "check the upstream traceback for a refused GL context before assuming the model."
            )
            bake_texture = False
            retry = [c for c in cmd if c not in ("--bake-texture", texture_resolution)]
            retry = [c for c in retry if c != "--texture-resolution"]
            run_upstream(
                retry,
                cwd=self.REPO_DIR,
                hf_home=os.environ["HF_HOME"],
                extra_env={"TRIPOSR_OUTPUT_DIR": str(out_dir)},
                timeout=1800,
            )

        preferred = (".obj", ".glb", ".ply")
        mesh_path = find_generated_mesh(str(out_dir), preferred)
        textures = collect_sidecars(mesh_path)

        notes = self.base_notes()
        notes.update({
            "triposrMarchingCubesResolution": mc_resolution,
            "triposrBakedTexture": str(bake_texture).lower(),
            "triposrSaveFormat": save_format,
            "generatedMesh": Path(mesh_path).name,
        })
        logger.info(f"TripoSR generated {mesh_path} with {len(textures)} texture sidecars")
        return GenerationResult(mesh_path=mesh_path, texture_paths=textures, notes=notes)
