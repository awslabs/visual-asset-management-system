#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The X display the analysis renders on: reused when a server already runs, started otherwise.

The PyPI vtk wheel renders through GLX, so an off-screen render still needs an X server. A Lambda image
has no entrypoint of its own to start one in, so the handler does it once per execution environment,
with the server's socket and lock under /tmp — the only writable path — and reuses it on warm calls. The
Fargate image's ENTRYPOINT starts Xvfb on :99 before the SYSTEM GenAI render job runs, and that server is
reused as it is: its lock and socket are never removed and no second server is started on its display."""

import os
import shutil
import socket
import subprocess  # nosec B404 - the tests start Xvfb and a short Python child with fixed argument lists
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import pytest

from preview_pipeline.analysis import display

needs_unix_sockets = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs AF_UNIX sockets")
needs_proc = pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="needs /proc (Linux)")


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


@pytest.fixture
def x_dirs():
    """Socket and lock directories under a short temporary root: a listening AF_UNIX socket's path is
    limited to about 107 bytes, which a pytest tmp_path can exceed."""
    root = tempfile.mkdtemp(prefix="vx11-")
    dirs = {"socket_dir": os.path.join(root, "s"), "lock_dir": os.path.join(root, "l")}
    os.makedirs(dirs["socket_dir"])
    os.makedirs(dirs["lock_dir"])
    yield dirs
    shutil.rmtree(root, ignore_errors=True)


class _Server:
    """A listening socket at the display's socket path: a stand-in for an X server another process runs
    (the Fargate image's ENTRYPOINT, xvfb-run). ``closed`` leaves the socket file of a server that exited."""

    def __init__(self, path, closed=False):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(8)
        if closed:
            self.close()

    def close(self):
        self.sock.close()


def _write_lock(lock_dir, disp, pid):
    """The lock an X server writes: its pid, right-aligned in ten characters."""
    with open(display.lock_path(disp, lock_dir), "w") as handle:
        handle.write(f"{pid:>10}\n")


def _exited_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])  # nosec B603 - fixed argument list
    child.wait()
    return child.pid


