#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The physics setting and the Isaac Lab 3.0 entry points (issue #1669).

``trainingConfig.physics`` selects the engine for training and for evaluation (Isaac Lab's
``physics=<preset>`` override): ``newton_mjwarp``, the default, or ``isaacsim_physx``. These tests
pin the command line each library and engine gets (the argv was read from runs of the stock
``nvcr.io/nvidia/isaac-lab:3.0.0`` image), the configuration parsing, the early refusal of a task and
engine pair the task does not offer (from the real error text of a rejected preset) and
what the uploaded run configuration records.
"""

import importlib.util
import json
import os
from types import SimpleNamespace

import pytest

from utils.training.config import DEFAULT_PHYSICS, PHYSICS_ENGINES, PipelineConfig
from utils.training.task_physics import (
    IsaacLabRun, parse_unknown_preset_error, run_isaac_lab, unknown_preset_message)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CONTAINER_DIR = os.path.dirname(_TESTS_DIR)
_OUTPUT_PREFIX = "s3://run-bucket/pipelines/isaacLab/JOB/output/3f2c9a10/files/"
_LIBRARIES = ("rsl_rl", "rl_games", "skrl")


@pytest.fixture(scope="module")
def main_module():
    spec = importlib.util.spec_from_file_location(
        "isaaclab_container_main_physics", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def error_text():
    with open(os.path.join(_TESTS_DIR, "fixtures", "unknown_preset_error.txt"), encoding="utf-8") as f:
        return f.read()


def _config(**overrides):
    values = dict(job_name="job1", mode="train", task="Isaac-Cartpole", rl_library="rsl_rl",
                  input_s3_path="", output_s3_path=_OUTPUT_PREFIX, num_envs=64, max_iterations=3)
    values.update(overrides)
    return PipelineConfig(**values)


@pytest.mark.unit
class TestPhysicsField:
    def test_the_allowed_engines_and_the_default(self):
        assert PHYSICS_ENGINES == ("newton_mjwarp", "isaacsim_physx")
        assert DEFAULT_PHYSICS == "newton_mjwarp"

    @pytest.mark.parametrize("training", [{}, {"physics": None}, {"physics": ""}, {"physics": "  "}])
    def test_absent_null_or_blank_is_newton(self, training):
        config = PipelineConfig.from_dict({"trainingConfig": training})
        assert config.physics == "newton_mjwarp"

    @pytest.mark.parametrize("value", PHYSICS_ENGINES)
    def test_each_allowed_value_is_kept(self, value):
        config = PipelineConfig.from_dict({"trainingConfig": {"physics": f" {value} "}})
        assert config.physics == value

    @pytest.mark.parametrize("value", ["physx", "ovphysx", "newton_kamino", "NEWTON_MJWARP",
                                       "newton_mjwarp physics=x", 3, True])
    def test_any_other_value_is_rejected_naming_both_engines(self, value):
        with pytest.raises(ValueError, match="physics must be one of newton_mjwarp, isaacsim_physx"):
            PipelineConfig.from_dict({"trainingConfig": {"physics": value}})

    def test_the_task_default_is_the_3_0_cartpole_id(self):
        assert PipelineConfig.from_dict({}).task == "Isaac-Cartpole"

    def test_the_lambda_notes_are_read_and_stay_advisory(self):
        config = PipelineConfig.from_dict({"trainingConfig": {
            "physicsNote": "  trained on PhysX  ", "taskIdRequested": "Isaac-Cartpole-v0"}})
        assert (config.physics_note, config.task_id_requested) == (
            "trained on PhysX", "Isaac-Cartpole-v0")
        assert PipelineConfig.from_dict({"trainingConfig": {"physicsNote": 5}}).physics_note == ""


@pytest.mark.unit
class TestTrainingCommand:
    @pytest.mark.parametrize("physics", PHYSICS_ENGINES)
    @pytest.mark.parametrize("library", _LIBRARIES)
    def test_the_argv_is_the_3_0_train_entry_point_with_physics_last(
            self, main_module, library, physics):
        cmd = main_module.build_training_command(
            _config(rl_library=library, physics=physics), "/tmp/ck")
        assert cmd == ["./isaaclab.sh", "train", "--rl_library", library, "--task", "Isaac-Cartpole",
                       "--num_envs", "64", "--max_iterations", "3", "--viz", "none",
                       f"physics={physics}"]

    def test_headless_is_gone_and_viz_is_none(self, main_module):
        cmd = main_module.build_training_command(_config(), "/tmp/ck")
        assert "--headless" not in cmd
        assert cmd[cmd.index("--viz") + 1] == "none"

    def test_the_seed_goes_before_the_physics_override(self, main_module):
        cmd = main_module.build_training_command(_config(seed=7), "/tmp/ck")
        assert cmd[-3:] == ["--seed", "7", "physics=newton_mjwarp"]

    def test_the_default_engine_is_newton(self, main_module):
        assert main_module.build_training_command(_config(), "/tmp/ck")[-1] == "physics=newton_mjwarp"

    @pytest.mark.parametrize("value", ["physx", "newton_mjwarp; rm -rf /", ""])
    def test_a_value_outside_the_allow_list_never_reaches_the_command(self, main_module, value):
        with pytest.raises(ValueError, match="Unsupported physics"):
            main_module.build_training_command(_config(physics=value), "/tmp/ck")


@pytest.mark.unit
class TestEvaluationCommand:
    @pytest.mark.parametrize("library", _LIBRARIES)
    def test_newton_records_through_newtons_gl_viewer(self, main_module, library):
        cmd = main_module.build_evaluation_command(
            _config(mode="evaluate", rl_library=library, num_envs=4, num_episodes=2,
                    steps_per_episode=100), "/p/policy.pt")
        assert cmd == ["./isaaclab.sh", "play", "--rl_library", library, "--task", "Isaac-Cartpole",
                       "--num_envs", "4", "--checkpoint", "/p/policy.pt", "--video",
                       "--video_length", "200", "--viz", "newton_gl", "physics=newton_mjwarp"]

    @pytest.mark.parametrize("library", _LIBRARIES)
    def test_physx_keeps_the_default_kit_visualizer(self, main_module, library):
        cmd = main_module.build_evaluation_command(
            _config(mode="evaluate", rl_library=library, physics="isaacsim_physx", num_envs=4,
                    num_episodes=2, steps_per_episode=100), "/p/policy.pt")
        assert "--viz" not in cmd
        assert cmd[-1] == "physics=isaacsim_physx"
        assert "--headless" not in cmd

    def test_video_length_still_bounds_the_run(self, main_module):
        cmd = main_module.build_evaluation_command(
            _config(mode="evaluate", num_episodes=3, steps_per_episode=50), "/p/policy.pt")
        assert "--video" in cmd and cmd[cmd.index("--video_length") + 1] == "150"

    def test_a_value_outside_the_allow_list_is_rejected(self, main_module):
        with pytest.raises(ValueError, match="Unsupported physics"):
            main_module.build_evaluation_command(
                _config(mode="evaluate", physics="ovphysx"), "/p/policy.pt")


def _run_result(returncode=0, tail="", timed_out=False):
    return IsaacLabRun(returncode, tail, timed_out)


def _error_without(text, engine):
    """The fixture's option list as a task that does not list ``engine`` would print it."""
    return text.replace("Unknown preset(s): bogus", f"Unknown preset(s): {engine}").replace(
        "  isaacsim_physx, newton_kamino, newton_mjwarp, ovphysx, physx",
        "  isaacsim_physx")


