#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the evaluation MSE parser.

The upstream Isaac-GR00T eval_policy.py reports its metric with print() and writes no machine-readable
output, so stdout parsing IS the metric contract. If it breaks, a VAMS execution succeeds with a
missing or wrong number — which is why the parser is tested directly rather than only through the
container.
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# The evaluation module lives in the container directory, not the lambda package.
sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "container")),
)

import evaluation  # noqa: E402
from evaluation import (  # noqa: E402
    _run_streaming,
    action_modality_keys,
    log_hardware_context,
    diagnostic_tail,
    _to_lerobot_modality_schema,
    ensure_dataset_modality_file,
    parse_mse,
)


@pytest.mark.unit
class TestParseMse:
    def test_reads_per_trajectory_and_average(self):
        out = (
            "Loading dataset...\n"
            "MSE: 0.01234\n"
            "MSE: 0.02\n"
            "MSE: 1.5e-3\n"
            "Average MSE across all trajs: 0.0111\n"
            "Done\n"
        )
        metrics = parse_mse(out)
        assert metrics["perTrajectoryMse"] == [0.01234, 0.02, 0.0015]
        assert metrics["averageMse"] == 0.0111

    def test_derives_the_average_when_upstream_omits_it(self):
        # The per-trajectory lines are the primary evidence, so a missing summary line is recoverable.
        metrics = parse_mse("MSE: 0.2\nMSE: 0.4\n")
        assert metrics["averageMse"] == pytest.approx(0.3)

    def test_unparseable_output_yields_null_rather_than_a_guess(self):
        # run_evaluation raises on a null average, so a silent upstream format change fails the job
        # instead of recording a plausible wrong metric.
        metrics = parse_mse("initialising policy server\nno metric here\n")
        assert metrics["averageMse"] is None
        assert metrics["perTrajectoryMse"] == []

    def test_ignores_mse_mentioned_mid_line(self):
        # Only whole "MSE: <n>" lines are the metric; prose that happens to contain the word is not.
        metrics = parse_mse("computing MSE: for trajectory 3 now\nMSE: 0.5\n")
        assert metrics["perTrajectoryMse"] == [0.5]

    def test_handles_scientific_and_negative_notation(self):
        metrics = parse_mse("MSE: 1e-05\nAverage MSE across all trajs: 1e-05\n")
        assert metrics["averageMse"] == pytest.approx(1e-05)

    def test_empty_output(self):
        metrics = parse_mse("")
        assert metrics["averageMse"] is None


@pytest.mark.unit
class TestEnsureDatasetModalityFile:
    """A LeRobot export does not ship meta/modality.json, but eval_policy.py ASSERTS on it — so a
    dataset that trains fine still cannot be evaluated as-is. The mapping is recovered from the
    checkpoint's own experiment_cfg, which guarantees evaluation reads the dataset the way training
    did."""

    MODALITIES = {
        "video": {"front": {"resolution": [640, 480]}},
        "state": {"single_arm": {"shape": [5]}},
        "action": {"single_arm": {"shape": [5]}},
    }

    def _checkpoint(self, tmp_path, payload):
        ckpt = tmp_path / "ckpt"
        (ckpt / "experiment_cfg").mkdir(parents=True)
        (ckpt / "experiment_cfg" / "metadata.json").write_text(
            json.dumps(payload), encoding="utf-8")
        return ckpt

    def test_writes_the_mapping_from_the_checkpoint(self, tmp_path):
        dataset = tmp_path / "dataset"
        (dataset / "meta").mkdir(parents=True)
        ckpt = self._checkpoint(tmp_path, {"new_embodiment": {"modalities": self.MODALITIES}})

        written = ensure_dataset_modality_file(str(dataset), str(ckpt))
        assert written
        # Written in the CONVERTED schema: LeRobot validates start/end column ranges, not `shape`.
        payload = json.loads((dataset / "meta" / "modality.json").read_text(encoding="utf-8"))
        assert payload["state"]["single_arm"] == {"start": 0, "end": 5}
        assert payload["video"]["front"] == {"original_key": "observation.images.front"}

    def test_creates_the_meta_directory_when_absent(self, tmp_path):
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        ckpt = self._checkpoint(tmp_path, {"new_embodiment": {"modalities": self.MODALITIES}})
        ensure_dataset_modality_file(str(dataset), str(ckpt))
        assert (dataset / "meta" / "modality.json").exists()

    def test_leaves_an_existing_file_alone(self, tmp_path):
        # A dataset exported WITH the file is authoritative; overwriting it would silently change how
        # the dataset is read.
        dataset = tmp_path / "dataset"
        (dataset / "meta").mkdir(parents=True)
        (dataset / "meta" / "modality.json").write_text('{"mine": true}', encoding="utf-8")
        ckpt = self._checkpoint(tmp_path, {"new_embodiment": {"modalities": self.MODALITIES}})

        assert ensure_dataset_modality_file(str(dataset), str(ckpt)) is None
        assert json.loads((dataset / "meta" / "modality.json").read_text(encoding="utf-8")) ==             {"mine": True}

    def test_raises_when_the_checkpoint_has_no_experiment_cfg(self, tmp_path):
        # Fail with an explanation rather than letting the upstream AssertionError surface.
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        ckpt = tmp_path / "ckpt"
        ckpt.mkdir()
        with pytest.raises(RuntimeError, match="no meta/modality.json"):
            ensure_dataset_modality_file(str(dataset), str(ckpt))

    def test_raises_when_the_experiment_cfg_has_no_modalities(self, tmp_path):
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        ckpt = self._checkpoint(tmp_path, {"new_embodiment": {"statistics": {}}})
        with pytest.raises(RuntimeError, match="no 'modalities' block"):
            ensure_dataset_modality_file(str(dataset), str(ckpt))

    def test_finds_the_modalities_under_any_embodiment_tag(self, tmp_path):
        # The tag is author-chosen ('new_embodiment' by default), so the lookup must not hard-code it.
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        ckpt = self._checkpoint(tmp_path, {"so101_follower": {"modalities": self.MODALITIES}})
        ensure_dataset_modality_file(str(dataset), str(ckpt))
        payload = json.loads((dataset / "meta" / "modality.json").read_text(encoding="utf-8"))
        assert payload["state"]["single_arm"] == {"start": 0, "end": 5}


