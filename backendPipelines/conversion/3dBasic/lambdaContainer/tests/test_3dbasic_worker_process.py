#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/3dBasic: the model is parsed and written in a child process that has no AWS credentials,
an address-space limit and a wall-clock limit.

``lambda.run_worker`` starts ``conversion_worker.py`` with a scrubbed environment and reads back what
it reports. These tests start real child interpreters, with a test script standing in for the worker
where the behaviour under test is the parent's handling (timeouts, crashes, error mapping) and with the
real ``conversion_worker.main`` where it is the worker's (the address-space limit)."""

import ast
import json
import os
import signal
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import size_check  # noqa: E402
from test_conversion_contract import _load  # noqa: E402

_LAMBDA_DIR = os.path.dirname(_HERE)


def _job(**extra):
    job = {"input_name": "m.stl", "address_space_bytes": None}
    job.update(extra)
    return job


def _script(tmp_path, body):
    path = tmp_path / "fake_worker.py"
    path.write_text("import json, os, sys\njob_path, result_path = sys.argv[1], sys.argv[2]\n" + body)
    return str(path)


def _run(mod, tmp_path, body, timeout=20, **job_extra):
    with patch.object(mod, "_WORKER_SCRIPT", _script(tmp_path, body)):
        mod.run_worker(_job(**job_extra), str(tmp_path), timeout_seconds=timeout)


@pytest.mark.unit
class TestTheWorkerEnvironment:
    def test_no_aws_variable_reaches_the_worker(self):
        env = _load().worker_environment({
            "AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "s", "AWS_SESSION_TOKEN": "t",
            "AWS_REGION": "us-east-1", "AWS_LAMBDA_FUNCTION_NAME": "f", "AWS_CONTAINER_CREDENTIALS_FULL_URI": "u",
            "_X_AMZN_TRACE_ID": "x", "PATH": "/usr/bin", "HOME": "/home/sbx_user1051"})
        assert not [name for name in env if name.startswith("AWS_") or name.startswith("_X_AMZN")]
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/tmp"

    def test_only_what_the_interpreter_needs_is_passed_through(self):
        env = _load().worker_environment({"PATH": "/p", "LD_LIBRARY_PATH": "/l", "SECRET": "s", "TOKEN": "t"}, "/work")
        assert set(env) == {"PATH", "LD_LIBRARY_PATH", "HOME", "TMPDIR", "PYTHONDONTWRITEBYTECODE",
                            "PYTHONNOUSERSITE"}
        assert env["TMPDIR"] == "/work"

    def test_the_running_child_sees_no_aws_variable(self, tmp_path):
        mod = _load()
        body = ("json.dump(dict(os.environ), open(os.path.join(os.path.dirname(job_path), 'env.json'), 'w'))\n"
                "json.dump({'ok': True}, open(result_path, 'w'))\n")
        secrets = {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "AWS_SECRET_ACCESS_KEY": "secret",
                   "AWS_SESSION_TOKEN": "token", "AWS_LAMBDA_RUNTIME_API": "127.0.0.1:9001"}
        with patch.dict(os.environ, secrets):
            _run(mod, tmp_path, body)
        seen = json.loads((tmp_path / "env.json").read_text())
        assert not [name for name in seen if name.startswith("AWS_")], seen
        assert "secret" not in json.dumps(seen) and "token" not in json.dumps(seen)
        assert seen["HOME"] == "/tmp"


