#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Fargate render branch entry module: exit codes and the state hand-off to the render3d handler."""

import json
import os
import sys
from unittest.mock import MagicMock

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

# lambda_handler imports the rendering stack at module level; the entry module only needs its name.
sys.modules.setdefault("lambda_handler", MagicMock())

from preview_pipeline.analysis import batch_job  # noqa: E402


def test_missing_state_returns_2(monkeypatch, capsys):
    monkeypatch.delenv("ANALYSIS_STATE_JSON", raising=False)
    assert batch_job.main() == 2
    assert "ANALYSIS_STATE_JSON" in capsys.readouterr().err


def test_invalid_json_returns_1(monkeypatch):
    monkeypatch.setenv("ANALYSIS_STATE_JSON", "{not json")
    assert batch_job.main() == 1


def test_handler_exception_returns_1(monkeypatch, capsys):
    monkeypatch.setenv("ANALYSIS_STATE_JSON", json.dumps({"renderBranch": "FARGATE"}))

    def boom(state, context):
        raise RuntimeError("render failed")

    monkeypatch.setattr(batch_job.lambda_handler, "lambda_handler", boom)
    assert batch_job.main() == 1
    assert "render failed" in capsys.readouterr().err


def test_success_passes_state_and_context(monkeypatch):
    state = {"renderBranch": "FARGATE", "fileClass": "mesh", "renderViews": 8}
    monkeypatch.setenv("ANALYSIS_STATE_JSON", json.dumps(state))
    monkeypatch.setenv("AWS_BATCH_JOB_ID", "job-123")
    seen = {}

    def handler(event, context):
        seen["event"] = event
        seen["remaining"] = context.get_remaining_time_in_millis()
        seen["request_id"] = context.aws_request_id
        return event

    monkeypatch.setattr(batch_job.lambda_handler, "lambda_handler", handler)
    assert batch_job.main() == 0
    assert seen["event"] == state
    assert seen["remaining"] == 4 * 60 * 60 * 1000
    assert seen["request_id"] == "job-123"


def test_main_module_dispatches_analysis_batch(monkeypatch):
    """`python3 -m preview_pipeline analysisBatch` (the image entry point) runs this module."""
    calls = []
    monkeypatch.setattr(batch_job, "main", lambda: calls.append("ran") or 7)
    monkeypatch.setattr(sys, "argv", ["preview_pipeline", "analysisBatch"])
    import importlib

    import preview_pipeline.__main__ as entry

    importlib.reload(entry)
    try:
        entry.main()
    except SystemExit as exc:
        assert exc.code == 7
    else:
        raise AssertionError("analysisBatch must exit with batch_job.main()'s status")
    assert calls == ["ran"]