@pytest.mark.unit
class TestModalitySchemaConversion:
    """expercheckpoint and modality.json carry the SAME information in different forms.

    experiment_cfg describes each state/action group by its `shape`; LeRobotModalityMetadata requires
    the COLUMN RANGE (start/end) that group occupies in the flat state/action vector. Passing the raw
    block through fails validation with "Field required: state.single_arm.start" for every group — which
    is exactly what a real evaluation hit.
    """

    def test_shapes_become_contiguous_column_ranges(self):
        out = _to_lerobot_modality_schema({
            "state": {"single_arm": {"shape": [5]}, "gripper": {"shape": [1]}},
        })
        assert out["state"]["single_arm"] == {"start": 0, "end": 5}
        assert out["state"]["gripper"] == {"start": 5, "end": 6}

    def test_ranges_tile_the_datasets_column_count(self):
        # single_arm[5] + gripper[1] must cover exactly the 6 columns meta/info.json reports for
        # observation.state and action; a gap or overlap would silently mis-read the vector.
        out = _to_lerobot_modality_schema({
            "action": {"single_arm": {"shape": [5]}, "gripper": {"shape": [1]}},
        })
        ranges = sorted((v["start"], v["end"]) for v in out["action"].values())
        assert ranges[0][0] == 0
        assert ranges[-1][1] == 6
        for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:]):
            assert prev_end == next_start

    def test_shape_is_dropped_and_descriptors_are_kept(self):
        out = _to_lerobot_modality_schema({
            "state": {"single_arm": {
                "shape": [3], "absolute": True, "continuous": True, "rotation_type": None}},
        })
        entry = out["state"]["single_arm"]
        assert "shape" not in entry, "shape is not accepted by LeRobot and start/end replace it"
        assert entry["absolute"] is True and entry["continuous"] is True
        # A null rotation_type carries no information and is omitted rather than sent as null.
        assert "rotation_type" not in entry

    def test_video_groups_are_mapped_to_their_dataset_columns(self):
        out = _to_lerobot_modality_schema({
            "video": {"front": {"resolution": [640, 480]}, "wrist": {"resolution": [640, 480]}},
        })
        assert out["video"]["front"] == {"original_key": "observation.images.front"}
        assert out["video"]["wrist"] == {"original_key": "observation.images.wrist"}

    def test_a_group_with_no_usable_shape_is_left_alone(self):
        # Left as-is rather than guessed at, so a malformed block surfaces as a validation error rather
        # than a silently wrong column mapping.
        raw = {"state": {"odd": {"shape": [2, 3]}, "missing": {"absolute": True}}}
        out = _to_lerobot_modality_schema(raw)
        assert out["state"]["odd"] == {"shape": [2, 3]}
        assert out["state"]["missing"] == {"absolute": True}

    def test_written_file_uses_the_converted_schema(self, tmp_path):
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        ckpt = tmp_path / "ckpt"
        (ckpt / "experiment_cfg").mkdir(parents=True)
        (ckpt / "experiment_cfg" / "metadata.json").write_text(json.dumps({
            "new_embodiment": {"modalities": {
                "state": {"single_arm": {"shape": [5]}, "gripper": {"shape": [1]}},
                "video": {"front": {"resolution": [640, 480]}},
            }}}), encoding="utf-8")

        ensure_dataset_modality_file(str(dataset), str(ckpt))
        written = json.loads((dataset / "meta" / "modality.json").read_text(encoding="utf-8"))
        assert written["state"]["gripper"] == {"start": 5, "end": 6}
        assert written["video"]["front"]["original_key"] == "observation.images.front"

    @pytest.mark.parametrize("modalities", [None, {}, {"state": "not-a-dict"}])
    def test_tolerates_junk(self, modalities):
        _to_lerobot_modality_schema(modalities)


