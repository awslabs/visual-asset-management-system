#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Xvfb is started by the Lambda handler, not by an image entrypoint.

The PyPI vtk wheel renders through GLX, so an off-screen render still needs an X server. A Lambda image
has no entrypoint of its own to start one in, so the handler does it once per execution environment,
with the server's socket and lock under /tmp — the only writable path — and reuses it on warm calls."""

import os
from unittest.mock import patch

import pytest

from preview_pipeline.analysis import display


class _FakeXvfb:
    def __init__(self, socket_file, exit_code=None):
        self.socket_file = socket_file
        self.returncode = exit_code
        if exit_code is None:
            with open(socket_file, "wb"):
                pass

    def poll(self):
        return self.returncode


def _fake_popen(calls, socket_file, exit_code=None):
    def _popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _FakeXvfb(socket_file, exit_code)
    return _popen


@pytest.fixture
def no_server(monkeypatch):
    monkeypatch.setattr(display, "_process", None)
    monkeypatch.delenv("DISPLAY", raising=False)


def _dirs(tmp_path):
    """Socket and lock directories redirected into tmp_path: the module's defaults are the real X server
    locations, and xvfb-run's default server number is 99, so a test must never touch /tmp/.X99-lock."""
    return {"socket_dir": str(tmp_path / "sockets"), "lock_dir": str(tmp_path / "locks")}


@pytest.mark.unit
class TestEnsureDisplay:
    def test_starts_xvfb_with_a_fixed_argument_list_and_exports_display(self, tmp_path, no_server):
        dirs = _dirs(tmp_path)
        socket_file = display.socket_path(":99", dirs["socket_dir"])
        calls = []
        with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                patch.object(display.subprocess, "Popen", _fake_popen(calls, socket_file)):
            assert display.ensure_display(":99", timeout_seconds=1.0, **dirs) == ":99"

        assert [c[0] for c in calls] == [
            ["Xvfb", ":99", "-screen", "0", "1280x1024x24", "-nolisten", "tcp", "-noreset"]]
        assert calls[0][1]["stdout"] is display.subprocess.DEVNULL
        assert calls[0][1]["stderr"] is display.subprocess.DEVNULL
        assert os.environ["DISPLAY"] == ":99"
        assert os.path.exists(socket_file)
        assert display.is_running() is True

    def test_a_running_server_is_reused(self, tmp_path, no_server):
        dirs = _dirs(tmp_path)
        socket_file = display.socket_path(":99", dirs["socket_dir"])
        calls = []
        with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                patch.object(display.subprocess, "Popen", _fake_popen(calls, socket_file)):
            display.ensure_display(":99", timeout_seconds=1.0, **dirs)
            display.ensure_display(":99", timeout_seconds=1.0, **dirs)
        assert len(calls) == 1

    def test_a_stale_socket_and_lock_are_removed_before_the_start(self, tmp_path, no_server):
        """A socket or lock left by a server that no longer runs blocks the new one from binding the
        display. Both live under tmp_path here, so the removal is asserted rather than assumed."""
        dirs = _dirs(tmp_path)
        os.makedirs(dirs["socket_dir"])
        os.makedirs(dirs["lock_dir"])
        socket_file = display.socket_path(":99", dirs["socket_dir"])
        lock_file = display.lock_path(":99", dirs["lock_dir"])
        for stale in (socket_file, lock_file):
            with open(stale, "wb") as handle:
                handle.write(b"stale")
        seen = {}

        def _popen(cmd, **kwargs):
            seen["socket_present_at_start"] = os.path.exists(socket_file)
            seen["lock_present_at_start"] = os.path.exists(lock_file)
            return _FakeXvfb(socket_file)

        with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                patch.object(display.subprocess, "Popen", _popen):
            display.ensure_display(":99", timeout_seconds=1.0, **dirs)
        assert seen == {"socket_present_at_start": False, "lock_present_at_start": False}
        assert os.path.getsize(socket_file) == 0, "the socket present afterwards is the new server's"
        assert not os.path.exists(lock_file), "the fake server writes no lock, so none may remain"

    def test_a_missing_binary_is_a_clear_error(self, tmp_path, no_server):
        with patch.object(display.shutil, "which", return_value=None):
            with pytest.raises(RuntimeError, match="Xvfb is not installed"):
                display.ensure_display(":99", timeout_seconds=1.0, **_dirs(tmp_path))

    def test_an_early_exit_is_reported_with_its_code(self, tmp_path, no_server):
        dirs = _dirs(tmp_path)
        socket_file = display.socket_path(":99", dirs["socket_dir"])
        with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                patch.object(display.subprocess, "Popen", _fake_popen([], socket_file, exit_code=1)):
            with pytest.raises(RuntimeError, match="exited with code 1"):
                display.ensure_display(":99", timeout_seconds=1.0, **dirs)
        assert display.is_running() is False

    def test_a_server_that_never_opens_the_socket_times_out(self, tmp_path, no_server):
        class _Silent:
            returncode = None

            def poll(self):
                return None

        with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                patch.object(display.subprocess, "Popen", lambda cmd, **kw: _Silent()):
            with pytest.raises(RuntimeError, match="did not open :99"):
                display.ensure_display(":99", timeout_seconds=0.2, **_dirs(tmp_path))


@pytest.mark.unit
class TestPaths:
    def test_socket_and_lock_follow_the_x_server_conventions(self):
        assert display.socket_path(":99").replace("\\", "/") == "/tmp/.X11-unix/X99"
        assert display.lock_path(":99").replace("\\", "/") == "/tmp/.X99-lock"
        assert display.lock_path(":99", "/elsewhere").replace("\\", "/") == "/elsewhere/.X99-lock"
        assert display.X11_SOCKET_DIR == "/tmp/.X11-unix"
        assert display.X11_LOCK_DIR == "/tmp"
        assert display.DEFAULT_DISPLAY == ":99"
