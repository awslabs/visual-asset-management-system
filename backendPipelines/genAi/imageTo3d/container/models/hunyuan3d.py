# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tencent Hunyuan3D-2 adapter.

Hunyuan3D-2 splits reconstruction into a flow-matching DiT that decodes a shape from the photograph and
a separate multi-view diffusion pass that paints it, so a run produces a textured mesh from the model
itself rather than from anything this pipeline projects. Two properties make it worth benchmarking
against the others: it is the lightest textured model here -- upstream puts the shape stage at 6 GB of
GPU memory and shape plus texture at 16 GB, which fits the general fleet's 22 GiB card without any
memory control -- and its texture stage ships its own rasteriser rather than depending on nvdiffrast.

**Licence.** The weights and code are under the Tencent Hunyuan 3D 2.0 Community License Agreement. It
permits commercial use, but only inside a Territory defined as "the worldwide territory, excluding the
territory of the European Union, United Kingdom and South Korea", and a deployment serving more than one
million monthly active users must request a separate licence from Tencent. Both are deployment-level
conditions this pipeline cannot check at run time, so the model ships disabled and `getConfig()` refuses
to enable it in a region outside the Territory. Note that the upstream source files carry an older
"TENCENT HUNYUAN NON-COMMERCIAL LICENSE AGREEMENT" header that the repository's own LICENSE file
supersedes; the discrepancy is upstream's and is worth a licence review before a commercial deployment.
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

# Post-process stages upstream ships alongside the shape pipeline, and what each one is for. Face
# reduction is deliberately absent: this pipeline decimates to its own TARGET_FACE_COUNT after the repair
# ladder, and decimating twice loses detail the ladder could have kept.
POSTPROCESS_STAGES = ("floaters", "degenerate")

# The view names upstream's multi-view image processor knows, in its own view-index order. Its lookup is a
# bare dict index, so an unrecognised name raises KeyError deep inside the pipeline rather than here.
VIEW_NAMES = ("front", "left", "back", "right")

# Which checkpoint reconstructs from several views. The single-view DiT cannot: its image processor takes
# one image and has no notion of a view index, so handing it a view dict would either fail or quietly
# condition on one image. The multi-view checkpoint lives in its own repository.
MULTI_VIEW_REPO = "tencent/Hunyuan3D-2mv"
MULTI_VIEW_SUBFOLDER = "hunyuan3d-dit-v2-mv"


def assign_views(image_paths):
    """``{viewName: path}`` for a set of input files, taken from each FILENAME.

    Assigned by name rather than by position. Position looks simpler and is a trap: the obvious ordering
    is whatever the manifest or a directory listing gives, `back.png` sorts before `front.png`, and an
    operator who lists the views in a different order gets a confident reconstruction conditioned on the
    wrong faces of the subject with nothing to indicate it. A filename carrying its own view is
    self-describing, and anything unrecognised raises here naming the file.

    One image needs no token and is the front view, which is what a single-photograph run has always been.
    """
    paths = list(image_paths)
    if len(paths) == 1:
        return {"front": paths[0]}

    assigned = {}
    for path in paths:
        stem = Path(path).stem.lower()
        matched = [name for name in VIEW_NAMES if name in stem]
        if len(matched) != 1:
            raise ValueError(
                f"Cannot tell which view '{Path(path).name}' is. Each input file's name must contain "
                f"exactly one of {', '.join(VIEW_NAMES)} -- for example 'cat_front.png'. "
                f"It matched: {matched or 'nothing'}.")
        view = matched[0]
        if view in assigned:
            raise ValueError(
                f"Two input files both name the '{view}' view: "
                f"{Path(assigned[view]).name} and {Path(path).name}.")
        assigned[view] = path

    if "front" not in assigned:
        raise ValueError(
            f"A multi-view run needs a front view; got {', '.join(sorted(assigned))}. The texture stage "
            f"is conditioned on the front view alone, so without it there is nothing to paint from.")
    return assigned

