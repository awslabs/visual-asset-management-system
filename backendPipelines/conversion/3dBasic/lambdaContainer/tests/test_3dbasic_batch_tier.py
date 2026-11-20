#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The AWS Batch tier of the 3D Basic Conversion container (#1732): task memory and the limits that
follow from it, the named results a Lambda run returns instead of an error, the refusals both tiers
make, ``conversion-run.json``, the worker's scrubbed environment on a Batch task, ``batch_entry.py``,
and the Dockerfile's two targets."""

import importlib.util
import json
import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAMBDA_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

import batch_entry  # noqa: E402
import conversion_service  # noqa: E402
import size_check  # noqa: E402

MB = size_check.MB
GIB = 1024 ** 3


def _load():
    spec = importlib.util.spec_from_file_location("trimesh_pipeline_lambda_batch_tier",
                                                  os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _read(name):
    with open(os.path.join(_LAMBDA_DIR, name), encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.unit
class TestTaskMemory:
    def test_the_batch_task_memory_sets_the_limits(self):
        env = {"VAMS_TASK_MEMORY_MIB": "122880"}
        assert size_check.memory_limit_bytes(env) == 122880 * MB
        assert size_check.compute_tier(env) == "batch"

    def test_the_lambda_memory_is_used_without_a_task(self):
        env = {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}
        assert size_check.memory_limit_bytes(env) == 10240 * MB
        assert size_check.compute_tier(env) == "lambda"

    @pytest.mark.parametrize("value", ["", "0", "-5", "many", "1.5"])
    def test_a_task_memory_that_is_not_a_positive_integer_is_ignored(self, value):
        env = {"VAMS_TASK_MEMORY_MIB": value, "AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}
        assert size_check.memory_limit_bytes(env) == 10240 * MB
        assert size_check.compute_tier(env) == "lambda"

    def test_the_task_memory_wins_when_both_are_set(self):
        env = {"VAMS_TASK_MEMORY_MIB": "30720", "AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}
        assert size_check.memory_limit_bytes(env) == 30720 * MB

    @pytest.mark.parametrize("extension,output,mib,expected", [
        # (0.9 x task MiB - 300 MiB) / factor, from size_check.limits, in MiB (rounded down).
        (".stl", ".glb", 30 * 1024, 1_953),
        (".stl", ".usda", 30 * 1024, 1_302),
        (".stl", ".usdc", 120 * 1024, 5_013),
        (".glb", ".glb", 60 * 1024, 2_036),
        (".glb", ".usdc", 120 * 1024, 2_626),
        (".usdc", ".usdc", 120 * 1024, 2_042),
        (".step", ".glb", 120 * 1024, 1_575),
        (".step", ".usdc", 120 * 1024, 1_011),
        (".3mf", ".glb", 120 * 1024, 1_040),
    ])
    def test_the_batch_limit_table_comes_from_the_size_check(self, extension, output, mib, expected):
        limit, resource = size_check.limits(extension, mib * MB, None, output)
        assert limit // MB == expected
        assert resource == size_check.MEMORY_RESOURCE

    def test_the_required_memory_is_the_inverse_of_the_limit(self):
        for extension, output in ((".stl", ".usdc"), (".step", ".glb"), (".glb", ".glb")):
            total = 700 * MB
            required = size_check.required_memory_bytes(extension, total, output)
            limit, _ = size_check.limits(extension, required, None, output)
            assert limit >= total
            below, _ = size_check.limits(extension, required - 2 * MB, None, output)
            assert below < total

    def test_the_limits_for_each_job_size_are_listed_for_the_router(self):
        listed = size_check.limits_by_task_memory(".stl", ".usdc")
        assert sorted(listed) == [30 * 1024, 60 * 1024, 120 * 1024]
        assert listed[120 * 1024] == size_check.limits(".stl", 120 * 1024 * MB, None, ".usdc")[0]


@pytest.mark.unit
class TestTriangleBudget:
    def test_the_lambda_tier_keeps_the_constant_budget(self):
        # The function's memory is not a task memory: no derived budget, so the scene walk uses 30 M.
        assert size_check.triangle_budget({"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}) is None
        assert size_check.triangle_budget({}) is None

    @pytest.mark.parametrize("mib,lower,upper", [
        (30 * 1024, 106_000_000, 107_000_000),
        (60 * 1024, 213_000_000, 214_000_000),
        (120 * 1024, 428_000_000, 429_000_000),
    ])
    def test_a_batch_task_derives_the_budget_from_its_memory(self, mib, lower, upper):
        budget = size_check.triangle_budget({"VAMS_TASK_MEMORY_MIB": str(mib)})
        assert lower <= budget <= upper

    def test_an_stl_output_gets_a_smaller_budget_than_a_glb_output(self):
        environ = {"VAMS_TASK_MEMORY_MIB": str(30 * 1024)}
        glb = size_check.triangle_budget(environ, output_extension=".glb")
        stl = size_check.triangle_budget(environ, output_extension=".STL")
        assert glb == size_check.triangle_budget(environ)
        assert 0.68 < stl / glb < 0.70  # 1 / 1.45
        assert size_check.triangle_budget({"VAMS_TASK_MEMORY_MIB": "4096"}, output_extension=".stl") == 30_000_000

    def test_the_budget_never_falls_below_the_constant(self):
        assert size_check.triangle_budget({"VAMS_TASK_MEMORY_MIB": "4096"}) == 30_000_000

    def test_the_derived_budget_fits_the_task_with_the_size_check_headroom(self):
        mib = 120 * 1024
        budget = size_check.triangle_budget({"VAMS_TASK_MEMORY_MIB": str(mib)})
        peak = budget * 267  # the measured bytes per triangle, USD to GLB
        assert peak <= 0.9 * mib * MB - size_check.BASE_MEMORY_BYTES

    def test_the_scene_walks_take_the_budget_from_the_job(self):
        import scene_limits
        assert scene_limits.SceneLimit("x", 400_000_000).max_triangles == 400_000_000
        assert scene_limits.SceneLimit("x", None).max_triangles == scene_limits.MAX_TRIANGLES

    def test_a_usd_scene_over_the_job_budget_is_refused_and_under_it_is_read(self, tmp_path):
        import model_formats
        trimesh = pytest.importorskip("trimesh")
        pytest.importorskip("pxr")
        directory = tmp_path / "in"
        directory.mkdir()
        layer = directory / "m.usda"
        layer.write_text(
            '#usda 1.0\n(\n    upAxis = "Y"\n)\n'
            'def Mesh "M"\n{\n    int[] faceVertexCounts = [3, 3]\n    int[] faceVertexIndices = [0, 1, 2, 1, 2, 3]\n'
            '    point3f[] points = [(0,0,0), (1,0,0), (0,1,0), (1,1,0)]\n}\n')
        job = {"input_file": str(layer), "input_dir": str(directory), "input_name": "m.usda",
               "input_relative_path": "m.usda", "by_reference": {}}
        with pytest.raises(ValueError, match="expands to more nodes or geometry than this function converts"):
            model_formats.load_model({**job, "max_triangles": 1}, trimesh)
        scene, _, _ = model_formats.load_model({**job, "max_triangles": 10}, trimesh)
        assert sum(len(g.faces) for g in scene.geometry.values()) == 2


@pytest.mark.unit
class TestAttemptDeadline:
    def test_an_absolute_deadline_is_used(self):
        assert conversion_service.attempt_deadline_epoch({"VAMS_ATTEMPT_DEADLINE": "2000000000"}, now=1) == 2e9

    def test_a_relative_bound_is_counted_from_the_start(self):
        assert conversion_service.attempt_deadline_epoch({"VAMS_ATTEMPT_SECONDS": "600"}, now=1000) == 1600

    def test_the_default_is_four_hours(self):
        assert conversion_service.attempt_deadline_epoch({}, now=0) == 4 * 3600

    @pytest.mark.parametrize("value", ["", "-1", "0", "soon"])
    def test_an_unusable_value_falls_back(self, value):
        assert conversion_service.attempt_deadline_epoch({"VAMS_ATTEMPT_DEADLINE": value}, now=0) == 4 * 3600

    def test_the_worker_time_limit_follows_the_attempt_deadline(self):
        mod = _load()
        mod.remember_attempt_deadline_epoch(1_000_600, now_wall=1_000_000, now_monotonic=50)
        assert mod.worker_timeout_seconds(now=50) == 600 - mod.WORKER_RESERVE_SECONDS
        assert mod.worker_timeout_seconds(now=700) == 1  # past the deadline: never zero or negative


@pytest.mark.unit
class TestWorkerFailuresAreNamed:
    def _run(self, mod, tmp_path, returncode=None, timeout=False):
        process = MagicMock()
        process.pid = 4242
        process.returncode = returncode
        if timeout:
            import subprocess
            process.wait.side_effect = [subprocess.TimeoutExpired("w", 1), None, None]
        with patch.object(mod.subprocess, "Popen", return_value=process), \
                patch.object(mod, "_kill_group"), patch.object(mod, "reap_stray_processes"):
            return mod.run_worker({"input_name": "m.stl", "address_space_bytes": 4 * GIB}, str(tmp_path),
                                  timeout_seconds=5)

    def test_sigkill_is_the_named_memory_kill_with_the_text_it_always_had(self, tmp_path):
        mod = _load()
        with pytest.raises(conversion_service.WorkerKilledForMemory) as excinfo:
            self._run(mod, tmp_path, returncode=-9)
        assert str(excinfo.value) == (
            "The conversion of m.stl stopped unexpectedly: the converter was killed by signal 9. "
            "A model larger than the function's memory is the usual cause.")
        assert isinstance(excinfo.value, RuntimeError)

    def test_another_signal_is_the_plain_error(self, tmp_path):
        mod = _load()
        with pytest.raises(RuntimeError) as excinfo:
            self._run(mod, tmp_path, returncode=-11)
        assert not isinstance(excinfo.value, conversion_service.WorkerKilledForMemory)

    def test_a_timeout_is_the_named_time_limit(self, tmp_path):
        mod = _load()
        with pytest.raises(conversion_service.WorkerOutOfTime, match="did not finish within 5 seconds"):
            self._run(mod, tmp_path, timeout=True)

    def test_a_reported_memory_error_is_the_named_memory_kill(self, tmp_path):
        mod = _load()
        with patch.object(mod.subprocess, "Popen") as popen, patch.object(mod, "_kill_group"), \
                patch.object(mod, "reap_stray_processes"):
            popen.return_value.pid = 1
            popen.return_value.returncode = 1
            (tmp_path / "result.json").write_text(json.dumps({"ok": False, "type": "MemoryError", "message": "x"}))
            with patch.object(mod.os.path, "lexists", return_value=False):
                with pytest.raises(conversion_service.WorkerKilledForMemory, match="needs more memory"):
                    mod.run_worker({"input_name": "m.stl", "address_space_bytes": 4 * GIB}, str(tmp_path),
                                   timeout_seconds=5)


def _escalating(mod, size, extension=".stl", output=".glb", memory_mib=10240, allow=True, task_mib=None):
    """Run convert_input_output against a stubbed S3 with an input of ``size`` bytes."""
    env = {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": str(memory_mib)}
    if task_mib:
        env["VAMS_TASK_MEMORY_MIB"] = str(task_mib)
    extra = {"allow_escalation": True} if allow else {}
    with patch.dict(os.environ, env), \
            patch.object(mod, "head_input", MagicMock(return_value=(size, "etag"))), \
            patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: open(p, "wb").close() or p)), \
            patch.object(mod, "uploadV2", MagicMock()):
        return mod.convert_input_output(f"s3://abkt/xid/model{extension}", "s3://obkt/out/", output, "", "", None, **extra)


@pytest.mark.unit
class TestEscalationOnTheLambdaTier:
    def test_an_input_over_the_function_limit_returns_the_named_result(self):
        mod = _load()
        with patch.dict(os.environ, {"ENABLE_STEP_INPUT": "true"}):
            with pytest.raises(conversion_service.EscalationRequired) as excinfo:
                _escalating(mod, 120 * MB, extension=".step", output=".usdc")
        result = excinfo.value.result
        assert result["escalate"] is True and result["reason"] == "size"
        assert result["inputName"] == "model.step" and result["inputBytes"] == 120 * MB
        assert result["formatPair"] == ".step to .usdc"
        assert 10240 < result["requiredMemoryMiB"] < 30 * 1024
        assert sorted(result["limitBytesByTaskMemoryMiB"]) == ["122880", "30720", "61440"]
        assert result["minTaskMemoryMiB"] == 10240
        assert "limit for converting .step to .usdc" in result["message"]

    def test_without_the_flag_the_same_input_raises_the_error_it_always_did(self):
        mod = _load()
        with patch.dict(os.environ, {"ENABLE_STEP_INPUT": "true"}):
            with pytest.raises(size_check.InputTooLargeError, match="over the .* limit for converting .step to .usdc"):
                _escalating(mod, 120 * MB, extension=".step", output=".usdc", allow=False)

    def test_the_error_text_is_the_same_with_and_without_the_flag(self):
        mod = _load()
        with patch.dict(os.environ, {"ENABLE_STEP_INPUT": "true"}):
            with pytest.raises(size_check.InputTooLargeError) as plain:
                _escalating(mod, 120 * MB, extension=".step", output=".usdc", allow=False)
            with pytest.raises(conversion_service.EscalationRequired) as named:
                _escalating(mod, 120 * MB, extension=".step", output=".usdc")
        assert named.value.result["message"] == str(plain.value)

    def test_the_batch_tier_never_escalates(self):
        mod = _load()
        with patch.dict(os.environ, {"ENABLE_STEP_INPUT": "true"}):
            with pytest.raises(size_check.InputTooLargeError):
                _escalating(mod, 3 * GIB, extension=".step", output=".usdc", task_mib=30 * 1024)

    def test_a_ply_over_4_gib_is_refused_on_both_tiers_and_never_escalated(self):
        mod = _load()
        for task in (None, 120 * 1024):
            with pytest.raises(conversion_service.FormatLimitError, match="4 GiB a PLY file can be read at"):
                _escalating(mod, 5 * GIB, extension=".ply", output=".glb", task_mib=task)

    def test_a_ply_just_under_4_gib_is_left_to_the_size_check(self):
        mod = _load()
        with pytest.raises(conversion_service.EscalationRequired):
            _escalating(mod, 4 * GIB - 1, extension=".ply", output=".glb")

    @pytest.mark.parametrize("error,reason", [
        (conversion_service.WorkerKilledForMemory("killed"), "memory"),
        (conversion_service.WorkerOutOfTime("late"), "time"),
    ])
    def test_a_worker_killed_for_memory_or_time_escalates(self, error, reason):
        mod = _load()
        with patch.object(mod, "run_worker", MagicMock(side_effect=error)), \
                patch.object(mod, "head_input", MagicMock(return_value=(5 * MB, ""))), \
                patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: open(p, "wb").close() or p)), \
                patch.object(mod, "uploadV2", MagicMock()), \
                patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}):
            with pytest.raises(conversion_service.EscalationRequired) as excinfo:
                mod.convert_input_output("s3://abkt/xid/m.stl", "s3://obkt/out/", ".glb", "", "", None,
                                         allow_escalation=True)
            assert excinfo.value.result["reason"] == reason
            assert excinfo.value.result["message"] == str(error)
            # and without the flag, on Lambda, the error is the one the run has always reported: a plain
            # RuntimeError of the same text (the workflow shows the error type by name)
            with pytest.raises(RuntimeError) as plain:
                mod.convert_input_output("s3://abkt/xid/m.stl", "s3://obkt/out/", ".glb", "", "", None)
            assert type(plain.value) is RuntimeError and str(plain.value) == str(error)
            # on a Batch task the named type stays
            with patch.dict(os.environ, {"VAMS_TASK_MEMORY_MIB": "30720"}):
                with pytest.raises(type(error)):
                    mod.convert_input_output("s3://abkt/xid/m.stl", "s3://obkt/out/", ".glb", "", "", None)

    def test_a_referenced_file_over_the_limits_escalates_with_the_bytes_it_needs(self):
        import referenced_files
        mod = _load()
        error = referenced_files.ReferencedFileError("too big", for_size=True, needed_bytes=900 * MB)
        with patch.object(mod.referenced_files, "fetch_referenced_files", MagicMock(side_effect=error)), \
                patch.object(mod, "head_input", MagicMock(return_value=(1 * MB, ""))), \
                patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: open(p, "wb").close() or p)), \
                patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}):
            with pytest.raises(conversion_service.EscalationRequired) as excinfo:
                mod.convert_input_output("s3://abkt/xid/m.gltf", "s3://obkt/out/", ".glb", "", "", None,
                                         allow_escalation=True)
        assert excinfo.value.result["inputBytes"] == 901 * MB

    def _skipped_for_size_report(self):
        import referenced_files
        report = referenced_files.FetchReport()
        report.skipped_for_size_count = 2
        report.skipped_for_size = ["a.png: too big", "b.png: too big"]
        # 1 MB fetched; 900 MB of declared sizes over everything the model references
        report.total_bytes = 1 * MB
        report.declared_bytes = 900 * MB
        return report

    def test_an_optional_file_skipped_for_size_escalates_sized_from_every_referenced_file(self):
        mod = _load()
        report = self._skipped_for_size_report()
        with patch.object(mod.referenced_files, "fetch_referenced_files", MagicMock(return_value=report)), \
                patch.object(mod, "head_input", MagicMock(return_value=(1 * MB, ""))), \
                patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: open(p, "wb").close() or p)), \
                patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}):
            with pytest.raises(conversion_service.EscalationRequired) as excinfo:
                mod.convert_input_output("s3://abkt/xid/m.gltf", "s3://obkt/out/", ".glb", "", "", None,
                                         allow_escalation=True)
        # the input plus the declared sizes of all referenced files, not only what was fetched (1 MB)
        assert excinfo.value.result["inputBytes"] == 901 * MB
        assert excinfo.value.result["reason"] == "size"

    def test_without_the_flag_a_skipped_optional_file_completes_as_an_incomplete_conversion(self):
        mod = _load()
        report = self._skipped_for_size_report()
        reached = RuntimeError("reached the conversion")
        with patch.object(mod.referenced_files, "fetch_referenced_files", MagicMock(return_value=report)), \
                patch.object(mod, "run_worker", MagicMock(side_effect=reached)), \
                patch.object(mod, "head_input", MagicMock(return_value=(1 * MB, ""))), \
                patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: open(p, "wb").close() or p)), \
                patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}):
            # no escalation: the run goes on to the conversion, as it did before the tier existed
            with pytest.raises(RuntimeError, match="reached the conversion"):
                mod.convert_input_output("s3://abkt/xid/m.gltf", "s3://obkt/out/", ".glb", "", "", None)
        message = mod.incomplete_message("m.gltf", report)
        assert "2 referenced files could not be included" in message and "incomplete" in message

    def test_a_referenced_file_that_is_missing_is_not_escalated(self):
        import referenced_files
        mod = _load()
        error = referenced_files.ReferencedFileError("missing")
        with patch.object(mod.referenced_files, "fetch_referenced_files", MagicMock(side_effect=error)), \
                patch.object(mod, "head_input", MagicMock(return_value=(1 * MB, ""))), \
                patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: open(p, "wb").close() or p)), \
                patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}):
            with pytest.raises(referenced_files.ReferencedFileError):
                mod.convert_input_output("s3://abkt/xid/m.gltf", "s3://obkt/out/", ".glb", "", "", None,
                                         allow_escalation=True)

    def test_the_flag_only_counts_as_the_json_value_true(self):
        for value in ("true", 1, "yes", None, [True]):
            assert conversion_service.allow_escalation({"allowEscalation": value}) is False
        assert conversion_service.allow_escalation({"allowEscalation": True}) is True
        assert conversion_service.allow_escalation({}) is False


