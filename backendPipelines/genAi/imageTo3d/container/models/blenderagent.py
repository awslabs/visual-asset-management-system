# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Agentic reconstruction: a coding agent drives headless Blender until the model matches the photograph.

Every other adapter here runs a neural reconstruction model once and takes what comes out. This one runs a
LOOP: OpenAI's Codex CLI, pointed at a vision-capable model on Amazon Bedrock, reads the reference
photographs, writes Blender Python scripts, renders what it built, looks at the renders beside the
references, and keeps going until the comparison says it is done or the budget says it is not. The output
is a glTF binary like any other model's, so it flows into the same cleanup, gate and export.

Three things hold the design together, and each is here for a measured reason.

**The model comes from Amazon Bedrock, through a local bridge.** Codex speaks only the OpenAI Responses API;
Bedrock exposes OpenAI-compatible Chat Completions and nothing else. LiteLLM's proxy bridges the two and
talks to Bedrock's Converse API over the task role's own credentials, so no API key is ever minted or
stored. The model id is deployment configuration: which foundation models an account may invoke differs
by partition and entitlement.

**The agent is confined by construction, not by instruction.** Codex runs its commands under its own
sandbox in ``workspace-write`` mode, with ``/tmp`` and ``$TMPDIR`` deliberately excluded from the writable
set, so the only place it can write is the workspace this adapter creates -- which sits outside ``/tmp``
precisely so the pipeline's own working directories are out of reach. Its commands have no network. The
workspace is wiped before every run. The Blender-side tools refuse to write outside it as well.

**Completion is a set of files, not a message.** The agent's instructions make the deliverable
``output/model.glb`` plus ``output/DONE.json``, and this adapter checks for the mesh, loads it, and fails
legibly when it is absent, whatever the final message says. A "done" without the file is a failed run.

**A scripted refit can run before the agent, or instead of it.** ``BLENDERAGENT_MODE`` is ``agent``, the
agent alone; ``refit``, the fixed Blender chain in ``blenderagent_refit`` alone, with no agent and no model
call; or ``refit-agent``, the refit and then the agent starting from its result. The refit fits another
model's mesh to the photograph's outline and adds relief from a MoGe-2 depth estimate of the photograph.
When the agent fails after a refit, the refit's result is delivered.

**Licences.** Blender is GPL-2.0-or-later and is installed in this image only; the model files an agent
produces are its own output and are not derivatives of Blender. Codex CLI is Apache-2.0. LiteLLM is MIT.
The model's terms are Amazon Bedrock's for the selected model. MoGe-2, the refit's depth model, is MIT in
code and weights, with a vendored DINOv2 under Apache-2.0; PyTorch is BSD-3-Clause.
"""

import json
import os
import secrets
import shutil

from agent_broker import ImageEditBroker
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from vams_utils.logging import log

from . import blenderagent_refit
from .base import GenerationResult, ModelAdapter, setting

logger = log.get_logger()

# Everything the agent touches lives under here. NOT under /tmp: the sandbox's workspace-write mode
# carves out /tmp as writable by default, and the pipeline's own input, work and output directories are
# under /tmp. Putting the agent root elsewhere and switching those carve-outs off is what keeps the
# agent's reach to its own workspace.
AGENT_ROOT = Path(os.environ.get("BLENDERAGENT_ROOT", "/var/agent"))

# The helper scripts and instructions this image carries, copied into every workspace fresh.
TOOLS_SOURCE = Path(__file__).resolve().parent.parent / "blenderagent_tools"

# Extensions accepted as a STARTING mesh on a refine run.
STARTING_MESH_EXTENSIONS = (".glb", ".gltf", ".obj", ".stl", ".ply")

# The most reference images attached to the opening prompt. Every image is still in refs/ for the agent
# to view on demand; this bounds the first request's size.
MAX_PROMPT_IMAGES = 6

# Codex's sandbox mode for every run. workspace-write is what confines the agent's commands to its
# workspace with no network; it is not a run setting, because the only other mode removes the confinement.
SANDBOX_MODE = "workspace-write"

# The alias the bridge exposes to Codex. Codex asks for this name; LiteLLM maps it to the Bedrock model.
BRIDGE_MODEL_ALIAS = "agent"
# How long one model call may go without progress before the bridge and Codex give it up and Codex
# retries it with its history. A call the model completes takes seconds to about two minutes; a stream
# Bedrock never finishes would otherwise wait out the run budget.
BRIDGE_CALL_TIMEOUT_SECONDS = 180

# What a run does: the agent alone, the scripted refit alone, or the refit and then the agent from its result.
RUN_MODES = ("agent", "refit", "refit-agent")
REFIT_REBUILDS = ("voxel", "metaball", "none")
REFIT_RETOPOS = ("decimate", "quadriflow")


def resolve_model_id() -> str:
    """The Bedrock model id or inference-profile id the agent reasons with: the deployment's configured id,
    delivered by the job definition. It is not a run setting, because the job role is granted that model and
    no other. There is deliberately no hard-coded default: which foundation models an account may invoke
    differs by partition and entitlement, and a guess deploys cleanly and fails at the first call.
    """
    configured = os.environ.get("BLENDERAGENT_BEDROCK_MODEL_ID", "").strip()
    if configured:
        return configured
    raise ValueError(
        "No Bedrock model configured for the Blender agent. Set "
        "app.pipelines.useImageTo3d.models.blenderagent.bedrockModelId in the deployment configuration.")


def image_editing_enabled() -> bool:
    """Whether the deployment lets the agent's image-edit broker invoke the Stability AI models."""
    return os.environ.get("BLENDERAGENT_IMAGE_EDITING", "").strip().lower() == "true"