@pytest.mark.unit
class TestUnknownPresetError:
    """The error Isaac Lab 3.0.0 raised for ``physics=bogus`` (fixture: the spike's real output)."""

    def test_the_rejected_and_available_presets_are_read(self, error_text):
        parsed = parse_unknown_preset_error(error_text)
        assert parsed == {"rejected": ["bogus"],
                          "available": ["isaacsim_physx", "newton_kamino", "newton_mjwarp",
                                        "ovphysx", "physx"]}

    def test_other_output_is_not_a_preset_error(self):
        assert parse_unknown_preset_error("RuntimeError: CUDA out of memory") is None
        assert parse_unknown_preset_error("") is None

    def test_the_message_names_the_pipelines_options_for_the_task(self, error_text):
        message = unknown_preset_message("Isaac-Lift-Soft-Franka", "newton_mjwarp",
                                         _error_without(error_text, "newton_mjwarp"),
                                         PHYSICS_ENGINES)
        assert message == (
            "Task Isaac-Lift-Soft-Franka does not offer physics=newton_mjwarp in Isaac Lab 3.0.0. "
            "Options for this task: isaacsim_physx. Set \"Physics engine\" to one of these.")

    def test_a_task_offering_neither_engine_says_what_it_lists(self):
        text = ("ValueError: Unknown preset(s): newton_mjwarp\n\nAvailable presets (grouped by "
                "affected paths):\n\n  newton_kamino\n    -> env.sim.physics\n")
        message = unknown_preset_message("Isaac-Fourbar-Pole-Swingup", "newton_mjwarp", text,
                                         PHYSICS_ENGINES)
        assert "none of the engines this pipeline selects" in message
        assert "newton_kamino" in message

    def test_a_task_with_no_presets_gets_the_porting_hint(self):
        text = "ValueError: Unknown preset(s): newton_mjwarp\n"
        message = unknown_preset_message("My-Env", "newton_mjwarp", text, PHYSICS_ENGINES)
        assert "no physics presets" in message and "ported from Isaac Lab 2.x" in message

    def test_the_message_is_none_for_any_other_failure(self):
        assert unknown_preset_message("T", "newton_mjwarp", "Traceback ... KeyError",
                                      PHYSICS_ENGINES) is None