# Driver run inside the upstream repo. The shape pipeline resolves its weights from the HuggingFace cache
# by repo id, so HF_HOME pointing at this model's slice of the EFS cache is all the staging it needs. The
# paint pipeline is handed the resolved snapshot PATH instead: given a repo id it runs a download of its
# own, outside the pre-fetch and its retry.
DRIVER_TEMPLATE = '''
import json
import os
import sys

from PIL import Image
import torch

# Upstream API: Tencent-Hunyuan/Hunyuan3D-2 minimal_demo.py --
# Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(repo, subfolder=..., variant=...), then
# pipeline(image=..., output_type="trimesh")[0], then Hunyuan3DPaintPipeline.from_pretrained(repo)
# called as (mesh, image=...). Confirm against the commit pinned by HUNYUAN3D_COMMIT in
# Dockerfile.hunyuan3d before deploying.
from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline

image_path = sys.argv[1]
output_dir = sys.argv[2]
settings = json.loads(sys.argv[3])

os.makedirs(output_dir, exist_ok=True)

# Upstream's image processor reads the alpha channel as the object mask: it recentres the subject inside
# a square canvas from the alpha bounding box and composites the result over white. An image WITHOUT
# alpha gets a whole-frame bounding box instead, so it is never cropped to the subject and the model
# reconstructs the composition rather than the object. The pipeline's own segmentation stage normally
# supplies that alpha; this covers the run where it was switched off.
with Image.open(image_path) as opened:
    rgba = opened.convert("RGBA")
    already_opaque = min(rgba.getchannel("A").getextrema()) == 255

remove_bg = str(settings["remove_bg"]).lower()
if remove_bg == "true" or (remove_bg == "auto" and already_opaque):
    from hy3dgen.rembg import BackgroundRemover

    print(f"Segmenting upstream (remove_bg={remove_bg}, image was opaque={already_opaque})", flush=True)
    model_image = os.path.join(output_dir, "upstream_cutout.png")
    BackgroundRemover()(rgba).save(model_image)
else:
    model_image = image_path

# The shape stage's conditioning. One view is passed as a path, exactly as upstream's single-image demo
# does. Several are passed as a {viewName: PIL} dict, which upstream's multi-view image processor maps onto
# its own view indices -- the SAME `image=` argument in both cases, but only the multi-view checkpoint
# carries a processor that understands the dict.
views = settings["views"]
if len(views) > 1:
    from PIL import Image as PILImage

    shape_image = {}
    for name, path in views.items():
        with PILImage.open(path) as opened:
            shape_image[name] = opened.convert("RGBA")
    print(f"Shape stage conditioned on {len(shape_image)} views: {sorted(shape_image)}", flush=True)
else:
    shape_image = model_image

shape_pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
    settings["repo"],
    subfolder=settings["shape_subfolder"],
    variant=settings["variant"],
    use_safetensors=True,
    device="cuda",
)

# torch.manual_seed returns the default CPU generator, which is what upstream's own examples pass.
# diffusers' randn_tensor draws on the CPU and moves the result, so this is portable; a CUDA generator
# would have to match the device the pipeline placed its latents on.
mesh = shape_pipeline(
    image=shape_image,
    num_inference_steps=settings["geometry_steps"],
    guidance_scale=settings["guidance_scale"],
    octree_resolution=settings["octree_resolution"],
    num_chunks=settings["num_chunks"],
    generator=torch.manual_seed(settings["seed"]),
    output_type="trimesh",
)[0]
print(f"Shape stage produced {len(mesh.vertices)} vertices, {len(mesh.faces)} faces", flush=True)

stages = [name for name in str(settings["postprocess"]).split(",") if name.strip()]
if stages:
    from hy3dgen.shapegen import DegenerateFaceRemover, FloaterRemover

    available = {"floaters": FloaterRemover, "degenerate": DegenerateFaceRemover}
    for name in stages:
        name = name.strip()
        if name not in available:
            raise ValueError(
                f"Unknown Hunyuan3D post-process stage '{name}'; supported: {sorted(available)}")
        before = len(mesh.faces)
        mesh = available[name]()(mesh)
        print(f"Post-process {name}: {before} -> {len(mesh.faces)} faces", flush=True)

if settings["generate_texture"]:
    # The shape pipeline is released before the paint pipeline loads. Upstream puts the shape stage at
    # 6 GB and shape plus texture at 16 GB, so holding both is what takes a 22 GiB card to its limit.
    del shape_pipeline
    torch.cuda.empty_cache()

    from hy3dgen.texgen import Hunyuan3DPaintPipeline

    # Given the already-resolved LOCAL snapshot directory rather than the repository id. Handed a repo id,
    # upstream's from_pretrained runs its own snapshot_download for the two checkpoints it needs -- a second
    # download path, outside the pre-fetch and outside its retry, on the shared filesystem that has failed
    # three times mid-write. Handed a path that exists, it skips that branch entirely.
    #
    # NO attention slicing and no component offload on this stage. It is a multi-view diffusion pipeline
    # whose conditioning and cross-view coupling live in its own attention processors, and diffusers'
    # Attention.set_attention_slice finishes with an unconditional set_processor to a built-in sliced
    # processor -- it checks nothing and preserves nothing. Applying it to the peer model's texture stage
    # replaced the adapter and produced a clean-looking atlas of content unrelated to the photograph,
    # with no error anywhere. See backendPipelines/CLAUDE.md, "Fitting a Diffusion Pipeline onto a
    # Smaller Card".
    paint_pipeline = Hunyuan3DPaintPipeline.from_pretrained(
        settings["weights_dir"], subfolder=settings["paint_subfolder"])
    # The FRONT view only, which is upstream's own behaviour: its multi-view example paints from
    # images["front"] even when the shape stage was conditioned on three views. So extra views improve the
    # geometry and not the texture, and the back of a multi-view subject is still painted from its front.
    mesh = paint_pipeline(mesh, image=views.get("front", model_image))
    print("Texture stage complete", flush=True)

# One mesh file only. find_generated_mesh picks the first candidate by extension then by path, so a
# second glb written as a diagnostic would compete with this one for that choice.
mesh_path = os.path.join(output_dir, "hunyuan3d.glb")
mesh.export(mesh_path)
print(f"Wrote {mesh_path} with {len(mesh.vertices)} vertices and {len(mesh.faces)} faces")
'''