@pytest.mark.unit
class TestAnnotationModalityIsSynthesized:
    """The checkpoint records no annotation group, but every data config asks for one.

    experiment_cfg/metadata.json carries video/state/action only. Each data config's
    modality_config() additionally requests `annotation.human.task_description` as its language
    modality, so a modality.json derived purely from the checkpoint is accepted by pydantic and then
    fails the dataset integrity check:

        AssertionError: Trying to get annotation metadata for a dataset with no annotations
        ValueError: Unable to find key annotation.human.task_description in modality metadata

    That is a hard failure before a single trajectory is replayed, and it is what a real evaluation
    hit after the earlier conversion bugs were cleared. A LeRobot export always carries the task
    index, and the fine-tuning path maps this key to the `task_index` column — so the same mapping is
    written here, which is also what makes evaluation read the dataset the way training did.
    """

    def test_annotation_group_is_added_when_the_checkpoint_omits_it(self):
        out = _to_lerobot_modality_schema({
            "state": {"single_arm": {"shape": [5]}},
            "action": {"single_arm": {"shape": [5]}},
            "video": {"front": {"resolution": [640, 480]}},
        })
        assert out["annotation"] == {"human.task_description": {"original_key": "task_index"}}

    def test_an_explicit_annotation_group_is_preserved(self):
        # A checkpoint that DOES record annotations is authoritative; overwriting it would change how
        # the dataset is read.
        out = _to_lerobot_modality_schema({
            "state": {"single_arm": {"shape": [5]}},
            "annotation": {"human.validity": {"original_key": "valid"}},
        })
        assert out["annotation"] == {"human.validity": {"original_key": "valid"}}

    def test_the_written_file_carries_the_annotation_mapping(self, tmp_path):
        # End-to-end through the writer: the file the eval script actually reads must contain it.
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        ckpt = tmp_path / "ckpt"
        (ckpt / "experiment_cfg").mkdir(parents=True)
        (ckpt / "experiment_cfg" / "metadata.json").write_text(json.dumps({
            "new_embodiment": {"modalities": {
                "state": {"single_arm": {"shape": [5]}, "gripper": {"shape": [1]}},
                "action": {"single_arm": {"shape": [5]}, "gripper": {"shape": [1]}},
                "video": {"front": {"resolution": [640, 480]},
                          "wrist": {"resolution": [640, 480]}},
            }}}), encoding="utf-8")

        ensure_dataset_modality_file(str(dataset), str(ckpt))
        written = json.loads((dataset / "meta" / "modality.json").read_text(encoding="utf-8"))
        assert written["annotation"]["human.task_description"]["original_key"] == "task_index"

    def test_matches_the_mapping_the_fine_tuning_path_writes(self):
        # Training and evaluation must agree on this key. If finetune_gr00t.py's mapping changes and
        # this does not, the two paths read the same dataset differently and the MSE is meaningless.
        source = (Path(__file__).resolve().parents[2] / "container" / "finetune_gr00t.py")
        text = source.read_text(encoding="utf-8")
        assert '"human.task_description": {"original_key": "task_index"}' in text

    def test_the_default_is_not_shared_mutable_state(self):
        # Returned by reference, a caller mutating one run's mapping would corrupt every later run in
        # the same process.
        first = _to_lerobot_modality_schema({"state": {"a": {"shape": [1]}}})
        first["annotation"]["injected"] = True
        second = _to_lerobot_modality_schema({"state": {"a": {"shape": [1]}}})
        assert "injected" not in second["annotation"]


