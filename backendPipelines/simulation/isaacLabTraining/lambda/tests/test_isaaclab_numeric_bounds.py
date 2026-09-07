#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Bounds and launch-time checks on the isaacLab run configuration.

Every value in the run configuration is operator-supplied — a template config body that allows custom
edits, or a JSON file sitting in the asset — so a mistyped count or a mistyped checkpoint path is an
ordinary input. Both are rejected in `openPipeline`, the first state of the pipeline's state machine,
where nothing has been provisioned yet. The alternatives are expensive: an out-of-range `numEnvs`
exhausts GPU memory only after Isaac Sim has booted, and a checkpoint path naming no object fails
inside the container after a GPU node has started and pulled the image.

The container keeps its own copy of the coercion and the ceilings, which is what covers a direct
container invocation; the ceilings are asserted equal here because the two copies cannot import each
other (the lambda's code bundle is the `lambda/` directory alone).
"""

import ast
import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONTAINER_CONFIG = os.path.join(
    os.path.dirname(_LAMBDA_DIR), "container", "utils", "training", "config.py")

if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(_k, _v)

# Every pipeline ships a module called openPipeline, so `import openPipeline` in one pytest process
# resolves to whichever lambda directory leads on sys.path. Both modules here are loaded by path under
# names only this suite uses, and each load asserts the file it got.
_OPEN_PIPELINE_MODULE = "isaacLab_openPipeline_bounds_undertest"
_CONTAINER_CONFIG_MODULE = "isaacLab_container_config_undertest"


def _load_by_path(module_name, path):
    assert os.path.isfile(path), path
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    loaded = os.path.normcase(os.path.normpath(os.path.abspath(mod.__file__)))
    assert loaded == os.path.normcase(os.path.normpath(path)), (
        f"module shadow: loaded {mod.__file__}, expected {path}")
    return mod


def _open_pipeline():
    return _load_by_path(_OPEN_PIPELINE_MODULE, os.path.join(_LAMBDA_DIR, "openPipeline.py"))


def _container_config():
    return _load_by_path(_CONTAINER_CONFIG_MODULE, _CONTAINER_CONFIG)


CEILINGS = ("MAX_NUM_ENVS", "MAX_MAX_ITERATIONS", "MAX_NUM_EPISODES", "MAX_STEPS_PER_EPISODE")


def _s3_found():
    """An s3 client mock whose head_object succeeds and whose listings are empty."""
    s3 = MagicMock()
    s3.get_object.side_effect = Exception("no input file in these cases")
    s3.head_object.return_value = {"ContentLength": 1}
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    s3.get_paginator.return_value = paginator
    return s3


def _s3_head_error(code):
    s3 = _s3_found()
    s3.head_object.side_effect = ClientError(
        {"Error": {"Code": code, "Message": "stub"}}, "HeadObject")
    return s3


def _s3_with_input_file(body):
    """An s3 client mock whose get_object returns ``body``, so the input-file defaults route runs."""
    s3 = _s3_found()
    s3.get_object.side_effect = None
    s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body.encode()))}
    return s3


def _event(training_config, input_file="model.usd"):
    return {
        "jobName": "isaaclab-job-abcd1234",
        "bucketAsset": "asset-bucket",
        "inputAssetLocationKey": "xid130a6/",
        "inputS3AssetFilePath": f"s3://asset-bucket/xid130a6/{input_file}",
        "outputS3AssetFilesPath": "s3://asset-bucket/pipelines/p1/JOB/output/E1/files/",
        "externalSfnTaskToken": "tok-123",
        "trainingConfig": training_config,
    }


def _train(**overrides):
    config = {"mode": "train", "task": "Isaac-Cartpole-Direct-v0", "rlLibrary": "rsl_rl"}
    config.update(overrides)
    return config


def _evaluate(**overrides):
    config = {
        "mode": "evaluate",
        "task": "Isaac-Cartpole-Direct-v0",
        "rlLibrary": "rsl_rl",
        "policyS3Uri": "s3://asset-bucket/xid130a6/checkpoints/model_300.pt",
    }
    config.update(overrides)
    return config


def _definition(training_config, s3=None, input_file="model.usd"):
    """The job configuration the state machine payload carries, with AWS calls stubbed."""
    mod = _open_pipeline()
    with patch.object(mod, "s3_client", s3 if s3 is not None else _s3_found()), \
            patch.object(mod, "sfn_client", MagicMock()):
        payload = mod.lambda_handler(_event(training_config, input_file), MagicMock())
    return json.loads(payload["definition"])


def _rejects(training_config, s3=None, input_file="model.usd"):
    """The error the handler raises for a run configuration, with the callback mock it reported to."""
    mod = _open_pipeline()
    sfn = MagicMock()
    with patch.object(mod, "s3_client", s3 if s3 is not None else _s3_found()), \
            patch.object(mod, "sfn_client", sfn):
        with pytest.raises(Exception) as raised:
            mod.lambda_handler(_event(training_config, input_file), MagicMock())
    return raised.value, sfn


@pytest.mark.unit
class TestCountCeilings:
    """A count far above any workable value is refused before a Batch job is submitted."""

    @pytest.mark.parametrize("field,value", [
        ("numEnvs", 400000),
        ("maxIterations", 10000000),
    ])
    def test_training_count_above_its_ceiling_is_rejected(self, field, value):
        error, sfn = _rejects(_train(**{field: value}))

        assert field in str(error)
        # The workflow task waits on the callback token, so the rejection is reported rather than only
        # raised.
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-123"

    @pytest.mark.parametrize("field,value", [
        ("numEnvs", 400000),
        ("numEpisodes", 1000000),
        ("stepsPerEpisode", 10000000),
    ])
    def test_evaluation_count_above_its_ceiling_is_rejected(self, field, value):
        error, _ = _rejects(_evaluate(**{field: value}))

        assert field in str(error)

    def test_the_ceiling_value_itself_is_accepted(self):
        """The boundary control: the ceiling is inclusive, so a bound that had been set one too low
        would fail here rather than passing quietly."""
        mod = _open_pipeline()
        definition = _definition(_train(numEnvs=mod.MAX_NUM_ENVS,
                                        maxIterations=mod.MAX_MAX_ITERATIONS))

        assert definition["trainingConfig"]["numEnvs"] == mod.MAX_NUM_ENVS
        assert definition["trainingConfig"]["maxIterations"] == mod.MAX_MAX_ITERATIONS

    def test_a_shipped_default_run_is_accepted(self):
        """The positive control for the whole class: the template's own values still run."""
        definition = _definition(_train(numEnvs=4096, maxIterations=1500))

        assert definition["trainingConfig"]["numEnvs"] == 4096
        assert definition["trainingConfig"]["maxIterations"] == 1500


@pytest.mark.unit
class TestValuesReachTheContainerAsNumbers:
    """The container multiplies numEpisodes by stepsPerEpisode for --video_length, so a string there
    concatenates into a plausible-looking argument that is not the requested one."""

    def test_a_quoted_count_is_coerced_by_the_lambda(self):
        definition = _definition(_evaluate(numEpisodes="50", stepsPerEpisode="1000"))

        assert definition["trainingConfig"]["numEpisodes"] == 50
        assert definition["trainingConfig"]["stepsPerEpisode"] == 1000
        assert isinstance(definition["trainingConfig"]["numEpisodes"], int)
        assert isinstance(definition["trainingConfig"]["stepsPerEpisode"], int)
        # What the container computes from them: 50000 steps, not a 2000-character string.
        product = (definition["trainingConfig"]["numEpisodes"]
                   * definition["trainingConfig"]["stepsPerEpisode"])
        assert product == 50000

    def test_a_quoted_seed_is_coerced(self):
        definition = _definition(_train(seed="7"))

        assert definition["trainingConfig"]["seed"] == 7

    @pytest.mark.parametrize("value", [True, 2.5, "many"])
    def test_a_count_that_names_no_whole_number_is_rejected(self, value):
        error, _ = _rejects(_train(numEnvs=value))

        assert "numEnvs" in str(error)

    def test_a_blank_count_still_means_the_default(self):
        """merge_configs treats a blank configuration value as unset, which is what lets an input file
        hold a standing default. Coercion must not turn that into a rejection."""
        definition = _definition(_train(numEnvs="", maxIterations=""))

        assert definition["trainingConfig"]["numEnvs"] == 4096
        assert definition["trainingConfig"]["maxIterations"] == 1500

    def test_a_task_that_is_not_a_task_id_is_rejected(self):
        error, _ = _rejects(_train(task=5))

        assert "task" in str(error)

    def test_a_blank_task_in_the_input_file_is_rejected(self):
        """A blank in the run configuration falls back, but the input file's own values are taken as
        written — so this is the route a blank task actually reaches the container's command line by."""
        error, _ = _rejects({"mode": "train", "rlLibrary": "rsl_rl"},
                            s3=_s3_with_input_file(json.dumps({"trainingConfig": {"task": "   "}})),
                            input_file="defaults.json")

        assert "task" in str(error)


@pytest.mark.unit
class TestCheckpointExistence:
    def test_a_checkpoint_naming_no_object_is_rejected(self):
        error, sfn = _rejects(_evaluate(checkpointPath="checkpoints/typo.pt"),
                              s3=_s3_head_error("404"))

        assert "checkpointPath" in str(error)
        assert sfn.send_task_failure.call_count == 1

    def test_an_undeterminable_checkpoint_does_not_block_the_run(self):
        """A denied HeadObject is not evidence of absence, so the run proceeds and the container
        decides. Turning a permissions gap into a rejected run would be a worse failure than the one
        this check removes."""
        definition = _definition(_evaluate(checkpointPath="checkpoints/model_300.pt"),
                                 s3=_s3_head_error("AccessDenied"))

        assert definition["trainingConfig"]["policyS3Uri"].endswith("checkpoints/model_300.pt")

    def test_a_present_checkpoint_is_accepted(self):
        definition = _definition(_evaluate(checkpointPath="checkpoints/model_300.pt"))

        assert definition["trainingConfig"]["policyS3Uri"] == (
            "s3://asset-bucket/xid130a6/checkpoints/model_300.pt")


@pytest.mark.unit
class TestContainerKeepsTheSameBounds:
    """The container is invocable on its own, so its copy of the ceilings has to hold too."""

    @pytest.mark.parametrize("field,value", [
        ("numEnvs", 400000),
        ("maxIterations", 10000000),
        ("numEpisodes", 1000000),
        ("stepsPerEpisode", 10000000),
    ])
    def test_a_count_above_its_ceiling_is_rejected(self, field, value):
        config = _container_config()
        data = {"jobName": "j", "trainingConfig": {"mode": "evaluate", field: value}}

        with pytest.raises(ValueError) as raised:
            config.PipelineConfig.from_dict(data)

        assert field in str(raised.value)

    def test_a_shipped_default_run_is_accepted(self):
        config = _container_config()
        parsed = config.PipelineConfig.from_dict(
            {"jobName": "j", "trainingConfig": {"mode": "train", "numEnvs": 4096,
                                                "maxIterations": 1500}})

        assert parsed.num_envs == 4096
        assert parsed.max_iterations == 1500

    @pytest.mark.parametrize("name", CEILINGS)
    def test_the_ceiling_matches_the_lambda_copy(self, name):
        """Neither module can import the other, so the two copies are compared directly. A ceiling
        enforced in only one of them lets the value through on whichever route skips it."""
        lambda_value = getattr(_open_pipeline(), name)
        container_value = getattr(_container_config(), name)

        assert isinstance(lambda_value, int)
        assert lambda_value == container_value

    @pytest.mark.parametrize("name", CEILINGS)
    def test_each_ceiling_is_declared_exactly_once_per_file(self, name):
        """The comparison above reads the loaded attribute, which a second assignment would silently
        win. Parsing the source is what makes the single declaration visible."""
        for path in (os.path.join(_LAMBDA_DIR, "openPipeline.py"), _CONTAINER_CONFIG):
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            declarations = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
            ]
            assert len(declarations) == 1, f"{name} in {path}: {len(declarations)} declarations"
