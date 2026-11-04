# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Adapter interface shared by the four image-to-3D models.

Each model ships in its own container image with its own upstream repo cloned to ``REPO_DIR`` and
its own conflicting dependency stack, so an adapter's job is narrow: confirm the model is present
in this image, make sure its HuggingFace weights are on the shared EFS cache, shell out to the
upstream entrypoint, and report back where the mesh landed.
"""

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from vams_utils.logging import log

logger = log.get_logger()

# Mesh containers an upstream repo may emit, in the order they are preferred: a glTF container
# carries geometry and texture together, an OBJ carries them as a file set, a PLY/STL is geometry
# only.
MESH_EXTENSIONS = (".glb", ".gltf", ".obj", ".ply", ".stl")
# Sidecars that belong to an OBJ/glTF file set and must travel with the mesh.
SIDECAR_EXTENSIONS = (".mtl", ".png", ".jpg", ".jpeg", ".ktx2", ".bin")


@dataclass
class GenerationResult:
    """What a model produced: the mesh, any texture sidecars, and free-form run notes."""

    mesh_path: str
    texture_paths: List[str] = field(default_factory=list)
    notes: Dict[str, str] = field(default_factory=dict)
    # Which axis the model treated as up in the mesh it wrote, copied from the adapter so the
    # pipeline can bring every model into one canonical orientation before anything measures or
    # exports it.
    up_axis: str = "z"
    # The azimuth the input photograph corresponds to in this model's output frame, copied from the
    # adapter, or None where it has not been calibrated. Only the colour projection reads it.
    input_view_azimuth = None
    # Files that describe HOW the mesh was made and belong on the execution rather than in the asset --
    # an agent's renders, notes and transcript. Uploaded to the run's results prefix beside the report.
    diagnostics: List[str] = field(default_factory=list)


def setting(params: Optional[dict], key: str, default):
    """Resolved value for a template setting, treating an absent or blank entry as the default.

    An optional enum field submits an empty string when the operator leaves it alone, and a
    headless fileUpload trigger fills no form at all, so both cases must fall back to the default.
    """
    raw = (params or {}).get(key)
    if raw is None:
        return default
    text = str(raw).strip()
    return text if text != "" else default


def run_upstream(
    cmd: Sequence[str],
    cwd: str,
    hf_home: str,
    extra_env: Optional[dict] = None,
    timeout: Optional[int] = None,
) -> None:
    """Invoke an upstream model entrypoint, raising a legible error on a non-zero exit.

    The upstream repo root is the working directory so the repo's own relative imports and asset
    paths resolve, and HF_HOME points at this model's slice of the EFS cache.
    """
    env = os.environ.copy()
    env["HF_HOME"] = hf_home
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    # The xet transport has deadlocked mid-download on the shared EFS cache; the standard HF
    # transport is used instead.
    env.setdefault("HF_HUB_DISABLE_XET", "1")
    env.setdefault("HF_XET_DISABLE", "1")
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})

    logger.info(f"Running upstream command in {cwd}: {' '.join(str(c) for c in cmd)}")
    try:
        subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            [str(c) for c in cmd], cwd=cwd, env=env, check=True, text=True, timeout=timeout,
        )  # nosemgrep: dangerous-subprocess-use-audit
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Upstream generation command failed with exit code {e.returncode}: "
            f"{' '.join(str(c) for c in cmd)}. Check the CloudWatch log for the upstream traceback."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Upstream generation command timed out after {timeout}s: "
            f"{' '.join(str(c) for c in cmd)}"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError(
            f"Upstream generation command not found: {' '.join(str(c) for c in cmd)}. "
            f"The entrypoint is missing from {cwd} in this image."
        ) from e
    logger.info("Upstream command completed")


def detected_vram_gb() -> Optional[float]:
    """Total memory of the first visible GPU in GiB, or ``None`` when it cannot be read."""
    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30,
        )  # nosemgrep: dangerous-subprocess-use-audit
        if result.returncode != 0:
            logger.warning(f"nvidia-smi returned {result.returncode}: {result.stderr.strip()}")
            return None
        first = result.stdout.strip().splitlines()[0]
        return int(first.strip()) / 1024.0
    except Exception as e:  # nosec B110 - advisory only; an unreadable GPU is not fatal here
        logger.warning(f"Could not read GPU memory: {e}")
        return None


def find_generated_mesh(search_dir: str, extensions: Sequence[str] = MESH_EXTENSIONS) -> str:
    """The generated mesh under ``search_dir``, chosen by container preference then by path.

    Sorting the candidates keeps the choice deterministic when a repo writes several meshes (a
    coarse and a refined pass, say) into the same output directory.
    """
    root = Path(search_dir)
    for ext in extensions:
        candidates = sorted(p for p in root.rglob(f"*{ext}") if p.is_file())
        if candidates:
            if len(candidates) > 1:
                logger.info(
                    f"Found {len(candidates)} '{ext}' meshes under {search_dir}; "
                    f"using {candidates[0]}"
                )
            return str(candidates[0])
    raise RuntimeError(
        f"The model produced no mesh under {search_dir} (looked for {', '.join(extensions)}). "
        f"The upstream command exited successfully, so its output location does not match what "
        f"this adapter searches."
    )


def collect_sidecars(mesh_path: str) -> List[str]:
    """The texture/material files sitting alongside ``mesh_path``.

    An OBJ's MTL references its maps by a relative name, so the whole set has to be kept together
    and uploaded into the same relative subdirectory as the mesh.
    """
    mesh = Path(mesh_path)
    sidecars = [
        str(p)
        for p in sorted(mesh.parent.iterdir())
        if p.is_file() and p != mesh and p.suffix.lower() in SIDECAR_EXTENSIONS
    ]
    if sidecars:
        logger.info(f"Collected {len(sidecars)} texture/material sidecars next to {mesh.name}")
    return sidecars


class ModelAdapter:
    """One image-to-3D model: where it lives in the image, what it needs, how it runs."""

    # Model key: the MODEL config value, the SFN Choice discriminator, the Dockerfile suffix and
    # the Batch job definition suffix are all this string.
    KEY = ""
    # GPU memory the upstream model needs to complete a run at its default resolution.
    MIN_VRAM_GB = 0
    # HuggingFace repos holding the weights, pre-fetched onto the EFS cache before inference.
    HF_REPOS: Sequence[str] = ()
    # Where this model's upstream repo is cloned in its image.
    REPO_DIR = ""
    # Licence the weights and upstream code carry, recorded in the run report.
    LICENSE = ""
    # Human-readable model name for logs and the run report.
    DISPLAY_NAME = ""
    # How the subject is framed before this model sees it. A model whose upstream pipeline frames
    # the cutout itself, or which reads the alpha channel as an object mask, declares ``none`` and
    # receives the cutout untouched; flattening it would destroy the mask. A model that skips its
    # own framing when handed a mask declares the fraction of the frame the subject fills and the
    # background colour it was trained against, and framing.py applies them. A template's
    # FRAMING_RATIO / FRAMING_BACKGROUND override these per run.
    FRAMING_RATIO = 0.85
    FRAMING_BACKGROUND = "none"
    # The axis this model treats as up in the mesh it writes. The models do not agree: one reconstructs
    # into a Z-up space while another applies its own transform on the way out and hands back a Y-up
    # mesh. The pipeline normalises to Z-up on this declaration, so a wrong value here is invisible to
    # every gate, volume, face count and file size, and shows up only as a model lying on its side in
    # a preview or a slicer.
    OUTPUT_UP_AXIS = "z"
    # The azimuth, in the pipeline's z-up space, from which this model's output is seen the way the INPUT
    # PHOTOGRAPH saw it. A reconstruction is written into a canonical frame keyed to the input view, so
    # this is a property of the model rather than of the photograph, and it is the pose colour has to be
    # projected from (see texturing.project_photograph). ``None`` means it has not been calibrated for
    # this model, and the projection falls back to recovering the pose from the silhouette.
    #
    # Calibrate it on a subject whose outline DOES determine its pose, then check it against one whose
    # outline does not: a figure's silhouette is nearly symmetric, so the search can prefer a wrong
    # azimuth there with no sign of trouble in any number it reports.
    INPUT_VIEW_AZIMUTH = None
    # Whether this model's upstream pipeline conditions one reconstruction on SEVERAL views of the
    # subject. False means the adapter takes exactly one image, and generate.py refuses a run that
    # supplied more rather than letting it reconstruct from whichever view came first.
    ACCEPTS_MULTIPLE_VIEWS = False
    # Whether this model can start from an EXISTING mesh and refine it, rather than reconstructing from
    # pixels. Only the agentic path can; a neural reconstruction model has no input for one, so a mesh
    # among its input files is refused rather than ignored.
    ACCEPTS_STARTING_MESH = False
    # Whether a run needs at least one photograph. A model that can refine a mesh may run without one.
    REQUIRES_REFERENCE_IMAGE = True
    # Whether this model bakes its colour into a densely CHART-PACKED atlas -- thousands of small
    # auto-unwrap islands with unwritten background between them -- rather than a contiguous texture or
    # per-vertex colour. A renderer samples an atlas per fragment and blends across an island edge into
    # that background, so a chart-packed one reads as speckle and torn seams in a GL viewer even when it
    # is complete. When True, a PRESERVED atlas is collapsed to per-vertex colour at export instead of
    # kept: glTF carries a colour array natively and a renderer interpolates it over the continuous
    # surface correctly. A model whose atlas is contiguous -- a texture stage's own 2048x2048 output --
    # leaves this False and ships its atlas untouched.
    ATLAS_IS_CHART_PACKED = False

    def __init__(self) -> None:
        # Where each HF repo's snapshot resolved to on the cache, recorded by prefetch_weights. A
        # snapshot directory is named for the revision hash, so it cannot be assembled from the repo
        # id -- a model that reads its own configuration out of the weights repo asks for it here.
        self._snapshot_dirs = {}

    def snapshot_dir(self, repo: str) -> str:
        """The local directory ``repo``'s pre-fetched snapshot resolved to."""
        recorded = self._snapshot_dirs.get(repo)
        if recorded:
            return recorded

        # ensure_ready pre-fetches every repo before generation, so arriving here means it has not
        # run on this instance. Resolve from the cache alone: a network fetch would mask a missing
        # pre-fetch as a download failure.
        from huggingface_hub import snapshot_download

        path = snapshot_download(
            repo_id=repo,
            cache_dir=str(Path(os.environ.get("HF_HOME", "")) / "hub"),
            local_files_only=True,
        )
        self._snapshot_dirs[repo] = path
        return path

    def is_installed(self) -> bool:
        """Whether this image carries the model's upstream repo."""
        return bool(self.REPO_DIR) and Path(self.REPO_DIR).is_dir()

    def ensure_ready(self, hf_home: str) -> None:
        """Confirm the model is installed here and its weights are on the EFS cache."""
        if not self.is_installed():
            raise RuntimeError(
                f"Model '{self.KEY}' ({self.DISPLAY_NAME}) is not installed in this container "
                f"image: {self.REPO_DIR} is missing. Each model has its own image built from "
                f"Dockerfile.{self.KEY}; the Batch job definition selected for this run points at "
                f"a different one."
            )
        available = detected_vram_gb()
        if available is not None and self.MIN_VRAM_GB and available < self.MIN_VRAM_GB:
            # Advisory rather than fatal: the upstream repos accept lower-memory settings, and an
            # out-of-memory abort names the shortfall better than a pre-flight guess can.
            logger.warning(
                f"Model '{self.KEY}' expects at least {self.MIN_VRAM_GB} GB of GPU memory but this "
                f"instance reports {available:.1f} GB. Generation may abort out of memory; select a "
                f"larger instance type in app.pipelines.useImageTo3d.instanceTypes."
            )
        Path(hf_home).mkdir(parents=True, exist_ok=True)
        self.prefetch_weights(hf_home)

    def prefetch_weights(self, hf_home: str) -> None:
        """Download this model's HuggingFace repos into ``hf_home`` if they are not cached.

        Pre-fetching keeps a weight-download failure (a gated repo, a missing token) legible here
        rather than surfacing as an opaque traceback from inside the upstream entrypoint.
        """
        if not self.HF_REPOS:
            return
        try:
            # fetch_repo wraps snapshot_download with the retry the shared cache filesystem needs; the
            # import is checked here so a missing huggingface_hub still names the repos it was for.
            import huggingface_hub  # noqa: F401
            from model_manager import fetch_repo
        except ImportError as e:
            raise RuntimeError(
                f"huggingface_hub is not installed in the '{self.KEY}' image, so the weights for "
                f"{', '.join(self.HF_REPOS)} cannot be fetched."
            ) from e

        token = os.environ.get("HF_TOKEN") or None
        cache_dir = str(Path(hf_home) / "hub")
        for repo in self.HF_REPOS:
            logger.info(f"Ensuring weights for {repo} in {cache_dir}")
            try:
                path = fetch_repo(repo, cache_dir=cache_dir, token=token)
            except Exception as e:
                raise RuntimeError(
                    f"Could not fetch HuggingFace repo '{repo}' for model '{self.KEY}': {e}. "
                    f"Weights under a gated licence need an HF_TOKEN whose account has accepted "
                    f"the model's terms ({self.LICENSE})."
                ) from e
            self._snapshot_dirs[repo] = path
            logger.info(f"Weights for {repo} available at {path}")

    def generate(self, image_path: str, work_dir: str, params: dict,
                 extra_images: Sequence[str] = (), source_images: Sequence[str] = (),
                 starting_mesh: Optional[str] = None) -> GenerationResult:
        """Reconstruct a mesh from one image, writing under ``work_dir``.

        ``extra_images`` are further views of the same subject, already preprocessed like ``image_path``.
        ``source_images`` are the photographs AS SUPPLIED, before background removal and framing, for a
        model that wants the context the cutout removed. ``starting_mesh`` is an existing model to refine.
        An adapter reads only what its capability flags declare; generate.py rejects a run that supplies
        anything else before it gets here, so no adapter has to defend against input it cannot use.
        """
        raise NotImplementedError

    def output_dir(self, work_dir: str) -> Path:
        """This model's generation output directory under the job's working directory."""
        out_dir = Path(work_dir) / "generate" / self.KEY
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def base_notes(self) -> Dict[str, str]:
        """Run-report notes every adapter records."""
        return {
            "model": self.KEY,
            "modelName": self.DISPLAY_NAME,
            "modelLicense": self.LICENSE,
            "modelMinVramGb": str(self.MIN_VRAM_GB),
            "modelWeights": ", ".join(self.HF_REPOS),
        }