@pytest.mark.unit
class TestRunMapsThePresetError:
    @pytest.mark.parametrize("mode", ["train", "evaluate"])
    def test_a_rejected_preset_fails_the_run_with_the_readable_message(
            self, main_module, monkeypatch, error_text, mode):
        monkeypatch.setattr(main_module, "run_isaac_lab",
                            lambda cmd, cwd, timeout_seconds=None: _run_result(1, error_text))
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        monkeypatch.setattr(main_module, "setup_checkpoint_dir", lambda config: "/unused")
        monkeypatch.setattr(main_module, "download_policy", lambda s3, config: "/unused/p.pt")
        config = _config(mode=mode, physics="isaacsim_physx", task="Isaac-Lift-Cable-Franka")
        runner = main_module.run_training if mode == "train" else main_module.run_evaluation
        with pytest.raises(RuntimeError, match="does not offer physics=isaacsim_physx"):
            runner(config, None, {})

    def test_another_failure_keeps_the_generic_exit_code_message(self, main_module, monkeypatch):
        monkeypatch.setattr(main_module, "run_isaac_lab",
                            lambda cmd, cwd, timeout_seconds=None: _run_result(1, "boom"))
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        monkeypatch.setattr(main_module, "setup_checkpoint_dir", lambda config: "/unused")
        with pytest.raises(RuntimeError, match="Training failed with exit code 1"):
            main_module.run_training(_config(), None, {})


