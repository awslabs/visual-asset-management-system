# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The scripted refit's orchestration, with Blender replaced by a runner that records what it was asked, and
the Blender agent's run modes that start it.

What the chain runs, in what order and with which settings, what it skips and why, and what a failing step
leaves behind decide whether a refit run is legible before it costs a GPU instance. Which mode a run takes,
what it refuses before Blender starts, and what an agent run after a refit is handed decide the same for the
adapter. The Blender steps themselves are exercised in `test_blender_tools_live.py`.
"""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from models import blenderagent, blenderagent_refit
from models.base import GenerationResult
from models.blenderagent import BlenderAgentAdapter, resolve_mode, resolve_refit_settings, task_prompt
from models.blenderagent_refit import RefitFailed, run_refit

pytestmark = pytest.mark.unit

_SETTINGS = {"rebuild": "voxel", "resolution": 0.004, "outline_fit": True, "relief": True,
             "relief_strength": 0.01, "retopo": "decimate", "faces": 200000, "min_volume_retention": 0.5}
# What the leak check finds before it repairs, as bl_refit.py's `check` records it.
_CHECK_FOUND = {"boundaryEdges": 6, "nonManifoldEdges": 0, "nonManifoldVertices": 1, "pieces": 2,
                "signedVolume": 0.0123, "selfIntersections": 4}


class _FakeBlender:
    """Answers each step as bl_refit.py would: the report it records, the files it writes."""

    def __init__(self, registration_iou=0.88, fail_step=None, hang_step=None):
        self.calls = []
        self.registration_iou = registration_iou
        self.fail_step, self.hang_step = fail_step, hang_step

    def _record(self, workspace, name, data):
        path = workspace / "refit" / "refit_report.json"
        doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        doc.setdefault("steps", {})[name] = data
        path.write_text(json.dumps(doc), encoding="utf-8")

    def __call__(self, workspace, script, args, blend=None):
        name = Path(script).stem if script != "tools/bl_refit.py" else args[0]
        self.calls.append((name, list(args), blend))
        if name == self.hang_step:
            raise subprocess.TimeoutExpired(["blender"], 1800)
        if name == self.fail_step:
            return 1, f"Traceback\n  {name} went wrong\nError: {name} failed"
        if name == "register":
            self._record(workspace, "register", {"iou": self.registration_iou, "azimuth": 45, "elevation": 0})
        elif name == "overlay":
            final = "--final" in args
            self._record(workspace, "after" if final else "before", {"iou": 0.95 if final else 0.88})
            Path(workspace, args[args.index("--out") + 1]).write_bytes(b"png")
        elif name == "fit-outline":
            self._record(workspace, "fit-outline", {"iou": [0.88, 0.93, 0.95], "stop": "converged"})
        elif name == "check":
            self._record(workspace, "check", {"found": _CHECK_FOUND, "leaks": False,
                                              "repairs": ["filled the holes along 6 open edges"]})
        elif name == "bl_bake":
            Path(workspace, "textures", "baked_basecolor.png").write_bytes(b"png")
        elif name == "bl_export":
            Path(workspace, args[args.index("--out") + 1]).write_bytes(b"glTF")
        return 0, f"{name}: ok"


def _workspace(tmp_path):
    for sub in ("refs", "start", "refit", "textures", "tools"):
        (tmp_path / sub).mkdir()
    (tmp_path / "refs" / "cat_cutout.png").write_bytes(b"png")
    (tmp_path / "start" / "cat.glb").write_bytes(b"glTF")
    return tmp_path


def _names(runner):
    return [name for name, _, _ in runner.calls]


def _depth_ok(cutout, out):
    out.write_bytes(b"npz")
    return {"validPixels": 1000, "metricWidth": 0.4}


class TestTheChain:
    def test_every_step_runs_in_order_and_the_settings_reach_the_tools(self, tmp_path):
        workspace, runner = _workspace(tmp_path), _FakeBlender()
        refit = run_refit(workspace, "refs/cat_cutout.png", "start/cat.glb", dict(_SETTINGS, faces=150000),
                          runner, estimate_depth=_depth_ok)
        assert _names(runner) == ["bl_import", "register", "overlay", "rebuild", "fit-outline", "relief", "retopo",
                                  "check", "colour", "overlay", "unturn", "bl_export", "bl_project_photo", "bl_bake",
                                  "unturn", "bl_export"]
        args = {name: a for name, a, _ in runner.calls}
        assert args["rebuild"][args["rebuild"].index("--method") + 1] == "voxel"
        assert args["retopo"][args["retopo"].index("--faces") + 1] == "150000"
        assert args["relief"][args["relief"].index("--depth") + 1] == "refs/front_depth.npz"
        assert args["bl_project_photo"][-2:] == ["--azimuth", "0"], "the registered view is the projection's"
        # The hand-over is turned back and exported from a scene of its own, before the projection and the bake;
        # every other step works on scene.blend.
        handover_unturn, handover_export = runner.calls[10], runner.calls[11]
        assert handover_unturn == ("unturn", ["unturn", "--save", "handover.blend"], "scene.blend")
        assert handover_export == ("bl_export", ["--out", "refit/refit_handover.glb"], "handover.blend")
        assert runner.calls[0][2] is None
        assert all(blend == "scene.blend" for _, _, blend in runner.calls[1:11] + runner.calls[12:])
        assert refit["mesh"].endswith("refit.glb") and refit["textures"][0].endswith("baked_basecolor.png")
        assert refit["handover"] == str(workspace / "refit" / "refit_handover.glb")

    def test_the_report_carries_the_settings_the_timings_and_the_summary(self, tmp_path):
        workspace = _workspace(tmp_path)
        refit = run_refit(workspace, "refs/cat_cutout.png", "start/cat.glb", _SETTINGS, _FakeBlender(),
                          estimate_depth=_depth_ok)
        report = json.loads((workspace / "refit" / "refit_report.json").read_text(encoding="utf-8"))
        assert report["settings"] == _SETTINGS and report["skipped"] == []
        assert "00_depth" in report["timings"] and "16_export" in report["timings"]
        assert report["depth"]["validPixels"] == 1000
        summary = refit["summary"]
        assert summary["registrationIou"] == 0.88 and summary["finalIou"] == 0.95
        assert summary["outlineIou"] == [0.88, 0.93, 0.95] and summary["leaks"] is False
        assert summary["leakFindings"] == _CHECK_FOUND
        assert summary["repairs"] == ["filled the holes along 6 open edges"]
        json.dumps(summary)

    def test_an_agent_run_after_it_receives_the_report_the_overlays_the_depth_and_the_source(self, tmp_path):
        workspace = _workspace(tmp_path)
        refit = run_refit(workspace, "refs/cat_cutout.png", "start/cat.glb", _SETTINGS, _FakeBlender(),
                          estimate_depth=_depth_ok)
        assert set(refit["files"]) == {"refit/refit_report.json", "refit/refit.glb", "refit/source.glb",
                                       "refit/refit_front_before.png", "refit/refit_front_after.png",
                                       "refs/front_depth.npz"}
        assert all(Path(p).is_file() for p in refit["files"].values())

    def test_the_diagnostics_are_the_refit_record_and_not_the_mesh(self, tmp_path):
        workspace = _workspace(tmp_path)
        refit = run_refit(workspace, "refs/cat_cutout.png", "start/cat.glb", _SETTINGS, _FakeBlender(),
                          estimate_depth=_depth_ok)
        names = {Path(p).name for p in refit["diagnostics"]}
        assert {"refit_report.json", "refit_front_before.png", "refit_front_after.png"} <= names
        assert "refit_blender_01_import.log" in names and "refit_blender_16_export.log" in names
        assert not any(n.endswith((".glb", ".blend", ".npz")) for n in names)


class TestWhatIsSkippedAndWhy:
    def test_switched_off_steps_are_not_run(self, tmp_path):
        runner = _FakeBlender()
        run_refit(_workspace(tmp_path), "refs/cat_cutout.png", "start/cat.glb",
                  dict(_SETTINGS, outline_fit=False, relief=False), runner, estimate_depth=_depth_ok)
        assert "fit-outline" not in _names(runner) and "relief" not in _names(runner)

    def test_a_failed_depth_estimate_skips_the_relief_and_says_why(self, tmp_path):
        def broken(cutout, out):
            raise RuntimeError("CUDA out of memory")

        runner = _FakeBlender()
        refit = run_refit(_workspace(tmp_path), "refs/cat_cutout.png", "start/cat.glb", _SETTINGS, runner,
                          estimate_depth=broken)
        assert "relief" not in _names(runner) and "fit-outline" in _names(runner)
        assert any("CUDA out of memory" in s for s in refit["report"]["skipped"])

    def test_a_poor_registration_skips_the_fit_and_the_relief_and_still_delivers(self, tmp_path):
        runner = _FakeBlender(registration_iou=0.31)
        refit = run_refit(_workspace(tmp_path), "refs/cat_cutout.png", "start/cat.glb", _SETTINGS, runner,
                          estimate_depth=_depth_ok)
        assert "fit-outline" not in _names(runner) and "relief" not in _names(runner)
        assert "bl_export" in _names(runner)
        assert any("0.31" in s for s in refit["summary"]["skipped"])


class TestAFailingStep:
    def test_a_blender_step_that_exits_non_zero_fails_the_run_with_its_log(self, tmp_path):
        workspace = _workspace(tmp_path)
        with pytest.raises(RefitFailed, match="'retopo' exited 1") as raised:
            run_refit(workspace, "refs/cat_cutout.png", "start/cat.glb", _SETTINGS, _FakeBlender(fail_step="retopo"),
                      estimate_depth=_depth_ok)
        assert "retopo failed" in str(raised.value)
        names = {Path(p).name for p in raised.value.diagnostics}
        assert "refit_report.json" in names and any(n.endswith("_retopo.log") for n in names)

    def test_a_blender_step_that_hangs_fails_the_run_naming_the_step(self, tmp_path):
        with pytest.raises(RefitFailed, match="'rebuild' ran past 1800 s"):
            run_refit(_workspace(tmp_path), "refs/cat_cutout.png", "start/cat.glb", _SETTINGS,
                      _FakeBlender(hang_step="rebuild"), estimate_depth=_depth_ok)


class TestTheBlenderRunner:
    def test_blender_resolves_the_steps_relative_paths_in_the_workspace(self, tmp_path, monkeypatch):
        # Blender on Linux reads a relative script or .blend against PWD rather than the process's working
        # directory, and the image's entrypoint shell leaves PWD at the code directory.
        monkeypatch.setenv("PWD", "/opt/ml/code")
        monkeypatch.setenv("HF_TOKEN", "hf_example")
        seen = {}

        def run(command, cwd, env, **_kwargs):
            seen.update(cwd=cwd, env=env)
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(blenderagent_refit.subprocess, "run", run)
        blenderagent_refit.blender_runner("blender")(tmp_path, "tools/bl_refit.py", ["check"], "scene.blend")
        assert seen["cwd"] == str(tmp_path) and seen["env"]["PWD"] == str(tmp_path)
        assert "HF_TOKEN" not in seen["env"]


class TestTheDepthEstimate:
    def test_only_the_subjects_finite_pixels_are_valid_and_the_width_is_robust(self):
        depth = np.full((20, 20), 2.0)
        depth[0, 0] = np.inf
        points = np.zeros((20, 20, 3))
        points[..., 0] = np.linspace(-0.5, 0.5, 20)[None, :]
        alpha = np.zeros((20, 20), dtype=bool)
        alpha[:, 5:15] = True
        estimate = blenderagent_refit.depth_estimate(depth, np.ones((20, 20), dtype=bool), points, alpha)
        assert estimate["mask"].sum() == 200 and not estimate["mask"][:, :5].any()
        assert np.isfinite(estimate["depth"]).all()
        assert 0.4 < float(estimate["metricWidth"]) < 0.5

    def test_an_estimate_that_misses_the_subject_is_refused(self):
        with pytest.raises(RuntimeError, match="covers 0"):
            blenderagent_refit.depth_estimate(np.ones((20, 20)), np.zeros((20, 20), dtype=bool),
                                              np.zeros((20, 20, 3)), np.ones((20, 20), dtype=bool))


class TestThePhotographMustBeACutout:
    def test_an_opaque_photograph_is_refused_naming_the_setting(self, tmp_path):
        from PIL import Image

        Image.new("RGB", (32, 32), (200, 120, 40)).save(tmp_path / "photo.png")
        with pytest.raises(ValueError, match="BACKGROUND_REMOVAL"):
            blenderagent_refit.require_cutout(tmp_path / "photo.png")
        Image.new("RGBA", (32, 32), (200, 120, 40, 255)).save(tmp_path / "opaque.png")
        with pytest.raises(ValueError, match="BACKGROUND_REMOVAL"):
            blenderagent_refit.require_cutout(tmp_path / "opaque.png")

    def test_a_cutout_passes(self, tmp_path):
        from PIL import Image

        image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        image.paste((200, 120, 40, 255), (8, 8, 24, 24))
        image.save(tmp_path / "cutout.png")
        blenderagent_refit.require_cutout(tmp_path / "cutout.png")


def _cutout_png(path, opaque=False):
    """A photograph as background removal leaves it: a subject on a transparent ground, unless opaque."""
    from PIL import Image

    image = Image.new("RGBA", (64, 64), (120, 90, 60, 255 if opaque else 0))
    if not opaque:
        image.paste((120, 90, 60, 255), (16, 16, 48, 48))
    image.save(path)
    return str(path)


def _fake_run_refit(calls):
    """Stands in for run_refit: records what it was handed and leaves what the chain leaves."""

    def run(workspace, cutout, start_mesh, settings, runner, estimate_depth=None):
        workspace = Path(workspace)
        calls.append({"workspace": workspace, "cutout": cutout, "start": start_mesh, "settings": settings,
                      "estimate_depth": estimate_depth})
        (workspace / "refit" / "refit.glb").write_bytes(b"glb")
        (workspace / "refit" / "refit_handover.glb").write_bytes(b"handover glb")
        (workspace / "textures" / "baked_basecolor.png").write_bytes(b"png")
        report = workspace / "refit" / "refit_report.json"
        report.write_text("{}", encoding="utf-8")
        return {"mesh": str(workspace / "refit" / "refit.glb"),
                "handover": str(workspace / "refit" / "refit_handover.glb"),
                "textures": [str(workspace / "textures" / "baked_basecolor.png")],
                "report": {}, "summary": {"finalIou": 0.95, "skipped": []}, "diagnostics": [str(report)],
                "files": {"refit/refit_report.json": str(report),
                          "refit/refit.glb": str(workspace / "refit" / "refit.glb"),
                          f"refit/source{Path(start_mesh).suffix}": str(workspace / start_mesh)}}

    return run


@pytest.fixture(autouse=True)
def configured_agent_model(monkeypatch):
    """The deployment's Bedrock model id, as the job definition delivers it; a test needing none removes it."""
    monkeypatch.setenv("BLENDERAGENT_BEDROCK_MODEL_ID", "m")
    monkeypatch.setenv("AWS_REGION", "us-west-2")