@pytest.mark.unit
class TestTheWorkerOutcome:
    def test_a_successful_worker_returns(self, tmp_path):
        _run(_load(), tmp_path, "json.dump({'ok': True}, open(result_path, 'w'))\n")

    def test_the_job_reaches_the_worker(self, tmp_path):
        body = ("job = json.load(open(job_path))\n"
                "json.dump({'ok': job['marker'] == 'm1'}, open(result_path, 'w'))\n")
        _run(_load(), tmp_path, body, marker="m1")

    def test_a_value_error_is_raised_as_one_with_the_same_text(self, tmp_path):
        body = ("json.dump({'ok': False, 'type': 'ValueError', 'message': 'm.stl contains no geometry'},"
                " open(result_path, 'w'))\nsys.exit(1)\n")
        with pytest.raises(ValueError, match="^m.stl contains no geometry$"):
            _run(_load(), tmp_path, body)

    def test_any_other_failure_is_raised_as_a_runtime_error_with_the_same_text(self, tmp_path):
        body = ("json.dump({'ok': False, 'type': 'KeyError', 'message': 'no loader for .xyzzy'},"
                " open(result_path, 'w'))\nsys.exit(1)\n")
        with pytest.raises(RuntimeError, match="^no loader for .xyzzy$"):
            _run(_load(), tmp_path, body)

    def test_a_worker_killed_without_a_result_reports_the_signal(self, tmp_path):
        body = "os.kill(os.getpid(), 9)\n"
        with pytest.raises(RuntimeError, match=r"stopped unexpectedly.*killed by signal 9"):
            _run(_load(), tmp_path, body)

    def test_a_worker_that_exits_without_a_result_reports_the_status_and_logs_its_stderr(self, tmp_path):
        mod = _load()
        body = "sys.stderr.write('Traceback: boom\\n')\nsys.exit(3)\n"
        with patch.object(mod, "logger") as logger, \
                pytest.raises(RuntimeError, match=r"stopped unexpectedly.*exited with status 3"):
            _run(mod, tmp_path, body)
        assert any("boom" in str(call) for call in logger.error.call_args_list)

    def test_a_reported_memory_error_names_the_file_and_the_function_memory(self, tmp_path):
        body = ("json.dump({'ok': False, 'type': 'MemoryError', 'message': ''}, open(result_path, 'w'))\n"
                "sys.exit(1)\n")
        with pytest.raises(RuntimeError, match=r"m\.stl needs more memory.*\(5,308 MB\)"):
            _run(_load(), tmp_path, body, address_space_bytes=5308 * 1024 ** 2)


@pytest.mark.unit
class TestTheWallClockLimit:
    def test_a_worker_that_runs_past_the_limit_is_stopped(self, tmp_path):
        mod = _load()
        start = time.monotonic()
        with pytest.raises(RuntimeError, match=r"m\.stl did not finish within 1 seconds"):
            _run(mod, tmp_path, "import time\ntime.sleep(60)\n", timeout=1)
        assert time.monotonic() - start < 30

    def test_what_the_worker_started_is_stopped_with_it(self, tmp_path):
        mod = _load()
        pid_file = tmp_path / "grandchild.pid"
        body = (f"import subprocess, time\n"
                f"child = subprocess.Popen(['sleep', '60'])\n"
                f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                f"time.sleep(60)\n")
        with pytest.raises(RuntimeError, match="did not finish"):
            _run(mod, tmp_path, body, timeout=2)
        pid = int(pid_file.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
            pytest.fail("the grandchild outlived the worker")

    def test_the_limit_is_the_remaining_invocation_time_less_the_reserve(self):
        mod = _load()
        mod._invocation_deadline = 1000.0 + 600
        assert mod.worker_timeout_seconds(now=1000.0) == 600 - mod.WORKER_RESERVE_SECONDS

    def test_the_limit_never_drops_below_one_second(self):
        mod = _load()
        mod._invocation_deadline = 1000.0 + 5
        assert mod.worker_timeout_seconds(now=1000.0) == 1

    def test_without_a_deadline_the_default_applies(self):
        mod = _load()
        mod._invocation_deadline = None
        assert mod.worker_timeout_seconds() == mod.WORKER_DEFAULT_TIMEOUT_SECONDS

    def test_the_handler_records_the_deadline_from_the_context(self):
        mod = _load()
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 120_000
        mod._remember_deadline(context)
        assert 100 < mod._invocation_deadline - time.monotonic() <= 120

    @pytest.mark.parametrize("context", [object(), MagicMock()])
    def test_a_context_without_a_remaining_time_leaves_no_deadline(self, context):
        mod = _load()
        mod._invocation_deadline = 5.0
        mod._remember_deadline(context)
        assert mod._invocation_deadline is None


@pytest.mark.unit
class TestTheAddressSpaceLimit:
    def test_a_model_that_outgrows_the_limit_is_reported_as_out_of_memory(self, tmp_path):
        # The real worker entry point with its conversion replaced by an allocation, so the limit is
        # what the process is held to without depending on what a parser happens to allocate.
        mod = _load()
        body = ("sys.path.insert(0, %r)\n"
                "import conversion_worker\n"
                "conversion_worker.convert_model = lambda job: bytearray(2 * 1024 ** 3)\n"
                "sys.exit(conversion_worker.main(sys.argv))\n") % _LAMBDA_DIR
        with pytest.raises(RuntimeError, match=r"m\.stl needs more memory to convert than this function has"):
            _run(mod, tmp_path, body, address_space_bytes=1024 ** 3)

    def test_without_a_limit_the_same_allocation_succeeds(self, tmp_path):
        mod = _load()
        body = ("sys.path.insert(0, %r)\n"
                "import conversion_worker\n"
                "conversion_worker.convert_model = lambda job: len(bytearray(300 * 1024 ** 2))\n"
                "sys.exit(conversion_worker.main(sys.argv))\n") % _LAMBDA_DIR
        _run(mod, tmp_path, body, address_space_bytes=None)

    def test_the_handler_passes_the_function_memory_plus_the_overhead_as_the_limit(self):
        # run_worker's job carries the function's memory in bytes (None outside Lambda).
        mod = _load()
        captured = {}

        def _worker(job, work_dir, timeout_seconds=None):
            captured.update(job)
            open(job["output_file"], "w").close()

        with patch.object(mod, "run_worker", _worker), \
                patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: open(p, "w").close())), \
                patch.object(mod, "uploadV2", MagicMock()), \
                patch.object(mod, "head_input_size", MagicMock(return_value=10)), \
                patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "5308"}):
            mod.convert_input_output("s3://abkt/xid/m.stl", "s3://obkt/files/", ".glb")
        assert captured["address_space_bytes"] == 5308 * 1024 ** 2 + size_check.ADDRESS_SPACE_OVERHEAD_BYTES


