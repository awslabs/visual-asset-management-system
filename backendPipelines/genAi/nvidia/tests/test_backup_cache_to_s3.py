#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the post-inference S3 backup of each NVIDIA pipeline's HuggingFace cache.

``backup_cache_to_s3`` runs after a successful run to persist the downloaded weights to S3 so the next
run restores them instead of downloading again -- the whole point of the cache-persistence work. Its body
guards with ``_has_cached_models(hf_home, repos)`` to skip a backup when nothing is cached, but the
function took only ``(hf_home, s3_bucket)``: ``repos`` was undefined in that scope, so the guard raised
``NameError`` on EVERY call. The caller treats the backup as non-fatal and swallows the exception, so the
backup silently never ran and no run's weights were ever persisted -- a green pipeline that quietly defeats
its own cache.

Nothing exercised ``backup_cache_to_s3`` before (``test_model_cache_check.py`` covers only
``_has_cached_models``), which is why the ``NameError`` stayed invisible. These tests call it directly
with ``subprocess`` stubbed so no S3 is reached, assert it does not raise, and assert it reaches the sync
path when this run's weights are present and skips it when they are not. Every pipeline's copy is
exercised through the ``model_manager`` fixture, because the copies have diverged and a fix applied to
four of five is the same defect with a smaller blast radius.
"""

import subprocess

import pytest


class _RecordingRun:
    """A ``subprocess.run`` stand-in that records the argv it was asked to run and reports success."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, *args, **kwargs):
        self.calls.append(argv)

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()


def _stub_subprocess(monkeypatch, model_manager):
    """Replace the module's ``subprocess`` so a backup reaches the sync path without touching S3."""
    recorder = _RecordingRun()
    fake = type("_FakeSubprocess", (), {
        "run": staticmethod(recorder),
        "TimeoutExpired": subprocess.TimeoutExpired,
    })
    monkeypatch.setattr(model_manager, "subprocess", fake)
    return recorder


def _synced_to_s3(recorder) -> bool:
    """Whether any recorded argv is the ``aws s3 sync <local> s3://...`` backup command."""
    for argv in recorder.calls:
        if (len(argv) >= 4 and argv[:3] == ["aws", "s3", "sync"]
                and any(str(part).startswith("s3://") for part in argv)):
            return True
    return False


@pytest.mark.unit
class TestBackupReachesS3WhenThisRunsWeightsArePresent:
    def test_backup_does_not_raise_name_error(self, model_manager, hf_cache, monkeypatch):
        """The D1 regression: the undefined ``repos`` in the guard raised NameError on every call.

        Fails on the old signature ``backup_cache_to_s3(hf_home, s3_bucket)`` because ``repos`` is
        undefined when the guard runs; passes once ``repos`` is a parameter threaded through.
        """
        _stub_subprocess(monkeypatch, model_manager)
        root = hf_cache("nvidia/Cosmos-Reason2-2B")

        # Must not raise -- NameError here is the defect.
        model_manager.backup_cache_to_s3(str(root), "model-cache-bucket",
                                         repos=("nvidia/Cosmos-Reason2-2B",))

    def test_backup_syncs_to_s3_when_the_runs_weights_are_cached(self, model_manager, hf_cache,
                                                                 monkeypatch):
        """With this run's weights present the guard passes and the sync command is issued."""
        recorder = _stub_subprocess(monkeypatch, model_manager)
        root = hf_cache("nvidia/Cosmos-Reason2-2B")

        model_manager.backup_cache_to_s3(str(root), "model-cache-bucket",
                                         repos=("nvidia/Cosmos-Reason2-2B",))

        assert _synced_to_s3(recorder), "a cached run must reach the aws s3 sync backup path"

    def test_backup_is_skipped_when_the_runs_weights_are_absent(self, model_manager, hf_cache,
                                                                monkeypatch):
        """A different variant's weights are not this run's: nothing to back up, no sync issued."""
        recorder = _stub_subprocess(monkeypatch, model_manager)
        root = hf_cache("nvidia/Cosmos-Reason2-2B")

        model_manager.backup_cache_to_s3(str(root), "model-cache-bucket",
                                         repos=("nvidia/Cosmos-Reason2-8B",))

        assert not _synced_to_s3(recorder), "an uncached run must skip the backup, not sync"