@pytest.fixture
def refit_calls(monkeypatch):
    """run_refit replaced by the fake, and the agent's bridge made to fail the test if anything starts it."""
    calls = []
    monkeypatch.setattr(blenderagent_refit, "run_refit", _fake_run_refit(calls))

    def no_bridge(*args, **kwargs):
        raise AssertionError("the agent's model bridge was started")

    monkeypatch.setattr(BlenderAgentAdapter, "_start_bridge", no_bridge)
    return calls


def _generate(tmp_path, mode, params=None, photos=None, mesh="sam3d.glb"):
    if photos is None:
        photos = [_cutout_png(tmp_path / "cat_nobg_framed.png")]
    start = None
    if mesh:
        start = tmp_path / mesh
        start.write_bytes(b"mesh")
    return BlenderAgentAdapter().generate(
        photos[0] if photos else None, str(tmp_path / "work"), {"BLENDERAGENT_MODE": mode, **(params or {})},
        extra_images=photos[1:], starting_mesh=str(start) if start else None)


class TestTheRunMode:
    @pytest.mark.parametrize("value, mode", [(None, "agent"), ("", "agent"), ("refit", "refit"),
                                             (" Refit-Agent ", "refit-agent")])
    def test_a_run_is_the_agent_alone_unless_its_template_names_another_mode(self, value, mode):
        assert resolve_mode({} if value is None else {"BLENDERAGENT_MODE": value}) == mode

    def test_an_unknown_mode_is_refused_naming_the_ones_there_are(self):
        with pytest.raises(ValueError, match="agent, refit, refit-agent"):
            resolve_mode({"BLENDERAGENT_MODE": "scripted"})


