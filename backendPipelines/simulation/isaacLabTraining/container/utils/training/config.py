#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Configuration for IsaacLab pipeline (training and evaluation).

The job config is JSON assembled from the template's config body and an optional input
configuration file, so every value is operator-supplied and arrives with whatever JSON type was
written. Each field is coerced and range-checked while the config is parsed, before Isaac Sim
starts and any GPU work is done.
"""

from dataclasses import dataclass
from typing import Optional

MODES = ("train", "evaluate")
RL_LIBRARIES = ("rsl_rl", "rl_games", "skrl")

# Ceilings for the operator-supplied counts, each far above any shipped default. They catch a value
# that cannot be what was meant — a mistyped extra digit — rather than describing capacity, which
# depends on the task and the GPU: a count inside its ceiling can still exhaust GPU memory. Rejecting
# one here costs nothing, while the same value reaches the failure only after Isaac Sim has started.
# A second copy lives in lambda/openPipeline.py, which rejects it before the Batch job is submitted;
# lambda/tests/test_isaaclab_numeric_bounds.py asserts the two agree.
MAX_NUM_ENVS = 65536
MAX_MAX_ITERATIONS = 100000
MAX_NUM_EPISODES = 10000
MAX_STEPS_PER_EPISODE = 100000


def _section(data: dict, key: str) -> dict:
    """One section of the job config, or ``{}`` when it carries none."""
    section = data.get(key)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ValueError(f"{key} must be a configuration object (received {section!r})")
    return section


def _parse_whole(value) -> Optional[int]:
    """``value`` as an int when it names one exactly, else ``None``.

    Rejects a fraction rather than truncating it, and rejects ``nan``/``inf``, which are integral to
    neither ``int()`` nor ``is_integer()``.
    """
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    try:
        number = float(value)
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


def _int_field(section: dict, key: str, default, minimum: Optional[int] = 1,
               maximum: Optional[int] = None):
    """The whole number ``key`` names, or ``default`` when the section leaves it out.

    A quoted number is accepted because a hand-edited config body easily carries one. A boolean, a
    fraction, a blank value, or a count outside ``minimum``..``maximum`` is rejected naming the field:
    each reaches a command line where it either fails once Isaac Sim is running or, for a string,
    multiplies into a plausible-looking argument that is not the requested one.
    """
    value = section.get(key)
    if value is None:
        return default

    if isinstance(value, bool):
        raise ValueError(f"{key} must be a whole number, not a boolean (received {value!r})")

    number = value if isinstance(value, int) else None
    if number is None and isinstance(value, (float, str)):
        number = _parse_whole(value)
    if number is None:
        raise ValueError(f"{key} must be a whole number (received {value!r})")

    if minimum is not None and number < minimum:
        raise ValueError(f"{key} must be {minimum} or greater (received {number})")
    if maximum is not None and number > maximum:
        raise ValueError(f"{key} must be {maximum} or less (received {number})")
    return number


def _bool_field(section: dict, key: str, default: bool) -> bool:
    """The boolean ``key`` names, or ``default`` when the section leaves it out.

    Only ``true`` and ``false`` are accepted, quoted or not — every other non-empty string is truthy,
    so a value such as ``"false"`` would otherwise turn the flag on.
    """
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError(f"{key} must be true or false (received {value!r})")


def _choice_field(section: dict, key: str, default: str, allowed: tuple) -> str:
    """The one value of ``allowed`` that ``key`` names, or ``default`` when the section leaves it out.

    An unrecognised value is rejected rather than falling back, so a run cannot complete against a
    task script or a mode the operator did not ask for.
    """
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, str) and value.strip() in allowed:
        return value.strip()
    raise ValueError(f"{key} must be one of {', '.join(allowed)} (received {value!r})")


def _text_field(section: dict, key: str, default: str) -> str:
    """The non-blank string ``key`` names, or ``default`` when the section leaves it out."""
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string (received {value!r})")
    return value.strip()


@dataclass
class PipelineConfig:
    job_name: str
    mode: str  # "train" or "evaluate"
    task: str
    rl_library: str

    # VAMS S3 paths
    input_s3_path: str  # Input assets from VAMS (config file location)
    output_s3_path: str  # Output destination in VAMS asset bucket (outputS3AssetFilesPath)
    custom_environment_s3_uri: str = ""  # Custom environment package S3 URI

    # Training-specific
    num_envs: int = 4096
    max_iterations: int = 1500
    seed: Optional[int] = None

    # Evaluation-specific
    policy_s3_uri: Optional[str] = None
    num_episodes: int = 50
    steps_per_episode: int = 1000
    record_video: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineConfig":
        config = _section(data, "trainingConfig")
        mode = _choice_field(config, "mode", "train", MODES)

        # Adjust defaults based on mode
        default_num_envs = 100 if mode == "evaluate" else 4096

        return cls(
            job_name=_text_field(data, "jobName", "isaaclab-job"),
            mode=mode,
            task=_text_field(config, "task", "Isaac-Cartpole-v0"),
            rl_library=_choice_field(config, "rlLibrary", "rsl_rl", RL_LIBRARIES),
            # VAMS S3 paths - use outputS3AssetFilesPath (asset bucket)
            input_s3_path=data.get("inputS3AssetFilePath", ""),
            output_s3_path=data.get("outputS3AssetFilesPath", ""),
            custom_environment_s3_uri=data.get("customEnvironmentS3Uri", ""),
            # Training params
            num_envs=_int_field(config, "numEnvs", default_num_envs, maximum=MAX_NUM_ENVS),
            max_iterations=_int_field(config, "maxIterations", 1500,
                                      maximum=MAX_MAX_ITERATIONS),
            seed=_int_field(config, "seed", None, minimum=None),
            # Evaluation params
            policy_s3_uri=config.get("policyS3Uri"),
            num_episodes=_int_field(config, "numEpisodes", 50, maximum=MAX_NUM_EPISODES),
            steps_per_episode=_int_field(config, "stepsPerEpisode", 1000,
                                         maximum=MAX_STEPS_PER_EPISODE),
            record_video=_bool_field(config, "recordVideo", False),
        )
