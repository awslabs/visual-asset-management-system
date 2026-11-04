# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender agent's scripted refit: a reconstruction fitted to its photograph in headless Blender.

The chain is ``bl_refit.py``'s steps between the existing import, projection, bake and export tools, one
Blender process per step, each saving the scene for the next: register the mesh to the photograph's view,
rebuild it, fit its outline, add relief from a monocular depth estimate, bring it to the face budget, check
it for leaks, carry its colour across, export a turned-back copy wearing that colour for an agent run to
start from, project the photograph, bake, turn it back and export. No agent and no model call is involved;
the one neural model is MoGe-2, which estimates the photograph's depth for the relief in the pipeline's own
Python before Blender starts.

Every skip, revert and fallback is recorded in ``refit/refit_report.json`` beside what each step measured,
and a Blender step that exits non-zero fails the run with its log's tail.
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from vams_utils.logging import log

logger = log.get_logger()

# Below this front-outline IoU after registration, no searched view shows the photograph's subject, and an
# outline fit would bend the mesh into another shape.
MIN_REGISTRATION_IOU = 0.5
# The furthest the outline fit moves any vertex, and the furthest a rebuilt surface may drift from the one it
# replaces on average, as fractions of the subject's height.
MAX_OUTLINE_SHIFT = 0.03
MAX_REBUILD_DRIFT = 0.02
BAKE_SIZE = 2048
BAKE_SAMPLES = 16
# The longest one Blender step may run before the run is failed.
STEP_TIMEOUT_SECONDS = 1800

MOGE_REPO = "Ruicheng/moge-2-vitl-normal"
MOGE_CHECKPOINT = "model.pt"
# Written by the image build beside the pinned MoGe checkout, so a run's report names the code it ran.
MOGE_COMMIT_FILE = Path("/opt/moge/VAMS_UPSTREAM_COMMIT")
# MoGe sees the cutout composited over mid grey, so a dark subject's outline does not merge into black.
MOGE_BACKGROUND = 0.5
# Fewer valid depth pixels than this is not an estimate of the subject.
MIN_DEPTH_PIXELS = 100

DEPTH_FILE = "refs/front_depth.npz"
REPORT_FILE = "refit/refit_report.json"
RESULT_FILE = "refit/refit.glb"
# What an agent run after the refit starts from: the refit's body before the photograph is projected and
# baked, turned back, with the reconstruction's colour on its vertices for the agent's own projection to keep.
# RESULT_FILE's colour is a baked image, which an import does not turn back into vertex colour.
HANDOVER_FILE = "refit/refit_handover.glb"
HANDOVER_BLEND = "handover.blend"
DIAGNOSTIC_SUFFIXES = (".json", ".png", ".log")

Runner = Callable[[Path, str, List[str], Optional[str]], Tuple[int, str]]


class RefitFailed(RuntimeError):
    """A refit step failed. Carries the refit's record so far, for upload."""

    def __init__(self, message: str, diagnostics=None):
        super().__init__(message)
        self.diagnostics = list(diagnostics or [])


def blender_runner(blender: str = "blender", timeout: int = STEP_TIMEOUT_SECONDS) -> Runner:
    """A runner that starts one background Blender per step, inside the workspace."""

    def run(workspace: Path, script: str, args: List[str], blend: Optional[str] = None) -> Tuple[int, str]:
        command = [blender, "-b"] + ([blend] if blend else []) + [
            "--factory-startup", "--python-exit-code", "1", "-P", script, "--"] + list(args)
        env = {k: v for k, v in os.environ.items() if k != "HF_TOKEN"}
        # Blender on Linux resolves a relative script or .blend against PWD, not the process's working directory.
        env.update({"AGENT_WORKSPACE": str(workspace), "PYTHONUTF8": "1", "PWD": str(workspace)})
        done = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            command, cwd=str(workspace), env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)  # nosemgrep: dangerous-subprocess-use-audit
        return done.returncode, done.stdout + done.stderr

    return run


def require_cutout(path) -> None:
    """Refuse a photograph with no cut-out background: the refit fits the mesh to the photograph's outline."""
    from PIL import Image

    with Image.open(path) as image:
        alpha = np.asarray(image.getchannel("A")) if "A" in image.getbands() else None
    if alpha is None or not ((alpha > 127).any() and (alpha <= 127).any()):
        raise ValueError(
            f"BLENDERAGENT_MODE refit fits the mesh to the photograph's outline, and {Path(path).name} has no "
            f"cut-out background to read it from. Set BACKGROUND_REMOVAL to a remover rather than none.")


