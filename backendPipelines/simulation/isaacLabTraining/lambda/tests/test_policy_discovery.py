#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for isaacLab evaluation policy auto-discovery.

Each training run writes checkpoints under its own execution folder, so discovery selects the run
folder with the newest checkpoint write, then the highest training ITERATION within it (numeric, not
lexicographic — 'model_999.pt' sorts above 'model_1500.pt' as a string)."""

import os
import re
import sys
import types
import importlib
import importlib.util
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


# Real file names each library writes under Isaac Lab 2.3.0 (issue #1315); the layouts are in
# container/utils/training/checkpoints.py.
RL_GAMES_NAMES = ["Cartpole.pth", "last_Cartpole_ep_50_rew__3.1_.pth",
                  "last_Cartpole_ep_150_rew__12.5_.pth", "last_Cartpole_ep_1000_rew__9.75_.pth"]
SKRL_NAMES = ["agent_1000.pt", "agent_30000.pt", "agent_9000.pt", "best_agent.pt"]


def _discover(mod, keys, library):
    objects = [_obj(k, 1) for k in keys]
    with patch.object(mod.s3_client, "get_paginator", MagicMock(return_value=_paginator_for(objects))):
        return mod.discover_policy_file("abkt", "xid/", library)


@pytest.mark.unit
class TestLibraryCheckpoints:
    def test_rl_games_pth_checkpoints_are_discovered(self):
        mod = _load()
        keys = [f"xid/0a/checkpoints/{n}" for n in RL_GAMES_NAMES]
        assert _discover(mod, keys, "rl_games") == (
            "s3://abkt/xid/0a/checkpoints/last_Cartpole_ep_1000_rew__9.75_.pth")

    def test_rl_games_best_checkpoint_alone_is_discovered(self):
        mod = _load()
        assert _discover(mod, ["xid/0a/checkpoints/Cartpole.pth"], "rl_games") == (
            "s3://abkt/xid/0a/checkpoints/Cartpole.pth")

    def test_skrl_highest_timestep_wins_over_best_agent(self):
        mod = _load()
        keys = [f"xid/0a/checkpoints/{n}" for n in SKRL_NAMES]
        assert _discover(mod, keys, "skrl") == "s3://abkt/xid/0a/checkpoints/agent_30000.pt"

    def test_skrl_best_agent_alone_is_discovered(self):
        mod = _load()
        assert _discover(mod, ["xid/0a/checkpoints/best_agent.pt"], "skrl") == (
            "s3://abkt/xid/0a/checkpoints/best_agent.pt")

    def test_reward_digits_do_not_decide_rl_games_order(self):
        mod = _load()
        # The last run of digits in this name is the reward's fraction (5), not the epoch (200).
        assert mod.checkpoint_iteration("c/last_Cartpole_ep_200_rew__12.5_.pth", "rl_games") == 200
        assert mod.checkpoint_iteration("c/last_Cartpole_ep_200_rew__12.5_.pth") == 5

    def test_library_files_win_over_a_newer_run_of_another_library(self):
        mod = _load()
        objects = [_obj("xid/old/checkpoints/last_Cartpole_ep_150_rew__1.0_.pth", 1),
                   _obj("xid/new/checkpoints/model_900.pt", 9)]
        with patch.object(mod.s3_client, "get_paginator", MagicMock(return_value=_paginator_for(objects))):
            assert mod.discover_policy_file("abkt", "xid/", "rl_games") == (
                "s3://abkt/xid/old/checkpoints/last_Cartpole_ep_150_rew__1.0_.pth")
            assert mod.discover_policy_file("abkt", "xid/", "rsl_rl") == (
                "s3://abkt/xid/new/checkpoints/model_900.pt")

    def test_any_policy_file_is_still_found_when_no_name_matches_the_library(self):
        mod = _load()
        assert _discover(mod, ["xid/policies/ant_trained.pt"], "skrl") == (
            "s3://abkt/xid/policies/ant_trained.pt")

    def test_pth_is_found_without_a_library(self):
        mod = _load()
        assert _discover(mod, ["xid/0a/checkpoints/Cartpole.pth"], None) == (
            "s3://abkt/xid/0a/checkpoints/Cartpole.pth")


@pytest.mark.unit
class TestTablesAgreeWithContainer:
    """The lambda's file names and the container's paths come from separate tables; a library added to
    one and not the other would upload checkpoints the evaluation then cannot choose."""

    @staticmethod
    def _container_table():
        path = os.path.join(_LAMBDA_DIR, "..", "container", "utils", "training", "checkpoints.py")
        spec = importlib.util.spec_from_file_location("isaaclab_container_checkpoints", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_same_libraries(self):
        mod, container = _load(), self._container_table()
        assert set(mod.CHECKPOINT_RANKING) == set(container.CHECKPOINT_PATTERNS)

    def test_every_file_the_container_uploads_is_one_the_lambda_recognizes(self, tmp_path):
        mod, container = _load(), self._container_table()
        layouts = {
            "rsl_rl": ["rsl_rl/cartpole/2026-01-01_00-00-00/model_0.pt",
                       "rsl_rl/cartpole/2026-01-01_00-00-00/model_149.pt"],
            "rl_games": [f"rl_games/cartpole_direct/2026-01-01_00-00-00/nn/{n}" for n in RL_GAMES_NAMES],
            "skrl": [f"skrl/cartpole_direct/2026-01-01_00-00-00_ppo_torch/checkpoints/{n}" for n in SKRL_NAMES],
        }
        for library, files in layouts.items():
            root = tmp_path / library
            for rel in files:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(b"x")
            found = container.find_checkpoints([str(root)], library)
            assert sorted(os.path.basename(p) for p in found) == sorted(os.path.basename(f) for f in files)
            for path in found:
                assert re.search(mod.CHECKPOINT_RANKING[library]["name"], os.path.basename(path)), path
