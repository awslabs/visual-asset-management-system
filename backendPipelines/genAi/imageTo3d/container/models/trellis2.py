# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Microsoft TRELLIS.2 (4B) adapter.

TRELLIS.2 is the default model: a 4B structured-latent generator that produces the best surface and
texture of the four at a cost of roughly 24 GB of GPU memory and a few minutes per run. It is a
library rather than a command-line tool, so the adapter writes a driver script into the job working
directory and runs it inside the upstream repo, keeping the upstream dependency stack and its
relative asset paths intact.
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

# Driver run inside the upstream repo. The pipeline is loaded from the HF repo, run on the one
# preprocessed image, and its result is written out as a glTF container plus, when the run produces
# separate maps, their sidecars.
DRIVER_TEMPLATE = '''
import json
import os
import sys

# Set before the upstream imports: the decoder reads EXR environment maps, and the allocator hint keeps
# a large reconstruction inside the card's memory.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from PIL import Image

# Upstream API: microsoft/TRELLIS.2 example.py -- Trellis2ImageTo3DPipeline.from_pretrained(repo),
# pipeline.run(image, ...) returning a list of MeshWithVoxel, then o_voxel.postprocess.to_glb for the
# export. Confirm against the commit pinned by TRELLIS2_COMMIT in Dockerfile.trellis2 before deploying.
import o_voxel
from trellis2.pipelines import Trellis2ImageTo3DPipeline

image_path = sys.argv[1]
output_dir = sys.argv[2]
settings = json.loads(sys.argv[3])

os.makedirs(output_dir, exist_ok=True)

# from_pretrained builds the pipeline's background remover EAGERLY --
# `pipeline.rembg_model = getattr(rembg, args['rembg_model']['name'])(...)` -- and that class loads
# briaai/RMBG-2.0, a GATED repository. It happens whether or not preprocess_image is set, so a run
# fails there five minutes in even though the pipeline never calls the component: this pipeline has
# already segmented and framed the image before the model sees it.
#
# The classes in the rembg namespace are therefore replaced with a stub before construction, which
# keeps the run clear of both the access request and RMBG-2.0's separate commercial-use terms. The
# stub RAISES if anything actually invokes it, so enabling upstream preprocessing cannot silently
# reconstruct without a background remover -- it fails with a message saying why.
if not settings["preprocess_image"]:
    import inspect

    import trellis2.pipelines.rembg as _rembg_namespace

    class _StubbedRembg:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, *args, **kwargs):
            raise RuntimeError(
                "TRELLIS.2 tried to run its own background removal, which is stubbed out because "
                "TRELLIS2_PREPROCESS_IMAGE is false and the pipeline had already segmented the "
                "image. Set TRELLIS2_PREPROCESS_IMAGE to true to use it, which needs HuggingFace "
                "access to the gated briaai/RMBG-2.0 repository.")

    # Only classes DEFINED in the package are replaced. The namespace also re-exports typing helpers
    # (Protocol, TypeVar, Annotated and friends), and swapping those for a stub would break unrelated
    # imports in ways that are far harder to trace than the problem being avoided.
    _stubbed = [
        name for name, obj in list(vars(_rembg_namespace).items())
        if inspect.isclass(obj)
        and not name.startswith("_")
        and getattr(obj, "__module__", "").startswith("trellis2")
    ]
    for _name in _stubbed:
        setattr(_rembg_namespace, _name, _StubbedRembg)
    print("Stubbed the upstream background remover so no gated repo is fetched: "
          + (", ".join(_stubbed) if _stubbed else "(no classes found in the rembg namespace)"))

pipeline = Trellis2ImageTo3DPipeline.from_pretrained(settings["hf_repo"])
pipeline.cuda()

image = Image.open(image_path)
if image.mode != "RGBA":
    image = image.convert("RGBA")

# preprocess_image is off by default. The pipeline has already segmented and framed this image, so
# upstream's own preprocessing is redundant work -- and it is not merely redundant: it loads
# briaai/RMBG-2.0 to do the background removal, which is a GATED repository, so a run fails seven
# minutes in on a 403 for a step whose output the pipeline had already produced. Enabling it requires
# access to that repo, and RMBG-2.0's terms restrict commercial use separately from the gate.
# The texture sampler is separate from the shape sampler here, which is what makes a colour-print result
# tunable independently of the geometry.
#
# The guidance key is `guidance_strength`, not `cfg_strength`. The samplers name it that way and pass
# anything they do not recognise straight through to the model's forward(), so a wrong key does not
# raise where it is set -- it surfaces four minutes later as
# "SparseStructureFlowModel.forward() got an unexpected keyword argument". The operator-facing setting
# keeps the CFG wording, which is what the value means.
meshes = pipeline.run(
    image,
    seed=settings["seed"],
    sparse_structure_sampler_params={
        "steps": settings["sparse_steps"],
        "guidance_strength": settings["sparse_cfg_strength"],
    },
    shape_slat_sampler_params={
        "steps": settings["shape_steps"],
        "guidance_strength": settings["shape_cfg_strength"],
    },
    tex_slat_sampler_params={
        "steps": settings["tex_steps"],
        "guidance_strength": settings["tex_cfg_strength"],
    },
    preprocess_image=settings["preprocess_image"],
    pipeline_type=settings["pipeline_type"],
    max_num_tokens=settings["max_num_tokens"],
)
if not meshes:
    raise RuntimeError("TRELLIS.2 returned no mesh for this image")
mesh = meshes[0]

# nvdiffrast cannot rasterize beyond this many triangles, so the reconstruction is reduced before the
# texture is baked against it.
mesh.simplify(settings["rasterizer_face_limit"])

glb = o_voxel.postprocess.to_glb(
    vertices=mesh.vertices,
    faces=mesh.faces,
    attr_volume=mesh.attrs,
    coords=mesh.coords,
    attr_layout=mesh.layout,
    voxel_size=mesh.voxel_size,
    aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
    decimation_target=settings["decimation_target"],
    texture_size=settings["texture_size"],
    remesh=True,
    remesh_band=1,
    remesh_project=0,
    verbose=False,
)
glb_path = os.path.join(output_dir, "trellis2.glb")
# WebP textures are deliberately not requested: the pipeline reloads this GLB with trimesh to run the
# cleanup and re-export, and a WebP-encoded texture would not survive that read.
glb.export(glb_path, extension_webp=False)
print("TRELLIS.2 wrote " + glb_path)
'''


