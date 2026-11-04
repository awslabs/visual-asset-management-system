# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Model dispatch for the image-to-3D pipeline.

One model key selects one adapter. The key has already been validated at the vamsExecute boundary
and used by the state machine's Choice to pick a Batch job definition, so by the time this module
runs the key and the container image should agree; when they do not -- a job definition pointing at
the wrong image, a direct container invocation -- the mismatch is raised here with the model name and
the image it needs.
"""

import os
from typing import Dict, Type

import framing
from vams_utils.logging import log

from models.base import GenerationResult, ModelAdapter, setting
from models.blenderagent import BlenderAgentAdapter
from models.hunyuan3d import Hunyuan3dAdapter
from models.sam3d import Sam3dAdapter
from models.stable3dgen import Stable3DGenAdapter
from models.step1x3d import Step1X3dAdapter
from models.trellis2 import Trellis2Adapter
from models.triposr import TripoSRAdapter

logger = log.get_logger()

# The dispatch table. Keys are the MODEL config value, the SFN Choice discriminator, the Dockerfile
# suffix and the Batch job definition suffix.
MODEL_ADAPTERS: Dict[str, Type[ModelAdapter]] = {
    Sam3dAdapter.KEY: Sam3dAdapter,
    Stable3DGenAdapter.KEY: Stable3DGenAdapter,
    Trellis2Adapter.KEY: Trellis2Adapter,
    TripoSRAdapter.KEY: TripoSRAdapter,
    Step1X3dAdapter.KEY: Step1X3dAdapter,
    Hunyuan3dAdapter.KEY: Hunyuan3dAdapter,
    BlenderAgentAdapter.KEY: BlenderAgentAdapter,
}

# Stable3DGen is the default: it builds from wheels alone, carries no NVIDIA-licensed component, and
# targets geometric fidelity, which is what the print-readiness path is for. TRELLIS.2 remains in the
# table so a deployment can enable it deliberately, but its image needs a toolchain this one does not
# provide, so it is not a usable default.
DEFAULT_MODEL = Stable3DGenAdapter.KEY


def normalize_model_key(model) -> str:
    """The canonical model key for a raw MODEL value, falling back to the default when blank."""
    key = str(model or "").strip().lower()
    return key or DEFAULT_MODEL


def get_adapter(model) -> ModelAdapter:
    """The adapter for a model key, raising a legible error for an unknown one."""
    key = normalize_model_key(model)
    adapter_class = MODEL_ADAPTERS.get(key)
    if adapter_class is None:
        raise ValueError(
            f"Unknown image-to-3D model '{key}'. Supported models: "
            f"{', '.join(sorted(MODEL_ADAPTERS))}."
        )
    return adapter_class()


def _framing_value(params: dict, key: str, adapter_default):
    """A framing setting, where ``auto`` defers to the value the selected adapter declares.

    Each model expects its own framing, so a single pipeline-wide number would be wrong for at least
    one of them. ``auto`` is the default in config.json precisely so the adapter stays authoritative
    unless a template deliberately overrides it for a comparison run.
    """
    raw = str(setting(params, key, "auto")).strip().lower()
    return adapter_default if raw in ("", "auto") else raw


def generate(image_path, work_dir: str, params: dict, extra_images=(), source_images=(),
             starting_mesh=None) -> GenerationResult:
    """Reconstruct a mesh from one preprocessed image with the model named by MODEL.

    ``params`` is the resolved setting map (input configuration first, asset metadata filling
    blanks). The mesh and any texture sidecars land under ``work_dir``; nothing is uploaded here.

    ``extra_images`` are FURTHER VIEWS of the same subject, beyond ``image_path``. Only a model whose
    upstream pipeline conditions on several views can use them, and one that cannot REFUSES them rather
    than ignoring them: silently reconstructing from one of four supplied views is a wrong answer that
    looks like a right one, and the operator has no way to see which happened.

    ``source_images`` are the photographs as supplied, and ``starting_mesh`` an existing model to refine;
    both follow the same rule. ``image_path`` may be None only for a model that does not require a
    reference image and was given a starting mesh.
    """
    model_key = normalize_model_key(setting(params, "MODEL", DEFAULT_MODEL))
    adapter = get_adapter(model_key)

    extra_images = list(extra_images or ())
    if extra_images and not adapter.ACCEPTS_MULTIPLE_VIEWS:
        raise ValueError(
            f"{adapter.DISPLAY_NAME} reconstructs from a single image, but this run supplied "
            f"{len(extra_images) + 1} views. Choose a model that accepts several views, or run it with "
            f"one input file.")
    if starting_mesh and not adapter.ACCEPTS_STARTING_MESH:
        raise ValueError(
            f"{adapter.DISPLAY_NAME} reconstructs from photographs and cannot start from an existing "
            f"mesh, but this run supplied {os.path.basename(str(starting_mesh))}. Only the Blender agent "
            f"refines an existing model.")
    if not image_path:
        if adapter.REQUIRES_REFERENCE_IMAGE or not starting_mesh:
            raise ValueError(
                f"{adapter.DISPLAY_NAME} needs a reference image and this run supplied none.")

    logger.info(
        f"Generating with {adapter.DISPLAY_NAME} (key={adapter.KEY}, licence={adapter.LICENSE}, "
        f"weights={', '.join(adapter.HF_REPOS)})"
    )

    hf_home = os.environ.get("HF_HOME")
    if not hf_home:
        raise ValueError(
            "HF_HOME is not set. The Batch job definition mounts the shared EFS weight cache and "
            "the container sets HF_HOME to this model's slice of it before generation."
        )

    adapter.ensure_ready(hf_home)

    # Framing needs an alpha channel to locate the subject in, so it applies only when an earlier
    # stage produced a cutout. BACKGROUND_REMOVAL=none asks for the photograph as supplied, which
    # leaves no mask to frame against and hands the model whatever composition the photo has.
    background_removal = str(setting(params, "BACKGROUND_REMOVAL", "rembg")).lower()
    framing_background = _framing_value(params, "FRAMING_BACKGROUND", adapter.FRAMING_BACKGROUND)
    if image_path is None:
        # A mesh-only refine: nothing to frame.
        model_image = None
    elif background_removal == "none":
        logger.info(
            "Background removal is off, so there is no mask to frame the subject from; the image "
            "reaches the model as supplied and its composition drives the reconstruction.")
        model_image = image_path
    else:
        model_image = framing.frame_image(
            image_path,
            work_dir,
            _framing_value(params, "FRAMING_RATIO", adapter.FRAMING_RATIO),
            framing_background,
        )

    # Every view is framed on the same terms as the primary. A model conditioned on several views reads
    # them as one subject seen from several places, so framing one of them differently would describe a
    # subject that changes size between views.
    model_extra_images = list(extra_images)
    if extra_images and background_removal != "none":
        model_extra_images = [
            framing.frame_image(
                extra,
                work_dir,
                _framing_value(params, "FRAMING_RATIO", adapter.FRAMING_RATIO),
                framing_background,
            )
            for extra in extra_images
        ]

    if model_extra_images:
        logger.info(f"Reconstructing from {len(model_extra_images) + 1} views")
    result = adapter.generate(image_path=model_image, work_dir=work_dir, params=params,
                              extra_images=model_extra_images, source_images=list(source_images or ()),
                              starting_mesh=starting_mesh)

    # The adapter knows which axis its model wrote as up; the caller normalises on this.
    result.up_axis = adapter.OUTPUT_UP_AXIS
    # And which azimuth its output frame puts the input photograph at, for the colour projection.
    result.input_view_azimuth = adapter.INPUT_VIEW_AZIMUTH

    if not result.mesh_path:
        raise RuntimeError(
            f"{adapter.DISPLAY_NAME} reported success but returned no mesh path."
        )
    logger.info(
        f"Generation complete: mesh={result.mesh_path}, "
        f"textures={len(result.texture_paths)}"
    )
    return result
