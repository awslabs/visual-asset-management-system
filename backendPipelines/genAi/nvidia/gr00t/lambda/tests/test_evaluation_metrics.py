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
