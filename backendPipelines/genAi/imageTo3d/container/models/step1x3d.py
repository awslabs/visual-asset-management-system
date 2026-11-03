# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""StepFun Step1X-3D adapter.

Step1X-3D splits reconstruction into two stages: a geometry stage produces a watertight-ish mesh from
the photo, then an SD-XL-based texture module paints it. The texture stage is the expensive half, and
skipping it (STEP1X3D_GENERATE_TEXTURE=false) yields geometry alone in roughly a third of the time --
worth doing when the run only needs the printable STL.
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


def _label(params: dict):
    """Upstream's geometry label, or ``None`` when it is not fully specified.

    Read as a whole dict upstream, so a half-set label is not passed at all. Symmetry forces the
    reconstruction to be symmetric about an axis, which is right for a mug and wrong for a subject that
    is not, so there is no default.
    """
    symmetry = str(setting(params, "STEP1X3D_LABEL_SYMMETRY", "") or "").strip()
    edge_type = str(setting(params, "STEP1X3D_LABEL_EDGE_TYPE", "") or "").strip()
    if not symmetry or not edge_type:
        return None
    return {"symmetry": symmetry, "edge_type": edge_type}


# Driver run inside the upstream repo. Geometry and texture are separate pipelines upstream, so the
# driver runs the geometry stage, exports it, then optionally feeds the exported mesh to the texture
# stage and exports the textured result over the top.
DRIVER_TEMPLATE = '''
import json
import os
import sys
import traceback

import torch

image_path = sys.argv[1]
output_dir = sys.argv[2]
settings = json.loads(sys.argv[3])

os.makedirs(output_dir, exist_ok=True)

from step1x3d_geometry.models.pipelines.pipeline import Step1X3DGeometryPipeline
from step1x3d_geometry.models.pipelines.pipeline_utils import reduce_face, remove_degenerate_face
from step1x3d_texture.pipelines.step1x_3d_texture_synthesis_pipeline import (
    Step1X3DTexturePipeline,
)

geometry_pipeline = Step1X3DGeometryPipeline.from_pretrained(
    settings["hf_repo"], subfolder=settings["geometry_subfolder"]
).to("cuda")

# Seeded exactly as upstream's inference.py does. Without a generator the pipeline draws from the
# global RNG and the run is not reproducible whatever seed the operator asked for.
generator = torch.Generator(device=geometry_pipeline.device)
generator.manual_seed(settings["seed"])

geometry_kwargs = {
    "guidance_scale": settings["guidance_scale"],
    "num_inference_steps": settings["geometry_steps"],
    "octree_resolution": settings["octree_resolution"],
    "max_facenum": settings["max_face_num"],
    "generator": generator,
}
if settings.get("label"):
    geometry_kwargs["label"] = settings["label"]

# The PATH, not an opened image: upstream's check_inputs accepts a str and raises
# "Input image must be a torch.Tensor or PIL.Image.Image" for a PIL image, its guard being inverted.
# Upstream opens and preprocesses the file itself, reading the alpha channel as the object mask.
geometry_out = geometry_pipeline(image_path, **geometry_kwargs)
geometry_mesh = geometry_out.mesh[0]

geometry_path = os.path.join(output_dir, "step1x3d_geometry.glb")
geometry_mesh.export(geometry_path)
print("Step1X-3D geometry stage wrote " + geometry_path)

if settings["generate_texture"]:
    # The geometry model is released before the SD-XL texture stack loads. Upstream builds each
    # pipeline inside its own function so the first is collected before the second exists; holding both
    # raises peak GPU memory past what a 24 GB card has.
    del geometry_pipeline
    torch.cuda.empty_cache()
    try:
        texture_pipeline = Step1X3DTexturePipeline.from_pretrained(
            settings["hf_repo"], subfolder=settings["texture_subfolder"]
        )

        # The texture stage is an SD-XL multi-view pipeline and is what puts this model past a 24 GiB
        # card: upstream's own table says geometry plus texture needs 27 GB, and a live run died with
        # "CUDA out of memory ... GPU 0 has a total capacity of 22.03 GiB" on an L4. Upstream never calls
        # diffusers' memory controls, but the pipeline it builds is a diffusers pipeline, so they are
        # available -- and making the stage FIT the fleet is worth more than restricting it to the one
        # instance family that has 48 GiB, which returned InsufficientInstanceCapacity when tried.
        #
        # ATTENTION SLICING MUST NOT BE APPLIED HERE, and calling it is what made every textured run
        # produce a collage of garments unrelated to the photograph.
        #
        # Upstream builds this pipeline around a CUSTOM attention processor and loads learned weights into
        # it: init_custom_adapter(num_views=..., self_attn_processor=DecoupledMVRowColSelfAttnProcessor2_0)
        # followed by load_custom_adapter(adapter_path, "step1x-3d-ig2v.safetensors"). That processor is
        # both the image conditioning and the coupling that keeps the six generated views consistent with
        # one another.
        #
        # diffusers' Attention.set_attention_slice ends in an unconditional self.set_processor(...) with a
        # built-in class -- SlicedAttnProcessor -- with no check of what is already installed and no
        # preservation path; set_processor even logs that it is removing "possibly trained weights". So
        # enable_attention_slicing REPLACES the adapter, and the stage then samples SD-XL with neither the
        # reference image nor any cross-view attention. Content drawn from the prior is exactly what the
        # atlas showed: several different pairs of jeans, a teal top, red hair, a framed picture of a shoe.
        #
        # It also explains why nothing else moved the result. The slicing loop ran whenever the mode was
        # not "off", so both the 'offload' and 'slicing' arms destroyed the adapter, and the only arm that
        # did not -- 'off' -- ran out of memory and wrote no texture. Every textured run ever produced here
        # had the adapter clobbered, which is why varying the memory mode, the background removal and the
        # reference image each changed the collage without ever fixing it.
        #
        # VAE slicing is safe and kept: it splits the decode, which is where a 2048x2048 output puts its
        # largest allocation, and touches nothing on the UNet's attention. Component offload is safe too --
        # it moves modules between devices rather than replacing processors.
        # COMPONENT OFFLOAD ALSO BREAKS THE ADAPTER, so 'auto' never selects it however small the card is.
        # With the custom attention processor intact, enable_model_cpu_offload raises inside it --
        # step1x3d_texture/models/attention_processor.py during the forward pass -- because the adapter holds
        # learned weights that upstream places on the device itself and the offload hooks do not know about.
        # It appeared to work before only because attention slicing had already replaced that processor with
        # a built-in one that offload handles: the slicing was masking this incompatibility.
        #
        # VAE slicing alone is enough. Measured: the texture stage completes on a 22.0 GiB card in 335 s of
        # the generate stage, so there is no card in this fleet that needs more than this. 'offload' stays
        # selectable for a future upstream or diffusers version that fixes it, and says what it will do.
        low_vram = settings.get("texture_low_vram", "auto")
        total_gib = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        if low_vram == "auto":
            low_vram = "slicing"
        print(f"Step1X-3D texture stage: {total_gib:.1f} GiB visible, low-VRAM mode '{low_vram}'")
        if low_vram == "offload":
            print("Step1X-3D texture stage: WARNING -- component offload is known to raise inside this "
                  "model's own attention processor, which loses the texture and leaves the geometry. "
                  "'slicing' fits a 22 GiB card on its own.")

        inner = getattr(texture_pipeline, "ig2mv_pipe", None)
        if low_vram != "off" and inner is not None:
            try:
                inner.enable_vae_slicing()
                print("Step1X-3D texture stage: enable_vae_slicing applied")
            except Exception as slicing_error:
                print(f"Step1X-3D texture stage: enable_vae_slicing unavailable ({slicing_error})")
            if low_vram == "offload":
                # Only when asked for explicitly. It does not raise HERE -- installing the hooks succeeds --
                # so the failure lands later, inside the adapter's forward pass, and the stage is wrapped so
                # the geometry export still stands.
                try:
                    inner.enable_model_cpu_offload()
                    print("Step1X-3D texture stage: model CPU offload applied")
                except Exception as offload_error:
                    print(f"Step1X-3D texture stage: CPU offload unavailable ({offload_error})")

        # Upstream's own order: drop degenerate faces, then reduce, then paint. The texture stage
        # rasterizes UVs per face, and handing it the geometry stage's full face budget is not
        # something upstream ever does.
        mesh = remove_degenerate_face(geometry_mesh)
        mesh = reduce_face(mesh)
        # Upstream defaults remove_bg to True. Turning it off saves a BiRefNet segmentation model on a
        # card that is short of memory, and a second segmentation pass over an image this pipeline has
        # already cut out -- but the saving is only worth having if the texture is still right, and the
        # first textured run came back monochrome with content unrelated to the photograph. That is what
        # a weakly-conditioned diffusion pass looks like, so this is now a SETTING rather than an
        # assumption, defaulting to upstream's value until the cheaper path is measured to be equivalent.
        textured_mesh = texture_pipeline(
            image_path, mesh, remove_bg=settings["texture_remove_bg"], seed=settings["seed"])
        textured_path = os.path.join(output_dir, "step1x3d_textured.glb")
        textured_mesh.export(textured_path)
        print("Step1X-3D texture stage wrote " + textured_path)
    except Exception:
        # The geometry is already on disk and is a usable result on its own. Losing it because the
        # optional half failed would throw away the whole GPU run, so the failure is reported and the
        # adapter ships the geometry -- reporting the texture stage as failed rather than as run.
        traceback.print_exc()
        print("Step1X-3D texture stage FAILED; the geometry export stands")
'''