class TestTheRefitSettings:
    def test_nothing_set_resolves_to_the_documented_defaults(self):
        assert resolve_refit_settings({}) == {
            "rebuild": "none", "resolution": 0.004, "outline_fit": True, "relief": True,
            "relief_strength": 0.01, "retopo": "decimate", "faces": 200000, "min_volume_retention": 0.5}

    def test_the_forms_text_is_read_as_the_values_it_names(self):
        settings = resolve_refit_settings({
            "BLENDERAGENT_REFIT_REBUILD": "Metaball", "BLENDERAGENT_REFIT_RESOLUTION": "0.01",
            "BLENDERAGENT_REFIT_OUTLINE_FIT": "FALSE", "BLENDERAGENT_REFIT_RELIEF": "false",
            "BLENDERAGENT_REFIT_RELIEF_STRENGTH": "0", "BLENDERAGENT_REFIT_RETOPO": "quadriflow",
            "TARGET_FACE_COUNT": "50000", "MIN_VOLUME_RETENTION": "0.8"})
        assert settings == {"rebuild": "metaball", "resolution": 0.01, "outline_fit": False, "relief": False,
                            "relief_strength": 0.0, "retopo": "quadriflow", "faces": 50000,
                            "min_volume_retention": 0.8}

    @pytest.mark.parametrize("key, value", [
        ("BLENDERAGENT_REFIT_REBUILD", "remesh"), ("BLENDERAGENT_REFIT_RETOPO", "instant"),
        ("BLENDERAGENT_REFIT_OUTLINE_FIT", "yes"), ("BLENDERAGENT_REFIT_RELIEF", "1"),
        ("BLENDERAGENT_REFIT_RESOLUTION", "0.5"), ("BLENDERAGENT_REFIT_RESOLUTION", "fine"),
        ("BLENDERAGENT_REFIT_RELIEF_STRENGTH", "-0.01"), ("TARGET_FACE_COUNT", "-1")])
    def test_a_value_the_refit_cannot_use_is_refused_naming_its_key(self, key, value):
        with pytest.raises(ValueError, match=key):
            resolve_refit_settings({key: value})