class Hunyuan3dAdapter(ModelAdapter):
    KEY = "hunyuan3d"
    DISPLAY_NAME = "Tencent Hunyuan3D-2"
    LICENSE = "Tencent Hunyuan 3D 2.0 Community License Agreement"
    # Upstream's own table: 6 GB for the shape stage, 16 GB for shape plus texture at octree 384. The
    # driver frees the shape pipeline before loading the paint pipeline, so 16 GB is the peak.
    MIN_VRAM_GB = 16
    # The single-view repository carries both stages, in the hunyuan3d-dit-v2-0 and
    # hunyuan3d-paint-v2-0 subfolders, and is what a one-image run uses end to end. The multi-view DiT is
    # a SEPARATE repository and is listed so the pre-fetch covers it under fetch_repo's retry rather than
    # leaving upstream to download it mid-run onto the shared filesystem. The cost is that a single-view
    # deployment also caches the multi-view weights once; the alternative is an un-retried download on the
    # first multi-view run, on the filesystem that has failed three times mid-write.
    HF_REPOS = ("tencent/Hunyuan3D-2", MULTI_VIEW_REPO)

    # Upstream's multi-view checkpoint conditions one reconstruction on up to four named views.
    ACCEPTS_MULTIPLE_VIEWS = True
    REPO_DIR = "/opt/hunyuan3d"

    # Upstream's image processor consumes the alpha channel as the object mask and does its own
    # recentring, so the cutout has to reach it with alpha intact -- compositing a flat colour behind the
    # subject here would remove the mask it reads and leave it reconstructing the whole frame.
    FRAMING_BACKGROUND = "none"

    # NOT CALIBRATED against a live run yet. glTF declares Y as up and upstream exports through
    # trimesh's glTF writer, which is the reason for the value; a wrong choice here is invisible to every
    # gate, volume and face count and shows up only as a model lying on its side in a preview or slicer,
    # so confirm it on the first run and correct it here rather than in the renderer.
    OUTPUT_UP_AXIS = "y"

    # Left uncalibrated: this model textures its own output, so the photograph projection is off for it
    # by default and has no constant to read. Calibrate it if PROJECT_PHOTO_COLOUR is ever wanted here --
    # on a subject whose outline determines its pose, then checked against one whose outline does not.
    INPUT_VIEW_AZIMUTH = None

    def generate(self, image_path: str, work_dir: str, params: dict,
                 extra_images=(), source_images=(), starting_mesh=None) -> GenerationResult:
        out_dir = self.output_dir(work_dir)

        # Which views this run has, keyed by name taken from each filename. One image is the front view.
        views = assign_views([image_path, *list(extra_images or ())])
        multi_view = len(views) > 1

        postprocess = str(setting(params, "HUNYUAN3D_POSTPROCESS", "off")).strip().lower()
        if postprocess in ("off", "none"):
            postprocess = ""
        for name in (n.strip() for n in postprocess.split(",") if n.strip()):
            if name not in POSTPROCESS_STAGES:
                raise ValueError(
                    f"HUNYUAN3D_POSTPROCESS names an unknown stage '{name}'. Supported stages: "
                    f"{', '.join(POSTPROCESS_STAGES)}, or 'off'.")

        # The defaults are upstream's own signature defaults, so a run that sets nothing reproduces the
        # model as it ships. The HUNYUAN3D_* keys are this model's own block in config.json.
        # A multi-view run needs the multi-view checkpoint: the single-view DiT's image processor takes one
        # image and has no view index, so it cannot read a view dict. Selected from the input count rather
        # than from a setting, because the two must agree and an operator cannot see the coupling.
        shape_repo = MULTI_VIEW_REPO if multi_view else self.HF_REPOS[0]
        shape_subfolder = (MULTI_VIEW_SUBFOLDER if multi_view
                           else setting(params, "HUNYUAN3D_SHAPE_SUBFOLDER", "hunyuan3d-dit-v2-0"))
        if multi_view:
            logger.info(
                f"Multi-view run over {', '.join(sorted(views))}; using {shape_repo} "
                f"({shape_subfolder}) for the shape stage")

        driver_settings = {
            "repo": shape_repo,
            "views": {name: str(path) for name, path in views.items()},
            # The pre-fetched snapshot on the shared cache. The paint pipeline is opened by PATH so it
            # does not run a download of its own; see the driver.
            "weights_dir": self.snapshot_dir(self.HF_REPOS[0]),
            "shape_subfolder": shape_subfolder,
            # Upstream's own default. The two paint checkpoints differ in sampler -- the turbo one runs an
            # LCM schedule in fewer steps -- and the name also selects which custom pipeline is loaded, so
            # an unrecognised value raises a bare KeyError upstream rather than a legible error.
            "paint_subfolder": setting(
                params, "HUNYUAN3D_PAINT_SUBFOLDER", "hunyuan3d-paint-v2-0-turbo"),
            "variant": setting(params, "HUNYUAN3D_VARIANT", "fp16"),
            "seed": int(setting(params, "HUNYUAN3D_SEED", 1)),
            "geometry_steps": int(setting(params, "HUNYUAN3D_GEOMETRY_STEPS", 50)),
            "guidance_scale": float(setting(params, "HUNYUAN3D_GUIDANCE_SCALE", 5.0)),
            "octree_resolution": int(setting(params, "HUNYUAN3D_OCTREE_RESOLUTION", 384)),
            # How many query points the surface extractor evaluates per batch. It trades GPU memory
            # against time and changes nothing about the result.
            "num_chunks": int(setting(params, "HUNYUAN3D_NUM_CHUNKS", 8000)),
            "generate_texture": str(
                setting(params, "HUNYUAN3D_GENERATE_TEXTURE", "true")).lower() == "true",
            "remove_bg": str(setting(params, "HUNYUAN3D_REMOVE_BG", "auto")).lower(),
            "postprocess": postprocess,
        }

        driver_path = Path(work_dir) / "hunyuan3d_driver.py"
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
            timeout=5400,
        )

        # The upstream cutout is written beside the mesh when the driver segments the image itself; it is
        # a diagnostic rather than a texture, so it does not travel with the mesh.
        mesh_path = find_generated_mesh(str(out_dir), (".glb", ".gltf", ".obj", ".ply"))
        textures = [p for p in collect_sidecars(mesh_path)
                    if Path(p).name != "upstream_cutout.png"]

        notes = self.base_notes()
        notes.update({
            "hunyuan3dSeed": str(driver_settings["seed"]),
            "hunyuan3dShapeRepo": str(shape_repo),
            "hunyuan3dShapeSubfolder": str(driver_settings["shape_subfolder"]),
            "hunyuan3dViews": ",".join(sorted(views)),
            "hunyuan3dPaintSubfolder": str(driver_settings["paint_subfolder"]),
            "hunyuan3dGeometrySteps": str(driver_settings["geometry_steps"]),
            "hunyuan3dOctreeResolution": str(driver_settings["octree_resolution"]),
            "hunyuan3dGenerateTexture": str(driver_settings["generate_texture"]).lower(),
            "hunyuan3dPostProcess": postprocess or "off",
            "generatedMesh": Path(mesh_path).name,
        })
        logger.info(
            f"Hunyuan3D-2 generated {mesh_path} "
            f"(texture stage {'on' if driver_settings['generate_texture'] else 'off'})")
        return GenerationResult(mesh_path=mesh_path, texture_paths=textures, notes=notes)
