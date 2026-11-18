#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/3dBasic: how the worker process is held down and how what it wrote is taken back.

The parent closes its own /proc files to the child, uploads only regular single-name files from the
export folder, stops every process the worker left running, applies the child's resource limits or
fails the job, reads the input only if it is still the object that was measured, and keeps the work
folder's path out of the run's error text."""

import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import size_check  # noqa: E402
import referenced_files as rf  # noqa: E402
from test_conversion_contract import _load  # noqa: E402
from test_3dbasic_referenced_files import FakeS3, ROOT, _fetch  # noqa: E402

_LAMBDA_DIR = os.path.dirname(_HERE)
_LINUX_USER = sys.platform.startswith("linux") and os.geteuid() != 0


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


def _real_worker_body(replacement):
    return ("sys.path.insert(0, %r)\nimport conversion_worker\n%s\n"
            "sys.exit(conversion_worker.main(sys.argv))\n") % (_LAMBDA_DIR, replacement)


@pytest.mark.unit
@pytest.mark.skipif(not _LINUX_USER, reason="needs Linux /proc and a non-root user")
class TestTheParentsProcFilesAreClosedToTheChild:
    def test_the_child_cannot_read_the_parents_environment(self, tmp_path):
        mod = _load()
        body = ("try:\n"
                "    open('/proc/%d/environ' % os.getppid(), 'rb').read(1)\n"
                "    read = True\n"
                "except OSError:\n"
                "    read = False\n"
                "json.dump({'read': read}, open(os.path.join(os.path.dirname(job_path), 'probe.json'), 'w'))\n"
                "json.dump({'ok': True}, open(result_path, 'w'))\n")
        _run(mod, tmp_path, body)
        assert json.loads((tmp_path / "probe.json").read_text()) == {"read": False}

    def test_the_parent_is_marked_non_dumpable(self):
        assert _load()._DUMPABLE_DISABLED is True
        import ctypes
        assert ctypes.CDLL(None).prctl(3, 0, 0, 0, 0) == 0  # PR_GET_DUMPABLE

    def test_a_platform_without_the_call_keeps_going(self):
        mod = _load()
        with patch("ctypes.CDLL", side_effect=OSError("no libc")):
            assert mod._disable_dumpable() is False
        with patch.object(mod.sys, "platform", "win32"):
            assert mod._disable_dumpable() is False


@pytest.mark.unit
class TestOnlyRegularFilesAreUploaded:
    def test_regular_files_in_nested_folders_are_listed(self, tmp_path):
        mod = _load()
        (tmp_path / "a.gltf").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.bin").write_text("y")
        assert mod.exported_files(str(tmp_path)) == ["a.gltf", "sub/b.bin"]

    def test_a_symlink_to_a_file_fails_the_run(self, tmp_path):
        mod = _load()
        target = tmp_path / "outside.txt"
        target.write_text("not output")
        export = tmp_path / "export"
        export.mkdir()
        os.symlink(target, export / "m.glb")
        with pytest.raises(RuntimeError, match=r"m\.glb, which is not a regular file"):
            mod.exported_files(str(export))

    def test_a_symlink_to_a_folder_fails_the_run(self, tmp_path):
        mod = _load()
        export = tmp_path / "export"
        export.mkdir()
        os.symlink(tmp_path, export / "up")
        with pytest.raises(RuntimeError, match="not a regular file"):
            mod.exported_files(str(export))

    def test_a_hard_link_fails_the_run(self, tmp_path):
        mod = _load()
        other = tmp_path / "other.txt"
        other.write_text("data")
        export = tmp_path / "export"
        export.mkdir()
        os.link(other, export / "m.glb")
        with pytest.raises(RuntimeError, match=r"m\.glb, which is not a regular file"):
            mod.exported_files(str(export))

    def test_a_worker_that_writes_a_link_fails_the_conversion_before_any_upload(self, tmp_path):
        mod = _load()
        export = tmp_path / "export"
        export.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("s")
        body = ("os.symlink(%r, %r)\njson.dump({'ok': True}, open(result_path, 'w'))\n"
                % (str(secret), str(export / "m.glb")))
        _run(mod, tmp_path, body)
        with pytest.raises(RuntimeError, match="not a regular file"):
            mod.exported_files(str(export))


@pytest.mark.unit
class TestNothingTheWorkerStartedSurvivesIt:
    def _tree(self, tmp_path, processes):
        """A fake /proc: pid -> (uid, ppid, start ticks, has_address_space)."""
        for pid, (uid, ppid, start, mem) in processes.items():
            folder = tmp_path / str(pid)
            folder.mkdir()
            text = f"Name:\tx\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\nPPid:\t{ppid}\n" + ("VmSize:\t10 kB\n" if mem else "")
            (folder / "status").write_text(text)
            fields = ["S", str(ppid)] + ["0"] * 17 + [str(start)] + ["0"] * 5
            (folder / "stat").write_text(f"{pid} (name) " + " ".join(fields) + "\n")
        return str(tmp_path)

    def test_it_selects_this_users_later_processes_and_spares_the_rest(self, tmp_path):
        mod = _load()
        root = self._tree(tmp_path, {
            1: (0, 0, 1, True),          # init: another user, and an ancestor
            50: (1000, 1, 100, True),    # the runtime's parent: an ancestor
            100: (1000, 50, 200, True),  # this process
            101: (1000, 1, 150, True),   # started before this process: an extension
            102: (1000, 1, 300, True),   # a descendant that left its session
            103: (1000, 102, 310, True),
            104: (1001, 1, 320, True),   # another user's
            105: (1000, 2, 330, False),  # kernel thread or zombie
        })
        killed = []
        result = mod.reap_other_processes(proc_root=root, uid=1000, own_pid=100,
                                          kill=lambda pid, sig: killed.append((pid, sig)), passes=1)
        assert sorted(result) == [102, 103]
        assert sorted(killed) == [(102, 9), (103, 9)]

    def test_a_process_that_is_already_gone_is_tolerated(self, tmp_path):
        mod = _load()
        root = self._tree(tmp_path, {100: (1000, 1, 200, True), 102: (1000, 1, 300, True)})

        def gone(pid, sig):
            raise ProcessLookupError()

        assert mod.reap_other_processes(proc_root=root, uid=1000, own_pid=100, kill=gone, passes=1) == []

    def test_it_does_nothing_outside_a_lambda_environment(self):
        mod = _load()
        with patch.dict(os.environ, {}, clear=False), patch.object(mod, "reap_other_processes") as reap:
            os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)
            assert mod.reap_stray_processes() == []
        assert not reap.called

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs Linux /proc")
    def test_a_descendant_in_its_own_session_is_stopped_when_the_worker_returns(self, tmp_path):
        mod = _load()
        pid_file = tmp_path / "escaped.pid"
        body = ("import subprocess\n"
                "child = subprocess.Popen(['sleep', '60'], start_new_session=True)\n"
                "open(%r, 'w').write(str(child.pid))\n"
                "json.dump({'ok': True}, open(result_path, 'w'))\n" % str(pid_file))
        seen = {}

        def only(pid):
            # Only the process the worker recorded, so nothing else of this user on the machine is touched.
            seen["pid"] = int(pid_file.read_text()) if pid_file.exists() else None
            return pid == seen["pid"]

        try:
            with patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_NAME": "f"}), \
                    patch.object(mod, "_STRAY_PROCESS_FILTER", only):
                _run(mod, tmp_path, body)
            pid = int(pid_file.read_text())
            for _ in range(50):
                try:
                    with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
                        if "VmSize:" not in handle.read():
                            break  # a zombie until its parent reaps it
                except OSError:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("the descendant outlived the worker")
        finally:
            if pid_file.exists():
                try:
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
                except OSError:
                    pass


@pytest.mark.unit
class TestTheWorkingDirectoryIsRemoved:
    def test_a_folder_the_worker_made_unreadable_is_removed(self, tmp_path):
        mod = _load()
        work = tmp_path / "work"
        (work / "export" / "locked").mkdir(parents=True)
        (work / "export" / "locked" / "f").write_text("x")
        os.chmod(work / "export" / "locked", 0o000)
        mod.remove_work_dir(str(work))
        assert not work.exists()

    def test_a_directory_that_cannot_be_removed_fails_the_run(self, tmp_path):
        mod = _load()
        work = tmp_path / "work"
        work.mkdir()
        with patch.object(mod.shutil, "rmtree"), pytest.raises(RuntimeError, match="could not be removed"):
            mod.remove_work_dir(str(work))


@pytest.mark.unit
class TestTheWorkersLimits:
    def test_the_file_size_limit_is_the_free_disk_less_headroom_with_a_floor(self):
        assert size_check.file_size_limit_bytes(0) == size_check.FILE_SIZE_FLOOR_BYTES
        free = 10 * 1024 ** 3
        assert size_check.file_size_limit_bytes(free) == free - size_check.DISK_HEADROOM_BYTES

    def test_the_file_size_limit_needs_a_measured_disk(self):
        with pytest.raises(TypeError):
            size_check.file_size_limit_bytes(None)

    def test_the_handler_passes_the_file_size_limit(self):
        mod = _load()
        captured = {}

        def _worker(job, work_dir, timeout_seconds=None):
            captured.update(job)
            open(job["output_file"], "w").close()

        with patch.object(mod, "run_worker", _worker), \
                patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: open(p, "w").close())), \
                patch.object(mod, "uploadV2", MagicMock()), \
                patch.object(size_check, "free_disk_bytes", return_value=5 * 1024 ** 3), \
                patch.object(mod, "head_input", MagicMock(return_value=(100 * 1024 ** 2, ""))):
            mod.convert_input_output("s3://abkt/xid/m.stl", "s3://obkt/files/", ".glb")
        assert captured["file_size_bytes"] == 5 * 1024 ** 3 - size_check.DISK_HEADROOM_BYTES

    def test_a_photo_textured_glb_converts_to_obj_under_the_handlers_limit(self, tmp_path):
        # An OBJ export re-encodes the texture as PNG, which for a photographic texture is several
        # times the size of the GLB that holds it as JPEG.
        # The suite stubs trimesh in this process, so the model is built in a child interpreter.
        glb = tmp_path / "photo.glb"
        build = (
            "import sys, numpy as np, trimesh\n"
            "from PIL import Image\n"
            "n = 6000\n"
            "rng = np.random.default_rng(5)\n"
            "ramp = np.linspace(0, 255, n, dtype=np.float32)\n"
            "photo = (ramp[None, :, None] * 0.5 + ramp[:, None, None] * 0.5\n"
            "         + rng.normal(0, 18, (n, n, 3))).clip(0, 255).astype(np.uint8)\n"
            "Image.fromarray(photo).save('photo.jpg', quality=90)\n"
            "quad = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], faces=[[0, 1, 2], [0, 2, 3]],\n"
            "    visual=trimesh.visual.TextureVisuals(uv=[[0, 0], [1, 0], [1, 1], [0, 1]], image=Image.open('photo.jpg')))\n"
            "open(sys.argv[1], 'wb').write(quad.export(file_type='glb'))\n")
        made = subprocess.run([sys.executable, "-c", build, str(glb)], cwd=tmp_path, capture_output=True, text=True)
        if made.returncode != 0:
            pytest.skip(f"trimesh, numpy and Pillow are needed to build the model: {made.stderr[-200:]}")
        mod = _load()
        uploaded = {}
        client = MagicMock()
        with patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: shutil.copy(glb, p))), \
                patch.object(mod, "uploadV2", MagicMock(side_effect=lambda b, k, p: uploaded.update({k: os.path.getsize(p)}))), \
                patch.object(mod, "_s3_client_for_bucket", lambda bucket: client), \
                patch.object(mod, "head_input", MagicMock(return_value=(glb.stat().st_size, ""))), \
                patch.object(size_check, "memory_limit_bytes", return_value=None):
            mod.convert_input_output("s3://abkt/xid/photo.glb", "s3://obkt/files/", ".obj")
        png = [size for key, size in uploaded.items() if key.endswith(".png")]
        assert png and png[0] > max(3.5 * glb.stat().st_size, size_check.FILE_SIZE_FLOOR_BYTES)

    def test_a_write_past_the_file_size_limit_fails_the_job(self, tmp_path):
        mod = _load()
        body = _real_worker_body(
            "conversion_worker.convert_model = lambda job: open(job['output_file'], 'wb').write(b'x' * (3 * 1024 ** 2))")
        with patch.object(mod, "logger"), pytest.raises(RuntimeError, match="failed unexpectedly"):
            _run(mod, tmp_path, body, file_size_bytes=1024 ** 2, output_file=str(tmp_path / "out.bin"))
        assert (tmp_path / "out.bin").stat().st_size <= 1024 ** 2

    def test_the_worker_runs_with_core_dumps_off_and_the_limits_set(self, tmp_path):
        mod = _load()
        body = _real_worker_body(
            "import resource\n"
            "def probe(job):\n"
            "    json.dump({'core': resource.getrlimit(resource.RLIMIT_CORE), 'fsize': resource.getrlimit(resource.RLIMIT_FSIZE)},\n"
            "              open(os.path.join(os.path.dirname(job_path), 'limits.json'), 'w'))\n"
            "conversion_worker.convert_model = probe")
        _run(mod, tmp_path, body, file_size_bytes=7 * 1024 ** 2)
        seen = json.loads((tmp_path / "limits.json").read_text())
        assert seen["core"] == [0, 0]
        assert seen["fsize"] == [7 * 1024 ** 2, 7 * 1024 ** 2]

    def test_a_limit_that_cannot_be_applied_fails_the_job_instead_of_running_without_it(self, tmp_path):
        mod = _load()
        body = _real_worker_body(
            "import resource\n"
            "def refuse(which, limits):\n"
            "    raise ValueError('refused')\n"
            "resource.setrlimit = refuse\n"
            "conversion_worker.convert_model = lambda job: open(job['output_file'], 'w').write('converted')")
        with patch.object(mod, "logger") as logger, pytest.raises(RuntimeError, match="failed unexpectedly"):
            _run(mod, tmp_path, body, address_space_bytes=8 * 1024 ** 3, output_file=str(tmp_path / "out.bin"))
        assert not (tmp_path / "out.bin").exists()
        assert any("could not be applied" in str(call) for call in logger.error.call_args_list)


@pytest.mark.unit
class TestTheInputIsReadOnlyIfItIsTheObjectThatWasMeasured:
    def _client(self, mod, body=b"abc"):
        client = MagicMock()
        client.get_object.return_value = {"Body": io.BytesIO(body)}
        return client, patch.object(mod, "_s3_client_for_bucket", lambda bucket: client)

    def test_the_head_answer_carries_the_size_and_the_etag(self):
        client = MagicMock()
        client.head_object.return_value = {"ContentLength": 7, "ETag": '"abc"'}
        assert size_check.head_object_info(client, "b", "k") == (7, '"abc"')
        assert size_check.head_size(client, "b", "k") == 7

    def test_the_current_object_is_read_only_if_it_still_has_the_measured_etag(self, tmp_path):
        mod = _load()
        client, patched = self._client(mod)
        path = tmp_path / "m.stl"
        with patched:
            mod.download("abkt", "xid/m.stl", str(path), etag='"abc"', expected_size=3)
        client.get_object.assert_called_once_with(Bucket="abkt", Key="xid/m.stl", IfMatch='"abc"')
        assert path.read_bytes() == b"abc"

    def test_a_versioned_read_stays_pinned_to_the_version(self, tmp_path):
        mod = _load()
        client, patched = self._client(mod)
        with patched:
            mod.download("abkt", "xid/m.stl", str(tmp_path / "m.stl"), version_id="v1", etag='"abc"')
        client.download_fileobj.assert_called_once()
        assert client.download_fileobj.call_args.kwargs["ExtraArgs"] == {"VersionId": "v1"}
        assert not client.get_object.called

    def test_an_object_replaced_after_the_check_is_refused(self, tmp_path):
        mod = _load()
        client, patched = self._client(mod)
        client.get_object.side_effect = ClientError({"Error": {"Code": "PreconditionFailed"}}, "GetObject")
        with patched, pytest.raises(Exception, match="was changed while the run was starting"):
            mod.download("abkt", "xid/m.stl", str(tmp_path / "m.stl"), etag='"abc"')

    def test_a_file_of_another_size_than_measured_is_refused(self, tmp_path):
        mod = _load()
        client, patched = self._client(mod, b"abcdef")
        with patched, pytest.raises(Exception, match="was changed while the run was starting"):
            mod.download("abkt", "xid/m.stl", str(tmp_path / "m.stl"), etag='"abc"', expected_size=3)

    def test_a_referenced_file_of_another_size_than_measured_is_skipped(self, tmp_path):
        class Growing(FakeS3):
            def download_fileobj(self, Bucket, Key, Fileobj, ExtraArgs=None):  # noqa: N803
                super().download_fileobj(Bucket, Key, Fileobj, ExtraArgs)
                Fileobj.write(b"more")

        client = Growing({ROOT + "m.mtl": b"newmtl a\n"})
        _, report = _fetch(tmp_path, client, "m.obj", "mtllib m.mtl\nv 0 0 0\n")
        assert "m.mtl" not in report.fetched
        assert report.skipped_count == 1
        assert not (tmp_path / "input" / "m.mtl").exists()


@pytest.mark.unit
class TestWorkerMessagesDoNotCarryThePathOfTheWorkFolder:
    def test_the_work_folder_is_removed_from_a_reported_user_error(self, tmp_path):
        mod = _load()
        body = ("json.dump({'ok': False, 'type': 'ValueError', 'message': 'bad file %s/input/m.stl at line 3'},"
                " open(result_path, 'w'))\nsys.exit(1)\n" % str(tmp_path))
        with pytest.raises(ValueError) as excinfo:
            _run(mod, tmp_path, body)
        assert str(tmp_path) not in str(excinfo.value)
        assert str(excinfo.value) == "bad file input/m.stl at line 3"