def depth_estimate(depth, mask, points, alpha) -> Dict[str, np.ndarray]:
    """MoGe's prediction as the relief step reads it: depth and validity on the cutout's grid, and the
    subject's width in the estimate's own units."""
    depth = np.asarray(depth, dtype=float)
    points = np.asarray(points, dtype=float)
    valid = np.asarray(mask, dtype=bool) & np.asarray(alpha, dtype=bool) & np.isfinite(depth)
    valid &= np.isfinite(points).all(axis=-1)
    if int(valid.sum()) < MIN_DEPTH_PIXELS:
        raise RuntimeError(f"the depth estimate covers {int(valid.sum())} of the subject's pixels")
    xs = points[..., 0][valid]
    return {"depth": np.where(valid, depth, 0.0).astype(np.float32), "mask": valid,
            "metricWidth": np.float64(np.percentile(xs, 99) - np.percentile(xs, 1))}


def estimate_front_depth(cutout: Path, out_path: Path, checkpoint: Path) -> dict:
    """MoGe-2 on the cutout, on the GPU when there is one, written to ``out_path`` as the relief step reads it."""
    import torch
    from PIL import Image
    from moge.model.v2 import MoGeModel

    with Image.open(cutout) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    rgb = rgba[:, :, :3] * rgba[:, :, 3:] + MOGE_BACKGROUND * (1.0 - rgba[:, :, 3:])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    started = time.monotonic()
    model = MoGeModel.from_pretrained(str(checkpoint)).to(device).eval()
    try:
        tensor = torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1))).to(device)
        output = model.infer(tensor, use_fp16=device.type == "cuda")
        depth, mask, points = (output[k].float().cpu().numpy() for k in ("depth", "mask", "points"))
    finally:
        del model
        if device.type == "cuda":
            torch.cuda.empty_cache()
    estimate = depth_estimate(depth, mask, points, rgba[:, :, 3] > 0.5)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, **estimate)
    stats = {"validPixels": int(estimate["mask"].sum()), "metricWidth": round(float(estimate["metricWidth"]), 4),
             "device": device.type, "seconds": round(time.monotonic() - started, 2)}
    if MOGE_COMMIT_FILE.is_file():
        stats["commit"] = MOGE_COMMIT_FILE.read_text(encoding="utf-8").strip()
    return stats


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def collect_diagnostics(workspace: Path) -> List[str]:
    """The refit's record: its report, frame, outline overlays, export check and every step's Blender log."""
    refit_dir = workspace / "refit"
    return sorted(str(p) for p in refit_dir.glob("*") if p.is_file() and p.suffix in DIAGNOSTIC_SUFFIXES)


def summarise(report: dict) -> dict:
    """The refit section of the run report: what each step found and did, without the per-step detail."""
    steps = report.get("steps", {})
    register, fit = steps.get("register", {}), steps.get("fit-outline", {})
    rebuild, relief = steps.get("rebuild", {}), steps.get("relief", {})
    retopo, check = steps.get("retopo", {}), steps.get("check", {})

    def pick(record, keys):
        return {k: record[k] for k in keys if k in record}

    return {
        "registrationIou": register.get("iou"),
        "view": pick(register, ("azimuth", "elevation")),
        "outlineIou": fit.get("iou"),
        "outlineStop": fit.get("stop"),
        "finalIou": steps.get("after", {}).get("iou"),
        "rebuild": pick(rebuild, ("method", "drift", "volumeRatio", "solve", "reverted")),
        "relief": pick(relief, ("vertices", "p95", "pinnedVertices", "reverted")),
        "retopo": pick(retopo, ("used", "fallback", "skipped", "trianglesAfter")),
        "leaks": check.get("leaks"),
        "leakFindings": check.get("found", {}),
        "repairs": check.get("repairs", []),
        "colour": steps.get("colour", {}).get("colour"),
        "depth": report.get("depth"),
        "skipped": report.get("skipped", []),
        "settings": report.get("settings", {}),
        "timings": report.get("timings", {}),
        "seconds": report.get("seconds"),
    }