@pytest.mark.unit
class TestConvertEntry:
    """``convert()`` is what both tiers run; the handler adds only the Lambda envelope."""

    def _patched(self, mod, escalation=None, message=None):
        stub = MagicMock(side_effect=escalation) if escalation else MagicMock(return_value=message)
        return [patch.object(mod, "fetch_input_configuration", MagicMock(return_value={"outputType": ".glb"})),
                patch.object(mod, "resolve_inputs_from_manifest",
                             MagicMock(return_value=("s3://abkt/xid/m.stl", "s3://obkt/out/", "", ""))),
                patch.object(mod, "convert_input_output", stub),
                patch.object(mod, "write_run_document"), patch.object(mod, "write_incomplete_result")]

    def _convert(self, mod, data, **kw):
        patches = self._patched(mod, **kw)
        started = [p.start() for p in patches]
        try:
            return mod.convert(data), started
        finally:
            for p in patches:
                p.stop()

    def test_the_handler_calls_convert_with_the_request_body(self):
        mod = _load()
        with patch.object(mod, "convert", MagicMock(return_value={"statusCode": 200, "body": "Success"})) as convert:
            mod.lambda_handler({"body": {"inputManifestS3Location": "s3://a/m.json"}}, MagicMock())
        convert.assert_called_once_with({"inputManifestS3Location": "s3://a/m.json"})

    def test_a_success_without_the_flag_writes_no_run_document(self):
        mod = _load()
        result, started = self._convert(mod, {})
        assert result == {"statusCode": 200, "body": "Success"}
        started[3].assert_not_called()

    def test_a_success_in_a_deployment_with_the_tier_writes_the_run_document(self):
        mod = _load()
        with patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}):
            _, started = self._convert(mod, {"allowEscalation": True})
        document = started[3].call_args[0][0]
        assert document["tier"] == "lambda" and document["taskMemoryMiB"] == 10240
        assert document["schema"] == "vams.conversion3dBasic.run/v1"
        assert "escalatedFrom" not in document

    def test_a_batch_run_records_why_it_continued(self):
        mod = _load()
        env = {"VAMS_TASK_MEMORY_MIB": "61440", "VAMS_TASK_VCPU": "8"}
        with patch.dict(os.environ, env):
            _, started = self._convert(mod, {"allowEscalation": False, "escalationReason": "size",
                                             "requiredMemoryMiB": 40000})
        document = started[3].call_args[0][0]
        assert (document["tier"], document["taskMemoryMiB"], document["taskVcpu"]) == ("batch", 61440, 8)
        assert document["escalatedFrom"] == "lambda" and document["reason"] == "size"
        assert document["requiredMemoryMiB"] == 40000
        assert "Lambda function converts" in document["reasonText"]

    def test_an_unknown_reason_is_not_recorded(self):
        mod = _load()
        with patch.dict(os.environ, {"VAMS_TASK_MEMORY_MIB": "30720"}):
            _, started = self._convert(mod, {"escalationReason": "<script>"})
        assert "reason" not in started[3].call_args[0][0]

    def test_an_escalation_is_returned_and_writes_no_run_document(self):
        mod = _load()
        named = {"escalate": True, "reason": "size", "message": "m"}
        result, started = self._convert(mod, {"allowEscalation": True},
                                        escalation=conversion_service.EscalationRequired(named))
        assert result == named
        started[3].assert_not_called()

    def test_the_run_document_holds_no_key_or_identity(self):
        document = conversion_service.run_document("batch", 30720, 4, {"convert": 1.5}, reason="memory",
                                                   escalated_from="lambda", required_memory_mib=33000,
                                                   message="Converted m.stl")
        text = json.dumps(document)
        assert "s3://" not in text and "arn:" not in text
        assert set(document) <= {"schema", "tier", "taskMemoryMiB", "taskVcpu", "phaseSeconds", "reason",
                                 "reasonText", "escalatedFrom", "requiredMemoryMiB", "message"}

    def test_the_phase_timer_adds_up_a_repeated_phase(self):
        ticks = iter([0.0, 1.0, 1.0, 4.0, 4.0, 5.0])
        timer = conversion_service.PhaseTimer(clock=lambda: next(ticks))
        timer.phase("a")
        timer.phase("b")
        timer.phase("a")
        timer.stop()
        assert timer.seconds == {"a": 2.0, "b": 3.0}


