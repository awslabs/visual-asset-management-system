#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the isaacLab container's failure reporting and operator-facing options.

The container runs behind a task-token pipeline (``waitForCallback: Enabled``), so every failure
route must reach the Step Functions callbacks that ``main()`` owns; a ``SystemExit`` raised below
``main()`` bypasses them and the workflow task waits for its heartbeat timeout instead. The other
cases here cover the custom-environment install, the RL-library lookup, the single-node-only
training path, and the ``recordVideo`` option that gates the video upload."""

import glob
import importlib.util
import inspect
import json
import os
import sys

import pytest

from utils.training.config import PipelineConfig

_OUTPUT_PREFIX = "s3://run-bucket/pipelines/isaacLab/JOB/output/3f2c9a10/files/"


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file (its name is ``__main__.py``)."""
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "isaaclab_container_main", os.path.join(container_dir, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingS3:
    """Stub S3 client that records every download and upload it is asked to perform."""

    def __init__(self):
        self.uploads = []
        self.downloads = []
        outer = self

        class _Client:
            def download_file(self, bucket, key, local):
                os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
                with open(local, "wb") as handle:
                    handle.write(b"stub-archive")
                outer.downloads.append((bucket, key, local))

        self.client = _Client()

    def upload_file(self, local_path, s3_uri):
        self.uploads.append((local_path, s3_uri))


def make_config(**overrides) -> PipelineConfig:
    values = dict(
        job_name="job1",
        mode="evaluate",
        task="Isaac-Cartpole-Direct-v0",
        rl_library="rsl_rl",
        input_s3_path="",
        output_s3_path=_OUTPUT_PREFIX,
    )
    values.update(overrides)
    return PipelineConfig(**values)


class _ExitCode:
    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


@pytest.mark.unit
class TestFailureReachesTheTaskTokens:
    """A non-zero child exit is the pipeline's most common failure and must be reported."""

    def _run_main(self, mod, monkeypatch, job_config, returncode):
        failures = []
        successes = []
        uploaded_logs = []

        monkeypatch.setattr(mod, "send_task_failure",
                            lambda token, error, cause: failures.append((token, error, cause)))
        monkeypatch.setattr(mod, "send_task_success",
                            lambda token, output: successes.append(token))
        monkeypatch.setattr(mod, "upload_logs",
                            lambda s3, config: uploaded_logs.append(config.output_s3_path))
        monkeypatch.setattr(mod, "upload_training_results", lambda *a, **k: None)
        monkeypatch.setattr(mod, "upload_evaluation_results", lambda *a, **k: None)
        monkeypatch.setattr(mod, "download_policy", lambda s3, config: "/tmp/policy.pt")
        monkeypatch.setattr(mod, "S3Client", RecordingS3)
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _ExitCode(returncode))
        monkeypatch.setenv("SFN_TASK_TOKEN", "INT-TOKEN")
        monkeypatch.setattr(sys, "argv", ["__main__.py", json.dumps(job_config)])

        exit_code = None
        with pytest.raises(SystemExit) as raised:
            mod.main()
        exit_code = raised.value.code
        return failures, successes, uploaded_logs, exit_code

    def test_training_failure_fails_both_tokens(self, main_module, monkeypatch):
        job_config = {
            "jobName": "job1",
            "trainingConfig": {"mode": "train", "task": "Isaac-Cartpole-Direct-v0",
                               "rlLibrary": "rsl_rl"},
            "outputS3AssetFilesPath": _OUTPUT_PREFIX,
            "externalSfnTaskToken": "EXT-TOKEN",
        }
        failures, successes, uploaded_logs, exit_code = self._run_main(
            main_module, monkeypatch, job_config, returncode=3)

        assert [f[0] for f in failures] == ["INT-TOKEN", "EXT-TOKEN"]
        assert successes == []
        assert uploaded_logs == [_OUTPUT_PREFIX]
        # the cause an operator reads must name the real failure, not a timeout
        assert "exit code 3" in failures[0][2]
        # AWS Batch must still see the job as failed
        assert exit_code not in (0, None)

    def test_evaluation_failure_fails_both_tokens(self, main_module, monkeypatch):
        job_config = {
            "jobName": "job1",
            "trainingConfig": {"mode": "evaluate", "task": "Isaac-Cartpole-Direct-v0",
                               "policyS3Uri": "s3://bucket/policy.pt", "rlLibrary": "rsl_rl"},
            "outputS3AssetFilesPath": _OUTPUT_PREFIX,
            "externalSfnTaskToken": "EXT-TOKEN",
        }
        failures, successes, _, exit_code = self._run_main(
            main_module, monkeypatch, job_config, returncode=7)

        assert [f[0] for f in failures] == ["INT-TOKEN", "EXT-TOKEN"]
        assert successes == []
        assert "exit code 7" in failures[0][2]
        assert exit_code not in (0, None)

    def test_missing_job_config_argument_fails_the_token(self, main_module, monkeypatch):
        failures = []
        monkeypatch.setattr(main_module, "send_task_failure",
                            lambda token, error, cause: failures.append(token))
        monkeypatch.setattr(main_module, "send_task_success",
                            lambda token, output: pytest.fail("success reported for a usage error"))
        monkeypatch.setenv("SFN_TASK_TOKEN", "INT-TOKEN")
        monkeypatch.setattr(sys, "argv", ["__main__.py"])

        with pytest.raises(SystemExit):
            main_module.main()

        assert failures == ["INT-TOKEN"]