class TestARefitRun:
    def test_a_refit_runs_the_chain_and_never_the_agent(self, tmp_path, refit_calls, monkeypatch):
        # No model id anywhere: a refit makes no model call, so it does not need one.
        monkeypatch.delenv("BLENDERAGENT_BEDROCK_MODEL_ID", raising=False)
        result = _generate(tmp_path, "refit")
        (call,) = refit_calls
        assert call["workspace"] == tmp_path / "work" / "blenderagent_refit"
        assert (call["workspace"] / "tools" / "bl_refit.py").is_file()
        assert call["cutout"] == "refs/cat_cutout.png" and call["start"] == "start/sam3d.glb"
        assert call["settings"] == resolve_refit_settings({})
        assert Path(result.mesh_path).name == "blenderagent_refit.glb" and Path(result.mesh_path).is_file()
        assert [Path(p).name for p in result.texture_paths] == ["blenderagent_refit_baked_basecolor.png"]
        assert result.notes["blenderagentRunMode"] == "refit"
        assert json.loads(result.notes["blenderagentRefit"]) == {"finalIou": 0.95, "skipped": []}
        assert result.notes["generatedMesh"] == "blenderagent_refit.glb"
        assert result.diagnostics == [str(call["workspace"] / "refit" / "refit_report.json")]

    @pytest.mark.parametrize("mesh, photos, message", [
        (None, "cutout", "supplied no mesh"), ("sam3d.fbx", "cutout", "must be one of"),
        ("sam3d.glb", "none", "supplied none"), ("sam3d.glb", "opaque", "BACKGROUND_REMOVAL")])
    def test_a_refit_without_what_it_fits_is_refused_before_blender_starts(self, tmp_path, refit_calls, mesh,
                                                                           photos, message):
        images = {"cutout": lambda: [_cutout_png(tmp_path / "cat.png")], "none": lambda: [],
                  "opaque": lambda: [_cutout_png(tmp_path / "cat.png", opaque=True)]}[photos]()
        with pytest.raises(ValueError, match=message):
            _generate(tmp_path, "refit", photos=images, mesh=mesh)
        assert refit_calls == []

    def test_several_photographs_fit_to_the_first(self, tmp_path, refit_calls):
        photos = [_cutout_png(tmp_path / "cat_front_nobg.png"), _cutout_png(tmp_path / "cat_side_nobg.png")]
        _generate(tmp_path, "refit", photos=photos)
        (call,) = refit_calls
        assert call["cutout"] == "refs/cat_front_cutout.png"
        assert [p.name for p in (call["workspace"] / "refs").iterdir()] == ["cat_front_cutout.png"]

    def test_the_depth_estimate_reads_moges_checkpoint_from_the_weight_cache(self, tmp_path, refit_calls,
                                                                             monkeypatch):
        _generate(tmp_path, "refit")
        seen = {}

        def snapshot_dir(adapter, repo):
            return str(tmp_path / "hub" / repo.replace("/", "--"))

        def estimate(cutout, out, checkpoint):
            seen.update(cutout=cutout, out=out, checkpoint=checkpoint)
            return {"validPixels": 1}

        monkeypatch.setattr(BlenderAgentAdapter, "snapshot_dir", snapshot_dir)
        monkeypatch.setattr(blenderagent_refit, "estimate_front_depth", estimate)
        assert refit_calls[0]["estimate_depth"](Path("c.png"), Path("d.npz")) == {"validPixels": 1}
        assert seen == {"cutout": Path("c.png"), "out": Path("d.npz"),
                        "checkpoint": tmp_path / "hub" / "Ruicheng--moge-2-vitl-normal" / "model.pt"}

    def test_the_depth_models_weights_are_fetched_before_any_run(self, tmp_path, monkeypatch):
        assert BlenderAgentAdapter.HF_REPOS == ("Ruicheng/moge-2-vitl-normal",)
        fetched = []
        monkeypatch.setattr(blenderagent.shutil, "which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(BlenderAgentAdapter, "prefetch_weights", lambda adapter, home: fetched.append(home))
        BlenderAgentAdapter().ensure_ready(str(tmp_path / "hf"))
        assert fetched == [str(tmp_path / "hf")]


class TestRefitThenAgent:
    @pytest.mark.parametrize("model_id, params", [
        ("m", {"BLENDERAGENT_REASONING_EFFORT": "max"}),
        ("", {})], ids=["reasoning", "no model id"])
    def test_the_agents_settings_are_checked_before_the_refit_spends_its_minutes(self, tmp_path, refit_calls,
                                                                                 monkeypatch, model_id, params):
        monkeypatch.setenv("BLENDERAGENT_BEDROCK_MODEL_ID", model_id)
        with pytest.raises(ValueError):
            _generate(tmp_path, "refit-agent", params)
        assert refit_calls == []

    def test_the_agent_starts_from_the_refits_hand_over_and_both_records_are_kept(self, tmp_path, refit_calls,
                                                                                  monkeypatch):
        handed = {}

        def agent(adapter, image_path, work_dir, params, extra_images, source_images, starting_mesh,
                  refit=None):
            handed.update(starting_mesh=starting_mesh, refit=refit)
            mesh = Path(work_dir) / "agent.glb"
            mesh.write_bytes(b"glb")
            return GenerationResult(mesh_path=str(mesh), notes={"blenderagentStatus": "done"},
                                    diagnostics=["/x/notes.md"])

        monkeypatch.setattr(BlenderAgentAdapter, "_generate_agent", agent)
        result = _generate(tmp_path, "refit-agent", {})
        (call,) = refit_calls
        assert handed["starting_mesh"] == str(call["workspace"] / "refit" / "refit_handover.glb")
        assert handed["refit"]["summary"] == {"finalIou": 0.95, "skipped": []}
        assert result.notes["blenderagentStatus"] == "done"
        assert result.notes["blenderagentRunMode"] == "refit-agent"
        assert json.loads(result.notes["blenderagentRefit"])["finalIou"] == 0.95
        assert result.diagnostics == ["/x/notes.md", str(call["workspace"] / "refit" / "refit_report.json")]

    @pytest.mark.parametrize("failure", [
        blenderagent.AgentRunFailed("the agent wrote no output/model.glb", diagnostics=["/x/events.jsonl"]),
        OSError(28, "No space left on device")], ids=["no deliverable", "os error"])
    def test_when_the_agent_fails_the_refit_is_delivered_with_the_failure_on_record(self, tmp_path,
                                                                                    refit_calls, monkeypatch,
                                                                                    failure):
        def agent(*args, **kwargs):
            raise failure

        monkeypatch.setattr(BlenderAgentAdapter, "_generate_agent", agent)
        result = _generate(tmp_path, "refit-agent", {})
        assert Path(result.mesh_path).name == "blenderagent_refit.glb"
        assert result.notes["blenderagentRunMode"] == "refit-agent"
        assert result.notes["blenderagentRefitAgentFailure"] == str(failure)
        assert result.diagnostics[-1] == (failure.diagnostics[-1] if hasattr(failure, "diagnostics")
                                          else str(refit_calls[0]["workspace"] / "refit" / "refit_report.json"))

    def test_the_failure_on_record_is_bounded(self, tmp_path, refit_calls, monkeypatch):
        def agent(*args, **kwargs):
            raise blenderagent.AgentRunFailed("x" * 5000)

        monkeypatch.setattr(BlenderAgentAdapter, "_generate_agent", agent)
        result = _generate(tmp_path, "refit-agent", {})
        assert result.notes["blenderagentRefitAgentFailure"] == "x" * 2000


class _Stop(BaseException):
    """Raised where the agent's bridge would start, so a test can read the workspace the agent was handed. Not
    an Exception, which the refit-agent fallback would catch and answer by delivering the refit."""


class TestTheAgentIsHandedTheRefit:
    @pytest.fixture
    def workspace(self, tmp_path, monkeypatch):
        monkeypatch.setattr(blenderagent, "AGENT_ROOT", tmp_path / "agent")
        monkeypatch.setattr(blenderagent_refit, "run_refit", _fake_run_refit([]))

        def stop(*args, **kwargs):
            raise _Stop()

        monkeypatch.setattr(BlenderAgentAdapter, "_start_bridge", stop)
        return tmp_path / "agent" / "workspace"

    def _brief(self, tmp_path, workspace, mode):
        with pytest.raises(_Stop):
            _generate(tmp_path, mode, {})
        return json.loads((workspace / "brief.json").read_text(encoding="utf-8"))

    def test_a_refit_agent_run_hands_the_agent_the_refit_and_what_it_started_from(self, tmp_path, workspace):
        brief = self._brief(tmp_path, workspace, "refit-agent")
        assert brief["mode"] == "refine" and Path(brief["startingMesh"]) == Path("start/refit_handover.glb")
        assert (workspace / "start" / "refit_handover.glb").read_bytes() == b"handover glb"
        assert brief["refit"] == {"finalIou": 0.95, "skipped": []}
        assert (workspace / "refit" / "refit_report.json").is_file()
        assert (workspace / "refit" / "refit.glb").read_bytes() == b"glb", "the refit's baked result, for reference"
        assert (workspace / "refit" / "source.glb").read_bytes() == b"mesh"

    def test_an_agent_run_carries_no_refit_in_its_brief_or_its_workspace(self, tmp_path, workspace):
        brief = self._brief(tmp_path, workspace, "agent")
        assert "refit" not in brief and Path(brief["startingMesh"]) == Path("start/sam3d.glb")
        assert not (workspace / "refit").exists()

    def test_the_prompt_points_a_refit_agent_run_at_the_refits_record(self):
        brief = {"mode": "refine", "references": ["refs/cat_cutout.png"], "startingMesh": "start/refit_handover.glb",
                 "maxIterations": 12, "maxMinutes": 45}
        assert "refit/refit_report.json" not in task_prompt(brief)
        assert "refit/refit_report.json" in task_prompt({**brief, "refit": {"finalIou": 0.95}})


class _Log:
    """Stands in for the adapter's logger, keeping its warnings."""

    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)

    def info(self, message):
        pass


