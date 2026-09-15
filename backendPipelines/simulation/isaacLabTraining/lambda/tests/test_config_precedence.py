#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for isaacLab run-configuration precedence.

The run configuration is the manifest-delivered input configuration — the template selection the
operator made on the execute screen. A JSON input file is an asset file, so anything it holds is a
standing default for the fields the configuration leaves blank.

The `rlLibrary` classes below cover the one field this handler now rejects on top of `mode`, and the
parity of the three copies of the supported-library list that the rejection depends on."""

import ast
import os
import sys
import types
import json
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

_CONTAINER_DIR = os.path.join(os.path.dirname(_LAMBDA_DIR), "container")

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(k, v)

# A JSON file sitting in the asset. Every value differs from the configuration used below, and the
# evaluation pipeline's inputFileFilters allow "*.json", so this is a file an operator can select.
_ASSET_FILE = json.dumps({
    "trainingConfig": {
        "mode": "train",
        "task": "Isaac-Ant-v0",
        "numEnvs": 4096,
        "maxIterations": 9999,
        "checkpointPath": "checkpoints/from_file.pt",
    },
    "computeConfig": {"numNodes": 8},
})


def _load():
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


def _literal_assignments(path: str, name: str) -> list:
    """Every literal assigned to ``name`` anywhere in ``path``, read from the file's source text.

    The container is its own code bundle — this lambda's asset is the `lambda/` directory alone — so
    the container package cannot be imported to share a list with it. Parsing the source is what
    makes a comparison possible at all, and it is why each caller asserts the number of declarations
    it found: an extraction that found none would agree with anything.
    """
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                found.append(ast.literal_eval(node.value))
    return found


def _s3_returning(body):
    """An s3 client mock whose get_object returns ``body`` and whose listings are empty."""
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body.encode()))}
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    s3.get_paginator.return_value = paginator
    return s3


def _event(**overrides):
    event = {
        "jobName": "isaaclab-job-abcd1234",
        "bucketAsset": "asset-bucket",
        "inputAssetLocationKey": "xid130a6/",
        "inputS3AssetFilePath": "s3://asset-bucket/xid130a6/unrelated-doc.json",
        "outputS3AssetFilesPath": "s3://asset-bucket/pipelines/p1/JOB/output/E1/files/",
        "externalSfnTaskToken": "tok-123",
    }
    event.update(overrides)
    return event


def _definition(event, file_body=_ASSET_FILE):
    mod = _load()
    with patch.object(mod, "s3_client", _s3_returning(file_body)):
        return json.loads(mod.lambda_handler(event, MagicMock())["definition"])


@pytest.mark.unit
class TestManifestConfigurationWins:
    def test_configuration_decides_the_mode(self):
        # The selected file says "train"; the operator chose the evaluation template.
        definition = _definition(_event(trainingConfig={
            "mode": "evaluate",
            "task": "Isaac-Cartpole-Direct-v0",
            "numEnvs": 100,
            "numEpisodes": 50,
            "checkpointPath": "checkpoints/model_499.pt",
        }))
        assert definition["trainingConfig"]["mode"] == "evaluate"

    def test_configuration_values_outrank_the_input_file(self):
        definition = _definition(_event(
            trainingConfig={
                "mode": "evaluate",
                "task": "Isaac-Cartpole-Direct-v0",
                "numEnvs": 100,
                "checkpointPath": "checkpoints/model_499.pt",
            },
        ))
        assert definition["trainingConfig"]["task"] == "Isaac-Cartpole-Direct-v0"
        assert definition["trainingConfig"]["numEnvs"] == 100
        assert definition["trainingConfig"]["policyS3Uri"] == \
            "s3://asset-bucket/xid130a6/checkpoints/model_499.pt"

    def test_a_field_the_configuration_omits_falls_back_to_the_input_file(self):
        definition = _definition(_event(
            trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0"},
        ))
        assert definition["trainingConfig"]["task"] == "Isaac-Cartpole-Direct-v0"
        assert definition["trainingConfig"]["maxIterations"] == 9999

    def test_no_configuration_falls_back_entirely_to_the_input_file(self):
        definition = _definition(_event())
        assert definition["trainingConfig"]["mode"] == "train"
        assert definition["trainingConfig"]["task"] == "Isaac-Ant-v0"
        assert definition["trainingConfig"]["maxIterations"] == 9999

    def test_the_input_file_supplies_no_compute_section(self):
        # The file below declares computeConfig.numNodes; this pipeline is single-node only, so a
        # section the file carries must not reach the container. See test_single_node_only.py.
        definition = _definition(_event())
        assert "computeConfig" not in definition

    def test_a_blank_configuration_value_falls_through(self):
        definition = _definition(_event(trainingConfig={"mode": "train", "task": "   "}))
        assert definition["trainingConfig"]["task"] == "Isaac-Ant-v0"


@pytest.mark.unit
class TestInputFileIsNotTrustedAsConfig:
    def test_a_json_file_that_is_not_an_object_yields_no_defaults(self):
        mod = _load()
        with patch.object(mod, "s3_client", _s3_returning("[1, 2, 3]")):
            assert mod.load_config_from_s3("s3://asset-bucket/xid130a6/points.json") == {}

    def test_a_json_array_input_file_does_not_break_the_build(self):
        definition = _definition(_event(trainingConfig={"mode": "train"}), file_body="[1, 2, 3]")
        assert definition["trainingConfig"]["mode"] == "train"
        assert definition["trainingConfig"]["task"] == _load().DEFAULT_TASK

    def test_a_non_json_input_file_is_never_read(self):
        mod = _load()
        s3 = _s3_returning(_ASSET_FILE)
        with patch.object(mod, "s3_client", s3):
            assert mod.load_config_from_s3("s3://asset-bucket/xid130a6/scene.usd") == {}
        s3.get_object.assert_not_called()

    def test_unparseable_json_yields_no_defaults(self):
        mod = _load()
        with patch.object(mod, "s3_client", _s3_returning("{not json")):
            assert mod.load_config_from_s3("s3://asset-bucket/xid130a6/broken.json") == {}


@pytest.mark.unit
class TestAnUnsupportedRlLibraryIsRejectedHere:
    """`rlLibrary` names the Isaac Lab script the container runs. An unrecognized value is rejected
    rather than substituted, so a run cannot train with a library the operator did not ask for,
    report success, and leave checkpoints the requested library cannot load.

    The container rejects it too. Rejecting in this handler is what makes the rejection free: it is
    the first state of the pipeline's state machine, so no GPU node is provisioned and no multi-GB
    Isaac Sim image is pulled. `mode` was already validated here; this closes the same gap for the
    other field the shipped template invites an operator to tune."""

    @pytest.mark.parametrize("library", ["sb3", "stable_baselines3", "rls_rl", "RSL_RL", "rsl-rl"])
    def test_an_unrecognized_library_is_rejected(self, library):
        with pytest.raises(ValueError, match="Unsupported rlLibrary"):
            _load().resolve_rl_library(library)

    def test_the_rejection_names_the_value_and_the_supported_set(self):
        with pytest.raises(ValueError) as excinfo:
            _load().resolve_rl_library("sb3")
        message = str(excinfo.value)
        assert "sb3" in message
        assert all(library in message for library in ("rsl_rl", "rl_games", "skrl"))

    def test_a_non_string_library_is_rejected_rather_than_raising_on_strip(self):
        with pytest.raises(ValueError, match="Unsupported rlLibrary"):
            _load().resolve_rl_library(3)

    @pytest.mark.parametrize("library", ["rsl_rl", "rl_games", "skrl"])
    def test_every_supported_library_still_reaches_the_container(self, library):
        # The paired arm: a handler that rejected everything would satisfy the cases above.
        definition = _definition(_event(trainingConfig={
            "mode": "train", "task": "Isaac-Cartpole-Direct-v0", "rlLibrary": library}))
        assert definition["trainingConfig"]["rlLibrary"] == library

    @pytest.mark.parametrize("library", ["rsl_rl", "rl_games", "skrl"])
    def test_every_supported_library_still_reaches_evaluation(self, library):
        definition = _definition(_event(trainingConfig={
            "mode": "evaluate", "task": "Isaac-Cartpole-Direct-v0",
            "checkpointPath": "checkpoints/model_499.pt", "rlLibrary": library}))
        assert definition["trainingConfig"]["rlLibrary"] == library

    def test_a_configuration_naming_no_library_defaults_rather_than_failing(self):
        definition = _definition(_event(trainingConfig={
            "mode": "train", "task": "Isaac-Cartpole-Direct-v0"}))
        assert definition["trainingConfig"]["rlLibrary"] == "rsl_rl"

    def test_a_null_library_in_the_input_file_falls_through_to_the_default(self):
        # merge_configs copies the input file's section wholesale, so a null it carries survives into
        # the merged configuration. The container reads a null as "absent"; so must this handler, or
        # the check would reject a configuration the container accepts.
        definition = _definition(
            _event(trainingConfig={"mode": "train", "task": "Isaac-Cartpole-Direct-v0"}),
            file_body=json.dumps({"trainingConfig": {"rlLibrary": None}}))
        assert definition["trainingConfig"]["rlLibrary"] == "rsl_rl"


@pytest.mark.unit
class TestTheSupportedLibraryListsAgree:
    """The supported-library set is declared in three places and nothing links them: this lambda's
    SUPPORTED_RL_LIBRARIES, the container's RL_LIBRARIES, and the container's two script maps.

    Now that an unrecognized value is rejected instead of substituted, drift between the copies
    matters in both directions. A fourth library added to the container alone is rejected at execute
    time for reasons that read like a permissions or configuration problem; one added to this lambda
    alone reaches a script map with no entry for it. Neither copy can import the other, so this
    parity assertion is the only thing holding them together."""

    def test_the_lambda_list_matches_the_container_config_list(self):
        declared = _literal_assignments(
            os.path.join(_CONTAINER_DIR, "utils", "training", "config.py"), "RL_LIBRARIES")
        assert len(declared) == 1, "RL_LIBRARIES is no longer one literal in the container config"
        assert set(declared[0]) == set(_load().SUPPORTED_RL_LIBRARIES)

    def test_the_lambda_list_matches_every_container_script_map(self):
        script_maps = _literal_assignments(
            os.path.join(_CONTAINER_DIR, "__main__.py"), "script_map")
        # build_training_command and build_evaluation_command carry one each. The count is the
        # positive control for the extraction: a parse that found no script map at all would leave
        # the loop below asserting nothing.
        assert len(script_maps) == 2, f"expected 2 script maps, found {len(script_maps)}"
        for script_map in script_maps:
            assert set(script_map) == set(_load().SUPPORTED_RL_LIBRARIES)
