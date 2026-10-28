# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stable-X Stable3DGen (Hi3DGen) adapter.

Hi3DGen reconstructs geometry by bridging through a predicted normal map: StableNormal turns the
photograph into a normal image, and the structured-latent pipeline generates the surface from that
rather than from the pixels directly. It is a fine-tune of the TRELLIS architecture that carries none
of the NVIDIA-licensed components its ancestor depends on -- FlexiCubes is replaced by the repo's own
marching cubes over scikit-image -- so the image builds from wheels alone.

**This model produces geometry only.** ``to_trimesh`` hands back vertices and faces; the pipeline
carries no uv field or texture, so a run yields an untextured mesh whatever the export formats ask
for. That suits the printing path, where the STL is the deliverable, and it is the wrong choice when
a textured glTF is what the run is for.
"""

import json
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

# Driver run inside the upstream repo. The weights are staged into the local-directory layout the
# upstream loaders expect -- ``Hi3DGenPipeline.from_pretrained`` takes a path, and StableNormal's
# torch.hub entry point resolves its checkpoint by version name under a cache directory -- rather than
# the HuggingFace hub cache layout. The download lands inside HF_HOME so the pipeline's existing EFS
# cache and its S3 backup cover it.
DRIVER_TEMPLATE = '''
import json
import os
import sys

from PIL import Image
import torch

# Upstream API: Stable-X/Stable3DGen app.py -- Hi3DGenPipeline.from_pretrained(path), a StableNormal
# predictor from torch.hub, then pipeline.run(normal_image, formats=["mesh"]) and
# outputs["mesh"][0].to_trimesh(). Confirm against the commit pinned by STABLE3DGEN_COMMIT in
# Dockerfile.stable3dgen before deploying.
from huggingface_hub import snapshot_download
from hi3dgen.pipelines import Hi3DGenPipeline

image_path = sys.argv[1]
output_dir = sys.argv[2]
settings = json.loads(sys.argv[3])

os.makedirs(output_dir, exist_ok=True)
weights_dir = settings["weights_dir"]
os.makedirs(weights_dir, exist_ok=True)

# Each repo is snapshotted to a directory named after the repo, which is the layout both loaders
# expect: the geometry pipeline is opened by path, and StableNormal looks its version up by name.
staged = {}
for repo_id in settings["hf_repos"]:
    local_path = os.path.join(weights_dir, repo_id.split("/")[-1])
    if not os.path.isdir(local_path):
        print(f"Staging {repo_id} -> {local_path}", flush=True)
        snapshot_download(repo_id=repo_id, local_dir=local_path)
    staged[repo_id] = local_path

pipeline = Hi3DGenPipeline.from_pretrained(staged[settings["geometry_repo"]])
pipeline.cuda()

# source='local' reads the checkout baked into the image's torch hub directory, so a run makes no
# call to GitHub for the predictor itself. The entry point takes only these arguments -- upstream's
# own demo also passes pretrained=True, which the current revision rejects -- and it resolves its
# checkpoint as {local_cache_dir}/{yoso_version}, which is where the snapshot above landed.
normal_predictor = torch.hub.load(
    os.path.join(torch.hub.get_dir(), settings["stablenormal_checkout"]),
    "StableNormal_turbo",
    yoso_version=settings["yoso_version"],
    source="local",
    local_cache_dir=weights_dir,
)

image = Image.open(image_path)
if image.mode != "RGBA":
    image = image.convert("RGBA")

# preprocess_image uses an alpha channel when the image carries one and only reaches for BiRefNet
# when it does not, so a cutout from the pipeline's own segmentation stage is respected here.
image = pipeline.preprocess_image(image, resolution=settings["preprocess_resolution"])
normal_image = normal_predictor(
    image,
    resolution=settings["normal_resolution"],
    match_input_resolution=True,
    data_type="object",
)
normal_image.save(os.path.join(output_dir, "normal.png"))

outputs = pipeline.run(
    normal_image,
    seed=settings["seed"],
    formats=["mesh"],
    preprocess_image=False,
    sparse_structure_sampler_params={
        "steps": settings["sparse_steps"],
        "cfg_strength": settings["sparse_cfg_strength"],
    },
    slat_sampler_params={
        "steps": settings["slat_steps"],
        "cfg_strength": settings["slat_cfg_strength"],
    },
)

mesh = outputs["mesh"][0].to_trimesh(transform_pose=True)
mesh_path = os.path.join(output_dir, "stable3dgen.glb")
mesh.export(mesh_path)
print(f"Wrote {mesh_path} with {len(mesh.vertices)} vertices and {len(mesh.faces)} faces")
'''


class Stable3DGenAdapter(ModelAdapter):
    KEY = "stable3dgen"
    DISPLAY_NAME = "Stable-X Stable3DGen (Hi3DGen)"
    LICENSE = "MIT"
    MIN_VRAM_GB = 16
    # The geometry pipeline, the StableNormal checkpoint its predictor resolves by name, and the
    # background-removal model upstream falls back to when an image arrives without an alpha channel.
    HF_REPOS = (
        "Stable-X/trellis-normal-v0-1",
        "Stable-X/yoso-normal-v1-8-1",
        "ZhengPeng7/BiRefNet",
    )
    REPO_DIR = "/opt/stable3dgen"

    # Upstream's preprocess_image consumes the alpha channel directly and frames the subject itself,
    # so the cutout reaches it untouched. Flattening it here would remove the mask it reads.
    FRAMING_BACKGROUND = "none"

    # ``to_trimesh(transform_pose=True)`` applies upstream's own transform on the way out, which lands
    # the mesh in a Y-up space rather than the Z-up the other models write.
    OUTPUT_UP_AXIS = "y"

    # Measured on two subjects. On the mug, whose handle makes its outline decisive, the silhouette
    # search recovers azimuth 0 at IoU 0.868 with a margin of 0.120 -- and the colour projected from it
    # is visibly right. On the standing figure the search has no decisive answer (margin 0.009) and
    # PREFERS azimuth 225, which projects the photograph's colours onto the wrong parts of the body;
    # azimuth 0 on the same figure reproduces the photograph. Two subjects agreeing on 0, one of them
    # decisively, is what makes this the model's constant rather than a per-run search.
    INPUT_VIEW_AZIMUTH = 0.0

    # Where torch.hub finds the StableNormal checkout baked into the image, named the way torch.hub
    # names a GitHub repo: {owner}_{repo}_{branch}.
    STABLENORMAL_CHECKOUT = "hugoycj_StableNormal_main"
    # The checkpoint version the predictor is asked for, matched to the staged repo's directory name.
    YOSO_VERSION = "yoso-normal-v1-8-1"

    def generate(self, image_path: str, work_dir: str, params: dict,
                 extra_images=(), source_images=(), starting_mesh=None) -> GenerationResult:
        out_dir = self.output_dir(work_dir)

        # The defaults are the upstream demo's values. The STABLE3DGEN_* keys are this model's own
        # block in config.json, so a stable3dgen run accepts them and a run on another model does not.
        driver_settings = {
            "hf_repos": list(self.HF_REPOS),
            "geometry_repo": self.HF_REPOS[0],
            "stablenormal_checkout": self.STABLENORMAL_CHECKOUT,
            "yoso_version": self.YOSO_VERSION,
            "weights_dir": os.path.join(os.environ["HF_HOME"], "stable3dgen_weights"),
            "seed": int(setting(params, "STABLE3DGEN_SEED", 1)),
            "sparse_steps": int(setting(params, "STABLE3DGEN_SPARSE_STEPS", 50)),
            "sparse_cfg_strength": float(setting(params, "STABLE3DGEN_SPARSE_CFG_STRENGTH", 3.0)),
            "slat_steps": int(setting(params, "STABLE3DGEN_SLAT_STEPS", 6)),
            "slat_cfg_strength": float(setting(params, "STABLE3DGEN_SLAT_CFG_STRENGTH", 3.0)),
            "preprocess_resolution": int(
                setting(params, "STABLE3DGEN_PREPROCESS_RESOLUTION", 1024)),
            "normal_resolution": int(setting(params, "STABLE3DGEN_NORMAL_RESOLUTION", 768)),
        }

        driver_path = Path(work_dir) / "stable3dgen_driver.py"
        driver_path.write_text(DRIVER_TEMPLATE, encoding="utf-8")

        run_upstream(
            [
                "python", "-u", str(driver_path),
                image_path,
                str(out_dir),
                json.dumps(driver_settings),
            ],
            cwd=self.REPO_DIR,
            hf_home=os.environ["HF_HOME"],
            # The sparse module accepts only 'xformers' or 'flash_attn' for attention and raises at
            # import otherwise, so these are set rather than left to the module defaults.
            extra_env={
                "ATTN_BACKEND": setting(params, "STABLE3DGEN_ATTN_BACKEND", "xformers"),
                "SPARSE_ATTN_BACKEND": setting(params, "STABLE3DGEN_ATTN_BACKEND", "xformers"),
                "SPARSE_BACKEND": setting(params, "STABLE3DGEN_SPARSE_BACKEND", "spconv"),
                "SPCONV_ALGO": setting(params, "STABLE3DGEN_SPCONV_ALGO", "native"),
            },
            timeout=5400,
        )

        # The normal map is written beside the mesh as a diagnostic and is not a texture, so it is
        # excluded from the sidecars that travel with the mesh.
        mesh_path = find_generated_mesh(str(out_dir), (".glb", ".gltf", ".obj", ".ply"))
        textures = [p for p in collect_sidecars(mesh_path)
                    if Path(p).name != "normal.png"]

        notes = self.base_notes()
        notes.update({
            "stable3dgenSeed": str(driver_settings["seed"]),
            "stable3dgenSparseSteps": str(driver_settings["sparse_steps"]),
            "stable3dgenSlatSteps": str(driver_settings["slat_steps"]),
            "stable3dgenNormalResolution": str(driver_settings["normal_resolution"]),
            "stable3dgenGeometryOnly": "true",
            "generatedMesh": Path(mesh_path).name,
        })
        logger.info(
            f"Stable3DGen generated {mesh_path}; this model produces geometry only, so the mesh "
            f"carries no texture whatever the export formats request")
        return GenerationResult(mesh_path=mesh_path, texture_paths=textures, notes=notes)
