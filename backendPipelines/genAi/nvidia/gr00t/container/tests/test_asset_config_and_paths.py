#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Three properties of the container's configuration handling, and what each group pins.

*   **The highest-priority configuration source cannot degrade silently.** ``gr00t_config.json`` lives
    in the asset and ``resolve_config`` merges it over everything else, so a file that cannot be parsed
    used to be a ``logger.warning`` followed by a full training run on the template's defaults — a
    checkpoint written, the execution recorded SUCCESS, and none of the requested parameters applied.
    It now fails the run, matching what ``manifest_io.fetch_input_configuration`` already does for the
    input configuration.

*   **A blank field means "unset", not "override with blank".** Every other source reads it that way:
    the lambda skips a blank input-configuration field and a blank ``GROOT_*`` metadata value. The
    asset file did not, so ``"datasetPath": ""`` replaced the default with an empty string and handed
    training the asset root — a path that exists, so nothing complained.

*   **A caller-supplied path stays inside the asset.** ``datasetPath`` reaches the container from asset
    metadata, the template's configuration body, or the asset's own config file, and it is joined onto
    the per-job download directory. That join does not confine it: an absolute value replaces the base
    and ``..`` is not normalized. The resolved path is read by training AND written by evaluation's
    modality repair, while the shared EFS model cache is mounted read-write, so containment — not mere
    existence — is what has to hold.

*   **Checkpoints reach S3 as they are written.** Training writes to the container's own volume and the
    whole folder used to be uploaded once, after training returned, so an interrupted attempt left
    nothing in S3 and the interrupted-run recovery in ``download_checkpoint_from_s3`` had nothing to
    find. The last group holds the incremental sync, its non-fatal failure handling, and the refusal to
    report success for an output folder with no files in it.
"""

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)


def _load_container_entrypoint():
    """``__main__.py`` cannot be imported under that name — it belongs to the running interpreter — so
    it is loaded from its path under an alias, which also leaves its ``if __name__`` guard inert."""
    spec = importlib.util.spec_from_file_location(
        "gr00t_container_entrypoint_paths", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


container = _load_container_entrypoint()


def _resolve(tmp_path, asset_file_config=None, lambda_config=None):
    """``resolve_config`` the way ``main()`` calls it: the lambda's merged ``gr00tConfig`` first, then
    the asset's own ``gr00t_config.json`` over it."""
    if asset_file_config is not None:
        (tmp_path / "gr00t_config.json").write_text(
            json.dumps(asset_file_config), encoding="utf-8")
    return container.resolve_config({"gr00tConfig": json.dumps(lambda_config or {})}, tmp_path)