@pytest.mark.unit
class TestDiagnosticTail:
    """The failure log has to actually reach the traceback.

    A real evaluation failure was reported as `exit code 1` with a log that ended mid-progress-bar and
    no exception anywhere — the tail budget had been spent on tqdm redraws and library import noise. The
    tail is the right window (the traceback is last); it just has to be a tail of informative lines.
    """

    def test_a_progress_bar_does_not_consume_the_budget(self):
        # tqdm redraws with \r, and str.splitlines() splits on \r too — one bar became hundreds of
        # "lines" and pushed the traceback out of a 120-line tail entirely.
        bar = "\r".join(f"Loading checkpoint shards: {p}%" for p in range(0, 400))
        text = f"{bar}\nTraceback (most recent call last):\nValueError: the real cause\n"
        out = diagnostic_tail(text, limit=10)
        assert "ValueError: the real cause" in out
        # Only the bar's final state survives, not every redraw.
        assert sum(1 for line in out if "Loading checkpoint shards" in line) == 1

    def test_library_import_noise_is_dropped(self):
        text = (
            "2026-01-01: E cuda_dnn.cc:9261] Unable to register cuDNN factory: blah\n"
            "2026-01-01: E cuda_fft.cc:607] Unable to register cuFFT factory: blah\n"
            "2026-01-01: E cuda_blas.cc:1515] Unable to register cuBLAS factory: blah\n"
            "TF-TRT Warning: Could not find TensorRT\n"
            "A new version of Albumentations is available: 2.0.8\n"
            "  check_for_updates()\n"
            "`use_fast` is set to `True` but the image processor class does not have a fast version.\n"
            "/usr/lib/tyro/_parsers.py:347: UserWarning: The field `model-path` is annotated\n"
            "  warnings.warn(message)\n"
            "AssertionError: something genuinely broke\n"
        )
        out = diagnostic_tail(text)
        assert out == ["AssertionError: something genuinely broke"]

    def test_the_traceback_survives_a_realistic_mixed_stream(self):
        noise = "\n".join([
            "2026-01-01: E cuda_dnn.cc] Unable to register cuDNN factory: x",
            "TF-TRT Warning: Could not find TensorRT",
        ] * 100)
        bar = "\r".join(f"Loading checkpoint shards: {p}/2" for p in range(300))
        text = f"{noise}\n{bar}\nTraceback (most recent call last):\n  File x\nRuntimeError: boom\n"
        out = diagnostic_tail(text, limit=20)
        assert out[-1] == "RuntimeError: boom"
        assert "Traceback (most recent call last):" in out

    def test_blank_and_absent_streams(self):
        assert diagnostic_tail(None) == []
        assert diagnostic_tail("") == []
        assert diagnostic_tail("\n\n   \n") == []

    def test_a_clean_short_stream_is_returned_in_order(self):
        assert diagnostic_tail("one\ntwo\nthree\n") == ["one", "two", "three"]


@pytest.mark.unit
class TestRunStreaming:
    """The eval child's output is streamed, not buffered by subprocess.run(capture_output=True).

    A synchronous run() does not read the pipe until the child exits, so a child that writes more than
    the OS pipe buffer (~64 KB) blocks forever and is eventually killed. That produced exactly the
    failure observed here: exit code 1, stdout truncated mid-progress-bar, and no traceback — the child
    never reached the point of printing one. Loading a multi-billion-parameter checkpoint emits far
    more than 64 KB of progress output, so the deadlock was reliable rather than occasional.
    """

    def _run(self, script, echo=None):
        # A real child process: the point of this helper is process I/O, so mocking Popen would test
        # nothing that matters here.
        # cwd is the container's repo dir by default, which does not exist off-container.
        return _run_streaming([sys.executable, "-u", "-c", script], dict(os.environ),
                              cwd=os.getcwd(), echo=echo)

    def test_returns_the_exit_code_and_full_output(self):
        code, text = self._run("print('hello'); print('world')")
        assert code == 0
        assert "hello" in text and "world" in text

    def test_a_nonzero_exit_is_reported_not_raised(self):
        # run_evaluation decides what to do with a failure; the helper must not raise on its own.
        code, _ = self._run("import sys; print('partial'); sys.exit(3)")
        assert code == 3

    def test_output_larger_than_the_pipe_buffer_does_not_deadlock(self):
        # THE regression test. ~2 MB is far beyond a 64 KB pipe buffer, so this hangs forever under
        # capture_output=True with a synchronous read.
        script = "\n".join([
            "for i in range(20000):",
            "    print('x' * 100)",
            "print('DONE-MARKER')",
        ])
        code, text = self._run(script)
        assert code == 0
        # The tail survived, which is what proves nothing was truncated or stalled.
        assert "DONE-MARKER" in text
        assert len(text) > 1_000_000

    def test_stderr_is_captured_too(self):
        # Merged into stdout: with two pipes and one reader, the unread pipe is the one that fills.
        script = "\n".join([
            "import sys",
            "print('to-stderr', file=sys.stderr)",
            "print('to-stdout')",
        ])
        code, text = self._run(script)
        assert code == 0
        assert "to-stderr" in text and "to-stdout" in text

    def test_the_metric_line_survives_streaming(self):
        # The whole reason output is captured at all: the MSE exists only in the child's stdout.
        code, text = self._run("print('MSE: 0.25'); print('Average MSE across all trajs: 0.25')")
        assert code == 0
        assert parse_mse(text)["averageMse"] == pytest.approx(0.25)

    def test_output_is_echoed_as_it_arrives(self):
        seen = []
        self._run("print('first'); print('second')", echo=seen.append)
        assert seen == ["first", "second"]

    def test_a_progress_bar_is_echoed_once_not_per_redraw(self):
        # Echoing every carriage-return redraw would flood the job log with the same line hundreds of
        # times. chr(13)/chr(10) rather than escapes: this text is a Python literal that becomes the
        # child's source, so an escape here would have to survive two levels of quoting.
        seen = []
        script = "\n".join([
            "import sys",
            "for p in range(200):",
            "    sys.stdout.write(chr(13) + 'Loading: %d%%' % p)",
            "sys.stdout.write(chr(10))",
        ])
        self._run(script, echo=seen.append)
        assert len(seen) == 1
        assert seen[0].startswith("Loading:")

    def test_library_noise_is_not_echoed_but_is_still_captured(self):
        # Kept out of the log for readability, but retained in the returned text so the failure tail
        # is complete if it turns out to matter.
        seen = []
        code, text = self._run(
            "print('TF-TRT Warning: Could not find TensorRT'); print('real output')", echo=seen.append)
        assert code == 0
        assert seen == ["real output"]
        assert "TF-TRT Warning" in text