@pytest.mark.unit
class TestPlayWatchdog:
    def test_the_limit_has_a_floor_a_per_step_allowance_and_a_cap(self, main_module):
        small = main_module.evaluation_time_limit(_config(num_episodes=1, steps_per_episode=10))
        big = main_module.evaluation_time_limit(_config(num_episodes=50, steps_per_episode=1000))
        huge = main_module.evaluation_time_limit(
            _config(num_episodes=10000, steps_per_episode=100000))
        assert small == 1801 and 1800 < small < big <= huge == 27000

    def test_the_play_process_gets_the_limit(self, main_module, monkeypatch):
        seen = {}

        def fake(cmd, cwd, timeout_seconds=None):
            seen["timeout"] = timeout_seconds
            return _run_result(0)

        monkeypatch.setattr(main_module, "run_isaac_lab", fake)
        monkeypatch.setattr(main_module, "download_policy", lambda s3, config: "/p.pt")
        monkeypatch.setattr(main_module, "upload_evaluation_results", lambda *a: None)
        config = _config(mode="evaluate", num_episodes=2, steps_per_episode=100)
        main_module.run_evaluation(config, None, {})
        assert seen["timeout"] == main_module.evaluation_time_limit(config)

    def test_a_run_that_does_not_finish_fails_naming_the_engine_and_the_limit(
            self, main_module, monkeypatch):
        monkeypatch.setattr(main_module, "run_isaac_lab",
                            lambda cmd, cwd, timeout_seconds=None: _run_result(-9, "", True))
        monkeypatch.setattr(main_module, "download_policy", lambda s3, config: "/p.pt")
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        config = _config(mode="evaluate", physics="isaacsim_physx", num_episodes=1,
                         steps_per_episode=10)
        with pytest.raises(RuntimeError, match=r"physics=isaacsim_physx did not finish within 1801"):
            main_module.run_evaluation(config, None, {})

    def test_a_process_that_never_exits_is_killed_at_the_limit(self):
        class Hung:
            returncode = None
            stdout = iter(["starting\n"])
            killed = False

            def poll(self):
                return -9 if self.killed else None

            def kill(self):
                self.killed = True
                self.returncode = -9

            def wait(self):
                return self.returncode

        ticks = iter(range(0, 10000, 2000))
        hung = Hung()
        result = run_isaac_lab(["play"], "/tmp", timeout_seconds=1000,
                               popen=lambda *a, **k: hung, clock=lambda: next(ticks))
        assert result.timed_out and result.returncode == -9 and "starting" in result.tail

    def test_output_is_kept_for_the_error_reader(self):
        result = run_isaac_lab(["python3", "-c", "print('a'); print('b')"], "/tmp")
        assert result.returncode == 0 and result.tail == "a\nb\n" and not result.timed_out


@pytest.mark.unit
class TestRunRecord:
    def test_the_uploaded_config_records_the_resolved_engine_and_the_image_version(
            self, main_module, monkeypatch, tmp_path):
        uploaded = {}

        class S3:
            def upload_file(self, local_path, s3_uri):
                with open(local_path, encoding="utf-8") as f:
                    uploaded[s3_uri] = json.load(f)

        monkeypatch.setenv("ISAAC_LAB_VERSION", "3.0.0")
        main_module.upload_config(S3(), _config(physics="isaacsim_physx"),
                                  {"jobName": "j", "trainingConfig": {"mode": "train"}})
        record = uploaded[f"{_OUTPUT_PREFIX}train-config.json"]
        assert record["trainingConfig"] == {"mode": "train", "physics": "isaacsim_physx"}
        assert record["isaacLabVersion"] == "3.0.0"

    def test_a_job_config_without_a_training_section_still_records_the_engine(
            self, main_module):
        uploaded = {}

        class S3:
            def upload_file(self, local_path, s3_uri):
                with open(local_path, encoding="utf-8") as f:
                    uploaded.update(json.load(f))

        main_module.upload_config(S3(), _config(), {"jobName": "j"})
        assert uploaded["trainingConfig"]["physics"] == "newton_mjwarp"