@pytest.mark.unit
class TestCustomEnvironmentInstall:
    """``pip install -e`` accepts only a project directory or VCS URL, never an archive."""

    def test_archive_is_installed_without_editable_or_build_isolation(
            self, main_module, monkeypatch):
        commands = []

        def fake_run(cmd, *args, **kwargs):
            commands.append(cmd)
            return _ExitCode(0)

        monkeypatch.setattr(main_module.subprocess, "run", fake_run)
        s3 = RecordingS3()
        main_module.download_and_install_custom_environment(
            s3, "s3://env-bucket/envs/my_env.tar.gz")

        assert s3.downloads == [("env-bucket", "envs/my_env.tar.gz", "/tmp/my_env.tar.gz")]
        assert len(commands) == 1
        cmd = commands[0]
        assert "-e" not in cmd
        assert cmd[-1] == "/tmp/my_env.tar.gz"
        # build against the backend already in the image; a package whose dependencies are absent
        # fails here rather than mid-run
        assert "--no-build-isolation" in cmd

    def test_a_failed_install_raises_with_pip_stderr(self, main_module, monkeypatch):
        failed = _ExitCode(1)
        failed.stderr = "ERROR: no setup.py found"
        monkeypatch.setattr(main_module.subprocess, "run", lambda *a, **k: failed)

        with pytest.raises(RuntimeError, match="no setup.py found"):
            main_module.download_and_install_custom_environment(
                RecordingS3(), "s3://env-bucket/envs/my_env.zip")

    @pytest.mark.parametrize("uri", [
        "https://example.invalid/env.tar.gz",
        "file:///tmp/env.tar.gz",
        "s3:///envs/env.tar.gz",
    ])
    def test_a_non_s3_uri_is_rejected_before_pip_runs(self, main_module, monkeypatch, uri):
        monkeypatch.setattr(main_module.subprocess, "run",
                            lambda *a, **k: pytest.fail("pip ran for a rejected URI"))
        with pytest.raises(ValueError):
            main_module.download_and_install_custom_environment(RecordingS3(), uri)