@pytest.mark.unit
class TestLogHardwareContext:
    """The run's GPU/RAM is recorded BEFORE the model loads.

    A container killed by the kernel or the GPU driver produces no traceback and does not reach this
    module's failure handler — the process is simply gone. An evaluation failed exactly that way: exit
    code 1, log ending mid-model-load, nothing explaining it, and no way to tell after the fact which
    instance it landed on. The compute environment mixes 48 GB and 24 GB GPUs while the job asks only
    for "1 GPU", so placement decides whether a model fits, and that fact has to be in the log.

    Every branch is best-effort: diagnostics must never be the reason a run fails.
    """

    def test_reports_each_gpu(self):
        completed = SimpleNamespace(
            stdout="NVIDIA L40S, 46068 MiB, 1 MiB, 550.90\nNVIDIA L40S, 46068 MiB, 1 MiB, 550.90\n",
            stderr="")
        with patch.object(evaluation.subprocess, "run", return_value=completed):
            ctx = log_hardware_context()
        assert len(ctx["gpus"]) == 2
        assert "L40S" in ctx["gpus"][0]

    def test_queries_the_fields_needed_to_diagnose_a_vram_kill(self):
        completed = SimpleNamespace(stdout="", stderr="")
        with patch.object(evaluation.subprocess, "run", return_value=completed) as m_run:
            log_hardware_context()
        query = next(a for a in m_run.call_args.args[0] if a.startswith("--query-gpu"))
        # memory.total is the load-bearing one: it distinguishes a 24 GB A10G from a 48 GB L40S.
        for field in ("name", "memory.total", "memory.used", "driver_version"):
            assert field in query

    def test_a_missing_nvidia_smi_is_not_fatal(self):
        # Diagnostics failing must not fail the evaluation.
        with patch.object(evaluation.subprocess, "run", side_effect=FileNotFoundError("no smi")):
            ctx = log_hardware_context()
        assert "gpus" not in ctx

    def test_no_gpus_reported_is_surfaced_as_a_warning(self):
        # An empty GPU list on a GPU job is itself the diagnosis, so it must not pass silently.
        completed = SimpleNamespace(stdout="", stderr="NVML: driver/library version mismatch")
        with patch.object(evaluation.subprocess, "run", return_value=completed), \
             patch.object(evaluation.logger, "warning") as m_warn:
            log_hardware_context()
        assert any("no GPUs" in str(c) for c in m_warn.call_args_list)

    def test_reports_host_memory_in_mib(self):
        meminfo = "MemTotal:       65536000 kB\nMemAvailable:   32768000 kB\nBuffers: 100 kB\n"
        completed = SimpleNamespace(stdout="", stderr="")
        with patch.object(evaluation.subprocess, "run", return_value=completed), \
             patch.object(evaluation.Path, "read_text", return_value=meminfo):
            ctx = log_hardware_context()
        # MemAvailable, not just MemTotal: available is what an OOM kill actually depends on.
        assert ctx["hostMemTotalMiB"] == 64000
        assert ctx["hostMemAvailableMiB"] == 32000

    def test_an_unreadable_proc_meminfo_is_not_fatal(self):
        completed = SimpleNamespace(stdout="", stderr="")
        with patch.object(evaluation.subprocess, "run", return_value=completed), \
             patch.object(evaluation.Path, "read_text", side_effect=OSError("no /proc")):
            ctx = log_hardware_context()
        assert "hostMemTotalMiB" not in ctx

    def test_reports_vcpu_count(self):
        completed = SimpleNamespace(stdout="", stderr="")
        with patch.object(evaluation.subprocess, "run", return_value=completed):
            ctx = log_hardware_context()
        assert ctx["cpuCount"] == os.cpu_count()