def run_refit(workspace: Path, cutout: str, start_mesh: str, settings: dict, runner: Runner,
              estimate_depth: Optional[Callable[[Path, Path], dict]] = None) -> dict:
    """Run the chain in ``workspace``, which holds ``tools/``, the cutout and the starting mesh at the
    workspace-relative paths given. Returns the refit's mesh, textures, report, summary and diagnostics, the
    hand-over mesh an agent run after it starts from, and the files that agent run also receives.

    ``settings`` is what ``resolve_refit_settings`` returns. ``estimate_depth(cutout, out)`` writes the depth
    estimate; when it is absent or fails, the relief is skipped and the reason recorded.
    """
    workspace = Path(workspace)
    (workspace / "refit").mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timings: Dict[str, float] = {}
    skipped: List[str] = []
    depth_stats = None
    counter = [0]

    def step(name: str, script: str, args: List[str], blend: Optional[str] = "scene.blend") -> None:
        counter[0] += 1
        key = f"{counter[0]:02d}_{name}"
        began = time.monotonic()
        try:
            code, output = runner(workspace, script, args, blend)
        except subprocess.TimeoutExpired as e:
            raise RefitFailed(f"The refit's Blender step '{name}' ran past {e.timeout} s",
                              diagnostics=collect_diagnostics(workspace)) from None
        timings[key] = round(time.monotonic() - began, 2)
        (workspace / "refit" / f"refit_blender_{key}.log").write_text(output, encoding="utf-8")
        if code != 0:
            raise RefitFailed(
                f"The refit's Blender step '{name}' exited {code}. Last of its log:\n{_tail(output)}",
                diagnostics=collect_diagnostics(workspace))
        logger.info(f"Refit step {key} finished in {timings[key]:.1f}s")

    if not settings["relief"]:
        depth = None
    elif estimate_depth is None:
        depth = None
        skipped.append("relief: this image carries no depth estimator")
    else:
        began = time.monotonic()
        try:
            depth_stats = estimate_depth(workspace / cutout, workspace / DEPTH_FILE)
            depth = DEPTH_FILE
        except Exception as e:  # the relief is optional; the reason is recorded rather than raised
            depth = None
            skipped.append(f"relief: the depth estimate failed ({type(e).__name__}: {e})")
        timings["00_depth"] = round(time.monotonic() - began, 2)

    step("import", "tools/bl_import.py", ["--mesh", start_mesh, "--save", "scene.blend"], blend=None)
    step("register", "tools/bl_refit.py", ["register", "--reference", cutout])
    step("overlay", "tools/bl_refit.py", ["overlay", "--reference", cutout, "--out",
                                          "refit/refit_front_before.png"])
    registration = _read_json(workspace / REPORT_FILE).get("steps", {}).get("register", {}).get("iou", 0.0)
    registered = registration >= MIN_REGISTRATION_IOU
    if not registered:
        skipped.append(f"outline fit and relief: the registration IoU {registration} is under "
                       f"{MIN_REGISTRATION_IOU}, so no searched view shows the photograph's subject")

    step("rebuild", "tools/bl_refit.py", ["rebuild", "--method", settings["rebuild"],
                                          "--resolution", str(settings["resolution"]),
                                          "--max-drift", str(MAX_REBUILD_DRIFT),
                                          "--min-volume-retention", str(settings["min_volume_retention"])])
    if registered and settings["outline_fit"]:
        step("fit-outline", "tools/bl_refit.py", ["fit-outline", "--reference", cutout,
                                                  "--max-shift", str(MAX_OUTLINE_SHIFT)])
    if registered and depth:
        step("relief", "tools/bl_refit.py", ["relief", "--reference", cutout, "--depth", depth,
                                             "--strength", str(settings["relief_strength"])])
    step("retopo", "tools/bl_refit.py", ["retopo", "--method", settings["retopo"],
                                         "--faces", str(settings["faces"])])
    step("check", "tools/bl_refit.py", ["check", "--resolution", str(settings["resolution"])])
    step("colour", "tools/bl_refit.py", ["colour"])
    step("overlay", "tools/bl_refit.py", ["overlay", "--reference", cutout, "--out",
                                          "refit/refit_front_after.png", "--final"])
    # The hand-over is turned back in a scene of its own and exported with its vertex colour, while
    # scene.blend goes on, still turned, to the projection and the bake.
    step("handover", "tools/bl_refit.py", ["unturn", "--save", HANDOVER_BLEND])
    step("export-handover", "tools/bl_export.py", ["--out", HANDOVER_FILE], blend=HANDOVER_BLEND)
    step("project", "tools/bl_project_photo.py", ["--reference", cutout, "--save", "scene.blend",
                                                   "--azimuth", "0"])
    step("bake", "tools/bl_bake.py", ["--save", "scene.blend", "--size", str(BAKE_SIZE),
                                      "--samples", str(BAKE_SAMPLES)])
    step("unturn", "tools/bl_refit.py", ["unturn"])
    step("export", "tools/bl_export.py", ["--out", RESULT_FILE])

    report = _read_json(workspace / REPORT_FILE)
    report.update({"settings": settings, "timings": timings, "skipped": skipped,
                   "seconds": round(time.monotonic() - started, 2)})
    if depth_stats is not None:
        report["depth"] = depth_stats
    (workspace / REPORT_FILE).write_text(json.dumps(report, indent=2), encoding="utf-8")
    mesh, handover = workspace / RESULT_FILE, workspace / HANDOVER_FILE
    for name, path in ((RESULT_FILE, mesh), (HANDOVER_FILE, handover)):
        if not path.is_file() or path.stat().st_size == 0:
            raise RefitFailed(f"The refit's export steps left no {name}", diagnostics=collect_diagnostics(workspace))

    files = {REPORT_FILE: str(workspace / REPORT_FILE), RESULT_FILE: str(mesh),
             f"refit/source{Path(start_mesh).suffix.lower()}": str(workspace / start_mesh)}
    for name in ("refit/refit_front_before.png", "refit/refit_front_after.png", DEPTH_FILE):
        if (workspace / name).is_file():
            files[name] = str(workspace / name)
    return {"mesh": str(mesh), "handover": str(handover),
            "textures": sorted(str(p) for p in (workspace / "textures").glob("*.png")),
            "report": report, "summary": summarise(report),
            "diagnostics": collect_diagnostics(workspace), "files": files}