@pytest.mark.unit
class TestPolicyDownload:
    """The policy is deserialized by torch, so it gets the same scheme check as the environment
    package. The bucket scope itself is enforced in the openPipeline Lambda, which is the only place
    that knows which bucket the execution's asset lives in."""

    def _config(self, main_module, policy_s3_uri):
        return main_module.PipelineConfig(
            job_name="isaaclab-job", mode="evaluate", task="Isaac-Cartpole-v0",
            rl_library="rsl_rl", input_s3_path="", output_s3_path="",
            policy_s3_uri=policy_s3_uri)

    @pytest.mark.parametrize("uri", [
        "https://example.invalid/model.pt",
        "file:///tmp/model.pt",
        "s3:///checkpoints/model.pt",
    ])
    def test_a_non_s3_uri_is_rejected_before_the_download(self, main_module, uri):
        s3 = RecordingS3()
        with pytest.raises(ValueError):
            main_module.download_policy(s3, self._config(main_module, uri))
        assert s3.downloads == []

    def test_an_s3_uri_is_downloaded(self, main_module):
        # Positive control: a check that rejected everything would pass the cases above.
        s3 = RecordingS3()
        path = main_module.download_policy(
            s3, self._config(main_module, "s3://asset-bucket/xid1/checkpoints/model_300.pt"))
        assert path == "/tmp/policy.pt"
        assert s3.downloads == [
            ("asset-bucket", "xid1/checkpoints/model_300.pt", "/tmp/policy.pt")]

    def test_a_missing_uri_still_raises(self, main_module):
        with pytest.raises(ValueError, match="No policy S3 URI provided"):
            main_module.download_policy(RecordingS3(), self._config(main_module, None))

    @pytest.mark.parametrize("filename", ["my_env.tar.bz2", "my_env", "my_env.py"])
    def test_an_unsupported_package_format_is_rejected(self, main_module, monkeypatch, filename):
        monkeypatch.setattr(main_module.subprocess, "run",
                            lambda *a, **k: pytest.fail("pip ran for a rejected package"))
        with pytest.raises(ValueError, match="Unsupported custom environment package"):
            main_module.download_and_install_custom_environment(
                RecordingS3(), f"s3://env-bucket/envs/{filename}")


@pytest.mark.unit
class TestRlLibraryResolution:
    """An unrecognized library must not silently substitute rsl_rl: the run would train with a
    different library than requested, succeed, and leave unloadable checkpoints."""

    @pytest.mark.parametrize("library", ["rsl_rl", "rl_games", "skrl"])
    def test_supported_libraries_resolve_to_their_own_scripts(self, main_module, library):
        train = main_module.build_training_command(
            make_config(mode="train", rl_library=library), "/tmp/checkpoints")
        play = main_module.build_evaluation_command(
            make_config(mode="evaluate", rl_library=library), "/tmp/policy.pt")

        assert train[train.index("-p") + 1] == \
            f"scripts/reinforcement_learning/{library}/train.py"
        assert play[play.index("-p") + 1] == \
            f"scripts/reinforcement_learning/{library}/play.py"

    @pytest.mark.parametrize("library", ["stable_baselines3", "RSL_RL", "", "rsl-rl"])
    def test_unsupported_library_is_rejected_for_training(self, main_module, library):
        with pytest.raises(ValueError, match="Unsupported rlLibrary"):
            main_module.build_training_command(
                make_config(mode="train", rl_library=library), "/tmp/checkpoints")

    @pytest.mark.parametrize("library", ["stable_baselines3", "RSL_RL", "", "rsl-rl"])
    def test_unsupported_library_is_rejected_for_evaluation(self, main_module, library):
        with pytest.raises(ValueError, match="Unsupported rlLibrary"):
            main_module.build_evaluation_command(
                make_config(mode="evaluate", rl_library=library), "/tmp/policy.pt")

    def test_the_error_names_the_rejected_value(self, main_module):
        with pytest.raises(ValueError, match="stable_baselines3"):
            main_module.build_training_command(
                make_config(mode="train", rl_library="stable_baselines3"), "/tmp/checkpoints")


