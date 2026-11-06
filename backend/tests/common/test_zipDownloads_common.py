# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common/zipDownloads.py: ids, keys, archive names, part sizes and the finalization rules."""

import importlib.util
import os
import sys

import pytest

pytestmark = pytest.mark.unit

_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "common", "zipDownloads.py")


@pytest.fixture(scope="module")
def zd():
    spec = importlib.util.spec_from_file_location("zipDownloads_common_under_test", os.path.abspath(_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_request_ids_sort_chronologically(zd):
    early = zd.new_request_id(now_ms=1_000, suffix="f" * 32)
    late = zd.new_request_id(now_ms=1_700_000_000_000, suffix="0" * 32)
    assert early < late
    assert zd.is_request_id(early) and zd.is_request_id(late)
    assert not zd.is_request_id("../../etc")


def test_prefix_layout(zd):
    prefix = zd.request_prefix("db1", "asset1", "alice", "r1")
    assert prefix == f"vams.preparedDownloads/db1/asset1/{zd.user_hash('alice')}/r1/"
    assert len(zd.user_hash("alice")) == 16 and zd.user_hash("alice") != zd.user_hash("bob")


@pytest.mark.parametrize("requested,fmt,expected", [
    (None, "zip", "Engine-20260101-000000.zip"),
    ("../../etc/passwd", "tar", "passwd.tar"),
    ("C:\\x\\évil<>.zip", "zip", "_vil__.zip"),
    ("name.tar.gz", "tar.gz", "name.tar.gz"),
    ("...hidden", "zip", "hidden.zip"),
    ("x" * 400, "tar.gz", "x" * 193 + ".tar.gz"),
])
def test_archive_name_sanitized(zd, requested, fmt, expected):
    from datetime import datetime, timezone
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert zd.sanitize_archive_file_name(requested, fmt, "Engine", now) == expected


@pytest.mark.parametrize("total,expected_mib", [
    (0, 16), (1024 ** 3, 16), (1024 ** 4, 111), (5 * 1024 ** 4, 552)])
def test_part_size(zd, total, expected_mib):
    size = zd.part_size_bytes(total)
    assert size == expected_mib * 1024 * 1024
    assert -(-total // size) <= 9500 if total else True
    assert size <= 5 * 1024 ** 3


def test_decide_finalization_rules(zd):
    running = {"status": "RUNNING"}
    assert zd.decide_finalization("RUNNING", running) is None
    assert zd.decide_finalization("SUCCEEDED", {"status": "SUCCEEDED"}) is None
    assert zd.decide_finalization("SUCCEEDED", running, archive_exists=lambda: (True, 5)) == (
        "SUCCEEDED", {"archive_size_bytes": 5})
    assert zd.decide_finalization("SUCCEEDED", running, archive_exists=lambda: (False, None))[0] == "FAILED"
    status, fields = zd.decide_finalization("ABORTED", running)
    assert status == "FAILED" and fields["cancelled_by"] == "execution"
    assert zd.decide_finalization("FAILED", running, "boom arn:aws:x")[1]["error_message"] == zd.MSG_JOB_FAILED
    assert zd.decide_finalization("FAILED", running, "ZIPDOWNLOAD: why")[1]["error_message"] == "why"


@pytest.mark.parametrize("execution_status", ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"])
def test_decide_finalization_landed_archive_wins(zd, execution_status):
    assert zd.decide_finalization(execution_status, {"status": "RUNNING"},
                                  archive_exists=lambda: (True, 9)) == ("SUCCEEDED", {"archive_size_bytes": 9})


def test_decide_finalization_never_checks_for_a_running_run(zd):
    def unexpected():
        raise AssertionError("a running run's archive is not checked")
    assert zd.decide_finalization("RUNNING", {"status": "RUNNING"}, archive_exists=unexpected) is None

