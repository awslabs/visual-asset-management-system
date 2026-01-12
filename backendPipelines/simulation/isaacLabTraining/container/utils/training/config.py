#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Configuration for IsaacLab pipeline (training and evaluation)."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PipelineConfig:
    job_name: str
    mode: str  # "train" or "evaluate"
    task: str
    rl_library: str
    
    # VAMS S3 paths
    input_s3_path: str  # Input assets from VAMS
    output_s3_path: str  # Output destination in VAMS asset bucket (outputS3AssetFilesPath)
    
    # Training-specific
    num_envs: int = 4096
    max_iterations: int = 1500
    seed: Optional[int] = None
    num_nodes: int = 1
    save_checkpoints: bool = True
    checkpoint_interval: int = 100
    
    # Evaluation-specific
    policy_s3_uri: Optional[str] = None
    num_episodes: int = 50
    steps_per_episode: int = 1000
    record_video: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineConfig":
        config = data.get("trainingConfig", {})
        output = data.get("outputConfig", {})
        mode = config.get("mode", "train")
        
        # Adjust defaults based on mode
        default_num_envs = 100 if mode == "evaluate" else 4096
        
        return cls(
            job_name=data.get("jobName", "isaaclab-job"),
            mode=mode,
            task=config.get("task", "Isaac-Cartpole-v0"),
            rl_library=config.get("rlLibrary", "rsl_rl"),
            # VAMS S3 paths - use outputS3AssetFilesPath (asset bucket)
            input_s3_path=data.get("inputS3AssetFilePath", ""),
            output_s3_path=data.get("outputS3AssetFilesPath", ""),
            # Training params
            num_envs=config.get("numEnvs", default_num_envs),
            max_iterations=config.get("maxIterations", 1500),
            seed=config.get("seed"),
            num_nodes=data.get("computeConfig", {}).get("numNodes", 1),
            save_checkpoints=output.get("saveCheckpoints", True),
            checkpoint_interval=output.get("checkpointInterval", 100),
            # Evaluation params
            policy_s3_uri=config.get("policyS3Uri"),
            num_episodes=config.get("numEpisodes", 50),
            steps_per_episode=config.get("stepsPerEpisode", 1000),
            record_video=config.get("recordVideo", False),
        )
