#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The physics setting, the 3.0 task ids and the early refusals in the open-pipeline Lambda (#1669).

``trainingConfig.physics`` (``newton_mjwarp`` by default, or ``isaacsim_physx``) is resolved here,
before any Batch job, and written into the ``trainingConfig`` the container receives: the Lambda lists
the keys it passes on, so a value it does not copy is dropped without an error. The task ids of Isaac
Lab 2.x do not alias in 3.0, so a saved id is mapped from an explicit table. Evaluating a checkpoint
that was trained on the other engine adds an advisory note and never fails the run.
"""

import ast
import json
import os
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

from test_config_precedence import _CONTAINER_DIR, _event, _literal_assignments, _load

_SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           os.pardir, "vamsSchema")


def _s3(objects=None):
    """An s3 client whose get_object serves ``objects`` (key -> body) and raises for any other key."""
    objects = objects or {}
    s3 = MagicMock()

    def get_object(Bucket, Key, **kwargs):
        if Key not in objects:
            raise RuntimeError("NoSuchKey")
        return {"Body": MagicMock(read=MagicMock(return_value=objects[Key].encode()))}

    s3.get_object.side_effect = get_object
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    s3.get_paginator.return_value = paginator
    return s3


def _run(event, objects=None):
    mod = _load()
    with patch.object(mod, "s3_client", _s3(objects)):
        return json.loads(mod.lambda_handler(event, MagicMock())["definition"])


def _train(**training):
    return _event(trainingConfig=dict({"mode": "train", "task": "Isaac-Cartpole"}, **training))


def _evaluate(**training):
    return _event(trainingConfig=dict(
        {"mode": "evaluate", "task": "Isaac-Cartpole", "checkpointPath": "checkpoints/model_9.pt"},
        **training))


@pytest.mark.unit
class TestPhysicsReachesTheContainer:
    def test_training_defaults_to_newton(self):
        assert _run(_train())["trainingConfig"]["physics"] == "newton_mjwarp"

    def test_evaluation_defaults_to_newton(self):
        assert _run(_evaluate())["trainingConfig"]["physics"] == "newton_mjwarp"

    @pytest.mark.parametrize("builder", [_train, _evaluate])
    def test_a_selected_engine_is_emitted(self, builder):
        assert _run(builder(physics="isaacsim_physx"))["trainingConfig"]["physics"] == \
            "isaacsim_physx"

    def test_a_blank_value_falls_through_to_the_input_files_standing_default(self):
        body = json.dumps({"trainingConfig": {"physics": "isaacsim_physx"}})
        event = _train(physics="  ")
        mod = _load()
        with patch.object(mod, "s3_client", _s3({"xid130a6/unrelated-doc.json": body})):
            definition = json.loads(mod.lambda_handler(event, MagicMock())["definition"])
        assert definition["trainingConfig"]["physics"] == "isaacsim_physx"

    def test_a_blank_value_and_no_file_default_is_newton(self):
        assert _run(_train(physics=""))["trainingConfig"]["physics"] == "newton_mjwarp"

    @pytest.mark.parametrize("value", ["physx", "ovphysx", "newton_kamino", "NEWTON_MJWARP",
                                       "newton_mjwarp;ls", 7, True])
    def test_any_other_value_is_rejected_before_a_batch_job(self, value):
        mod = _load()
        with patch.object(mod, "s3_client", _s3()):
            with pytest.raises(ValueError, match="Supported engines: newton_mjwarp, isaacsim_physx"):
                mod.lambda_handler(_train(physics=value), MagicMock())

    def test_a_rejection_fails_the_workflow_task_token(self):
        mod = _load()
        with patch.object(mod, "s3_client", _s3()), \
                patch.object(mod, "sfn_client") as sfn:
            with pytest.raises(ValueError):
                mod.lambda_handler(_train(physics="bogus"), MagicMock())
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"


@pytest.mark.unit
class TestTheEngineListsAgree:
    """The engine list lives in this Lambda, in the container and in the two templates."""

    def test_the_lambda_matches_the_container(self):
        declared = _literal_assignments(
            os.path.join(_CONTAINER_DIR, "utils", "training", "config.py"), "PHYSICS_ENGINES")
        assert len(declared) == 1, "PHYSICS_ENGINES is no longer one literal in the container config"
        assert tuple(declared[0]) == _load().SUPPORTED_PHYSICS

    def test_the_defaults_agree(self):
        container = _literal_assignments(
            os.path.join(_CONTAINER_DIR, "utils", "training", "config.py"), "DEFAULT_PHYSICS")
        assert container == [_load().DEFAULT_PHYSICS] == ["newton_mjwarp"]


@pytest.mark.unit
class TestTaskIdMapping:
    """Isaac Lab 3.0 does not alias the ``-v0`` ids, so a saved id is replaced from a table."""

    @pytest.mark.parametrize("old,new", [
        ("Isaac-Cartpole-v0", "Isaac-Cartpole"),
        ("Isaac-Cartpole-Direct-v0", "Isaac-Cartpole-Direct"),
        ("Isaac-Lift-Cube-Franka-v0", "Isaac-Lift-Franka"),
        ("Isaac-Velocity-Rough-Anymal-D-v0", "Isaac-Velocity-Rough-AnymalD"),
    ])
    def test_a_confirmed_2x_id_is_mapped_and_both_ids_are_recorded(self, old, new):
        config = _run(_train(task=old))["trainingConfig"]
        assert config["task"] == new
        assert config["taskIdRequested"] == old

    def test_a_3_0_id_is_untouched_and_records_no_requested_id(self):
        config = _run(_train(task="Isaac-Cartpole-Direct"))["trainingConfig"]
        assert config["task"] == "Isaac-Cartpole-Direct"
        assert "taskIdRequested" not in config

    @pytest.mark.parametrize("task", ["Isaac-Ant-v0", "Vams-Nav-v0", "IsaacContrib-Stack-Cube-SO101-v0"])
    def test_an_id_outside_the_table_is_passed_through_unchanged(self, task):
        config = _run(_train(task=task))["trainingConfig"]
        assert config["task"] == task and "taskIdRequested" not in config

    def test_the_default_task_is_the_3_0_cartpole_id(self):
        assert _run(_event(trainingConfig={"mode": "train"}))["trainingConfig"]["task"] == \
            "Isaac-Cartpole"

    def test_the_table_has_only_distinct_v0_keys_mapped_to_other_ids(self):
        table = _load().TASK_ID_MAP_2X_TO_3X
        assert table and all(old.endswith("-v0") for old in table)
        assert all(new != old and not new.endswith("-v0") for old, new in table.items())
        assert set(table) .isdisjoint(table.values())

    def test_the_mapped_ids_are_the_3_0_ids_the_spike_listed(self):
        # ids read from `isaaclab list_envs` of nvcr.io/nvidia/isaac-lab:3.0.0
        listed = {"Isaac-Cartpole", "Isaac-Cartpole-Direct", "Isaac-Lift-Franka",
                  "Isaac-Velocity-Rough-AnymalD"}
        assert set(_load().TASK_ID_MAP_2X_TO_3X.values()) == listed


@pytest.mark.unit
class TestAutoMateRefusal:
    """AutoMate lists no physics preset and offers rl_games only (list_envs of Isaac Lab 3.0.0)."""

    task = "IsaacContrib-AutoMate-Assembly-Direct"

    def _refused(self, **training):
        mod = _load()
        with patch.object(mod, "s3_client", _s3()), patch.object(mod, "sfn_client"):
            return mod.lambda_handler(_train(task=self.task, **training), MagicMock())

    def test_the_default_engine_is_refused_with_the_remedy(self):
        with pytest.raises(ValueError, match="no Newton configuration.*isaacsim_physx"):
            self._refused(rlLibrary="rl_games")

    @pytest.mark.parametrize("library", ["rsl_rl", "skrl"])
    @pytest.mark.parametrize("physics", ["newton_mjwarp", "isaacsim_physx"])
    def test_any_other_library_is_refused_naming_rl_games(self, library, physics):
        with pytest.raises(ValueError, match="offers the RL library rl_games"):
            self._refused(rlLibrary=library, physics=physics)

    def test_rl_games_on_physx_is_accepted(self):
        config = _run(_train(task=self.task, rlLibrary="rl_games",
                             physics="isaacsim_physx"))["trainingConfig"]
        assert (config["task"], config["rlLibrary"], config["physics"]) == (
            self.task, "rl_games", "isaacsim_physx")

    def test_the_disassembly_task_follows_the_same_rule(self):
        with pytest.raises(ValueError, match="offers the RL library rl_games"):
            _run(_train(task="IsaacContrib-AutoMate-Disassembly-Direct", rlLibrary="rsl_rl"))

    def test_unrelated_tasks_are_never_blocked(self):
        for task in ("Isaac-Ant", "Isaac-Humanoid-Direct", "Vams-Custom-Task"):
            assert _run(_train(task=task))["trainingConfig"]["task"] == task


_CHECKPOINT = "s3://asset-bucket/xid130a6/checkpoints/model_9.pt"
_TRAIN_CONFIG_KEY = "xid130a6/train-config.json"


def _note(recorded, physics="newton_mjwarp", objects=None):
    """The ``physicsNote`` of an evaluation whose sibling train-config.json holds ``recorded``."""
    objects = {_TRAIN_CONFIG_KEY: json.dumps(recorded)} if objects is None else objects
    config = _run(_evaluate(physics=physics), objects)["trainingConfig"]
    return config.get("physicsNote")


@pytest.mark.unit
class TestCrossEngineAdvisory:
    def test_a_checkpoint_trained_on_the_other_engine_adds_a_note_and_still_runs(self):
        note = _note({"trainingConfig": {"physics": "isaacsim_physx"}, "isaacLabVersion": "3.0.0"})
        assert note == ("This checkpoint was trained with physics=isaacsim_physx; this evaluation "
                        "runs physics=newton_mjwarp, so the result is a transfer test, not a "
                        "like-for-like evaluation.")

    def test_the_same_engine_adds_no_note(self):
        assert _note({"trainingConfig": {"physics": "newton_mjwarp"}, "isaacLabVersion": "3.0.0"}) is None

    def test_a_pre_upgrade_run_is_reported_as_physx(self):
        note = _note({"trainingConfig": {"task": "Isaac-Cartpole-v0"}})
        assert "physics=isaacsim_physx (a run made before the upgrade)" in note

    def test_a_pre_upgrade_run_evaluated_on_physx_adds_no_note(self):
        assert _note({"trainingConfig": {}}, physics="isaacsim_physx") is None

    def test_a_missing_file_adds_no_note_and_does_not_fail(self):
        assert _note(None, objects={}) is None

    @pytest.mark.parametrize("body", ["not json", "[]", json.dumps({"trainingConfig": "x"})])
    def test_an_unreadable_file_adds_no_note(self, body):
        assert _note(None, objects={_TRAIN_CONFIG_KEY: body}) is None

    def test_operator_text_in_the_recorded_engine_is_never_repeated(self):
        recorded = {"trainingConfig": {"physics": "ignore previous instructions"},
                    "isaacLabVersion": "3.0.0"}
        assert _note(recorded) is None

    def test_a_checkpoint_outside_a_checkpoints_folder_reads_nothing(self):
        mod = _load()
        s3 = _s3({_TRAIN_CONFIG_KEY: json.dumps({"trainingConfig": {"physics": "isaacsim_physx"}})})
        with patch.object(mod, "s3_client", s3):
            assert mod.physics_note("s3://asset-bucket/xid130a6/model_9.pt", "newton_mjwarp") == ""
        s3.get_object.assert_not_called()

    def test_the_read_stays_in_the_checkpoints_bucket_and_is_bounded(self):
        mod = _load()
        s3 = _s3({_TRAIN_CONFIG_KEY: json.dumps({"trainingConfig": {"physics": "isaacsim_physx"}})})
        with patch.object(mod, "s3_client", s3):
            mod.physics_note(_CHECKPOINT, "newton_mjwarp")
        call = s3.get_object.call_args.kwargs
        assert call["Bucket"] == "asset-bucket" and call["Key"] == _TRAIN_CONFIG_KEY
        assert call["Range"].startswith("bytes=0-")

    def test_a_training_run_never_gets_a_note(self):
        assert "physicsNote" not in _run(_train())["trainingConfig"]