@pytest.mark.unit
class TestTheWorkerEnvironmentOnABatchTask:
    CREDENTIAL_VARIABLES = {
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/0c4e-secret",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://169.254.170.23/v1/credentials",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN": "token-secret",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/run/token",
        "ECS_CONTAINER_METADATA_URI": "http://169.254.170.2/v3/abc",
        "ECS_CONTAINER_METADATA_URI_V4": "http://169.254.170.2/v4/abc",
        "AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "s", "AWS_SESSION_TOKEN": "t",
        "VAMS_CONVERSION_EVENT": "{}", "VAMS_TASK_MEMORY_MIB": "30720", "AWS_REGION": "us-east-1",
    }

    def test_no_credential_endpoint_or_task_variable_reaches_the_child(self):
        mod = _load()
        env = {**self.CREDENTIAL_VARIABLES, "PATH": "/usr/bin", "LANG": "C.UTF-8"}
        child = mod.worker_environment(env, "/tmp/work")
        assert set(child) == {"PATH", "LANG", "HOME", "TMPDIR", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE"}
        for name in self.CREDENTIAL_VARIABLES:
            assert name not in child

    def test_a_scrubbed_name_added_to_the_passthrough_still_never_passes(self):
        mod = _load()
        with patch.object(mod, "WORKER_ENV_PASSTHROUGH", mod.WORKER_ENV_PASSTHROUGH + tuple(self.CREDENTIAL_VARIABLES)):
            child = mod.worker_environment(dict(self.CREDENTIAL_VARIABLES), "/tmp/work")
        assert not set(child) & set(self.CREDENTIAL_VARIABLES)

    @pytest.mark.parametrize("name,scrubbed", [
        ("PATH", False), ("LD_LIBRARY_PATH", False), ("PYTHONPATH", False), ("LANG", False), ("TZ", False),
        ("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", True), ("aws_container_authorization_token_x", True),
        ("ECS_CONTAINER_METADATA_URI_V4", True), ("ECS_FUTURE_THING", True), ("VAMS_ANYTHING", True),
        ("AWS_LAMBDA_RUNTIME_API", True), ("_HANDLER", True),
    ])
    def test_the_scrub_list(self, name, scrubbed):
        assert conversion_service.is_scrubbed(name) is scrubbed

    def test_stray_processes_are_reaped_on_a_batch_task_too(self):
        mod = _load()
        with patch.dict(os.environ, {"VAMS_TASK_MEMORY_MIB": "30720"}, clear=False):
            assert mod._in_lambda_environment() is True


@pytest.mark.unit
class TestFormatLimitsOfTheUsdWriter:
    class Geometry:
        def __init__(self, vertices, faces, face_array=None):
            self.vertices, self.faces = _Len(vertices), (face_array if face_array is not None else _Len(faces))

    class Scene:
        def __init__(self, *geometries):
            self.geometry = {f"g{i}": g for i, g in enumerate(geometries)}

    def test_the_text_bound_is_below_the_measured_limit(self):
        import usd_io
        # Whole at 2,146,875,055 bytes, dropped at 2,175,000,055: the bound keeps 7 percent under the first.
        assert usd_io.USDA_MAX_ARRAY_TEXT_BYTES < 2_146_875_055 < 2 ** 31 < 2_175_000_055
        assert usd_io.USDA_MAX_ARRAY_TEXT_BYTES > 0.9 * 2_146_875_055

    def test_a_usda_points_array_past_the_text_bound_is_refused_with_the_alternative(self):
        import usd_io
        # 50.1 million vertices at 40 bytes each of text is 2.004 GB.
        scene = self.Scene(self.Geometry(50_100_000, 0))
        with pytest.raises(ValueError, match=r"points array would be about 2\.0 GB of text.*Convert it to \.usdc"):
            usd_io.check_array_bounds(scene, ".usda", "m.stl")

    def test_a_usda_mesh_just_under_the_bound_is_allowed(self):
        import usd_io
        usd_io.check_array_bounds(self.Scene(self.Geometry(49_900_000, 0)), ".usda", "m.stl")

    def test_the_same_mesh_is_allowed_as_usdc(self):
        import usd_io
        usd_io.check_array_bounds(self.Scene(self.Geometry(50_100_000, 0)), ".usdc", "m.stl")

    def test_index_text_is_counted_by_digits_not_by_entries(self):
        import usd_io
        # The same number of entries is 3 bytes each as short indices and 10 bytes each as 8-digit ones.
        short = np.zeros((4, 3), dtype=np.int64)
        long = np.full((4, 3), 12_345_678, dtype=np.int64)
        with patch.object(usd_io, "USDA_MAX_ARRAY_TEXT_BYTES", 1):
            # upper bounds reach the patched limit, so the exact count runs
            assert usd_io._index_text_bytes(short, 99_999_999) == 12 * (1 + 2)
            assert usd_io._index_text_bytes(long, 99_999_999) == 12 * (8 + 2)

    def test_an_index_array_over_the_bound_is_refused_and_a_shorter_one_is_not(self):
        import usd_io
        vertices = 100
        faces = np.arange(300, dtype=np.int64).reshape(-1, 3) % vertices
        scene = self.Scene(self.Geometry(vertices, len(faces), faces))
        # 900 entries of 1-2 digits and a separator: well under 3 KB, over a 1 KB limit.
        with patch.object(usd_io, "USDA_MAX_ARRAY_TEXT_BYTES", 1000):
            with pytest.raises(ValueError, match="face vertex indices array would be about"):
                usd_io.check_array_bounds(scene, ".usda", "m.ply")
        with patch.object(usd_io, "USDA_MAX_ARRAY_TEXT_BYTES", 10_000_000):
            usd_io.check_array_bounds(scene, ".usda", "m.ply")

    @pytest.mark.parametrize("extension", [".usdc", ".usdz"])
    def test_a_crate_array_past_the_lz4_block_is_refused(self, extension):
        import usd_io
        # 176.2 M triangles: 528.6 M indices x 4 bytes = 2,114 MB, over 2,113,929,216.
        scene = self.Scene(self.Geometry(10, 176_200_000))
        with pytest.raises(ValueError, match=r"face vertex indices array is .* GiB.*split the model"):
            usd_io.check_array_bounds(scene, extension, "m.stl")

    def test_a_crate_of_many_meshes_is_allowed_because_the_bound_is_per_array(self):
        import usd_io
        usd_io.check_array_bounds(self.Scene(*[self.Geometry(10, 100_000_000) for _ in range(4)]), ".usdc", "m.stl")

    def test_a_crate_point_array_is_counted_in_bytes(self):
        import usd_io
        # 12 bytes a point: 177 M points = 2,124 MB.
        with pytest.raises(ValueError, match="points array"):
            usd_io.check_array_bounds(self.Scene(self.Geometry(177_000_000, 0)), ".usdc", "m.ply")

    def test_the_write_checks_before_it_creates_the_stage(self, tmp_path):
        import usd_io
        trimesh = pytest.importorskip("trimesh")
        scene = trimesh.Scene(trimesh.creation.box())
        with patch.object(usd_io, "check_array_bounds", MagicMock(side_effect=ValueError("too big"))) as check, \
                patch("pxr.Usd.Stage.CreateNew") as create:
            with pytest.raises(ValueError, match="too big"):
                usd_io.write_usd(scene, str(tmp_path / "m.usda"), 1.0, "y", ".usda", "m.stl")
        check.assert_called_once()
        create.assert_not_called()

    def test_a_small_real_mesh_passes_and_writes_whole(self, tmp_path):
        import usd_io
        trimesh = pytest.importorskip("trimesh")
        scene = trimesh.Scene(trimesh.creation.icosphere(subdivisions=2))
        path = tmp_path / "m.usda"
        usd_io.write_usd(scene, str(path), 1.0, "y", ".usda", "m.stl")
        text = path.read_text()
        assert "faceVertexIndices" in text and "points" in text and "normals" in text

    def test_a_crate_that_cannot_be_decompressed_is_a_could_not_be_read_message(self):
        import conversion_worker
        error = RuntimeError("Error in 'pxrInternal::TfFastCompression::DecompressFromBuffer': LZ4 error -1")
        translated = conversion_worker.translate_error(error, {"input_name": "scene.usdc"})
        assert isinstance(translated, ValueError)
        assert str(translated).startswith("scene.usdc could not be read")
        assert "TfFastCompression" not in str(translated)

    def test_other_errors_are_left_alone(self):
        import conversion_worker
        error = RuntimeError("boom")
        assert conversion_worker.translate_error(error, {"input_name": "x"}) is error
        value = ValueError("LZ4 error in a user message")
        assert conversion_worker.translate_error(value, {"input_name": "x"}) is value


class _Len:
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n


@pytest.mark.unit
class TestBatchEntry:
    def _env(self, **extra):
        env = {"VAMS_TASK_MEMORY_MIB": "30720", "VAMS_CONVERSION_EVENT": json.dumps({"workflowExecutionId": "e1"})}
        env.update(extra)
        return env

    def test_it_converts_with_the_shared_entry_and_never_allows_escalation(self, capsys):
        implementation = MagicMock()
        implementation.convert.return_value = {"statusCode": 200, "body": "Success"}
        assert batch_entry.main(self._env(), implementation) == 0
        assert capsys.readouterr().out.strip().splitlines()[-1] == "VAMS_CONVERSION_RESULT: Success"
        sent = implementation.convert.call_args[0][0]
        assert sent == {"workflowExecutionId": "e1", "allowEscalation": False}
        implementation.remember_attempt_deadline_epoch.assert_called_once()

    def test_an_incomplete_message_is_printed(self, capsys):
        implementation = MagicMock()
        implementation.convert.return_value = {"statusCode": 200, "body": "Converted m.gltf, but 1 file ..."}
        assert batch_entry.main(self._env(), implementation) == 0
        assert capsys.readouterr().out.strip().splitlines()[-1] == (
            "VAMS_CONVERSION_RESULT: Converted m.gltf, but 1 file ...")

    def test_a_failure_prints_one_line_and_exits_1(self, capsys):
        implementation = MagicMock()
        implementation.convert.side_effect = ValueError("m.stl contains no geometry, so there is nothing to convert")
        assert batch_entry.main(self._env(), implementation) == 1
        err = capsys.readouterr().err
        assert err.strip() == ("VAMS_CONVERSION_ERROR: ValueError: m.stl contains no geometry, "
                               "so there is nothing to convert")

    def test_it_refuses_to_run_outside_a_batch_task(self, capsys):
        assert batch_entry.main({"VAMS_CONVERSION_EVENT": "{}"}, MagicMock()) == 2

    @pytest.mark.parametrize("raw,message", [
        ("", "is not set"), ("not json", "not valid JSON"), ("[1]", "not a JSON object"),
        (json.dumps({"TaskToken": "AAAA"}), "task token"),
    ])
    def test_a_bad_event_is_refused_before_anything_runs(self, raw, message):
        implementation = MagicMock()
        env = {"VAMS_TASK_MEMORY_MIB": "30720", "VAMS_CONVERSION_EVENT": raw}
        assert batch_entry.main(env, implementation) == 1
        implementation.convert.assert_not_called()
        with pytest.raises(ValueError, match=message):
            batch_entry.read_event(env)

    def test_the_token_is_never_printed(self, capsys):
        implementation = MagicMock()
        env = {"VAMS_TASK_MEMORY_MIB": "30720", "VAMS_CONVERSION_EVENT": json.dumps({"TaskToken": "SECRET-TOKEN"})}
        batch_entry.main(env, implementation)
        captured = capsys.readouterr()
        assert "SECRET-TOKEN" not in captured.out + captured.err

    def test_both_tiers_reach_the_same_convert(self):
        handler = _read("lambda.py")
        entry = _read("batch_entry.py")
        assert re.search(r"(?m)^    return convert\(data\)$", handler)
        assert "conversion_service.load_implementation()" in entry and "module.convert(data)" in entry
        service = _read("conversion_service.py")
        assert '"lambda.py"' in service

    def test_the_implementation_loader_returns_the_lambda_module(self):
        with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
            module = conversion_service.load_implementation(os.path.join(_LAMBDA_DIR, "lambda.py"))
        assert callable(module.convert) and callable(module.lambda_handler)


@pytest.mark.unit
class TestDockerfileTargets:
    def _stages(self):
        stages, current = {}, None
        for line in _read("Dockerfile").splitlines():
            match = re.match(r"^FROM (\S+(?: \S+)*?) AS (\S+)$", line)
            if match:
                current = match.group(2)
                stages[current] = {"from": match.group(1), "lines": []}
            elif current:
                stages[current]["lines"].append(line)
        return stages

    def test_the_stages(self):
        stages = self._stages()
        assert list(stages) == ["assimp-builder", "base-with-code", "batch", "lambda"]

    def test_both_targets_descend_from_the_same_stage(self):
        stages = self._stages()
        assert stages["batch"]["from"] == "base-with-code" == stages["lambda"]["from"]

    def test_the_conversion_code_and_the_step_argument_are_in_the_shared_stage(self):
        shared = "\n".join(self._stages()["base-with-code"]["lines"])
        for expected in ("ARG ENABLE_STEP_INPUT=false", "ENV ENABLE_STEP_INPUT=${ENABLE_STEP_INPUT}", "COPY . .",
                         "--require-hashes", "COPY --from=assimp-builder /opt/assimp /opt/assimp/bin/assimp"):
            assert expected in shared
        for name in ("batch", "lambda"):
            assert "ENABLE_STEP_INPUT" not in "\n".join(self._stages()[name]["lines"])

    def test_the_lambda_target_is_last_and_is_the_function_image_unchanged(self):
        lines = [l for l in self._stages()["lambda"]["lines"] if l.strip() and not l.startswith("#")]
        assert lines == ['CMD ["lambda.lambda_handler"]']

    def test_the_batch_target_runs_as_a_non_root_user_with_its_own_entry(self):
        lines = [l for l in self._stages()["batch"]["lines"] if l.strip() and not l.startswith("#")]
        assert "USER 10001:10001" in lines
        assert 'ENTRYPOINT ["python", "-u", "batch_entry.py"]' in lines
        assert lines.index("USER 10001:10001") < lines.index('ENTRYPOINT ["python", "-u", "batch_entry.py"]')
        assert any(l.startswith("RUN chmod -R a+rX") for l in lines)

    def test_the_batch_entry_is_not_excluded_from_the_image(self):
        ignored = _read(".dockerignore")
        assert "batch_entry.py" not in ignored and "conversion_service.py" not in ignored

    def test_the_task_sizes_are_the_fargate_pairs(self):
        assert size_check.BATCH_TASK_SIZES == ((4, 30720), (8, 61440), (16, 122880))


@pytest.mark.unit
class TestRunDocumentOnAFailedBatchRun:
    def _run(self, tier_env, error):
        mod = _load()
        patches = [patch.object(mod, "fetch_input_configuration", MagicMock(return_value={"outputType": ".glb"})),
                   patch.object(mod, "resolve_inputs_from_manifest",
                                MagicMock(return_value=("s3://abkt/xid/m.stl", "s3://obkt/out/", "", ""))),
                   patch.object(mod, "convert_input_output", MagicMock(side_effect=error)),
                   patch.object(mod, "write_run_document"), patch.object(mod, "write_incomplete_result"),
                   patch.dict(os.environ, tier_env)]
        started = [p.start() for p in patches]
        try:
            with pytest.raises(type(error)):
                mod.convert({"escalationReason": "memory"})
            return started[3]
        finally:
            for p in patches:
                p.stop()

    def test_a_failed_batch_run_writes_the_document_and_still_raises(self):
        writer = self._run({"VAMS_TASK_MEMORY_MIB": "30720"}, ValueError("The model could not be read."))
        document = writer.call_args[0][0]
        assert document["tier"] == "batch" and document["failed"] is True
        assert document["escalatedFrom"] == "lambda" and document["reason"] == "memory"
        assert document["message"] == "ValueError: The model could not be read."

    def test_a_failed_lambda_run_writes_no_document(self):
        writer = self._run({"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}, ValueError("The model could not be read."))
        writer.assert_not_called()


def _after_download(mod, extension, declared=None, env=None, allow=True, worker=None, output=".glb"):
    """Run convert_input_output to the worker, with an input of 1 MB whose zip declares ``declared``
    uncompressed bytes; the worker raises ``worker`` (or a sentinel) and records the job it got."""
    jobs = []
    sentinel = RuntimeError("reached the worker")

    def _worker(job, work_dir):
        jobs.append(job)
        raise worker or sentinel

    extra = {"allow_escalation": True} if allow else {}
    with patch.dict(os.environ, env or {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"}), \
            patch.object(mod, "head_input", MagicMock(return_value=(1 * MB, ""))), \
            patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: open(p, "wb").close() or p)), \
            patch.object(mod, "check_input_text", MagicMock()), \
            patch.object(mod.size_check, "declared_uncompressed_bytes", MagicMock(return_value=declared)), \
            patch.object(mod, "run_worker", MagicMock(side_effect=_worker)), \
            patch.object(mod, "uploadV2", MagicMock()):
        try:
            mod.convert_input_output(f"s3://abkt/xid/m{extension}", "s3://obkt/out/", output, "", "", None, **extra)
        except RuntimeError as e:
            if e is not sentinel:
                raise
    return jobs


@pytest.mark.unit
class TestMergedExpansionChecksOnBothTiers:
    """The 3MF declared-size check and the scene-expansion bounds from development, on both tiers."""

    def test_a_3mf_over_the_lambda_uncompressed_limit_escalates_with_its_declared_size(self):
        mod = _load()
        declared = 1 * GIB  # over about 343 MB at 10,240 MB, under the 30 GiB job's limit
        with pytest.raises(conversion_service.EscalationRequired) as excinfo:
            _after_download(mod, ".3mf", declared)
        result = excinfo.value.result
        assert result["reason"] == "size" and result["inputBytes"] == declared
        limits = {int(k): v for k, v in result["limitBytesByTaskMemoryMiB"].items()}
        assert limits == size_check.uncompressed_limits_by_task_memory(".3mf", ".glb")
        assert limits[30 * 1024] >= declared
        assert 10240 < result["requiredMemoryMiB"] <= 30 * 1024
        assert "uncompressed" in result["message"]

    def test_a_3mf_no_batch_size_admits_stays_a_refusal(self):
        mod = _load()
        with pytest.raises(size_check.InputTooLargeError, match="uncompressed"):
            _after_download(mod, ".3mf", 50 * GIB)

    def test_without_the_flag_a_3mf_over_the_limit_is_refused_as_before(self):
        mod = _load()
        with pytest.raises(size_check.InputTooLargeError, match="uncompressed"):
            _after_download(mod, ".3mf", 1 * GIB, allow=False)

    def test_on_batch_the_3mf_limit_follows_the_task_memory(self):
        mod = _load()
        env = {"VAMS_TASK_MEMORY_MIB": str(30 * 1024)}
        jobs = _after_download(mod, ".3mf", 1 * GIB, env=env)
        assert jobs, "a 1 GiB declared 3MF is admitted on a 30 GiB task"
        with pytest.raises(size_check.InputTooLargeError, match="uncompressed"):
            _after_download(mod, ".3mf", 2 * GIB, env=env)

    def test_the_node_bound_scales_from_the_task_memory_on_batch(self):
        import expansion_bounds
        mod = _load()
        # the first worker run of a COLLADA input reads its references; the conversion job is the last
        lambda_job = _after_download(mod, ".dae")[-1]
        assert lambda_job["max_scene_nodes"] == expansion_bounds.CONVERSION_MAX_SCENE_NODES
        batch_job = _after_download(mod, ".dae", env={"VAMS_TASK_MEMORY_MIB": str(30 * 1024)})[-1]
        assert batch_job["max_scene_nodes"] == expansion_bounds.max_scene_nodes(30 * GIB) == 3_000_000

    def test_a_scene_expansion_refusal_is_not_escalated(self):
        mod = _load()
        refusal = ValueError("m.dae: the COLLADA scene expands to more than 1,000,000 nodes, more than this "
                             "function converts.")
        with pytest.raises(ValueError, match="expands to more than"):
            _after_download(mod, ".dae", worker=refusal)

    def test_the_collada_pre_count_and_usd_walk_take_the_tier_budgets(self):
        import model_formats
        seen = {}
        with patch.object(model_formats, "_check_dae", MagicMock(return_value=(None, None))), \
                patch.object(model_formats, "check_collada_scene",
                             MagicMock(side_effect=lambda p, n, t=None, max_nodes=None: seen.update(t=t, n=max_nodes) or (_ for _ in ()).throw(ValueError("stop")))):
            with pytest.raises(ValueError, match="stop"):
                model_formats.load_model({"input_file": "/x/m.dae", "input_name": "m.dae", "input_dir": "/x",
                                          "max_triangles": 106_000_000, "max_scene_nodes": 3_000_000}, MagicMock())
        assert seen == {"t": 106_000_000, "n": 3_000_000}