def _stage_run(monkeypatch, tmp_path, asset_file_config=None, lambda_config=None, mode="finetune"):
    """Stage a full ``main()`` run with the AWS and GPU work replaced by recorders.

    The upload recorder records the FILES present under the output folder at the moment it was called,
    not just the fact of the call — an upload stub that only records its arguments would report a
    successful transfer of whatever happened to be on disk later, which is exactly the question the
    incremental-upload tests ask.
    """
    input_dir = tmp_path / "input"
    (input_dir / "dataset").mkdir(parents=True)
    if asset_file_config is not None:
        (input_dir / "gr00t_config.json").write_text(
            json.dumps(asset_file_config), encoding="utf-8")

    calls = {"training": [], "evaluation": [], "uploads": []}

    def _training(**kwargs):
        checkpoint = container.OUTPUT_DIR / "checkpoint-10"
        checkpoint.mkdir(parents=True, exist_ok=True)
        (checkpoint / "model.safetensors").write_text("weights", encoding="utf-8")
        calls["training"].append(kwargs)

    def _evaluation(**kwargs):
        (container.OUTPUT_DIR / "gr00tEvalMetrics.json").write_text(
            json.dumps({"averageMse": 0.5}), encoding="utf-8")
        calls["evaluation"].append(kwargs)
        return {"averageMse": 0.5}

    def _upload(local_dir, s3_output_path, output_folder_name):
        local = Path(local_dir)
        calls["uploads"].append({
            "destination": f"{s3_output_path.rstrip('/')}/{output_folder_name}/",
            "files": sorted(path.relative_to(local).as_posix()
                            for path in local.rglob("*") if path.is_file()),
        })
        return f"{s3_output_path.rstrip('/')}/{output_folder_name}/"

    monkeypatch.setattr(container, "INPUT_DIR", input_dir)
    monkeypatch.setattr(container, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setenv("S3_MODEL_BUCKET", "model-cache-bucket")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setattr(container, "load_pipeline_definition", lambda: {
        "inputS3AssetPath": "s3://abkt/xidM/",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "assetId": "xidM",
        "databaseId": "dbM",
        "gr00tConfig": json.dumps(lambda_config or {}),
        "mode": mode,
    })
    monkeypatch.setattr(container, "ensure_models_cached", lambda **kwargs: None)
    monkeypatch.setattr(container, "download_asset_from_s3", lambda *args, **kwargs: None)
    monkeypatch.setattr(container, "backup_cache_to_s3", lambda **kwargs: None)
    monkeypatch.setattr(container.manifest_io, "fetch_input_configuration",
                        lambda *args, **kwargs: {})
    monkeypatch.setattr(container, "run_training", _training)
    monkeypatch.setattr(container, "run_evaluation", _evaluation)
    monkeypatch.setattr(container, "upload_output_to_s3", _upload)
    # Only reached in evaluate mode, and both shell out to the AWS CLI.
    monkeypatch.setattr(container, "resolve_checkpoint_folder",
                        lambda *args, **kwargs: "gr00tOutput_N1.5-3B_trainingjob_20260101T000000_abcd")
    monkeypatch.setattr(container, "download_checkpoint_from_s3",
                        lambda *args, **kwargs: str(tmp_path / "checkpoint"))
    return calls


# ==================== the asset config file cannot degrade silently ====================

class TestMalformedAssetConfigFileFailsTheRun:
    """The file is the highest-priority source, so dropping it whole runs the job on parameters nobody
    asked for while every outward signal says the run succeeded."""

    @pytest.mark.parametrize("body", [
        '{"maxSteps": 20000, "loraRank": 32,}',   # trailing comma
        "{not json",
        '{"maxSteps": 20000',                      # truncated
    ])
    def test_unparseable_json_raises(self, tmp_path, body):
        (tmp_path / "gr00t_config.json").write_text(body, encoding="utf-8")
        with pytest.raises(container.AssetConfigurationError, match="not valid JSON"):
            container.resolve_config({"gr00tConfig": "{}"}, tmp_path)

    @pytest.mark.parametrize("body", ['["maxSteps"]', '"maxSteps"', "42", "null"])
    def test_json_that_is_not_an_object_raises(self, tmp_path, body):
        """A list or a bare scalar parses cleanly and then contributes nothing, which is the same silent
        no-op as a parse failure."""
        (tmp_path / "gr00t_config.json").write_text(body, encoding="utf-8")
        with pytest.raises(container.AssetConfigurationError, match="not a JSON object"):
            container.resolve_config({"gr00tConfig": "{}"}, tmp_path)

    def test_an_unreadable_file_raises(self, tmp_path):
        """A directory where the file should be: it exists, so the merge is attempted, and the read
        fails. Any OSError has to reach the run rather than being logged."""
        (tmp_path / "gr00t_config.json").mkdir()
        with pytest.raises(container.AssetConfigurationError, match="Could not read"):
            container.resolve_config({"gr00tConfig": "{}"}, tmp_path)

    def test_an_empty_file_is_tolerated(self, tmp_path):
        """Over-restriction control. An empty file supplies no overrides — the same reading
        ``manifest_io.fetch_input_configuration`` gives an empty body — and must not fail a run."""
        (tmp_path / "gr00t_config.json").write_text("   \n", encoding="utf-8")
        config = container.resolve_config({"gr00tConfig": '{"maxSteps": 42}'}, tmp_path)
        assert config["maxSteps"] == 42

    def test_a_well_formed_file_still_applies(self, tmp_path):
        """The other over-restriction control: the file remains the highest-priority source."""
        config = _resolve(tmp_path,
                          lambda_config={"maxSteps": 6000, "loraRank": 0},
                          asset_file_config={"maxSteps": 20000, "loraRank": 32})
        assert config["maxSteps"] == 20000
        assert config["loraRank"] == 32

    def test_the_failure_reaches_main_without_training_or_uploading(self, monkeypatch, tmp_path):
        """The point of failing at all: this container reports failure by raising, and the run must not
        have consumed GPU time or written an output folder that looks like a result."""
        calls = _stage_run(monkeypatch, tmp_path)
        (tmp_path / "input" / "gr00t_config.json").write_text(
            '{"maxSteps": 20000,}', encoding="utf-8")
        with pytest.raises(container.AssetConfigurationError):
            container.main()
        assert calls["training"] == []
        assert calls["uploads"] == []


# ==================== a blank value means unset ====================

class TestBlankValuesDoNotOverride:

    def test_a_blank_dataset_path_leaves_the_default_in_place(self, tmp_path):
        config = _resolve(tmp_path, asset_file_config={"datasetPath": ""})
        assert config["datasetPath"] == container.DEFAULTS["datasetPath"]

    def test_a_blank_value_does_not_erase_the_lambda_value(self, tmp_path):
        config = _resolve(tmp_path,
                          lambda_config={"dataConfig": "so101_dualcam"},
                          asset_file_config={"dataConfig": ""})
        assert config["dataConfig"] == "so101_dualcam"

    def test_a_blank_dataset_path_does_not_hand_training_the_asset_root(self, monkeypatch, tmp_path):
        """The failure this guard exists for. ``INPUT_DIR / ""`` is ``INPUT_DIR`` itself, which exists,
        so the existence check passed and training read the whole asset as if it were the dataset."""
        calls = _stage_run(monkeypatch, tmp_path, asset_file_config={"datasetPath": ""})
        container.main()
        assert len(calls["training"]) == 1
        assert Path(calls["training"][0]["dataset_path"]).name == "dataset"


# ==================== the dataset path stays inside the asset ====================

class TestDatasetPathContainment:

    @pytest.mark.parametrize("value", [
        "/mnt/efs/gr00t-models/hf_cache",   # the shared, read-write EFS model cache
        "/workspace",                        # the Isaac-GR00T source tree in the image
        "/",
        "../../workspace",
        "..",
        "dataset/../../..",
        "%2e%2e%2f%2e%2e%2fmnt%2fefs",      # the same traversal, percent-encoded
        "%2fmnt%2fefs",                      # an absolute path, percent-encoded
        "s3://bucket/dataset",
        "file:///mnt/efs",
        "..\\..\\windows",
        "",
        "   ",
        None,
    ])
    def test_values_that_leave_the_asset_are_rejected(self, tmp_path, value):
        with pytest.raises(ValueError, match="datasetPath"):
            container.resolve_asset_relative_path(tmp_path, value, "datasetPath")

    @pytest.mark.parametrize("value", ["dataset", "dataset/", "data/lerobot/so100", "my dataset",
                                       "dataset-v2.1", "100%", "dataset/../dataset"])
    def test_ordinary_values_still_resolve(self, tmp_path, value):
        """Over-restriction control. A containment check that rejected these would make the pipeline
        unusable while passing every rejection case above — including ``100%``, which is why the value
        used is the one the asset names rather than its percent-decoded form."""
        resolved = container.resolve_asset_relative_path(tmp_path, value, "datasetPath")
        base = Path(os.path.realpath(str(tmp_path)))
        assert resolved == base or base in resolved.parents

    def test_the_asset_root_itself_is_inside_the_asset(self, tmp_path):
        """'.' names the asset root explicitly, which is contained and therefore allowed. The blank
        value that used to arrive at the same place is rejected as blank, above."""
        assert (container.resolve_asset_relative_path(tmp_path, ".", "datasetPath")
                == Path(os.path.realpath(str(tmp_path))))

    def test_an_escaping_dataset_path_stops_the_run_before_training(self, monkeypatch, tmp_path):
        calls = _stage_run(monkeypatch, tmp_path,
                           asset_file_config={"datasetPath": "/mnt/efs/gr00t-models/hf_cache"})
        with pytest.raises(ValueError, match="datasetPath"):
            container.main()
        assert calls["training"] == []
        assert calls["uploads"] == []

    def test_an_escaping_dataset_path_stops_an_evaluation_before_it_writes(self, monkeypatch, tmp_path):
        """Evaluation is the half that WRITES: ``ensure_dataset_modality_file`` does
        ``mkdir(parents=True)`` and ``write_text`` under the dataset path, so an escaped value puts a
        file on storage shared by every job. The write is inside ``run_evaluation``, so the containment
        check has to reject before it is called."""
        calls = _stage_run(monkeypatch, tmp_path, mode="evaluate",
                           asset_file_config={"datasetPath": "../../../mnt/efs"})
        with pytest.raises(ValueError, match="datasetPath"):
            container.main()
        assert calls["evaluation"] == []
        assert calls["uploads"] == []

    def test_an_escape_is_rejected_even_when_its_target_exists(self, monkeypatch, tmp_path):
        """The one that does not rest on the error message. `/mnt/efs/gr00t-models/hf_cache` does not
        exist on a test machine, so the existence check alone would stop it there and hide whether
        containment is enforced at all — in the container that path DOES exist. Pointed at a real
        directory outside the download folder, the unfixed code hands it to training and reports
        success; this asserts it is refused."""
        outside = tmp_path / "shared-efs" / "hf_cache"
        outside.mkdir(parents=True)
        calls = _stage_run(monkeypatch, tmp_path,
                           asset_file_config={"datasetPath": outside.as_posix()})
        with pytest.raises(ValueError, match="outside the asset directory"):
            container.main()
        assert calls["training"] == []
        assert calls["uploads"] == []

    def test_the_path_handed_to_training_is_inside_the_download_directory(self, monkeypatch, tmp_path):
        calls = _stage_run(monkeypatch, tmp_path)
        container.main()
        handed = Path(calls["training"][0]["dataset_path"])
        assert Path(os.path.realpath(str(tmp_path / "input"))) in handed.parents


# ==================== checkpoints reach S3 while training runs ====================

class TestIncrementalOutputUpload:

    def test_a_sync_cycle_uploads_the_output_folder_to_its_final_destination(
            self, monkeypatch, tmp_path):
        """The intermediate sync must target the SAME folder the final upload writes, so a run that
        completes does not leave two partial copies in different places.

        That destination is the execution's STAGING prefix, NOT the asset prefix
        download_checkpoint_from_s3 reads -- promotion onto the asset is the workflow's process-output
        step, which a failed attempt never reaches. See TestWhereAnInterruptedRunsCheckpointsLand."""
        recorded = []
        monkeypatch.setattr(container, "OUTPUT_DIR", tmp_path / "output")
        (tmp_path / "output" / "checkpoint-2000").mkdir(parents=True)
        (tmp_path / "output" / "checkpoint-2000" / "model.safetensors").write_text(
            "w", encoding="utf-8")
        monkeypatch.setattr(container, "upload_output_to_s3",
                            lambda local, path, folder: recorded.append((local, path, folder)))

        uploader = container.PeriodicOutputUpload("s3://abkt/xidM/", "gr00tOutput_N1.5-3B_job")
        uploader.sync_once()

        assert recorded == [(tmp_path / "output", "s3://abkt/xidM/", "gr00tOutput_N1.5-3B_job")]
        assert uploader.cycles_uploaded == 1
        assert uploader.cycles_failed == 0

    def test_a_failed_sync_cycle_is_counted_and_does_not_stop_training(self, monkeypatch, tmp_path):
        """Deliberately non-fatal: the final upload after training is the one that decides the run's
        outcome, and failing the run on a transient S3 error would discard the GPU work this exists to
        protect. Counted rather than swallowed, so the log says how many cycles failed."""
        monkeypatch.setattr(container, "OUTPUT_DIR", tmp_path / "output")

        def _boom(*args, **kwargs):
            raise RuntimeError("S3 output upload failed: throttled")

        monkeypatch.setattr(container, "upload_output_to_s3", _boom)
        uploader = container.PeriodicOutputUpload("s3://abkt/xidM/", "folder")
        uploader.sync_once()
        assert uploader.cycles_failed == 1
        assert uploader.cycles_uploaded == 0

    def test_a_run_shorter_than_one_interval_adds_no_upload(self, monkeypatch, tmp_path):
        """The interval is waited out BEFORE the first cycle, so a short run pays nothing and the final
        upload remains the only one."""
        recorded = []
        monkeypatch.setattr(container, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(container, "upload_output_to_s3",
                            lambda *args, **kwargs: recorded.append(args))
        with container.PeriodicOutputUpload("s3://abkt/xidM/", "folder", interval_seconds=3600):
            pass
        assert recorded == []

    def test_a_checkpoint_written_during_training_is_in_s3_before_an_interruption(
            self, monkeypatch, tmp_path):
        """The whole point of the finding: an attempt that dies partway used to leave nothing at all.

        ``main()`` raises out of the training block, so the final upload line is never reached — every
        upload recorded here happened while training was still running.
        """
        calls = _stage_run(monkeypatch, tmp_path)
        monkeypatch.setattr(container, "CHECKPOINT_UPLOAD_INTERVAL_SECONDS", 0.01)

        def _interrupted(**kwargs):
            checkpoint = container.OUTPUT_DIR / "checkpoint-2000"
            checkpoint.mkdir(parents=True, exist_ok=True)
            (checkpoint / "model.safetensors").write_text("weights", encoding="utf-8")
            # Waits for the sync to be observed rather than for a duration: a fixed sleep is either
            # flaky or slow, and a sync that never happens fails on the deadline instead of passing.
            deadline = time.time() + 30
            while not calls["uploads"] and time.time() < deadline:
                time.sleep(0.01)
            raise RuntimeError("the attempt was interrupted at step 2000")

        monkeypatch.setattr(container, "run_training", _interrupted)

        with pytest.raises(RuntimeError, match="interrupted"):
            container.main()

        assert calls["uploads"], "no checkpoint was uploaded while training ran"
        assert any("checkpoint-2000/model.safetensors" in upload["files"]
                   for upload in calls["uploads"]), (
            f"the checkpoint written during training was not uploaded: {calls['uploads']}")
        assert all(upload["destination"].endswith("/")
                   and "gr00tOutput_" in upload["destination"]
                   for upload in calls["uploads"]), calls["uploads"]

    def test_an_output_folder_with_no_files_is_not_reported_as_a_success(self, monkeypatch, tmp_path):
        """``aws s3 sync`` of an empty directory exits 0, so a run whose work produced nothing would
        upload nothing and still be recorded as a successful execution."""
        calls = _stage_run(monkeypatch, tmp_path)
        monkeypatch.setattr(container, "run_training",
                            lambda **kwargs: calls["training"].append(kwargs))
        with pytest.raises(RuntimeError, match="no files"):
            container.main()
        assert calls["uploads"] == []

    def test_an_ordinary_run_still_uploads_once(self, monkeypatch, tmp_path):
        """Over-restriction control for both additions: the empty-output guard must not fire on a real
        run, and the periodic sync must not add uploads to a run that finishes promptly."""
        calls = _stage_run(monkeypatch, tmp_path)
        container.main()
        assert len(calls["uploads"]) == 1
        assert "checkpoint-10/model.safetensors" in calls["uploads"][0]["files"]

    def test_free_space_is_logged_and_never_raises(self, tmp_path):
        """An out-of-disk kill leaves no traceback, so the number has to be in the log before it. Best
        effort: an unreadable path returns None rather than failing a run."""
        assert container.log_free_space(tmp_path) is not None
        assert container.log_free_space(tmp_path / "does" / "not" / "exist") is None