@pytest.mark.unit
class TestActionModalityKeys:
    """`--modality-keys` must describe THIS dataset's robot, not the upstream default.

    eval_policy.py defaults the flag to ["right_arm"]. Against an so100/so101 dataset — whose action
    groups are single_arm and gripper — that default fails with:

        KeyError: 'action.right_arm'

    and it fails only AFTER the model has loaded and the first inference step has run, roughly nine
    minutes into a GPU job, which makes a wrong argument look like an inference bug. A real evaluation
    hit exactly this once the annotation-modality blocker was cleared.
    """

    def _dataset(self, tmp_path, payload):
        dataset = tmp_path / "dataset"
        (dataset / "meta").mkdir(parents=True)
        (dataset / "meta" / "modality.json").write_text(
            json.dumps(payload), encoding="utf-8")
        return dataset

    def test_reads_the_action_groups_in_declaration_order(self, tmp_path):
        # Order matters: the flag is repeated once per key and the metric is computed per group.
        dataset = self._dataset(tmp_path, {
            "action": {"single_arm": {"start": 0, "end": 5}, "gripper": {"start": 5, "end": 6}},
            "state": {"single_arm": {"start": 0, "end": 5}},
        })
        assert action_modality_keys(str(dataset)) == ["single_arm", "gripper"]

    def test_ignores_state_and_video_groups(self, tmp_path):
        # Only ACTION groups are valid values for --modality-keys.
        dataset = self._dataset(tmp_path, {
            "action": {"single_arm": {"start": 0, "end": 5}},
            "state": {"gripper": {"start": 0, "end": 1}},
            "video": {"front": {"original_key": "observation.images.front"}},
        })
        assert action_modality_keys(str(dataset)) == ["single_arm"]

    def test_returns_empty_when_the_file_is_missing(self, tmp_path):
        # Empty means "fall back to the upstream default" — better than passing something invalid.
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        assert action_modality_keys(str(dataset)) == []

    def test_returns_empty_on_malformed_json_rather_than_raising(self, tmp_path):
        # Diagnostics must never abort a run whose real work could still succeed.
        dataset = tmp_path / "dataset"
        (dataset / "meta").mkdir(parents=True)
        (dataset / "meta" / "modality.json").write_text("{not json", encoding="utf-8")
        assert action_modality_keys(str(dataset)) == []

    @pytest.mark.parametrize("payload", [{}, {"action": None}, {"action": "single_arm"},
                                         {"action": []}])
    def test_returns_empty_for_a_missing_or_non_mapping_action_group(self, tmp_path, payload):
        dataset = self._dataset(tmp_path, payload)
        assert action_modality_keys(str(dataset)) == []

    def test_derives_from_the_file_the_pipeline_itself_wrote(self, tmp_path):
        # The mapping is usually SYNTHESIZED from the checkpoint moments earlier, so the derivation
        # has to work off that generated file — not only off a dataset that shipped one.
        dataset = tmp_path / "dataset"
        dataset.mkdir()
        ckpt = tmp_path / "ckpt"
        (ckpt / "experiment_cfg").mkdir(parents=True)
        (ckpt / "experiment_cfg" / "metadata.json").write_text(json.dumps({
            "new_embodiment": {"modalities": {
                "action": {"single_arm": {"shape": [5]}, "gripper": {"shape": [1]}},
                "state": {"single_arm": {"shape": [5]}},
            }}}), encoding="utf-8")

        ensure_dataset_modality_file(str(dataset), str(ckpt))
        assert action_modality_keys(str(dataset)) == ["single_arm", "gripper"]

    def test_never_returns_the_upstream_default_for_this_dataset(self, tmp_path):
        # The specific regression: right_arm must not appear for an so100-style dataset.
        dataset = self._dataset(tmp_path, {
            "action": {"single_arm": {"start": 0, "end": 5}, "gripper": {"start": 5, "end": 6}}})
        assert "right_arm" not in action_modality_keys(str(dataset))


