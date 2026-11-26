#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The run's deadline: the earlier of the inner execution's timeout, counted from the execution's
start (the job queue wait included), and the Batch attempt's, counted from the container's start."""
import datetime
import os
import sys

import pytest

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time_budget  # noqa: E402
from miris_upload_container_harness import ASSET_ID, definition  # noqa: E402

START = "2026-11-06T23:10:40.349Z"
START_EPOCH = datetime.datetime(2026, 11, 6, 23, 10, 40, 349000, tzinfo=datetime.timezone.utc).timestamp()


def _env(start=START, execution_timeout="4800", attempt="3000"):
    env = {}
    if start is not None:
        env[time_budget.EXECUTION_START_TIME_ENV] = start
    if execution_timeout is not None:
        env[time_budget.EXECUTION_TIMEOUT_SECONDS_ENV] = execution_timeout
    if attempt is not None:
        env[time_budget.ATTEMPT_DURATION_SECONDS_ENV] = attempt
    return env


@pytest.mark.parametrize("env, waited, bound", [
    (_env(), 72 * 60, START_EPOCH + 4800),
    (_env(), 60, START_EPOCH + 60 + 3000),
    (_env(start="not-a-time"), 60, START_EPOCH + 60 + 3000),
    (_env(start="2026-11-06T23:10:40Z", attempt=None), 0, START_EPOCH - 0.349 + 4800),
], ids=["queued-execution-bound", "prompt-attempt-bound", "unreadable-execution-bound", "no-fraction"])
def test_the_deadline_is_the_earlier_readable_bound(env, waited, bound):
    assert time_budget.run_deadline(env, START_EPOCH + waited) == pytest.approx(bound)


@pytest.mark.parametrize("env", [
    _env(start=None, attempt=None),
    _env(execution_timeout="soon", attempt=None),
    {},
])
def test_no_deadline_without_a_readable_bound(env):
    assert time_budget.run_deadline(env, START_EPOCH) is None


def test_seconds_left_keeps_back_the_end_reserve():
    assert time_budget.seconds_left(1000.0, 100.0) == pytest.approx(900 - time_budget.END_RESERVE_SECONDS)
    assert time_budget.seconds_left(None, 100.0) is None


def test_the_upload_estimate_grows_with_the_artifact_size():
    assert time_budget.upload_seconds_needed(0) == time_budget.UPLOAD_BASE_SECONDS
    small = time_budget.upload_seconds_needed(10 * 1024 * 1024)
    large = time_budget.upload_seconds_needed(3 * 1024 * 1024 * 1024)
    assert time_budget.UPLOAD_BASE_SECONDS < small < large
    # A 3 GiB artifact gets at least five minutes.
    assert large >= 300


def _clear_bounds(monkeypatch):
    for name in (time_budget.EXECUTION_START_TIME_ENV, time_budget.EXECUTION_TIMEOUT_SECONDS_ENV,
                 time_budget.ATTEMPT_DURATION_SECONDS_ENV):
        monkeypatch.delenv(name, raising=False)


def test_a_run_whose_execution_deadline_has_passed_uploads_nothing(container, monkeypatch):
    container.s3.add("asset-bucket", f"{ASSET_ID}/model.usdz", "usdz")
    _clear_bounds(monkeypatch)
    started = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=4790)
    monkeypatch.setenv(time_budget.EXECUTION_START_TIME_ENV, started.isoformat().replace("+00:00", "Z"))
    monkeypatch.setenv(time_budget.EXECUTION_TIMEOUT_SECONDS_ENV, "4800")
    monkeypatch.setenv(time_budget.ATTEMPT_DURATION_SECONDS_ENV, "3000")

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="model.usdz"))

    assert exited.value.code == container.main.EXIT_INSUFFICIENT_TIME
    container.miris.start_upload.assert_not_called()


def test_a_run_with_time_left_bounds_the_processing_wait_by_the_attempt(container, monkeypatch):
    container.s3.add("asset-bucket", f"{ASSET_ID}/model.usdz", "usdz")
    _clear_bounds(monkeypatch)
    monkeypatch.setenv(time_budget.ATTEMPT_DURATION_SECONDS_ENV, "900")

    container.run(definition(trigger_rel="model.usdz", taskTimeoutSeconds=1800))

    container.miris.start_upload.assert_called_once()
    wait = container.miris.poll_until_terminal.call_args.kwargs["timeout_seconds"]
    assert 0 < wait <= 900 - time_budget.END_RESERVE_SECONDS


def test_a_run_without_bounds_waits_the_task_timeout(container, monkeypatch):
    container.s3.add("asset-bucket", f"{ASSET_ID}/model.usdz", "usdz")
    _clear_bounds(monkeypatch)

    container.run(definition(trigger_rel="model.usdz", taskTimeoutSeconds=60))

    assert container.miris.poll_until_terminal.call_args.kwargs["timeout_seconds"] == 60