@pytest.mark.unit
class TestCustomEnvironmentInstall:
    class _S3:
        def client_for_bucket(self, bucket):
            return SimpleNamespace(download_file=lambda *a: None)

    def test_the_install_uses_the_isaac_lab_environment_interpreter(self, main_module, monkeypatch):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(main_module.subprocess, "run", run)
        main_module.download_and_install_custom_environment(
            self._S3(), "s3://b/envs/my_env-1.0-cp312-cp312-linux_x86_64.whl")
        assert calls[0][:4] == ["/opt/isaaclab-venv/bin/python", "-m", "pip", "install"]
        assert "/isaac-sim/python.sh" not in calls[0]

    @pytest.mark.parametrize("name", ["my_env-1.0-cp311-cp311-linux_x86_64.whl",
                                      "my_env-1.0-cp313-cp313-manylinux_2_28_x86_64.whl"])
    def test_a_wheel_built_for_another_python_is_refused_naming_python_3_12(
            self, main_module, name):
        with pytest.raises(ValueError, match="runs Python 3.12"):
            main_module.download_and_install_custom_environment(self._S3(), f"s3://b/envs/{name}")

    @pytest.mark.parametrize("name", ["my_env-1.0-py3-none-any.whl",
                                      "my_env-1.0-cp39-abi3-manylinux_2_28_x86_64.whl",
                                      "my_env-1.0-cp312-cp312-linux_x86_64.whl"])
    def test_a_compatible_wheel_is_installed(self, main_module, monkeypatch, name):
        monkeypatch.setattr(main_module.subprocess, "run",
                            lambda cmd, **kw: SimpleNamespace(returncode=0, stderr=""))
        main_module.download_and_install_custom_environment(self._S3(), f"s3://b/envs/{name}")


@pytest.fixture(scope="module")
def typed_error_text():
    with open(os.path.join(_TESTS_DIR, "fixtures", "typed_preset_error.txt"), encoding="utf-8") as f:
        return f.read()