@pytest.mark.unit
class TestModalityKeysReachTheCommand:
    """Deriving the keys is useless unless they are actually passed to eval_policy.py.

    Isolated tests of action_modality_keys() cannot catch a broken hand-off: with the derivation
    correct but the flag never appended, the child still falls back to ["right_arm"] and the run still
    dies with KeyError: 'action.right_arm' nine minutes in. These tests assert the argv the child is
    invoked with.
    """

    def _run(self, tmp_path, config, action_groups=("single_arm", "gripper")):
        dataset = tmp_path / "dataset"
        (dataset / "meta").mkdir(parents=True)
        cursor = 0
        action = {}
        for g in action_groups:
            action[g] = {"start": cursor, "end": cursor + 1}
            cursor += 1
        (dataset / "meta" / "modality.json").write_text(
            json.dumps({"action": action}), encoding="utf-8")

        captured = {}

        def _fake_streaming(cmd, env, cwd=None, echo=None):
            captured["cmd"] = cmd
            return 0, "Average MSE across all trajs: 0.01\n"

        out = tmp_path / "out"
        out.mkdir()
        with patch.object(evaluation, "_run_streaming", side_effect=_fake_streaming), \
             patch.object(evaluation, "log_hardware_context", return_value={}):
            evaluation.run_evaluation(
                config, str(tmp_path / "ckpt"), str(dataset), str(out), str(tmp_path / "hf"))
        return captured["cmd"]

    def _flag_values(self, cmd, flag):
        return [cmd[i + 1] for i, a in enumerate(cmd) if a == flag and i + 1 < len(cmd)]

    def test_the_derived_keys_are_passed_to_the_eval_script(self, tmp_path):
        cmd = self._run(tmp_path, {"mode": "evaluate"})
        assert self._flag_values(cmd, "--modality-keys") == ["single_arm", "gripper"]

    def test_the_flag_is_repeated_once_per_key(self, tmp_path):
        # The upstream parser accumulates; a single comma-joined value would be read as one key name.
        cmd = self._run(tmp_path, {"mode": "evaluate"})
        assert cmd.count("--modality-keys") == 2
        assert not any("," in v for v in self._flag_values(cmd, "--modality-keys"))

    def test_an_explicit_template_value_overrides_the_derivation(self, tmp_path):
        cmd = self._run(tmp_path, {"evalModalityKeys": "left_arm, gripper"})
        assert self._flag_values(cmd, "--modality-keys") == ["left_arm", "gripper"]

    def test_an_explicit_list_is_accepted(self, tmp_path):
        cmd = self._run(tmp_path, {"evalModalityKeys": ["arm_a", "arm_b"]})
        assert self._flag_values(cmd, "--modality-keys") == ["arm_a", "arm_b"]

    def test_the_upstream_default_is_never_relied_on_when_the_dataset_declares_groups(self, tmp_path):
        # The regression in one line: the flag must be present, so ["right_arm"] is never used.
        cmd = self._run(tmp_path, {"mode": "evaluate"})
        assert "--modality-keys" in cmd

    def test_no_flag_is_passed_when_the_dataset_declares_nothing(self, tmp_path):
        # Passing an empty value would be worse than deferring to upstream.
        dataset = tmp_path / "dataset"
        (dataset / "meta").mkdir(parents=True)
        (dataset / "meta" / "modality.json").write_text(json.dumps({"state": {}}), encoding="utf-8")

        captured = {}
        out = tmp_path / "out"
        out.mkdir()
        with patch.object(evaluation, "_run_streaming",
                          side_effect=lambda cmd, env, cwd=None, echo=None: (
                              captured.setdefault("cmd", cmd),
                              (0, "Average MSE across all trajs: 0.01\n"))[1]), \
             patch.object(evaluation, "log_hardware_context", return_value={}):
            evaluation.run_evaluation(
                {"mode": "evaluate"}, str(tmp_path / "ckpt"), str(dataset), str(out),
                str(tmp_path / "hf"))
        assert "--modality-keys" not in captured["cmd"]


