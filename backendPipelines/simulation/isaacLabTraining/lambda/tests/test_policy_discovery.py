#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for isaacLab evaluation policy auto-discovery.

Each training run writes checkpoints under its own execution folder, so discovery selects the run
folder with the newest checkpoint write, then the highest training ITERATION within it (numeric, not
lexicographic — 'model_999.pt' sorts above 'model_1500.pt' as a string)."""

import os
import sys
import types
import importlib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(k, v)


def _load():
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


def _paginator_for(objects):
    """A list_objects_v2 paginator mock yielding one page of {Key, LastModified} dicts."""
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": objects}]
    return paginator


def _obj(key, minute):
    return {"Key": key, "LastModified": datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc)}


@pytest.mark.unit
class TestCheckpointIteration:
    def test_reads_trailing_iteration_number(self):
        mod = _load()
        assert mod.checkpoint_iteration("run/checkpoints/model_1500.pt") == 1500
        assert mod.checkpoint_iteration("run/checkpoints/model_999.pt") == 999

    def test_unnumbered_checkpoint_sorts_lowest(self):
        mod = _load()
        assert mod.checkpoint_iteration("run/checkpoints/model.pt") == -1


@pytest.mark.unit
class TestDiscoverPolicyFile:
    def test_highest_iteration_wins_over_lexicographic_order(self):
        mod = _load()
        objects = [_obj("xid/0a/checkpoints/model_999.pt", 1),
                   _obj("xid/0a/checkpoints/model_1500.pt", 2)]
        with patch.object(mod.s3_client, "get_paginator", MagicMock(return_value=_paginator_for(objects))):
            assert mod.discover_policy_file("abkt", "xid/") == "s3://abkt/xid/0a/checkpoints/model_1500.pt"

    def test_newest_run_folder_wins_over_reverse_string_sort(self):
        mod = _load()
        # The newest run ('0a...') sorts BELOW the stale run ('f3...') as a string.
        objects = [_obj("xid/f3ff/checkpoints/model_1500.pt", 1),
                   _obj("xid/0aaa/checkpoints/model_1500.pt", 9)]
        with patch.object(mod.s3_client, "get_paginator", MagicMock(return_value=_paginator_for(objects))):
            assert mod.discover_policy_file("abkt", "xid/") == "s3://abkt/xid/0aaa/checkpoints/model_1500.pt"

    def test_no_checkpoints_returns_empty(self):
        mod = _load()
        with patch.object(mod.s3_client, "get_paginator",
                          MagicMock(return_value=_paginator_for([_obj("xid/scene.usd", 1)]))):
            assert mod.discover_policy_file("abkt", "xid/") == ""

    def test_missing_last_modified_still_selects(self):
        mod = _load()
        objects = [{"Key": "xid/0a/checkpoints/model_100.pt"},
                   {"Key": "xid/0a/checkpoints/model_900.pt"}]
        with patch.object(mod.s3_client, "get_paginator", MagicMock(return_value=_paginator_for(objects))):
            assert mod.discover_policy_file("abkt", "xid/") == "s3://abkt/xid/0a/checkpoints/model_900.pt"

    def test_missing_bucket_or_root_returns_empty(self):
        mod = _load()
        assert mod.discover_policy_file("", "xid/") == ""
        assert mod.discover_policy_file("abkt", "") == ""

    def test_listing_error_is_best_effort(self):
        mod = _load()
        with patch.object(mod.s3_client, "get_paginator", MagicMock(side_effect=Exception("AccessDenied"))):
            assert mod.discover_policy_file("abkt", "xid/") == ""