def resolve_mode(params: dict) -> str:
    """Which of RUN_MODES this run is: the agent alone unless the template names another."""
    mode = str(setting(params, "BLENDERAGENT_MODE", "agent")).strip().lower()
    if mode not in RUN_MODES:
        raise ValueError(f"BLENDERAGENT_MODE must be one of {', '.join(RUN_MODES)}, not '{mode}'")
    return mode


def _choice(key: str, value, choices) -> str:
    word = str(value).strip().lower()
    if word not in choices:
        raise ValueError(f"{key} must be one of {', '.join(choices)}, not '{word}'")
    return word


def _number(key: str, value, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number, not '{value}'") from None
    if not low <= number <= high:
        raise ValueError(f"{key} must be between {low} and {high}, not {number}")
    return number


def resolve_refit_settings(params: dict) -> dict:
    """The scripted refit's settings, checked before any Blender starts. Lengths are fractions of the
    subject's height; the face budget and the volume a rebuild must keep are the pipeline's own settings."""
    return {
        "rebuild": _choice("BLENDERAGENT_REFIT_REBUILD",
                           setting(params, "BLENDERAGENT_REFIT_REBUILD", "none"), REFIT_REBUILDS),
        "resolution": _number("BLENDERAGENT_REFIT_RESOLUTION",
                              setting(params, "BLENDERAGENT_REFIT_RESOLUTION", 0.004), 0.001, 0.05),
        "outline_fit": _choice("BLENDERAGENT_REFIT_OUTLINE_FIT",
                               setting(params, "BLENDERAGENT_REFIT_OUTLINE_FIT", "true"),
                               ("true", "false")) == "true",
        "relief": _choice("BLENDERAGENT_REFIT_RELIEF",
                          setting(params, "BLENDERAGENT_REFIT_RELIEF", "true"), ("true", "false")) == "true",
        "relief_strength": _number("BLENDERAGENT_REFIT_RELIEF_STRENGTH",
                                   setting(params, "BLENDERAGENT_REFIT_RELIEF_STRENGTH", 0.01), 0.0, 0.05),
        "retopo": _choice("BLENDERAGENT_REFIT_RETOPO",
                          setting(params, "BLENDERAGENT_REFIT_RETOPO", "decimate"), REFIT_RETOPOS),
        "faces": int(_number("TARGET_FACE_COUNT", setting(params, "TARGET_FACE_COUNT", 200000), 0, 1e8)),
        "min_volume_retention": _number("MIN_VOLUME_RETENTION",
                                        setting(params, "MIN_VOLUME_RETENTION", 0.5), 0.0, 1.0),
    }


def validate_agent_settings(params: dict):
    """The agent's model id and reasoning effort, each checked before anything starts."""
    model_id = resolve_model_id()
    reasoning = str(setting(params, "BLENDERAGENT_REASONING_EFFORT", "high")).lower()
    if reasoning not in ("minimal", "low", "medium", "high", "xhigh"):
        raise ValueError(f"BLENDERAGENT_REASONING_EFFORT '{reasoning}' is not a Codex reasoning effort")
    return model_id, reasoning


def bedrock_runtime_endpoint(region: str) -> str:
    """The bedrock-runtime endpoint for this region, partition-aware, without hardcoding a DNS suffix."""
    try:
        import boto3
        from botocore.config import Config

        retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
        return boto3.client("bedrock-runtime", region_name=region, config=retry_config).meta.endpoint_url
    except Exception as e:  # pragma: no cover - only without boto3 or a region
        logger.warning(f"Could not resolve the bedrock-runtime endpoint: {e}")
        return f"https://bedrock-runtime.{region}.amazonaws.com"


def litellm_config(model_id: str, region: str) -> dict:
    """The bridge's configuration: one alias, routed to Bedrock Converse on the task role's credentials.

    ``bedrock/converse/`` forces the Converse route whatever LiteLLM's model map knows about the id, which
    matters for a model newer than the installed LiteLLM. ``drop_params`` discards request fields the
    provider does not take rather than failing the call; Codex sends several that only OpenAI's own
    endpoint understands.
    """
    return {
        "model_list": [{
            "model_name": BRIDGE_MODEL_ALIAS,
            "litellm_params": {
                "model": f"bedrock/converse/{model_id}",
                "aws_region_name": region,
            },
        }],
        "litellm_settings": {
            "drop_params": True,
            "set_verbose": False,
            "request_timeout": BRIDGE_CALL_TIMEOUT_SECONDS,
            # Lifts an image out of a tool result into the user turn beside it. The OpenAI models on
            # Bedrock reject an image INSIDE a tool result and accept one beside it; Codex's view_image
            # returns its image as the tool result. Without this the agent fails the first time it looks
            # at its own render. See blenderagent_tools/bridge_image_hook.py.
            "callbacks": ["bridge_image_hook.proxy_handler_instance"],
        },
    }


def codex_config(bridge_url: str, bridge_key_env: str, reasoning_effort: str) -> str:
    """Codex's config.toml for this run, written fresh into a per-run CODEX_HOME.

    The provider block is what points Codex at the bridge rather than at OpenAI. The sandbox block is what
    keeps the agent inside its workspace: ``exclude_slash_tmp`` and ``exclude_tmpdir_env_var`` remove the
    default writable carve-outs, so writable means the workspace and nothing else. The environment policy
    keeps credentials out of every command the agent runs -- the bridge key is read by Codex itself, not by
    the commands, and AWS credentials are the bridge's business.
    """
    return f'''# Written per run by the image-to-3D pipeline. Nothing here persists between runs.
model = "{BRIDGE_MODEL_ALIAS}"
model_provider = "bedrock_bridge"
approval_policy = "never"
sandbox_mode = "{SANDBOX_MODE}"
model_reasoning_effort = "{reasoning_effort}"
model_reasoning_summary = "none"
model_supports_reasoning_summaries = false
model_context_window = 180000
project_doc_max_bytes = 65536
web_search = "disabled"

[model_providers.bedrock_bridge]
name = "Amazon Bedrock via local bridge"
base_url = "{bridge_url}"
env_key = "{bridge_key_env}"
wire_api = "responses"
request_max_retries = 4
stream_max_retries = 8
stream_idle_timeout_ms = {BRIDGE_CALL_TIMEOUT_SECONDS * 1000}

[sandbox_workspace_write]
network_access = false
exclude_tmpdir_env_var = true
exclude_slash_tmp = true

[shell_environment_policy]
inherit = "all"
ignore_default_excludes = false
exclude = ["AWS_*", "*TOKEN*", "*SECRET*", "*KEY*", "HF_HOME"]

[history]
persistence = "none"

[tools]
view_image = true
web_search = false
'''


def task_prompt(brief: dict) -> str:
    """The opening instruction. The method lives in AGENTS.md, which Codex loads from the workspace; this
    names the specific task and restates the two things that most often go wrong."""
    mode = brief["mode"]
    lines = [
        "Read AGENTS.md and brief.json in this directory before doing anything else, then carry out the task.",
        "",
        f"Task: {'REFINE the model in start/ so that it matches' if mode == 'refine' else 'BUILD a 3D model of'} "
        f"the subject in the reference photograph(s) in refs/, as faithfully as you can, and deliver "
        f"output/model.glb and output/DONE.json.",
        "",
        f"References: {', '.join(Path(p).name for p in brief['references'])}.",
    ]
    if brief.get("startingMesh"):
        lines.append(f"Starting mesh: {Path(brief['startingMesh']).name}.")
    if brief.get("refit"):
        lines.append("The starting mesh is the scripted refit's body, wearing the reconstruction's colour on its "
                     "vertices. What it measured and changed is in brief.json's refit and "
                     "refit/refit_report.json; refit/ also holds its own baked result, refit.glb, the mesh it "
                     "started from and its outline before and after against the photograph's.")
    if brief.get("guidance"):
        lines += ["", f"Operator guidance: {brief['guidance']}"]
    if brief.get("targetSizeMm"):
        lines.append(f"Target size: the subject's longest dimension is about {brief['targetSizeMm']} mm.")
    lines += [
        "",
        f"Budget: at most {brief['maxIterations']} build-render-compare iterations and about "
        f"{brief['maxMinutes']} minutes. Finish -- bake, export, verify, write DONE.json -- before the "
        f"budget runs out, even if the model is not perfect.",
        "",
        "The two most common failures are a wrong overall silhouette and a grey export. Get the outline "
        "right first, and bake before you export. Look at every render you make.",
    ]
    return "\n".join(lines)


class BlenderAgentAdapter(ModelAdapter):
    KEY = "blenderagent"
    DISPLAY_NAME = "Blender agent (Codex on Amazon Bedrock)"
    LICENSE = ("Blender GPL-2.0-or-later (tooling only); Codex CLI Apache-2.0; LiteLLM MIT; "
               "MoGe-2 MIT (code and weights; vendored DINOv2 Apache-2.0); PyTorch BSD-3-Clause; "
               "model terms per Amazon Bedrock")
    # Nothing here needs a GPU: the refit's depth model and Cycles use one when present and the CPU otherwise.
    MIN_VRAM_GB = 0
    # The refit's depth model. Fetched before a run when it can be reached and cached thereafter, whichever
    # mode the run takes.
    HF_REPOS = (blenderagent_refit.MOGE_REPO,)
    # is_installed checks this directory; Blender is unpacked here by the Dockerfile.
    REPO_DIR = "/opt/blender"

    # The agent reads the cutout's alpha to see the subject's outline, so it must arrive intact.
    FRAMING_BACKGROUND = "none"
    # Blender's glTF exporter writes Y-up (its own Z-up converted on the way out), like the two peers
    # that also export through a glTF writer.
    OUTPUT_UP_AXIS = "y"
    # Not calibrated: this model textures its own output, so the photograph projection is off for it.
    INPUT_VIEW_AZIMUTH = None

    ACCEPTS_MULTIPLE_VIEWS = True
    # A refine run starts from another model's output. A reference image is still strongly preferred,
    # but a mesh-only refine (tidy what exists, without a photograph) is allowed.
    ACCEPTS_STARTING_MESH = True
    REQUIRES_REFERENCE_IMAGE = False
    # The agent unwraps and bakes its texture over a dense mesh, so its atlas is chart-packed: it tears
    # along island edges when a renderer samples it. Its preserved atlas is delivered as per-vertex
    # colour instead. See ModelAdapter.ATLAS_IS_CHART_PACKED.
    ATLAS_IS_CHART_PACKED = True

    def is_installed(self) -> bool:
        return all(shutil.which(tool) for tool in ("blender", "codex", "litellm"))

    def ensure_ready(self, hf_home: str) -> None:
        missing = [tool for tool in ("blender", "codex", "litellm") if not shutil.which(tool)]
        if missing:
            raise RuntimeError(
                f"Model '{self.KEY}' needs {', '.join(missing)} on PATH and this image does not carry "
                f"them. Each model has its own image built from Dockerfile.{self.KEY}; the Batch job "
                f"definition selected for this run points at a different one.")
        if not TOOLS_SOURCE.is_dir():
            raise RuntimeError(f"The agent's Blender tools are missing from the image: {TOOLS_SOURCE}")
        Path(hf_home).mkdir(parents=True, exist_ok=True)
        # The weights serve only a refit's relief, which records itself as skipped when the checkpoint is
        # not in the cache, so a fetch that fails is a warning and never the end of a run.
        try:
            self.prefetch_weights(hf_home)
        except Exception as e:
            logger.warning(f"Could not fetch {', '.join(self.HF_REPOS)}, the refit's depth model; the run goes "
                           f"ahead, and a refit without the weights skips its relief: {e}")

    # ------------------------------------------------------------------ workspace

    def _fresh_root(self) -> Dict[str, Path]:
        """Wipe and recreate the agent's directories. Nothing survives from a previous run."""
        if AGENT_ROOT.exists():
            shutil.rmtree(AGENT_ROOT, ignore_errors=True)
        dirs = {
            "root": AGENT_ROOT,
            "workspace": AGENT_ROOT / "workspace",
            "codex_home": AGENT_ROOT / "codex_home",
            "bridge": AGENT_ROOT / "bridge",
            "home": AGENT_ROOT / "home",
        }
        for path in dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        for sub in ("refs", "start", "tools", "scripts", "renders", "textures", "output", "requests"):
            (dirs["workspace"] / sub).mkdir(exist_ok=True)
        return dirs

    @staticmethod
    def _stage_references(workspace: Path, prepared: Sequence[str], originals: Sequence[str]) -> List[str]:
        """Copy the cutouts and the originals into refs/ with names that say which is which.

        The cutout keeps its stem, which carries the view name when there is one, so ``cat_front`` stays
        ``cat_front_cutout.png`` and the agent can pair it with ``cat_front_original.jpg``.
        """
        staged = []
        refs = workspace / "refs"
        for path in prepared:
            source = Path(path)
            stem = source.stem
            for suffix in ("_nobg_framed", "_nobg", "_framed"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
            target = refs / f"{stem}_cutout{source.suffix.lower() or '.png'}"
            shutil.copy2(source, target)
            staged.append(str(target))
        for path in originals:
            source = Path(path)
            target = refs / f"{source.stem}_original{source.suffix.lower()}"
            if not target.exists():
                shutil.copy2(source, target)
                staged.append(str(target))
        return staged

    # ------------------------------------------------------------------ bridge

    def _start_bridge(self, bridge_dir: Path, model_id: str, region: str, port: int, key: str,
                      env: dict) -> subprocess.Popen:
        config_path = bridge_dir / "config.yaml"
        config_path.write_text(json.dumps(litellm_config(model_id, region)), encoding="utf-8")
        log_path = bridge_dir / "bridge.log"
        bridge_env = dict(env)
        bridge_env.update({
            "LITELLM_MASTER_KEY": key,
            # Read by bridge_image_hook at import: a model id LiteLLM's map lacks is registered from a
            # known sibling, or the bridge drops the agent's tools as "unsupported" without a word.
            "BRIDGE_MODEL_ID": model_id,
            "AWS_REGION_NAME": region,
            "PYTHONUTF8": "1",
            "LITELLM_LOG": "INFO",
            # Where the proxy finds the pre-call hook named in its configuration.
            "PYTHONPATH": str(TOOLS_SOURCE) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
        })
        process = subprocess.Popen(  # nosemgrep: dangerous-subprocess-use-audit
            ["litellm", "--config", str(config_path), "--host", "127.0.0.1", "--port", str(port)],
            stdout=open(log_path, "w", encoding="utf-8"), stderr=subprocess.STDOUT, env=bridge_env,
        )  # nosemgrep: dangerous-subprocess-use-audit
        return process

    @staticmethod
    def _wait_for_bridge(port: int, process: subprocess.Popen, log_path: Path, timeout: int = 120) -> None:
        import urllib.request

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"The model bridge exited with code {process.returncode} before it was ready. "
                    f"Last of its log:\n{_tail(log_path)}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/liveliness", timeout=3):
                    return
            except Exception:
                time.sleep(2)
        raise RuntimeError(f"The model bridge did not become ready within {timeout}s:\n{_tail(log_path)}")

    # ------------------------------------------------------------------ run

    def generate(self, image_path: str, work_dir: str, params: dict,
                 extra_images=(), source_images=(), starting_mesh: Optional[str] = None,
                 ) -> GenerationResult:
        mode = resolve_mode(params)
        if mode == "agent":
            return self._generate_agent(image_path, work_dir, params, extra_images, source_images,
                                        starting_mesh)
        if mode == "refit-agent":
            # Checked before the refit spends its minutes rather than after.
            validate_agent_settings(params)
        refit = self._refit(image_path, work_dir, params, extra_images, starting_mesh)
        if mode == "refit":
            return self._refit_result(work_dir, refit, mode)
        handover = refit["handover"]
        # Any failure of the agent, whatever its type, delivers the refit's result; the settings it could
        # refuse were checked above, before the refit ran.
        try:
            result = self._generate_agent(image_path, work_dir, params, extra_images, source_images,
                                          handover, refit=refit)
        except Exception as e:
            logger.warning(f"The agent failed after the refit, so the refit's result is delivered: {e}")
            delivered = self._refit_result(work_dir, refit, mode)
            # The whole message is in the log line above. The note is cut because an agent's failure carries
            # its event digest and log tails, and the run report is read onto the execution record, where an
            # oversized report fails the run that the refit has just delivered.
            delivered.notes["blenderagentRefitAgentFailure"] = str(e)[:2000]
            delivered.diagnostics += list(getattr(e, "diagnostics", []))
            return delivered
        result.notes.update(self._refit_notes(refit, mode))
        result.diagnostics = list(result.diagnostics) + refit["diagnostics"]
        return result

    def _refit(self, image_path: str, work_dir: str, params: dict, extra_images,
               starting_mesh: Optional[str]) -> dict:
        """The scripted refit, in a workspace of its own under the job's working directory."""
        settings = resolve_refit_settings(params)
        if not starting_mesh:
            raise ValueError(
                "BLENDERAGENT_MODE refit fits an existing mesh to its photograph, and this run supplied no "
                "mesh. Select another model's output alongside the photograph, or run it after one.")
        if Path(starting_mesh).suffix.lower() not in STARTING_MESH_EXTENSIONS:
            raise ValueError(
                f"A starting mesh must be one of {', '.join(STARTING_MESH_EXTENSIONS)}, not "
                f"{Path(starting_mesh).suffix}")
        photographs = [p for p in [image_path, *(extra_images or ())] if p and Path(p).is_file()]
        if not photographs:
            raise ValueError("BLENDERAGENT_MODE refit fits the mesh to a photograph, and this run supplied none")
        # The refit fits to the first photograph, and reads the subject's outline off its cutout.
        blenderagent_refit.require_cutout(photographs[0])

        workspace = Path(work_dir) / "blenderagent_refit"
        shutil.rmtree(workspace, ignore_errors=True)
        for sub in ("refs", "start", "refit", "textures", "tools"):
            (workspace / sub).mkdir(parents=True, exist_ok=True)
        for source in TOOLS_SOURCE.iterdir():
            if source.suffix == ".py":
                shutil.copy2(source, workspace / "tools" / source.name)
        (cutout,) = self._stage_references(workspace, photographs[:1], ())
        start = workspace / "start" / Path(starting_mesh).name
        shutil.copy2(starting_mesh, start)

        def estimate_depth(cutout_path: Path, out_path: Path) -> dict:
            checkpoint = Path(self.snapshot_dir(blenderagent_refit.MOGE_REPO)) / blenderagent_refit.MOGE_CHECKPOINT
            return blenderagent_refit.estimate_front_depth(cutout_path, out_path, checkpoint)

        logger.info(f"Refitting {start.name} to {Path(cutout).name}: {json.dumps(settings)}")
        return blenderagent_refit.run_refit(
            workspace, Path(cutout).relative_to(workspace).as_posix(), start.relative_to(workspace).as_posix(),
            settings, blenderagent_refit.blender_runner(), estimate_depth=estimate_depth)

    @staticmethod
    def _refit_notes(refit: dict, mode: str) -> Dict[str, str]:
        return {"blenderagentRunMode": mode,
                "blenderagentRefit": json.dumps(refit["summary"], separators=(",", ":"))}

    def _refit_result(self, work_dir: str, refit: dict, mode: str) -> GenerationResult:
        """The refit's mesh and baked colour as this model's output."""
        out_dir = self.output_dir(work_dir)
        result_mesh = out_dir / "blenderagent_refit.glb"
        shutil.copy2(refit["mesh"], result_mesh)
        texture_paths = []
        for image in refit["textures"]:
            target = out_dir / f"blenderagent_refit_{Path(image).name}"
            shutil.copy2(image, target)
            texture_paths.append(str(target))
        notes = self.base_notes()
        notes.update(self._refit_notes(refit, mode))
        notes["generatedMesh"] = result_mesh.name
        return GenerationResult(mesh_path=str(result_mesh), texture_paths=texture_paths, notes=notes,
                                diagnostics=list(refit["diagnostics"]))

    def _generate_agent(self, image_path: str, work_dir: str, params: dict,
                        extra_images=(), source_images=(), starting_mesh: Optional[str] = None,
                        refit: Optional[dict] = None) -> GenerationResult:
        out_dir = self.output_dir(work_dir)
        model_id, reasoning = validate_agent_settings(params)
        # The region comes from the execution environment, with no fallback: AWS_REGION is always set by
        # the Batch job definition, and a literal default names a Region that may not exist in the
        # deployment's partition.
        region = os.environ["AWS_REGION"]

        max_minutes = int(setting(params, "BLENDERAGENT_MAX_MINUTES", 45))
        max_iterations = int(setting(params, "BLENDERAGENT_MAX_ITERATIONS", 12))
        render_engine = str(setting(params, "BLENDERAGENT_RENDER_ENGINE", "CYCLES")).upper()
        render_samples = int(setting(params, "BLENDERAGENT_RENDER_SAMPLES", 32))
        guidance = str(setting(params, "BLENDERAGENT_GUIDANCE", "")).strip()
        bridge_port = int(setting(params, "BLENDERAGENT_BRIDGE_PORT", 4000))
        target_size = float(setting(params, "TARGET_SIZE_MM", 0) or 0)

        dirs = self._fresh_root()
        workspace = dirs["workspace"]

        # References and the optional starting mesh, into the workspace.
        prepared = [image_path] + [p for p in (extra_images or ()) if p]
        prepared = [p for p in prepared if p and Path(p).is_file()]
        references = self._stage_references(workspace, prepared, [p for p in (source_images or ()) if p])
        staged_mesh = None
        if starting_mesh:
            if Path(starting_mesh).suffix.lower() not in STARTING_MESH_EXTENSIONS:
                raise ValueError(
                    f"A starting mesh must be one of {', '.join(STARTING_MESH_EXTENSIONS)}, not "
                    f"{Path(starting_mesh).suffix}")
            staged_mesh = workspace / "start" / Path(starting_mesh).name
            shutil.copy2(starting_mesh, staged_mesh)
        if not references and not staged_mesh:
            raise ValueError("The agent needs at least one reference image or a starting mesh")

        # Tools and instructions.
        for source in TOOLS_SOURCE.iterdir():
            if source.suffix == ".py":
                shutil.copy2(source, workspace / "tools" / source.name)
        shutil.copy2(TOOLS_SOURCE / "AGENTS.md", workspace / "AGENTS.md")
        if refit:
            # What the refit measured, its baked result and the mesh it started from, beside the hand-over in
            # start/.
            for relative, source in refit["files"].items():
                (workspace / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, workspace / relative)

        brief = {
            "mode": "refine" if staged_mesh else "build",
            "references": [str(Path(p).relative_to(workspace)) for p in references],
            "startingMesh": str(staged_mesh.relative_to(workspace)) if staged_mesh else None,
            "guidance": guidance or None,
            "targetSizeMm": target_size or None,
            "maxIterations": max_iterations,
            "maxMinutes": max_minutes,
            "renderEngine": render_engine,
            "conventions": {"up": "+Z", "front": "-Y", "restOn": "Z=0", "units": "metres"},
        }
        if refit:
            brief["refit"] = refit["summary"]
        (workspace / "brief.json").write_text(json.dumps(brief, indent=2), encoding="utf-8")
        (workspace / "notes.md").write_text("# Notes\n\n", encoding="utf-8")

        # The bridge, then Codex.
        bridge_key = "sk-" + secrets.token_hex(24)
        bridge_key_env = "BLENDERAGENT_BRIDGE_KEY"
        base_env = {k: v for k, v in os.environ.items() if k != "HF_TOKEN"}
        bridge = self._start_bridge(dirs["bridge"], model_id, region, bridge_port, bridge_key, base_env)
        started = time.monotonic()
        broker = None
        try:
            self._wait_for_bridge(bridge_port, bridge, dirs["bridge"] / "bridge.log")
            logger.info(f"Model bridge ready on 127.0.0.1:{bridge_port} -> {model_id}")
            # The agent's one route to an image model: request files in its workspace, served here with
            # the task role. Its own commands keep having no network and no credentials.
            broker = ImageEditBroker(workspace, region, enabled=image_editing_enabled())
            broker.start()

            (dirs["codex_home"] / "config.toml").write_text(
                codex_config(f"http://127.0.0.1:{bridge_port}/v1", bridge_key_env, reasoning),
                encoding="utf-8")

            agent_env = dict(base_env)
            agent_env.update({
                "CODEX_HOME": str(dirs["codex_home"]),
                bridge_key_env: bridge_key,
                # Blender and anything else that writes to a home directory land inside the writable
                # root rather than in a real home the sandbox would refuse.
                "HOME": str(dirs["home"]),
                "XDG_CONFIG_HOME": str(workspace / ".config"),
                "XDG_CACHE_HOME": str(workspace / ".cache"),
                # Blender writes a session directory under the system temp path, which the sandbox denies
                # -- its log showed the fallback to /tmp. Pointing TMPDIR into the workspace keeps that
                # inside the one writable root. The sandbox does not ADD this path as a writable root
                # (exclude_tmpdir_env_var); it does not need to, since the workspace already is one.
                "TMPDIR": str(workspace / ".tmp"),
                "TMP": str(workspace / ".tmp"),
                "TEMP": str(workspace / ".tmp"),
                "AGENT_WORKSPACE": str(workspace),
                # Where agent_gate.py finds the pipeline's own gate and silhouette code to run on a model.
                "PIPELINE_CODE_DIR": str(Path(__file__).resolve().parents[1]),
                "AGENT_RENDER_ENGINE": render_engine,
                "AGENT_RENDER_SAMPLES": str(render_samples),
                "PYTHONUTF8": "1",
            })
            # The home the sandbox will not let the agent write to is still where HOME points for the
            # bridge; the agent gets its own.
            (workspace / ".config").mkdir(exist_ok=True)
            (workspace / ".cache").mkdir(exist_ok=True)
            (workspace / ".tmp").mkdir(exist_ok=True)

            prompt_images = [p for p in references if p.endswith("_cutout.png")][:MAX_PROMPT_IMAGES]
            if len(prompt_images) < MAX_PROMPT_IMAGES:
                prompt_images += [p for p in references if p not in prompt_images][
                    : MAX_PROMPT_IMAGES - len(prompt_images)]

            # The prompt goes on STDIN, named by the trailing "-", never as a positional argument: Codex's
            # --image flag takes a comma-separated LIST, so a positional prompt placed after it is parsed as
            # one more image path and Codex then reads an empty stdin and exits without doing anything.
            # Measured before the first live run.
            command = ["codex", "exec", "--cd", str(workspace), "--sandbox", SANDBOX_MODE,
                       "--skip-git-repo-check", "--ephemeral", "--json",
                       "--output-last-message", str(dirs["root"] / "final_message.md")]
            for image in prompt_images:
                command += ["--image", image]
            command.append("-")
            prompt = task_prompt(brief)
            (dirs["root"] / "task_prompt.md").write_text(prompt, encoding="utf-8")

            events_path = dirs["root"] / "agent_events.jsonl"
            timeout_seconds = max_minutes * 60 + 300
            logger.info(
                f"Starting the agent: model={model_id}, sandbox={SANDBOX_MODE}, reasoning={reasoning}, "
                f"budget={max_iterations} iterations / {max_minutes} min, "
                f"{len(references)} reference(s), starting mesh={'yes' if staged_mesh else 'no'}")
            with open(events_path, "w", encoding="utf-8") as events:
                process = subprocess.Popen(  # nosemgrep: dangerous-subprocess-use-audit
                    command, cwd=str(workspace), env=agent_env, stdout=events, stdin=subprocess.PIPE,
                    stderr=open(dirs["root"] / "agent_stderr.log", "w", encoding="utf-8"),
                )  # nosemgrep: dangerous-subprocess-use-audit
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.close()
                timed_out = False
                try:
                    process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    logger.warning(
                        f"The agent exceeded its budget ({timeout_seconds}s); stopping it and taking "
                        f"whatever it exported")
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        process.kill()
            exit_code = process.returncode
        finally:
            if broker is not None:
                broker.stop()
                broker.join(timeout=10)
                if broker.served:
                    logger.info(f"Image edits served for the agent: {broker.served}")
            bridge.terminate()
            try:
                bridge.wait(timeout=20)
            except subprocess.TimeoutExpired:
                bridge.kill()
        elapsed = time.monotonic() - started

        # The deliverable, checked as a file rather than believed from the message.
        mesh_path = workspace / "output" / "model.glb"
        summary = _summarise_events(events_path)
        done = _read_json(workspace / "output" / "DONE.json")
        if not mesh_path.is_file() or mesh_path.stat().st_size == 0:
            # The agent's record is collected BEFORE raising, and the raise carries a digest of it. The
            # first live failure taught this: the adapter raised with only the stderr tail, which held one
            # line, and the event stream that explained the failure died with the container.
            diagnostics = self._collect_diagnostics(workspace, dirs["root"], out_dir / "agent_run", done)
            raise AgentRunFailed(
                f"The agent finished (exit {exit_code}{', timed out' if timed_out else ''}) after "
                f"{elapsed:.0f}s without writing output/model.glb. {summary.get('commands', 0)} commands "
                f"were run over {summary.get('turns', 0)} turn(s).\n"
                f"What the event stream recorded:\n{_failure_digest(events_path)}\n"
                f"Last of stderr:\n{_tail(dirs['root'] / 'agent_stderr.log', 15)}\n"
                f"Final message:\n{_tail(dirs['root'] / 'final_message.md', 30)}",
                diagnostics=diagnostics)

        # Copy the result and the evidence out of the workspace before anything else touches it.
        result_mesh = out_dir / "blenderagent.glb"
        shutil.copy2(mesh_path, result_mesh)
        # The baked colour is embedded in the glTF, and also kept as a raw image beside it: when the
        # cleanup cannot carry the texture across a topology change, the raw file is what still ships.
        texture_paths = []
        for image in sorted((workspace / "textures").glob("*.png")):
            target = out_dir / f"blenderagent_{image.name}"
            shutil.copy2(image, target)
            texture_paths.append(str(target))
        diagnostics = self._collect_diagnostics(workspace, dirs["root"], out_dir / "agent_run", done)

        notes = self.base_notes()
        notes.update({
            "blenderagentModelId": model_id,
            "blenderagentMode": brief["mode"],
            "blenderagentReferences": str(len(references)),
            "blenderagentStartingMesh": Path(starting_mesh).name if starting_mesh else "",
            "blenderagentSandbox": SANDBOX_MODE,
            "blenderagentReasoningEffort": reasoning,
            "blenderagentExitCode": str(exit_code),
            "blenderagentTimedOut": str(timed_out).lower(),
            "blenderagentElapsedSeconds": f"{elapsed:.1f}",
            "blenderagentCommandsRun": str(summary.get("commands", 0)),
            "blenderagentTurns": str(summary.get("turns", 0)),
            "blenderagentInputTokens": str(summary.get("inputTokens", 0)),
            "blenderagentOutputTokens": str(summary.get("outputTokens", 0)),
            "blenderagentStatus": str((done or {}).get("status", "unknown")),
            "blenderagentIterations": str((done or {}).get("iterations", "")),
            "blenderagentSelfAssessment": json.dumps((done or {}).get("selfAssessment", {})),
            "blenderagentKnownDeviations": json.dumps((done or {}).get("knownDeviations", [])),
            "generatedMesh": result_mesh.name,
        })
        logger.info(
            f"Blender agent produced {result_mesh} in {elapsed:.0f}s: status="
            f"{notes['blenderagentStatus']}, {summary.get('commands', 0)} commands, "
            f"{summary.get('inputTokens', 0)}/{summary.get('outputTokens', 0)} tokens in/out")
        return GenerationResult(mesh_path=str(result_mesh), texture_paths=texture_paths, notes=notes,
                                diagnostics=diagnostics)

    @staticmethod
    def _collect_diagnostics(workspace: Path, root: Path, target: Path, done) -> List[str]:
        """The agent's own record of its work, kept so the execution shows HOW the model was made.

        The final renders, the notes, the completion record, the scripts and the event stream. Not the
        whole workspace: a .blend and its textures can be hundreds of megabytes, and the mesh already
        travels as the output.
        """
        target.mkdir(parents=True, exist_ok=True)
        collected = []

        def take(source: Path, name: Optional[str] = None):
            if source.is_file():
                destination = target / (name or source.name)
                shutil.copy2(source, destination)
                collected.append(str(destination))

        take(workspace / "output" / "DONE.json")
        take(workspace / "notes.md")
        take(workspace / "brief.json")
        take(root / "final_message.md")
        take(root / "agent_events.jsonl")
        take(root / "agent_stderr.log")
        take(workspace / "output" / "model_export_check.json")
        for script in sorted((workspace / "scripts").glob("*.py")):
            take(script, f"scripts_{script.name}")
        # The final renders, chosen from DONE.json when it names them, otherwise the newest set.
        renders = [workspace / r for r in (done or {}).get("finalRenders", []) if isinstance(r, str)]
        if not any(r.is_file() for r in renders):
            all_renders = sorted((workspace / "renders").glob("*.png"), key=lambda p: p.stat().st_mtime)
            renders = all_renders[-6:]
        for render in renders:
            take(render, f"render_{render.name}")
        return collected


class AgentRunFailed(RuntimeError):
    """The agent ended without a deliverable. Carries the files that explain why, for upload."""

    def __init__(self, message: str, diagnostics=None):
        super().__init__(message)
        self.diagnostics = list(diagnostics or [])


def _failure_digest(events_path: Path, limit: int = 12) -> str:
    """The parts of Codex's event stream that say what went wrong, as text for a log line.

    Error items and turn failures first, then the last few commands with their exit codes and the tail of
    their output. Everything else in the stream is progress, and a failure digest that includes it buries
    the one line that matters.
    """
    errors, commands = [], []
    try:
        for line in Path(events_path).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = str(event.get("type", ""))
            item = event.get("item") or {}
            if kind == "turn.failed" or kind == "error":
                errors.append(f"[{kind}] {json.dumps(event.get('error') or event.get('message') or event)[:400]}")
            elif kind == "item.completed" and item.get("type") == "error":
                errors.append(f"[item error] {str(item.get('message', ''))[:400]}")
            elif kind == "item.completed" and item.get("type") == "command_execution":
                output = str(item.get("aggregated_output") or item.get("output") or "")
                commands.append(
                    f"$ {str(item.get('command', ''))[:160]}\n    exit={item.get('exit_code')} "
                    f"status={item.get('status')}\n    {output[-300:].strip()}")
    except OSError:
        return "(no event stream)"
    parts = errors[-limit:] + commands[-4:]
    return "\n".join(parts) if parts else "(the event stream holds no errors and no commands)"


def _tail(path: Path, lines: int = 40) -> str:
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])
    except OSError:
        return "(no log)"


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _summarise_events(path: Path) -> dict:
    """Counts out of Codex's JSONL event stream, tolerant of a shape this version does not know."""
    summary = {"commands": 0, "turns": 0, "inputTokens": 0, "outputTokens": 0}
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = str(event.get("type", ""))
            item = event.get("item") or {}
            if kind == "item.completed" and item.get("type") == "command_execution":
                summary["commands"] += 1
            if kind == "turn.completed":
                summary["turns"] += 1
                usage = event.get("usage") or {}
                summary["inputTokens"] += int(usage.get("input_tokens", 0) or 0)
                summary["outputTokens"] += int(usage.get("output_tokens", 0) or 0)
    except OSError:
        pass
    return summary