@pytest.mark.unit
class TestTasksWithoutPhysicsPresets:
    """A task that lists no physics preset rejects both engines (defect D2 of PR #1736); the run
    leaves the selector out and the task runs on the physics it registers."""

    def test_a_task_default_run_has_no_selector_and_no_newton_viewer(self, main_module):
        train = main_module.build_training_command(_config(physics="task-default"), "/tmp/ck")
        assert not any(arg.startswith("physics=") for arg in train)
        play = main_module.build_evaluation_command(
            _config(mode="evaluate", physics="task-default"), "/p/policy.pt")
        assert not any(arg.startswith("physics=") for arg in play) and "--viz" not in play

    def test_the_value_is_accepted_from_the_lambda_only_in_the_config(self):
        config = PipelineConfig.from_dict({"trainingConfig": {"physics": "task-default"}})
        assert config.physics == "task-default"
        assert "task-default" not in PHYSICS_ENGINES

    def test_the_typed_rejection_runs_the_task_again_without_the_selector(
            self, main_module, monkeypatch, typed_error_text, capsys):
        seen = []

        def fake(cmd, cwd, timeout_seconds=None):
            seen.append(cmd)
            return _run_result(1, typed_error_text) if len(seen) == 1 else _run_result(0)

        monkeypatch.setattr(main_module, "run_isaac_lab", fake)
        monkeypatch.setattr(main_module, "setup_checkpoint_dir", lambda config: "/unused")
        monkeypatch.setattr(main_module, "upload_training_results", lambda *a: None)
        config = _config(physics="isaacsim_physx", task="My-Ported-Task")
        main_module.run_training(config, None, {})
        assert seen[0][-1] == "physics=isaacsim_physx"
        assert not any(arg.startswith("physics=") for arg in seen[1])
        assert config.physics == "task-default"
        assert ("Task My-Ported-Task declares no physics presets; it runs on its registered "
                "physics. The physics setting is ignored.") in capsys.readouterr().out

    def test_the_second_failure_is_reported_and_there_is_no_third_run(
            self, main_module, monkeypatch, typed_error_text):
        calls = []

        def fake(cmd, cwd, timeout_seconds=None):
            calls.append(cmd)
            return _run_result(1, typed_error_text)

        monkeypatch.setattr(main_module, "run_isaac_lab", fake)
        monkeypatch.setattr(main_module, "setup_checkpoint_dir", lambda config: "/unused")
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        with pytest.raises(RuntimeError):
            main_module.run_training(_config(physics="newton_mjwarp"), None, {})
        assert len(calls) == 2

    def test_a_different_failure_is_not_run_again(self, main_module, monkeypatch):
        calls = []
        monkeypatch.setattr(main_module, "run_isaac_lab",
                            lambda cmd, cwd, timeout_seconds=None: calls.append(cmd) or _run_result(1, "boom"))
        monkeypatch.setattr(main_module, "setup_checkpoint_dir", lambda config: "/unused")
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        with pytest.raises(RuntimeError, match="exit code 1"):
            main_module.run_training(_config(), None, {})
        assert len(calls) == 1

    def test_evaluation_reruns_without_the_selector_too(
            self, main_module, monkeypatch, typed_error_text):
        seen = []

        def fake(cmd, cwd, timeout_seconds=None):
            seen.append(cmd)
            return _run_result(1, typed_error_text) if len(seen) == 1 else _run_result(0)

        monkeypatch.setattr(main_module, "run_isaac_lab", fake)
        monkeypatch.setattr(main_module, "download_policy", lambda s3, config: "/unused/p.pt")
        monkeypatch.setattr(main_module, "upload_evaluation_results", lambda *a: None)
        config = _config(mode="evaluate", physics="newton_mjwarp", rl_library="rl_games")
        main_module.run_evaluation(config, None, {})
        assert "--viz" in seen[0] and "--viz" not in seen[1]
        assert config.physics == "task-default"

    def test_any_other_preset_error_gets_a_readable_message(self, main_module, monkeypatch):
        text = "Traceback...\nValueError: Preset 'x' conflicts with preset 'y' for env.sim\n"
        monkeypatch.setattr(main_module, "run_isaac_lab",
                            lambda cmd, cwd, timeout_seconds=None: _run_result(1, text))
        monkeypatch.setattr(main_module, "setup_checkpoint_dir", lambda config: "/unused")
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        with pytest.raises(RuntimeError, match="failed while Isaac Lab resolved its presets.*conflicts"):
            main_module.run_training(_config(physics="task-default"), None, {})

    def test_the_banner_names_a_task_default_run(self, main_module, monkeypatch, capsys):
        monkeypatch.setattr(main_module, "S3Client", lambda: type("S", (), {
            "register_bucket_regions": lambda self, r: None})())
        monkeypatch.setattr(main_module, "run_training", lambda *a: None)
        monkeypatch.setattr(main_module.sys, "argv", ["x", json.dumps({
            "jobName": "j", "trainingConfig": {"mode": "train", "task": "T-1",
                                               "physics": "task-default"}})])
        main_module.run_pipeline()
        assert "Task T-1 declares no physics presets" in capsys.readouterr().out


@pytest.mark.unit
class TestPlayProcessGroup:
    """Killing the shell of ``isaaclab.sh`` left Kit running (defect D6 of PR #1736)."""

    def test_the_run_starts_in_its_own_session(self):
        seen = {}

        class Done:
            returncode = 0
            stdout = iter([])

            def poll(self):
                return 0

            def wait(self):
                return 0

        def popen(*args, **kwargs):
            seen.update(kwargs)
            return Done()

        run_isaac_lab(["x"], "/tmp", popen=popen)
        assert seen.get("start_new_session") is True

    def test_the_timeout_kills_the_group_and_not_only_the_shell(self, monkeypatch):
        import signal
        killed = []
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

        class Hung:
            pid = 4242
            returncode = -9
            stdout = iter([])
            single_kills = 0

            def poll(self):
                return None if not killed else -9

            def kill(self):
                self.single_kills += 1

            def wait(self):
                return -9

        hung = Hung()
        ticks = iter(range(0, 10000, 2000))
        result = run_isaac_lab(["play"], "/tmp", timeout_seconds=1000,
                               popen=lambda *a, **k: hung, clock=lambda: next(ticks))
        assert result.timed_out
        assert killed == [(4242, signal.SIGKILL)] and hung.single_kills == 0