def _refuse_start(calls):
    def _popen(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("a second X server was started on a display another process serves")
    return _popen


@pytest.mark.unit
@needs_unix_sockets
@needs_proc
class TestAServerAnotherProcessRuns:
    def test_the_entrypoint_server_is_reused_and_its_lock_and_socket_stay(self, x_dirs, no_server):
        """The Fargate render job: the image's ENTRYPOINT already serves :99. The render reuses it; removing
        its lock and socket and starting a second Xvfb on :99 makes that one exit 1."""
        sock = display.socket_path(":99", x_dirs["socket_dir"])
        server = _Server(sock)
        _write_lock(x_dirs["lock_dir"], ":99", os.getpid())
        calls = []
        try:
            with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                    patch.object(display.subprocess, "Popen", _refuse_start(calls)):
                assert display.ensure_display(":99", timeout_seconds=1.0, **x_dirs) == ":99"
            assert calls == []
            assert os.environ["DISPLAY"] == ":99"
            assert display.lock_owner(":99", x_dirs["lock_dir"]) == os.getpid(), "the server's lock stays"
            assert display.answers(sock), "the server's socket stays and still accepts connections"
            assert display.is_running() is False, "no server of this module's own"
        finally:
            server.close()

    def test_the_server_display_names_is_reused(self, x_dirs, no_server, monkeypatch):
        """xvfb-run picks a free display and sets DISPLAY to it; that server is used, not a new :99."""
        monkeypatch.setenv("DISPLAY", ":42")
        server = _Server(display.socket_path(":42", x_dirs["socket_dir"]))
        calls = []
        try:
            with patch.object(display.subprocess, "Popen", _refuse_start(calls)):
                assert display.ensure_display(":99", timeout_seconds=1.0, **x_dirs) == ":42"
            assert calls == [] and os.environ["DISPLAY"] == ":42"
            assert not os.path.exists(display.socket_path(":99", x_dirs["socket_dir"]))
        finally:
            server.close()

    def test_a_server_still_starting_is_waited_for(self, x_dirs, no_server):
        """A live process holds the display's lock and opens its socket a moment later (the ENTRYPOINT's
        Xvfb when the render comes first): the render waits for it rather than starting another."""
        sock = display.socket_path(":99", x_dirs["socket_dir"])
        _write_lock(x_dirs["lock_dir"], ":99", os.getpid())
        started = []
        opener = threading.Timer(0.3, lambda: started.append(_Server(sock)))
        opener.start()
        calls = []
        try:
            with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                    patch.object(display.subprocess, "Popen", _refuse_start(calls)):
                assert display.ensure_display(":99", timeout_seconds=5.0, **x_dirs) == ":99"
            assert calls == [] and started
        finally:
            opener.join()
            for server in started:
                server.close()

    def test_a_held_display_that_never_answers_is_an_error_and_its_lock_stays(self, x_dirs, no_server):
        _write_lock(x_dirs["lock_dir"], ":99", os.getpid())
        calls = []
        with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                patch.object(display.subprocess, "Popen", _refuse_start(calls)):
            with pytest.raises(RuntimeError, match=rf"holding :99 \(pid {os.getpid()}\) did not answer"):
                display.ensure_display(":99", timeout_seconds=0.3, **x_dirs)
        assert calls == []
        assert os.path.exists(display.lock_path(":99", x_dirs["lock_dir"]))

    def test_the_lock_and_socket_of_an_exited_server_are_removed_before_the_start(self, x_dirs, no_server):
        sock = display.socket_path(":99", x_dirs["socket_dir"])
        lock = display.lock_path(":99", x_dirs["lock_dir"])
        _Server(sock, closed=True)
        _write_lock(x_dirs["lock_dir"], ":99", _exited_pid())
        assert os.path.exists(sock) and not display.answers(sock)
        seen = {}

        def _popen(cmd, **kwargs):
            seen["at_start"] = (os.path.exists(lock), os.path.exists(sock))
            return _FakeXvfb(sock)

        with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                patch.object(display.subprocess, "Popen", _popen):
            assert display.ensure_display(":99", timeout_seconds=1.0, **x_dirs) == ":99"
        assert seen == {"at_start": (False, False)}
        assert display.is_running() is True

    def test_losing_the_start_race_reuses_the_server_that_won(self, x_dirs, no_server):
        """Nothing held the display when it was checked, then another process's server bound it first: the
        Xvfb started here exits 1, and the render uses the winner."""
        sock = display.socket_path(":99", x_dirs["socket_dir"])
        winners = []

        def _popen(cmd, **kwargs):
            winners.append(_Server(sock))
            _write_lock(x_dirs["lock_dir"], ":99", os.getpid())
            return _FakeXvfb(os.path.join(x_dirs["socket_dir"], "unused"), exit_code=1)

        try:
            with patch.object(display.shutil, "which", return_value="/usr/bin/Xvfb"), \
                    patch.object(display.subprocess, "Popen", _popen):
                assert display.ensure_display(":99", timeout_seconds=1.0, **x_dirs) == ":99"
            assert display.is_running() is False
        finally:
            for server in winners:
                server.close()


@pytest.mark.unit
@needs_proc
class TestLockOwner:
    def test_a_live_pid_owns_the_lock_and_an_exited_one_does_not(self, x_dirs):
        _write_lock(x_dirs["lock_dir"], ":7", os.getpid())
        assert display.lock_owner(":7", x_dirs["lock_dir"]) == os.getpid()
        _write_lock(x_dirs["lock_dir"], ":7", _exited_pid())
        assert display.lock_owner(":7", x_dirs["lock_dir"]) is None
        assert display.lock_owner(":8", x_dirs["lock_dir"]) is None, "no lock"

    def test_a_zombie_counts_as_exited(self):
        """The ENTRYPOINT execs Python, which never reaps a dead Xvfb: its pid stays as a zombie."""
        child = subprocess.Popen([sys.executable, "-c", "pass"])  # nosec B603 - fixed argument list
        try:
            deadline = time.monotonic() + 10
            state = b""
            while time.monotonic() < deadline:
                with open(f"/proc/{child.pid}/stat", "rb") as handle:
                    state = handle.read().rsplit(b")", 1)[-1].split()[0]
                if state == b"Z":
                    break
                time.sleep(0.02)
            assert state == b"Z"
            assert display.process_alive(child.pid) is False
        finally:
            child.wait()
        assert display.process_alive(os.getpid()) is True


@pytest.mark.unit
class TestLocalDisplay:
    @pytest.mark.parametrize("value,expected", [
        (":99", ":99"), (":1.0", ":1"), ("unix:5", ":5"), (" :3 ", ":3"),
        ("host:0", None), ("localhost:10.0", None), ("", None), (None, None), ("garbage", None),
    ])
    def test_only_a_server_on_this_host_is_a_local_display(self, value, expected):
        assert display.local_display(value) == expected


@pytest.mark.skipif(shutil.which("Xvfb") is None or not os.path.isdir("/proc/self"),
                    reason="needs Xvfb (the pipeline image, or CI's headless display)")
class TestARealXvfb:
    def test_a_server_started_the_way_the_entrypoint_does_is_reused(self, no_server):
        """A real Xvfb started as the Fargate image's ENTRYPOINT starts it, on a display no other server
        uses, then the render's display bootstrap: the server keeps running with its own lock, and no
        second server is started (a second Xvfb on that display exits 1)."""
        number = next(n for n in range(150, 250)
                      if not os.path.exists(display.lock_path(f":{n}"))
                      and not os.path.exists(display.socket_path(f":{n}")))
        disp = f":{number}"
        server = subprocess.Popen(  # nosec B603 B607 - fixed argument list
            ["Xvfb", disp, "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not display.answers(display.socket_path(disp)):
                assert server.poll() is None, "the test's Xvfb exited"
                time.sleep(0.05)
            assert display.answers(display.socket_path(disp))

            assert display.ensure_display(disp, timeout_seconds=10.0) == disp
            assert os.environ["DISPLAY"] == disp
            assert server.poll() is None, "the server another process started still runs"
            assert display.lock_owner(disp) == server.pid
            assert display.answers(display.socket_path(disp))
            assert display.is_running() is False, "no Xvfb of this module's own was started"
        finally:
            server.terminate()
            server.wait(timeout=10)


@pytest.mark.unit
class TestPaths:
    def test_socket_and_lock_follow_the_x_server_conventions(self):
        assert display.socket_path(":99").replace("\\", "/") == "/tmp/.X11-unix/X99"
        assert display.lock_path(":99").replace("\\", "/") == "/tmp/.X99-lock"
        assert display.lock_path(":99", "/elsewhere").replace("\\", "/") == "/elsewhere/.X99-lock"
        assert display.X11_SOCKET_DIR == "/tmp/.X11-unix"
        assert display.X11_LOCK_DIR == "/tmp"
        assert display.DEFAULT_DISPLAY == ":99"