@pytest.mark.unit
class TestMultiNodeTrainingIsNotSupported:
    """Multi-node training is not a supported configuration: nothing submits an AWS Batch
    multi-node parallel job, so the environment variables the old node setup read
    (``AWS_BATCH_JOB_NUM_NODES`` and friends) were never set and the torchrun branch was
    unreachable. The single-node path below is the one that runs."""

    def test_no_multi_node_helpers_remain(self, main_module):
        assert not hasattr(main_module, "setup_multi_node")
        assert not hasattr(main_module, "build_multi_node_command")

    def test_the_command_builders_thread_no_node_state(self, main_module):
        assert list(inspect.signature(main_module.build_training_command).parameters) == \
            ["config", "checkpoint_dir"]
        assert "node_info" not in inspect.signature(main_module.run_training).parameters

    def test_training_is_never_wrapped_in_torchrun(self, main_module):
        cmd = main_module.build_training_command(make_config(mode="train"), "/tmp/checkpoints")
        assert cmd[0] == "./isaaclab.sh"
        assert "torchrun" not in cmd
        assert not any(arg.startswith(("--nnodes", "--node_rank", "--master_addr", "--nproc_per_node"))
                       for arg in cmd)

    def test_a_single_node_run_builds_its_command_and_uploads_its_output(
            self, main_module, monkeypatch):
        """The over-restriction guard: results must still upload now that the is-main-node gate
        is gone, since with one node it was always satisfied."""
        executed = []
        uploaded = []

        def fake_run(cmd, *args, **kwargs):
            executed.append(cmd)
            return _ExitCode(0)

        monkeypatch.setattr(main_module.subprocess, "run", fake_run)
        monkeypatch.setattr(main_module, "setup_checkpoint_dir",
                            lambda config: "/tmp/checkpoints/job1")
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        monkeypatch.setattr(
            main_module, "upload_training_results",
            lambda s3, config, checkpoint_dir, job_config: uploaded.append(checkpoint_dir))

        main_module.run_training(make_config(mode="train"), RecordingS3(), {"jobName": "job1"})

        assert len(executed) == 1
        cmd = executed[0]
        assert cmd[:3] == \
            ["./isaaclab.sh", "-p", "scripts/reinforcement_learning/rsl_rl/train.py"]
        assert "--headless" in cmd and "--max_iterations" in cmd
        assert "torchrun" not in cmd
        assert uploaded == ["/tmp/checkpoints/job1"]


@pytest.mark.unit
class TestRecordVideoGatesTheUpload:
    """play.py always records (``--video`` is what makes it terminate), so recordVideo can only
    decide whether the recording is published to the asset."""

    @pytest.fixture
    def seeded_logs(self, main_module, monkeypatch, tmp_path):
        video_dir = tmp_path / "run1" / "videos"
        video_dir.mkdir(parents=True)
        for name in ("rollout-0.mp4", "rollout-1.avi"):
            (video_dir / name).write_bytes(b"stub-video")
        monkeypatch.setattr(main_module, "LOCAL_LOG_PATH", str(tmp_path))
        monkeypatch.setattr(main_module, "upload_config", lambda *a, **k: None)
        monkeypatch.setattr(main_module, "export_tensorboard_to_csv", lambda log_dir: None)
        assert glob.glob(f"{tmp_path}/**/videos/**/*.mp4", recursive=True)
        return tmp_path

    def test_record_video_disabled_uploads_no_video(self, main_module, seeded_logs):
        s3 = RecordingS3()
        main_module.upload_evaluation_results(s3, make_config(record_video=False), {})
        assert [u for u in s3.uploads if "/videos/" in u[1]] == []

    def test_record_video_enabled_uploads_every_recording(self, main_module, seeded_logs):
        s3 = RecordingS3()
        main_module.upload_evaluation_results(s3, make_config(record_video=True), {})
        uploaded = sorted(os.path.basename(u[1]) for u in s3.uploads if "/videos/" in u[1])
        assert uploaded == ["rollout-0.mp4", "rollout-1.avi"]
        assert all(u[1].startswith(f"{_OUTPUT_PREFIX}videos/")
                   for u in s3.uploads if "/videos/" in u[1])
