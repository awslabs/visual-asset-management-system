#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for coercion and range checking of the isaacLab job config.

The job config is JSON the operator assembled from a template body and an optional input
configuration file, so a value may arrive as any JSON type. A wrong type or an out-of-range count
reaches an Isaac Lab command line, where it either fails after Isaac Sim has started or — for
``numEpisodes`` as a string, which ``str * int`` happily multiplies — produces an argument that
looks plausible and is not the one requested. Both must be established while the config is parsed."""

import pytest

from utils.training.config import PipelineConfig


def _evaluate(**training):
    return {"trainingConfig": dict({"mode": "evaluate"}, **training)}


def _train(**training):
    return {"trainingConfig": dict({"mode": "train"}, **training)}


@pytest.mark.unit
class TestQuotedNumbersAreCoerced:
    """A hand-edited config body easily quotes a number; the intent is unambiguous."""

    def test_quoted_num_episodes_multiplies_as_a_number(self):
        config = PipelineConfig.from_dict(_evaluate(numEpisodes="50", stepsPerEpisode=1000))
        # Unfixed, "50" * 1000 built a 2000-character --video_length argument.
        assert config.num_episodes == 50
        assert config.num_episodes * config.steps_per_episode == 50000

    def test_quoted_num_envs(self):
        assert PipelineConfig.from_dict(_train(numEnvs="4096")).num_envs == 4096

    def test_integral_float_becomes_an_int(self):
        # 4096.0 == 4096, but str(4096.0) is "4096.0", which --num_envs rejects.
        num_envs = PipelineConfig.from_dict(_train(numEnvs=4096.0)).num_envs
        assert isinstance(num_envs, int) and num_envs == 4096

    def test_quoted_record_video_is_not_truthy(self):
        config = PipelineConfig.from_dict(_evaluate(recordVideo="false"))
        assert config.record_video is False


@pytest.mark.unit
class TestValuesThatWouldSilentlySucceed:
    """Each of these produced a usable-looking value rather than an error."""

    def test_boolean_is_not_taken_as_one(self):
        with pytest.raises(ValueError, match="numEnvs"):
            PipelineConfig.from_dict(_train(numEnvs=True))

    def test_fraction_is_not_truncated(self):
        with pytest.raises(ValueError, match="numEnvs"):
            PipelineConfig.from_dict(_train(numEnvs=3.9))

    def test_blank_value_is_rejected(self):
        with pytest.raises(ValueError, match="numEnvs"):
            PipelineConfig.from_dict(_train(numEnvs=""))

    def test_unparseable_value_is_rejected(self):
        with pytest.raises(ValueError, match="maxIterations"):
            PipelineConfig.from_dict(_train(maxIterations="lots"))

    def test_non_boolean_flag_is_rejected(self):
        # recordVideo gates the video upload, so an unrecognised value must not read as truthy.
        with pytest.raises(ValueError, match="recordVideo"):
            PipelineConfig.from_dict(_evaluate(recordVideo="yes"))

    def test_numeric_flag_is_rejected(self):
        with pytest.raises(ValueError, match="recordVideo"):
            PipelineConfig.from_dict(_evaluate(recordVideo=1))

    def test_unknown_rl_library_does_not_fall_back(self):
        with pytest.raises(ValueError, match="rlLibrary"):
            PipelineConfig.from_dict(_train(rlLibrary="sb3"))

    def test_mode_is_matched_exactly(self):
        with pytest.raises(ValueError, match="mode"):
            PipelineConfig.from_dict({"trainingConfig": {"mode": "Train"}})

    def test_blank_task_is_rejected(self):
        with pytest.raises(ValueError, match="task"):
            PipelineConfig.from_dict(_train(task=""))

    def test_non_object_section_is_rejected(self):
        with pytest.raises(ValueError, match="trainingConfig"):
            PipelineConfig.from_dict({"trainingConfig": "train"})


@pytest.mark.unit
class TestCountFloors:
    """Every count here reaches a command line, so zero and negative are rejected."""

    @pytest.mark.parametrize("field", ["numEnvs", "maxIterations", "numEpisodes",
                                      "stepsPerEpisode"])
    def test_zero_is_rejected(self, field):
        with pytest.raises(ValueError, match=field):
            PipelineConfig.from_dict(_train(**{field: 0}))

    @pytest.mark.parametrize("field", ["numEnvs", "maxIterations", "numEpisodes",
                                      "stepsPerEpisode"])
    def test_negative_is_rejected(self, field):
        with pytest.raises(ValueError, match=field):
            PipelineConfig.from_dict(_train(**{field: -5}))

    def test_seed_takes_any_whole_number(self):
        assert PipelineConfig.from_dict(_train(seed=-1)).seed == -1
        assert PipelineConfig.from_dict(_train(seed=0)).seed == 0


@pytest.mark.unit
class TestAbsentAndNullTakeTheDefault:
    """A key the config leaves out, or leaves null, keeps the documented default."""

    def test_empty_config_is_all_defaults(self):
        config = PipelineConfig.from_dict({})
        assert (config.job_name, config.mode, config.task) == (
            "isaaclab-job", "train", "Isaac-Cartpole-v0")
        assert (config.num_envs, config.max_iterations) == (4096, 1500)
        assert (config.num_episodes, config.steps_per_episode) == (50, 1000)
        assert config.seed is None

    def test_evaluate_mode_lowers_the_num_envs_default(self):
        assert PipelineConfig.from_dict(_evaluate()).num_envs == 100

    def test_null_section_is_treated_as_absent(self):
        # A hand-edited body may carry an explicit null in place of a section.
        config = PipelineConfig.from_dict({"trainingConfig": None})
        assert (config.mode, config.num_envs) == ("train", 4096)

    def test_null_seed_stays_none(self):
        # openPipeline emits "seed": training_config.get("seed"), i.e. a literal null when unset.
        assert PipelineConfig.from_dict(_train(seed=None)).seed is None


@pytest.mark.unit
class TestOpenPipelineOutputStillParses:
    """The job configs the lambda actually emits must be unaffected."""

    def test_training_job_config(self):
        config = PipelineConfig.from_dict({
            "jobName": "isaaclab-train",
            "trainingConfig": {"mode": "train", "task": "Isaac-Cartpole-Direct-v0",
                               "numEnvs": 4096, "maxIterations": 1500, "rlLibrary": "rsl_rl",
                               "seed": None},
            "inputS3AssetFilePath": "s3://assets/a1/config.json",
            "customEnvironmentS3Uri": "",
            "outputS3AssetFilesPath": "s3://assets/a1/pipelines/p/j/output/e1/files/",
        })
        assert (config.job_name, config.mode, config.task) == (
            "isaaclab-train", "train", "Isaac-Cartpole-Direct-v0")
        assert (config.num_envs, config.max_iterations, config.rl_library) == (
            4096, 1500, "rsl_rl")
        assert config.output_s3_path == "s3://assets/a1/pipelines/p/j/output/e1/files/"

    def test_evaluation_job_config(self):
        config = PipelineConfig.from_dict({
            "jobName": "isaaclab-eval",
            "trainingConfig": {"mode": "evaluate", "task": "Isaac-Cartpole-v0", "numEnvs": 100,
                               "numEpisodes": 50, "stepsPerEpisode": 1000,
                               "policyS3Uri": "s3://assets/a1/checkpoints/model_1500.pt",
                               "recordVideo": False, "rlLibrary": "rsl_rl"},
        })
        assert config.mode == "evaluate"
        assert config.num_episodes * config.steps_per_episode == 50000
        assert config.policy_s3_uri == "s3://assets/a1/checkpoints/model_1500.pt"
        assert config.record_video is False


@pytest.mark.unit
class TestRemovedOptions:
    """Multi-node training and the checkpoint options are not part of the pipeline.

    A stale key left in an operator's config body or input file is ignored rather than acted on, so
    reintroducing one of these fields would be a behaviour change rather than a no-op."""

    @pytest.mark.parametrize("field", ["num_nodes", "save_checkpoints", "checkpoint_interval"])
    def test_field_is_not_declared(self, field):
        assert not hasattr(PipelineConfig.from_dict({}), field)

    def test_stale_sections_are_ignored(self):
        config = PipelineConfig.from_dict({
            "trainingConfig": {"mode": "train"},
            "computeConfig": {"numNodes": 4},
            "outputConfig": {"saveCheckpoints": False, "checkpointInterval": 25},
        })
        assert config.mode == "train"
        assert not hasattr(config, "num_nodes")