@pytest.mark.unit
class TestUpstreamPrintsEachMetricTwice:
    """eval_policy.py prints the SAME per-trajectory value under two labels.

    Verified against a real successful run's log -- for every trajectory it emits both:

        Unnormalized Action MSE across single traj: 2.106872322806477
        MSE: 2.106872322806477

    plus one "Average MSE across all trajs" summary at the end. Matching both per-trajectory spellings
    therefore DOUBLE-COUNTS: a 5-trajectory run recorded 10 entries, each value twice. The average was
    right (taken from upstream's own summary), so the defect showed up only in perTrajectoryMse --
    a plausible-looking but wrong artifact.

    Only the bare "MSE:" label is matched; the descriptive line restates the same number.
    """

    REAL_PAIR = (
        "Unnormalized Action MSE across single traj: 2.106872322806477\n"
        "MSE: 2.106872322806477\n"
    )

    def test_a_trajectory_printed_under_both_labels_counts_once(self):
        metrics = parse_mse(self.REAL_PAIR)
        assert metrics["perTrajectoryMse"] == [pytest.approx(2.106872322806477)]

    def test_five_trajectories_yield_five_values_not_ten(self):
        # The exact shape of the real run that produced the doubled artifact.
        values = [2.106872322806477, 4.0467412044308055, 5.335834263783416,
                  3.9668699862870973, 4.732371085913413]
        log = "".join(
            f"Running trajectory: {i}\n"
            f"Unnormalized Action MSE across single traj: {v}\n"
            f"MSE: {v}\n"
            for i, v in enumerate(values))
        log += "Average MSE across all trajs: 4.037737772644242\n"

        metrics = parse_mse(log)
        assert len(metrics["perTrajectoryMse"]) == 5
        assert metrics["perTrajectoryMse"] == [pytest.approx(v) for v in values]

    def test_prefers_upstreams_own_average_over_recomputing(self):
        # Upstream DOES print the summary; using it avoids disagreeing with the tool's own number.
        log = "MSE: 1.0\nMSE: 3.0\nAverage MSE across all trajs: 4.037737772644242\n"
        assert parse_mse(log)["averageMse"] == pytest.approx(4.037737772644242)

    def test_falls_back_to_computing_the_average_when_the_summary_is_absent(self):
        # A run cut short before the summary still reports something from the per-trajectory lines.
        metrics = parse_mse("MSE: 1.0\nMSE: 3.0\n")
        assert metrics["averageMse"] == pytest.approx(2.0)

    def test_parses_a_log_with_surrounding_noise(self):
        log = ("inferencing at step:  128\n"
               "Unnormalized Action MSE across single traj: 3.8767073315924865\n"
               "MSE: 3.8767073315924865\n"
               "Running trajectory: 1\n")
        metrics = parse_mse(log)
        assert metrics["perTrajectoryMse"] == [pytest.approx(3.8767073315924865)]

    def test_the_descriptive_line_alone_is_not_counted(self):
        # If upstream ever drops the bare label, a missing metric is the correct outcome: it surfaces
        # the format change instead of silently reporting a different set of numbers.
        metrics = parse_mse("Unnormalized Action MSE across single traj: 2.5\n")
        assert metrics["perTrajectoryMse"] == []


@pytest.mark.unit
class TestPlotsAreOptional:
    """`--plot` is off unless asked for, because upstream's plotting helper crashes.

    plot_trajectory() does `for i, ax in enumerate(axes)`, but plt.subplots(1) returns a bare Axes
    rather than an array, so a single-dimension group raises:

        TypeError: 'Axes' object is not iterable

    That happens AFTER the MSE is computed and printed, so the job had finished its real work and
    still exited 1 with no metrics recorded — the most expensive possible way to fail.
    """

    def _cmd(self, tmp_path, config):
        dataset = tmp_path / "dataset"
        (dataset / "meta").mkdir(parents=True)
        (dataset / "meta" / "modality.json").write_text(
            json.dumps({"action": {"single_arm": {"start": 0, "end": 5}}}), encoding="utf-8")
        captured = {}
        out = tmp_path / "out"
        out.mkdir()
        with patch.object(evaluation, "_run_streaming",
                          side_effect=lambda cmd, env, cwd=None, echo=None: (
                              captured.setdefault("cmd", cmd),
                              (0, "MSE: 0.5\n"))[1]), \
             patch.object(evaluation, "log_hardware_context", return_value={}):
            evaluation.run_evaluation(
                config, str(tmp_path / "ckpt"), str(dataset), str(out), str(tmp_path / "hf"))
        return captured["cmd"]

    @pytest.mark.parametrize("config", [{}, {"mode": "evaluate"}, {"plots": False},
                                        {"plots": "false"}, {"plots": "no"}])
    def test_no_plot_flags_unless_requested(self, tmp_path, config):
        cmd = self._cmd(tmp_path, config)
        assert "--plot" not in cmd
        assert "--save_plot_path" not in cmd

    @pytest.mark.parametrize("value", [True, "true", "True", "yes", "1"])
    def test_plots_can_be_opted_into(self, tmp_path, value):
        cmd = self._cmd(tmp_path, {"plots": value})
        assert "--plot" in cmd
        # The path must accompany the flag, or upstream writes into its own working directory.
        assert "--save_plot_path" in cmd

    def test_the_metric_is_still_returned_with_plotting_off(self, tmp_path):
        # The point of disabling plots: the deliverable is the MSE, and it must survive.
        dataset = tmp_path / "dataset"
        (dataset / "meta").mkdir(parents=True)
        (dataset / "meta" / "modality.json").write_text(
            json.dumps({"action": {"single_arm": {"start": 0, "end": 5}}}), encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()
        with patch.object(evaluation, "_run_streaming",
                          return_value=(0, "MSE: 0.5\n")), \
             patch.object(evaluation, "log_hardware_context", return_value={}):
            metrics = evaluation.run_evaluation(
                {"mode": "evaluate"}, str(tmp_path / "ckpt"), str(dataset), str(out),
                str(tmp_path / "hf"))
        assert metrics["averageMse"] == pytest.approx(0.5)