class TestAFailedWeightFetch:
    """MoGe-2 serves only the refit's relief. A fetch that fails leaves an agent run as it was, and costs a
    refit its relief, recorded with the reason, rather than the run."""

    @pytest.fixture
    def adapter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(blenderagent.shutil, "which", lambda tool: f"/usr/bin/{tool}")

        def unreachable(adapter, home):
            raise RuntimeError("the network is unreachable")

        monkeypatch.setattr(BlenderAgentAdapter, "prefetch_weights", unreachable)
        # Nothing is in the weight cache either, so the checkpoint cannot be resolved from it.
        monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
        monkeypatch.setattr(blenderagent, "logger", _Log())
        adapter = BlenderAgentAdapter()
        adapter.ensure_ready(str(tmp_path / "hf"))
        return adapter

    def _run(self, adapter, tmp_path, mode):
        start = tmp_path / "sam3d.glb"
        start.write_bytes(b"mesh")
        return adapter.generate(_cutout_png(tmp_path / "cat_nobg_framed.png"), str(tmp_path / "work"),
                                {"BLENDERAGENT_MODE": mode},
                                starting_mesh=str(start))

    def test_the_failure_is_a_warning_that_names_the_repo(self, adapter):
        (warning,) = blenderagent.logger.warnings
        assert "Ruicheng/moge-2-vitl-normal" in warning and "the network is unreachable" in warning

    def test_an_agent_run_still_starts(self, adapter, tmp_path, monkeypatch):
        monkeypatch.setattr(blenderagent, "AGENT_ROOT", tmp_path / "agent")

        def stop(*args, **kwargs):
            raise _Stop()

        monkeypatch.setattr(BlenderAgentAdapter, "_start_bridge", stop)
        with pytest.raises(_Stop):
            self._run(adapter, tmp_path, "agent")

    def test_a_refit_records_its_relief_as_skipped_with_the_reason(self, adapter, tmp_path, monkeypatch):
        runner = _FakeBlender()
        monkeypatch.setattr(blenderagent_refit, "blender_runner", lambda: runner)
        result = self._run(adapter, tmp_path, "refit")
        assert "relief" not in _names(runner) and "bl_export" in _names(runner)
        (skipped,) = json.loads(result.notes["blenderagentRefit"])["skipped"]
        assert skipped.startswith("relief: the depth estimate failed (") and len(skipped) > 40, skipped
