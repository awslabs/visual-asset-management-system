#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Training checkpoints of every RL library reach the run's checkpoints/ folder (issue #1315).

The container uploaded only ``model_*.pt`` (rsl_rl), so a rl_games run (``nn/*.pth``) or a skrl run
(``checkpoints/agent_*.pt``, ``best_agent.pt``) finished green with an empty checkpoints folder. Each
test builds the log tree one library writes under Isaac Lab 2.3.0 and checks what is uploaded.
Layouts: ``utils/training/checkpoints.py``."""

import importlib.util
import os

import pytest

from utils.training.checkpoints import CHECKPOINT_PATTERNS, find_checkpoints
from utils.training.config import PipelineConfig

_OUTPUT_PREFIX = "s3://run-bucket/pipelines/isaacLab/JOB/output/3f2c9a10/files/"
_CHECKPOINTS = _OUTPUT_PREFIX + "checkpoints/"

# Relative to the Isaac Lab log root. Files that are NOT checkpoints sit beside the real ones.
LAYOUTS = {
    "rsl_rl": {
        "checkpoints": ["rsl_rl/cartpole/2026-01-01_00-00-00/model_0.pt",
                        "rsl_rl/cartpole/2026-01-01_00-00-00/model_149.pt"],
        "others": ["rsl_rl/cartpole/2026-01-01_00-00-00/params/env.yaml",
                   "rsl_rl/cartpole/2026-01-01_00-00-00/exported/policy.pt",
                   "rsl_rl/cartpole/2026-01-01_00-00-00/events.out.tfevents.1"],
    },
    "rl_games": {
        "checkpoints": ["rl_games/cartpole_direct/2026-01-01_00-00-00/nn/cartpole_direct.pth",
                        "rl_games/cartpole_direct/2026-01-01_00-00-00/nn/last_cartpole_direct_ep_150_rew__12.5_.pth"],
        "others": ["rl_games/cartpole_direct/2026-01-01_00-00-00/params/agent.yaml",
                   "rl_games/cartpole_direct/2026-01-01_00-00-00/summaries/events.out.tfevents.1"],
    },
    "skrl": {
        "checkpoints": ["skrl/cartpole_direct/2026-01-01_00-00-00_ppo_torch/checkpoints/agent_1000.pt",
                        "skrl/cartpole_direct/2026-01-01_00-00-00_ppo_torch/checkpoints/agent_30000.pt",
                        "skrl/cartpole_direct/2026-01-01_00-00-00_ppo_torch/checkpoints/best_agent.pt"],
        "others": ["skrl/cartpole_direct/2026-01-01_00-00-00_ppo_torch/params/agent.yaml",
                   "skrl/cartpole_direct/2026-01-01_00-00-00_ppo_torch/events.out.tfevents.1"],
    },
}


class RecordingS3:
    def __init__(self):
        self.uploads = []

    def upload_file(self, local_path, s3_uri):
        self.uploads.append((local_path, s3_uri))


@pytest.fixture(scope="module")
def main_module():
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "isaaclab_container_main_checkpoints", os.path.join(container_dir, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(library):
    return PipelineConfig(job_name="job1", mode="train", task="Isaac-Cartpole-Direct-v0",
                          rl_library=library, input_s3_path="", output_s3_path=_OUTPUT_PREFIX)


def _build_tree(root, library):
    for rel in LAYOUTS[library]["checkpoints"] + LAYOUTS[library]["others"]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def _upload(main_module, monkeypatch, tmp_path, library):
    logs, efs = tmp_path / "logs", tmp_path / "efs"
    efs.mkdir()
    _build_tree(logs, library)
    monkeypatch.setattr(main_module, "LOCAL_LOG_PATH", str(logs))
    monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
    monkeypatch.setattr(main_module, "upload_config", lambda *a, **k: None)
    s3 = RecordingS3()
    main_module.upload_training_results(s3, _config(library), str(efs), {})
    return s3.uploads


@pytest.mark.unit
class TestTrainingUploadsEveryLibrarysCheckpoints:
    @pytest.mark.parametrize("library", sorted(LAYOUTS))
    def test_checkpoints_are_uploaded_under_the_runs_checkpoints_folder(
            self, main_module, monkeypatch, tmp_path, library):
        uploads = _upload(main_module, monkeypatch, tmp_path, library)

        expected = sorted(_CHECKPOINTS + os.path.basename(p) for p in LAYOUTS[library]["checkpoints"])
        assert sorted(uri for _, uri in uploads) == expected

    @pytest.mark.parametrize("library", sorted(LAYOUTS))
    def test_files_that_are_not_checkpoints_are_left_out(
            self, main_module, monkeypatch, tmp_path, library):
        uploaded = {os.path.basename(path) for path, _ in _upload(main_module, monkeypatch, tmp_path, library)}

        for rel in LAYOUTS[library]["others"]:
            assert os.path.basename(rel) not in uploaded
        # The rsl_rl export folder holds a policy.pt that is not a checkpoint.
        assert "policy.pt" not in uploaded

    def test_a_run_of_another_library_in_the_log_root_is_not_uploaded(
            self, main_module, monkeypatch, tmp_path):
        logs, efs = tmp_path / "logs", tmp_path / "efs"
        efs.mkdir()
        _build_tree(logs, "rl_games")
        _build_tree(logs, "skrl")
        monkeypatch.setattr(main_module, "LOCAL_LOG_PATH", str(logs))
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        monkeypatch.setattr(main_module, "upload_config", lambda *a, **k: None)
        s3 = RecordingS3()
        main_module.upload_training_results(s3, _config("skrl"), str(efs), {})

        assert sorted(os.path.basename(uri) for _, uri in s3.uploads) == sorted(
            os.path.basename(p) for p in LAYOUTS["skrl"]["checkpoints"])

    def test_a_run_that_wrote_nothing_is_reported_in_the_job_log(
            self, main_module, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(main_module, "LOCAL_LOG_PATH", str(tmp_path / "logs"))
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        monkeypatch.setattr(main_module, "upload_config", lambda *a, **k: None)
        s3 = RecordingS3()
        main_module.upload_training_results(s3, _config("rl_games"), str(tmp_path / "efs"), {})

        assert s3.uploads == []
        assert "WARNING: no rl_games checkpoint found" in capsys.readouterr().out

    def test_two_files_with_one_name_upload_the_newer_last_and_warn(
            self, main_module, monkeypatch, tmp_path, capsys):
        logs = tmp_path / "logs"
        older = logs / "skrl/exp/run_a/checkpoints/best_agent.pt"
        newer = logs / "skrl/exp/run_b/checkpoints/best_agent.pt"
        for path, mtime in ((older, 1_000_000), (newer, 2_000_000)):
            path.parent.mkdir(parents=True)
            path.write_bytes(b"x")
            os.utime(path, (mtime, mtime))
        monkeypatch.setattr(main_module, "LOCAL_LOG_PATH", str(logs))
        monkeypatch.setattr(main_module, "upload_logs", lambda s3, config: None)
        monkeypatch.setattr(main_module, "upload_config", lambda *a, **k: None)
        s3 = RecordingS3()
        main_module.upload_training_results(s3, _config("skrl"), str(tmp_path / "efs"), {})

        assert [p for p, _ in s3.uploads] == [str(older), str(newer)]
        assert "same file name" in capsys.readouterr().out


@pytest.mark.unit
class TestFindCheckpoints:
    def test_every_supported_library_has_a_layout_under_test(self):
        assert set(CHECKPOINT_PATTERNS) == set(LAYOUTS)

    def test_unsupported_library_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported rlLibrary 'sb3'"):
            find_checkpoints([str(tmp_path)], "sb3")

    def test_a_file_reached_through_two_roots_is_returned_once(self, tmp_path):
        _build_tree(tmp_path, "skrl")
        found = find_checkpoints([str(tmp_path), str(tmp_path)], "skrl")
        assert len(found) == len(LAYOUTS["skrl"]["checkpoints"])

    def test_a_missing_root_is_skipped(self, tmp_path):
        assert find_checkpoints([str(tmp_path / "absent")], "rl_games") == []