class Step1X3dAdapter(ModelAdapter):
    KEY = "step1x3d"
    DISPLAY_NAME = "StepFun Step1X-3D"
    LICENSE = "Apache-2.0"
    MIN_VRAM_GB = 24
    HF_REPOS = ("stepfun-ai/Step1X-3D",)
    REPO_DIR = "/opt/step1x3d"

    # Upstream writes a Y-up mesh, like SAM 3D and unlike the inherited default. Left at "z" the
    # pipeline's normalise step is a no-op, the mesh stays on its side through every measurement taken
    # from its extents, and the glTF export's own rotation then ships it INVERTED -- a standing figure
    # with its head at the bottom, which is how this was found: the multimodal likeness judge reported
    # candidate C as "rendered upside down" while every gate criterion passed.
    #
    # Nothing the pipeline measures catches this. The print gate, the volume, the face count and the file
    # size are all orientation-invariant; the footprint and proportion descriptors are NOT, so they were
    # describing the wrong axis, and a print feature's millimetre dimensions would have been applied
    # along it too.
    OUTPUT_UP_AXIS = "y"

    # Measured on the standing figure by pinning each candidate and comparing what the projection can
    # actually reach, because the silhouette search cannot settle this one on its own: a person's outline
    # is close to symmetric front-to-back, so the search picks 180 at IoU 0.868 but with a margin of only
    # 0.031 over azimuth 0 -- under the 0.05 it needs to be a measurement rather than a coin toss.
    #
    # Pinned and compared, three signals agree on 180. Silhouette IoU is higher (0.868 against 0.837),
    # more of the photograph reaches the surface directly (41.7% of vertices against 36.3%), and the
    # render settles it: at the same geometric view, 180 puts a coherent face, top, jeans and shoes on the
    # mesh's FRONT, while 0 leaves that front streaked and paints the photograph's front onto the BACK.
    #
    # So this model writes its reconstruction 180 degrees from where Stable3DGen writes its own (which
    # declares 0.0). The two conventions differ, which is exactly why this is a per-model constant.
    INPUT_VIEW_AZIMUTH = 180.0

    # Weight subfolders within the one HF repo, one per stage. The base geometry checkpoint rather
    # than the Label variant: the Label weights condition on a label dict (upstream passes
    # {"symmetry": "x", "edge_type": "sharp"}), and selecting them without one leaves the conditioning
    # they exist for doing nothing. STEP1X3D_LABEL_* selects the variant and its label together.
    GEOMETRY_SUBFOLDER = "Step1X-3D-Geometry-1300m"
    TEXTURE_SUBFOLDER = "Step1X-3D-Texture"

    def generate(self, image_path: str, work_dir: str, params: dict,
                 extra_images=(), source_images=(), starting_mesh=None) -> GenerationResult:
        out_dir = self.output_dir(work_dir)

        # The STEP1X3D_* keys are this model's own block in config.json, so a step1x3d run accepts
        # them and a run on another model does not. The defaults below match that block.
        generate_texture = str(
            setting(params, "STEP1X3D_GENERATE_TEXTURE", "true")
        ).lower() == "true"
        driver_settings = {
            "hf_repo": self.HF_REPOS[0],
            "geometry_subfolder": setting(
                params, "STEP1X3D_GEOMETRY_SUBFOLDER", self.GEOMETRY_SUBFOLDER
            ),
            "texture_subfolder": setting(
                params, "STEP1X3D_TEXTURE_SUBFOLDER", self.TEXTURE_SUBFOLDER
            ),
            "seed": int(setting(params, "STEP1X3D_SEED", 1)),
            # Only meaningful with a Label checkpoint selected, and passed only when both halves are
            # set, since upstream reads the dict as a whole.
            "label": _label(params),
            "guidance_scale": float(setting(params, "STEP1X3D_GUIDANCE_SCALE", 7.5)),
            "geometry_steps": int(setting(params, "STEP1X3D_GEOMETRY_STEPS", 50)),
            "octree_resolution": int(setting(params, "STEP1X3D_OCTREE_RESOLUTION", 384)),
            # Upstream's own face cap. Decimation to the operator's budget is the cleanup stage's
            # job, so this is left generous.
            "max_face_num": int(setting(params, "STEP1X3D_MAX_FACE_NUM", 400000)),
            "generate_texture": generate_texture,
            # Upstream's default. See the call site for why it is not assumed away.
            "texture_remove_bg": str(
                setting(params, "STEP1X3D_TEXTURE_REMOVE_BG", "true")).strip().lower() == "true",
            "texture_low_vram": str(
                setting(params, "STEP1X3D_TEXTURE_LOW_VRAM", "auto")).strip().lower(),
            # Retained so an existing configuration naming it is still accepted, and deliberately unused:
            # 'auto' no longer picks offload at any card size, because offload breaks this model's adapter.
            "texture_offload_below_gib": float(
                setting(params, "STEP1X3D_TEXTURE_OFFLOAD_BELOW_GIB", 40)),
        }

        driver_path = Path(work_dir) / "step1x3d_driver.py"
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
            timeout=7200,
        )

        # The textured export is the finished article; the geometry export is what remains when the
        # texture stage is switched off OR when it failed, which the notes distinguish.
        textured_path = out_dir / "step1x3d_textured.glb"
        texture_ran = generate_texture and textured_path.is_file()
        if texture_ran:
            mesh_path = str(textured_path)
        else:
            mesh_path = find_generated_mesh(str(out_dir), (".glb", ".gltf", ".obj", ".ply"))
        textures = collect_sidecars(mesh_path)

        notes = self.base_notes()
        notes.update({
            "step1x3dSeed": str(driver_settings["seed"]),
            "step1x3dGeometrySteps": str(driver_settings["geometry_steps"]),
            "step1x3dOctreeResolution": str(driver_settings["octree_resolution"]),
            # Three outcomes, not two: a texture stage that was asked for and did not produce a
            # mesh has to be distinguishable from one that was never asked for, or a geometry-only
            # result reads as a deliberate choice.
            "step1x3dTextureStage": ("run" if texture_ran
                                     else "failed" if generate_texture else "skipped"),
            "generatedMesh": Path(mesh_path).name,
        })
        logger.info(f"Step1X-3D generated {mesh_path} with {len(textures)} texture sidecars")
        return GenerationResult(mesh_path=mesh_path, texture_paths=textures, notes=notes)