class Trellis2Adapter(ModelAdapter):
    KEY = "trellis2"
    DISPLAY_NAME = "Microsoft TRELLIS.2 (4B)"
    LICENSE = "MIT"
    MIN_VRAM_GB = 24
    # The model's own weights first -- the driver reads HF_REPOS[0] as the pipeline repo.
    #
    # DINOv3 is the image encoder the pipeline conditions on, and it is a SEPARATE gated Meta
    # repository: the model's own MIT weights are ungated, so a token that can fetch TRELLIS.2-4B can
    # still be refused here. Listing it makes prefetch_weights fail in the weight-cache stage with a
    # message naming the repo, rather than four minutes into generation as an encoder traceback, and
    # brings it into the EFS cache and its S3 backup like every other weight the run needs.
    HF_REPOS = (
        "microsoft/TRELLIS.2-4B",
        "facebook/dinov3-vitl16-pretrain-lvd1689m",
    )
    REPO_DIR = "/opt/trellis2"
    # The reconstruction is written out through o_voxel's glTF export, and glTF is Y-up by
    # specification. Left at the pipeline's Z-up default the mesh is rotated as though its up axis were
    # already Z, which lays the subject on its side -- a mug came out as a cylinder resting on its
    # curved wall. Stable3DGen needs the same value for the same reason.
    OUTPUT_UP_AXIS = "y"

    def generate(self, image_path: str, work_dir: str, params: dict,
                 extra_images=(), source_images=(), starting_mesh=None) -> GenerationResult:
        out_dir = self.output_dir(work_dir)

        # Sampler and export settings. The defaults are the upstream example values, which produce a
        # textured mesh at the resolution the print-readiness cleanup expects. The TRELLIS2_* keys are
        # this model's own block in config.json, so a trellis2 run accepts them and a run on another
        # model does not.
        driver_settings = {
            "hf_repo": self.HF_REPOS[0],
            "seed": int(setting(params, "TRELLIS2_SEED", 1)),
            # Off by default: the pipeline's own preprocessing has already run, and upstream's version
            # reaches for the gated briaai/RMBG-2.0. See the note above the driver template.
            "preprocess_image": str(
                setting(params, "TRELLIS2_PREPROCESS_IMAGE", "false")).strip().lower() == "true",
            # Three samplers, not two: the shape and the texture are sampled separately, so colour
            # fidelity is tunable without changing the geometry.
            #
            # The step counts are the difference between a printable mesh and a fragmented one, not a
            # speed dial. At 12 steps a photograph of a person reconstructed with 45829 non-manifold
            # edges and 38617 broken faces; at 50 the same image came out watertight with none, for 36
            # seconds more generation. 50 is also the samplers' own default. The texture sampler carries
            # fewer because it colours a surface the shape sampler has already settled.
            "sparse_steps": int(setting(params, "TRELLIS2_SPARSE_STEPS", 50)),
            "sparse_cfg_strength": float(setting(params, "TRELLIS2_SPARSE_CFG_STRENGTH", 7.5)),
            "shape_steps": int(setting(params, "TRELLIS2_SHAPE_STEPS", 50)),
            "shape_cfg_strength": float(setting(params, "TRELLIS2_SHAPE_CFG_STRENGTH", 3.0)),
            "tex_steps": int(setting(params, "TRELLIS2_TEX_STEPS", 25)),
            "tex_cfg_strength": float(setting(params, "TRELLIS2_TEX_CFG_STRENGTH", 3.0)),
            # The resolution tier the reconstruction runs at, and the main detail dial.
            "pipeline_type": str(setting(params, "TRELLIS2_PIPELINE_TYPE", "1024")),
            "max_num_tokens": int(setting(params, "TRELLIS2_MAX_NUM_TOKENS", 49152)),
            # The rasterizer used for the texture bake has a hard triangle ceiling, so the mesh is
            # reduced to it before baking. Distinct from TARGET_FACE_COUNT, which is the cleanup
            # stage's own budget applied afterwards.
            "rasterizer_face_limit": int(
                setting(params, "TRELLIS2_RASTERIZER_FACE_LIMIT", 16777216)),
            "decimation_target": int(setting(params, "TRELLIS2_DECIMATION_TARGET", 1000000)),
            "texture_size": int(setting(params, "TRELLIS2_TEXTURE_SIZE", 2048)),
        }

        driver_path = Path(work_dir) / "trellis2_driver.py"
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
            # The upstream attention and sparse-convolution backends are selected by env var; the
            # values below are the ones the pinned commit's example run uses.
            extra_env={
                # The upstream module validates this against a fixed list and silently keeps its own
                # default for anything else, so the underscored spelling is the one that takes effect.
                "ATTN_BACKEND": setting(params, "TRELLIS2_ATTN_BACKEND", "flash_attn"),
                "SPCONV_ALGO": setting(params, "TRELLIS2_SPCONV_ALGO", "native"),
            },
            timeout=5400,
        )

        mesh_path = find_generated_mesh(str(out_dir), (".glb", ".gltf", ".obj", ".ply"))
        textures = collect_sidecars(mesh_path)

        notes = self.base_notes()
        notes.update({
            "trellis2Seed": str(driver_settings["seed"]),
            "trellis2SparseSteps": str(driver_settings["sparse_steps"]),
            "trellis2ShapeSteps": str(driver_settings["shape_steps"]),
            "trellis2TexSteps": str(driver_settings["tex_steps"]),
            "trellis2PipelineType": driver_settings["pipeline_type"],
            "trellis2TextureSize": str(driver_settings["texture_size"]),
            "generatedMesh": Path(mesh_path).name,
        })
        logger.info(f"TRELLIS.2 generated {mesh_path} with {len(textures)} texture sidecars")
        return GenerationResult(mesh_path=mesh_path, texture_paths=textures, notes=notes)
