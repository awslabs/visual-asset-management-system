#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Work directories resolve against the runtime's writable root.

The Fargate image runs from WORKDIR /app with a pre-created /app/tmp, so a RELATIVE ``tmp/input`` is
correct there. AWS Lambda mounts everything read-only except /tmp and announces itself through
LAMBDA_TASK_ROOT, so the same call has to land under /tmp — and the caches PyVista, VTK and matplotlib
keep under HOME / MPLCONFIGDIR have to be created at cold start, because /tmp starts empty."""

import os
from unittest.mock import patch

import pytest

from preview_pipeline import core
from preview_pipeline.analysis import workdirs


class _FakePath:
    made = []

    def __init__(self, path):
        self.path = path

    def mkdir(self, parents=False):
        _FakePath.made.append((self.path, parents))


@pytest.mark.unit
class TestWorkRoot:
    def test_is_the_cwd_outside_lambda(self, monkeypatch):
        monkeypatch.delenv("LAMBDA_TASK_ROOT", raising=False)
        assert workdirs.is_lambda() is False
        assert workdirs.work_root() == ""

    def test_is_tmp_in_lambda(self, monkeypatch):
        monkeypatch.setenv("LAMBDA_TASK_ROOT", "/var/task")
        assert workdirs.is_lambda() is True
        assert workdirs.work_root() == workdirs.LAMBDA_WORK_ROOT == "/tmp"


@pytest.mark.unit
class TestCoreCreateDir:
    def _create(self, monkeypatch, parts):
        _FakePath.made = []
        with patch.object(core, "Path", _FakePath), \
                patch.object(core.os.path, "exists", return_value=False):
            return core._create_dir(parts)

    def test_stays_relative_outside_lambda(self, monkeypatch):
        """Control: the Fargate image pre-creates /app/tmp for exactly this relative path."""
        monkeypatch.delenv("LAMBDA_TASK_ROOT", raising=False)
        result = self._create(monkeypatch, ["tmp", "input"])
        assert result.replace("\\", "/") == "tmp/input"
        assert _FakePath.made == [(result, True)]

    def test_resolves_under_tmp_in_lambda(self, monkeypatch):
        monkeypatch.setenv("LAMBDA_TASK_ROOT", "/var/task")
        result = self._create(monkeypatch, ["tmp", "input"])
        assert result.replace("\\", "/") == "/tmp/tmp/input"
        assert _FakePath.made == [(result, True)]


@pytest.mark.unit
class TestRuntimeDirs:
    def test_creates_only_the_cache_paths_under_the_work_root(self, monkeypatch, tmp_path):
        root = str(tmp_path).replace("\\", "/")
        monkeypatch.setenv("LAMBDA_TASK_ROOT", "/var/task")
        monkeypatch.setattr(workdirs, "work_root", lambda: root)
        monkeypatch.setenv("HOME", root + "/home")
        monkeypatch.setenv("MPLCONFIGDIR", root + "/home/matplotlib")
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/var/elsewhere/runtime")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

        created = workdirs.ensure_runtime_dirs()

        assert sorted(created) == sorted([root + "/home", root + "/home/matplotlib"])
        assert os.path.isdir(root + "/home/matplotlib")
        assert not os.path.exists("/var/elsewhere/runtime")

    def test_is_a_no_op_outside_lambda(self, monkeypatch):
        monkeypatch.delenv("LAMBDA_TASK_ROOT", raising=False)
        monkeypatch.setenv("HOME", "/nonexistent-home-for-this-test")
        assert workdirs.ensure_runtime_dirs() == []

    def test_is_idempotent(self, monkeypatch, tmp_path):
        root = str(tmp_path).replace("\\", "/")
        monkeypatch.setenv("LAMBDA_TASK_ROOT", "/var/task")
        monkeypatch.setattr(workdirs, "work_root", lambda: root)
        monkeypatch.setenv("HOME", root + "/home")
        for var in ("MPLCONFIGDIR", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME"):
            monkeypatch.delenv(var, raising=False)
        assert workdirs.ensure_runtime_dirs() == [root + "/home"]
        assert workdirs.ensure_runtime_dirs() == []


@pytest.mark.unit
class TestInvocationDir:
    def test_is_fresh_under_the_work_root_and_removable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(workdirs, "work_root", lambda: str(tmp_path))
        first = workdirs.make_invocation_dir("render3d_")
        second = workdirs.make_invocation_dir("render3d_")
        assert first != second
        for path in (first, second):
            assert os.path.isdir(path)
            assert os.path.dirname(path) == str(tmp_path)
            assert os.path.basename(path).startswith("render3d_")
        workdirs.remove_dir(first)
        assert not os.path.exists(first)
        workdirs.remove_dir(first)  # removing twice is not an error

    def test_uses_the_system_temp_dir_outside_lambda(self, monkeypatch):
        monkeypatch.setattr(workdirs, "work_root", lambda: "")
        path = workdirs.make_invocation_dir("render3d_")
        try:
            assert os.path.isdir(path)
        finally:
            workdirs.remove_dir(path)