@pytest.mark.unit
class TestTheParserLivesOnlyInTheWorker:
    def _top_level_imports(self, path):
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_the_handler_does_not_import_the_mesh_library(self):
        assert "trimesh" not in self._top_level_imports(os.path.join(_LAMBDA_DIR, "lambda.py"))

    def test_the_worker_imports_neither_the_mesh_library_nor_boto(self):
        imports = self._top_level_imports(os.path.join(_LAMBDA_DIR, "conversion_worker.py"))
        assert not imports & {"trimesh", "boto3", "botocore", "aws_lambda_powertools"}


_ASCII_STL = ("solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\n"
              "endfacet\nendsolid t\n")


def _real_trimesh_available():
    import importlib.util
    import subprocess
    probe = subprocess.run([sys.executable, "-c", "import trimesh, numpy"], capture_output=True)
    return probe.returncode == 0


@pytest.mark.unit
@pytest.mark.skipif(not _real_trimesh_available(), reason="trimesh is not installed")
class TestTheRealWorker:
    """The shipped worker script, with real trimesh, under the limits the function gives it and with
    credentials in the parent's environment."""

    def _convert(self, mod, tmp_path, input_name, text, output_filetype):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        (input_dir / input_name).write_text(text)
        output_file = str(export_dir / (os.path.splitext(input_name)[0] + output_filetype))
        job = _job(input_file=str(input_dir / input_name), input_dir=str(input_dir),
                   input_relative_path=input_name, by_reference={}, output_file=output_file,
                   output_filetype=output_filetype, input_name=input_name,
                   address_space_bytes=5308 * 1024 ** 2 + size_check.ADDRESS_SPACE_OVERHEAD_BYTES)
        env = {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "AWS_SECRET_ACCESS_KEY": "secret"}
        with patch.dict(os.environ, env):
            mod.run_worker(job, str(tmp_path), timeout_seconds=120)
        return output_file

    def test_a_model_converts_under_the_function_limits(self, tmp_path):
        output = self._convert(_load(), tmp_path, "t.stl", _ASCII_STL, ".glb")
        assert os.path.getsize(output) > 0

    def test_a_model_without_triangles_fails_with_the_handlers_message(self, tmp_path):
        with pytest.raises(ValueError, match=r"^c\.xyz contains no triangles \(it is a point cloud\)"):
            self._convert(_load(), tmp_path, "c.xyz", "0 0 0\n1 0 0\n0 1 0\n", ".stl")
