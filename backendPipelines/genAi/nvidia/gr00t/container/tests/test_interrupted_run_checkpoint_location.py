#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Where an interrupted fine-tuning run's checkpoints actually land, and what says so.

The incremental sync writes to ``outputS3AssetFilesPath`` — the execution's own STAGING prefix,
``pipelines/{pipelineName}/{jobName}/output/{executionId}/files/``, which the workflow's process-output
step promotes onto the asset when the run SUCCEEDS. ``resolve_checkpoint_folder`` and
``download_checkpoint_from_s3`` read ``inputS3AssetPath``, the ASSET prefix. A failed or timed-out
attempt never reaches process-output, so its checkpoints are durable in S3 and invisible to the next
evaluation run: recovery is a copy from the staging prefix onto the asset.

The two prefixes are DIFFERENT VARIABLES, so a test that compares the sync's destination against the
final upload's destination compares one value with itself and cannot detect the gap. This file asserts
the distinction directly and pins the log line that names the staging URI, because that line is the
whole of what an operator has to work from after an interruption.

Its module name is private to this suite: every pipeline ships a top-level ``__main__.py``, so a
sibling suite loading one by path under a shared alias would assert against another pipeline's file.
"""

import importlib.util
import os
import sys

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)


def _load_container_entrypoint():
    spec = importlib.util.spec_from_file_location(
        "gr00t_container_entrypoint_interrupted_location",
        os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


container = _load_container_entrypoint()

# The two paths as the resolved pipeline definition carries them: the staging prefix the workflow
# hands the step, and the asset prefix the asset lives under. Deliberately different buckets-relative
# locations, which is the point.
STAGING = "s3://run-bucket/pipelines/gr00t-finetune/finetune/output/exec-1/files/"
ASSET = "s3://asset-bucket/xid-abc/"
FOLDER = "gr00tOutput_N1.5-3B_trainingjob_20260101T000000_job1"


@pytest.mark.unit
class TestWhereAnInterruptedRunsCheckpointsLand:
    def test_the_sync_destination_is_the_staging_prefix_not_the_asset_prefix(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(container, "upload_output_to_s3",
                            lambda local, path, folder: recorded.append((path, folder)))
        monkeypatch.setattr(container, "log_free_space", lambda path: None)

        container.PeriodicOutputUpload(STAGING, FOLDER).sync_once()

        assert recorded == [(STAGING, FOLDER)]
        # The distinction the corrected comment rests on. Without this the suite could assert the
        # destination against itself and never notice which prefix it is.
        assert recorded[0][0] != ASSET

    def test_the_recovery_read_sources_the_asset_prefix(self, monkeypatch):
        """The other half of the mismatch, from the real download path rather than from its docstring."""
        commands = []

        class _Result:
            returncode = 0
            stderr = ""

        def _run(cmd, **kwargs):
            commands.append(cmd)
            return _Result()

        monkeypatch.setattr(container.subprocess, "run", _run)
        local = container.Path(os.environ.get("TEMP", "/tmp")) / "gr00t-recovery-probe"
        with pytest.raises(ValueError):
            # No model lands in the (empty) local dir, so it raises AFTER issuing the sync — which is
            # the command being inspected.
            container.download_checkpoint_from_s3(ASSET, FOLDER, local)

        sync = next(cmd for cmd in commands if cmd[:3] == ["aws", "s3", "sync"])
        assert sync[3] == f"{ASSET.rstrip('/')}/{FOLDER}/"
        assert not sync[3].startswith(STAGING)

    def test_the_uploader_logs_the_staging_uri_it_can_be_recovered_from(self, monkeypatch, caplog):
        """The operator-facing half. A durable-but-unreachable folder is only recoverable if the run's
        own log says where it is, so the URI has to be logged rather than inferred from the prefix
        convention."""
        monkeypatch.setattr(container, "upload_output_to_s3", lambda *a, **k: None)
        monkeypatch.setattr(container, "log_free_space", lambda path: None)

        messages = []
        monkeypatch.setattr(container.logger, "info", lambda message: messages.append(str(message)))

        uploader = container.PeriodicOutputUpload(STAGING, FOLDER, interval_seconds=3600)
        with uploader:
            pass

        joined = "\n".join(messages)
        assert f"{STAGING.rstrip('/')}/{FOLDER}/" in joined, joined
        assert "recoverable" in joined
        assert "promoted onto the asset only when the run succeeds" in joined

    def test_the_source_comment_does_not_claim_the_recovery_path_finds_them(self):
        """The claim this file exists to correct. The comment above the sync interval used to say the
        interrupted-run recovery in download_checkpoint_from_s3 would have something to find, which is
        false for exactly the reason the tests above measure — and a wrong comment about a mechanism is
        what sends the next reader's fix in the wrong direction."""
        with open(os.path.join(_CONTAINER_DIR, "__main__.py"), encoding="utf-8") as handle:
            source = handle.read()
        interval = source.index("CHECKPOINT_UPLOAD_INTERVAL_SECONDS = ")
        comment = source[source.index("# How often the output folder is synced"):interval]
        assert "STAGING prefix" in comment
        assert "read the ASSET prefix" in comment
        assert "recovery has nothing to find" not in comment
