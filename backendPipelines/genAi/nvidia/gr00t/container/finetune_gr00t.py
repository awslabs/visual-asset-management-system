#!/usr/bin/env python3
"""
Workflow script for fine-tuning GR00T models with configurable parameters.
This script orchestrates the model-specific portion of the workflow:
1. Validate dataset path prepared by the entrypoint shell script
2. Run training
"""

import os
import sys
import logging
import json
from pathlib import Path
import torch
from transformers import TrainingArguments
from torch.distributed.run import main as torchrun

# Import GR00T training and data utilities
from gr00t.data.dataset import LeRobotMixtureDataset, LeRobotSingleDataset
from gr00t.data.schema import EmbodimentTag
from gr00t.experiment.data_config import load_data_config
from gr00t.experiment.runner import TrainRunner
from gr00t.model.gr00t_n1 import GR00T_N1_5
from gr00t.utils.peft import get_lora_model

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/workspace/finetune_gr00t.log"),
    ],
)
logger = logging.getLogger(__name__)


class FinetuneWorkflow:
    """Main workflow class for fine-tuning GR00T models."""

    def __init__(self):
        """Initialize the workflow with environment variables."""
        # Dataset directory
        self.dataset_local_dir = os.getenv("DATASET_LOCAL_DIR")

        # Output directories (prefer EFS paths if provided by the Batch Job Definition)
        self.output_dir = os.getenv("OUTPUT_DIR", "/workspace/checkpoints")

        # Training parameters
        self.max_steps = int(os.getenv("MAX_STEPS", "6000"))
        self.save_steps = int(os.getenv("SAVE_STEPS", "2000"))
        self.num_gpus = int(os.getenv("NUM_GPUS", "1"))
        self.data_config = os.getenv("DATA_CONFIG", "so100_dualcam")
        self.video_backend = os.getenv("VIDEO_BACKEND", "torchvision_av")
        self.batch_size = int(os.getenv("BATCH_SIZE", "32"))
        self.learning_rate = float(os.getenv("LEARNING_RATE", "1e-4"))
        self.base_model_path = os.getenv("BASE_MODEL_PATH", "nvidia/GR00T-N1.5-3B")
        self.embodiment_tag = os.getenv("EMBODIMENT_TAG", "new_embodiment")
        self.report_to = os.getenv("REPORT_TO", "tensorboard")

        # Optional parameters
        self.tune_llm = os.getenv("TUNE_LLM", "false").lower() == "true"
        self.tune_visual = os.getenv("TUNE_VISUAL", "false").lower() == "true"
        self.tune_projector = os.getenv("TUNE_PROJECTOR", "true").lower() == "true"
        self.tune_diffusion_model = (
            os.getenv("TUNE_DIFFUSION_MODEL", "true").lower() == "true"
        )
        self.lora_rank = int(os.getenv("LORA_RANK", "0"))
        self.lora_alpha = int(os.getenv("LORA_ALPHA", "16"))
        self.lora_dropout = float(os.getenv("LORA_DROPOUT", "0.1"))
        self.weight_decay = float(os.getenv("WEIGHT_DECAY", "1e-5"))
        self.warmup_ratio = float(os.getenv("WARMUP_RATIO", "0.05"))
        self.dataloader_num_workers = int(os.getenv("DATALOADER_NUM_WORKERS", "8"))
        self.dataloader_prefetch_factor = int(
            os.getenv("DATALOADER_PREFETCH_FACTOR", "4")
        )
        self.balance_dataset_weights = (
            os.getenv("BALANCE_DATASET_WEIGHTS", "true").lower() == "true"
        )
        self.balance_trajectory_weights = (
            os.getenv("BALANCE_TRAJECTORY_WEIGHTS", "true").lower() == "true"
        )
        self.lora_full_model = os.getenv("LORA_FULL_MODEL", "false").lower() == "true"
        self.resume = os.getenv("RESUME", "false").lower() == "true"

        # Validate required parameters
        self._validate_parameters()

    def _validate_parameters(self):
        """Validate required environment variables."""
        required_params = {
            "DATASET_LOCAL_DIR": self.dataset_local_dir,
        }

        missing_params = [
            param for param, value in required_params.items() if not value
        ]
        if missing_params:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing_params)}"
            )

        # Validate data_config
        valid_data_configs = [
            "so100_dualcam",
            "fourier_gr1_arms_only",
            "fourier_gr1_arms_waist",
            "agibot_genie1_dualcam",
            "oxe_droid_single_cam",
        ]
        if self.data_config not in valid_data_configs:
            logger.warning(f"Data config '{self.data_config}' may not be supported")

        # Validate embodiment_tag
        valid_embodiment_tags = ["new_embodiment", "gr1", "oxe_droid", "agibot_genie1"]
        if self.embodiment_tag not in valid_embodiment_tags:
            logger.warning(
                f"Embodiment tag '{self.embodiment_tag}' may not be supported"
            )

        logger.info("All required parameters validated successfully")

    def validate_dataset(self):
        """
        Ensure a local dataset directory is ready.
        """
        logger.info("Validating dataset...")

        # 1) Use explicit local directory if provided and non-empty
        if "DATASET_LOCAL_DIR" in os.environ and os.path.isdir(self.dataset_local_dir):
            if os.listdir(self.dataset_local_dir):
                logger.info(f"Using dataset directory: {self.dataset_local_dir}")

                meta_dir = os.path.join(self.dataset_local_dir, "meta")
                modality_json_path = os.path.join(meta_dir, "modality.json")
                if self.data_config == "so100_dualcam" and not os.path.isfile(
                    modality_json_path
                ):
                    os.makedirs(meta_dir, exist_ok=True)
                    modality_content = {
                        "state": {
                            "single_arm": {"start": 0, "end": 5},
                            "gripper": {"start": 5, "end": 6},
                        },
                        "action": {
                            "single_arm": {"start": 0, "end": 5},
                            "gripper": {"start": 5, "end": 6},
                        },
                        "video": {
                            "wrist": {"original_key": "observation.images.wrist"},
                            "front": {"original_key": "observation.images.front"},
                        },
                        "annotation": {
                            "human.task_description": {"original_key": "task_index"}
                        },
                    }
                    with open(modality_json_path, "w") as f:
                        json.dump(modality_content, f, indent=4)
                    logger.info(
                        f"Created missing modality.json at {modality_json_path} for so100_dualcam"
                    )
                return
            else:
                logger.warning(
                    f"DATASET_LOCAL_DIR is provided but empty: {self.dataset_local_dir}"
                )

        # Past this point, the dataset should already exist (shell prepared it)
        raise RuntimeError(
            "Dataset directory not prepared. Ensure entrypoint script resolved and downloaded the dataset."
        )

    def _train_once(self):
        """Run the fine-tuning steps in-process (ported from gr00t_finetune.py)."""
        logger.info("Starting training...")

        # ------------ step 1: load dataset ------------
        embodiment_tag = EmbodimentTag(self.embodiment_tag)

        # 1.1 modality configs and transforms
        data_config_cls = load_data_config(self.data_config)
        modality_configs = data_config_cls.modality_config()
        transforms = data_config_cls.transform()

        # 1.2 data loader: we will use either single dataset or mixture dataset
        dataset_path = [self.dataset_local_dir]
        if len(dataset_path) == 1:
            train_dataset = LeRobotSingleDataset(
                dataset_path=os.path.abspath(dataset_path[0]),
                modality_configs=modality_configs,
                transforms=transforms,
                embodiment_tag=embodiment_tag,  # This will override the dataset's embodiment tag to "new_embodiment"
                video_backend=self.video_backend,
            )
        else:
            single_datasets = []
            for p in dataset_path:
                assert os.path.exists(p), f"Dataset path {p} does not exist"
                # We use the same transforms, modality configs, and embodiment tag for all datasets here,
                # in reality, you can use dataset from different modalities and embodiment tags
                dataset = LeRobotSingleDataset(
                    dataset_path=p,
                    modality_configs=modality_configs,
                    transforms=transforms,
                    embodiment_tag=embodiment_tag,
                    video_backend=self.video_backend,
                )
                single_datasets.append(dataset)

            train_dataset = LeRobotMixtureDataset(
                data_mixture=[
                    (dataset, 1.0)
                    for dataset in single_datasets  # we will use equal weights for all datasets
                ],
                mode="train",
                balance_dataset_weights=self.balance_dataset_weights,
                balance_trajectory_weights=self.balance_trajectory_weights,
                seed=42,
                metadata_config={
                    "percentile_mixing_method": "weighted_average",
                },
            )
            print(f"Loaded {len(single_datasets)} datasets, with {dataset_path} ")

        # ------------ step 2: load model ------------
        # First, get the data config to determine action horizon
        data_action_horizon = len(data_config_cls.action_indices)

        # Load model
        model = GR00T_N1_5.from_pretrained(
            pretrained_model_name_or_path=self.base_model_path,
            tune_llm=self.tune_llm,  # backbone's LLM
            tune_visual=self.tune_visual,  # backbone's vision tower
            tune_projector=self.tune_projector,  # action head's projector
            tune_diffusion_model=self.tune_diffusion_model,  # action head's DiT
        )

        # Update action_horizon to match data config
        # Need to recreate action head with correct config since it was initialized with old config
        if data_action_horizon != model.action_head.config.action_horizon:
            print(
                f"Recreating action head with action_horizon {data_action_horizon} (was {model.action_head.config.action_horizon})"
            )

            # Update the action head config
            new_action_head_config = model.action_head.config
            new_action_head_config.action_horizon = data_action_horizon

            # Import the FlowmatchingActionHead class
            from gr00t.model.action_head.flow_matching_action_head import (
                FlowmatchingActionHead,
            )

            # Create new action head with updated config
            new_action_head = FlowmatchingActionHead(new_action_head_config)

            # Copy the weights from the old action head to the new one
            new_action_head.load_state_dict(
                model.action_head.state_dict(), strict=False
            )

            # Replace the action head
            model.action_head = new_action_head

            # Update model config AND the action_head_cfg dictionary that gets saved
            model.config.action_horizon = data_action_horizon
            model.action_horizon = data_action_horizon
            model.config.action_head_cfg["action_horizon"] = data_action_horizon

            # Set trainable parameters for the new action head
            model.action_head.set_trainable_parameters(
                tune_projector=self.tune_projector,
                tune_diffusion_model=self.tune_diffusion_model,
            )

        # Set the model's compute_dtype to bfloat16
        model.compute_dtype = "bfloat16"
        model.config.compute_dtype = "bfloat16"

        if self.lora_rank > 0:
            model = get_lora_model(
                model,
                rank=self.lora_rank,
                lora_alpha=self.lora_alpha,
                lora_dropout=self.lora_dropout,
                action_head_only=not self.lora_full_model,
            )

        # 2.1 modify training args
        training_args = TrainingArguments(
            output_dir=self.output_dir,
            run_name=None,
            remove_unused_columns=False,
            deepspeed="",
            gradient_checkpointing=False,
            bf16=True,
            tf32=True,
            per_device_train_batch_size=self.batch_size,
            gradient_accumulation_steps=1,
            dataloader_num_workers=self.dataloader_num_workers,
            dataloader_pin_memory=False,
            dataloader_prefetch_factor=self.dataloader_prefetch_factor,
            dataloader_persistent_workers=self.dataloader_num_workers > 0,
            optim="adamw_torch",
            adam_beta1=0.95,
            adam_beta2=0.999,
            adam_epsilon=1e-8,
            learning_rate=self.learning_rate,
            weight_decay=self.weight_decay,
            warmup_ratio=self.warmup_ratio,
            lr_scheduler_type="cosine",
            logging_steps=10.0,
            num_train_epochs=300,
            max_steps=self.max_steps,
            save_strategy="steps",
            save_steps=self.save_steps,
            # evaluation_strategy="no",
            save_total_limit=5,
            report_to=self.report_to,
            seed=42,
            do_eval=False,
            ddp_find_unused_parameters=False,
            ddp_bucket_cap_mb=100,
            torch_compile_mode=None,
        )

        # 2.2 run experiment
        experiment = TrainRunner(
            train_dataset=train_dataset,
            model=model,
            training_args=training_args,
            resume_from_checkpoint=self.resume,
        )

        # 2.3 run experiment
        experiment.train()

        logger.info("Training completed successfully")

    def run_training(self):
        """Run the training with optional multi-GPU support (torchrun)."""
        # Create output directories (checkpoints and tensorboard logs)
        os.makedirs(self.output_dir, exist_ok=True)

        available_gpus = torch.cuda.device_count() if torch.cuda.is_available() else 1

        # Validate GPU configuration
        assert (
            self.num_gpus <= available_gpus
        ), f"Number of GPUs requested ({self.num_gpus}) is greater than the available GPUs ({available_gpus})"
        assert self.num_gpus > 0, "Number of GPUs must be greater than 0"
        print(f"Using {self.num_gpus} GPUs")

        if self.num_gpus == 1:
            # Single GPU mode - set CUDA_VISIBLE_DEVICES=0
            os.environ["CUDA_VISIBLE_DEVICES"] = "0"
            # Run training in-process
            self._train_once()
        else:
            # Multi-GPU mode - use torchrun to re-invoke this script with multiple processes
            if os.environ.get("IS_TORCHRUN", "0") == "1":
                # We are already inside a torchrun worker
                self._train_once()
            else:
                script_path = Path(__file__).absolute()
                # Remove any existing CUDA_VISIBLE_DEVICES from environment
                if "CUDA_VISIBLE_DEVICES" in os.environ:
                    del os.environ["CUDA_VISIBLE_DEVICES"]

                # Build torchrun args (call torch.distributed.run main directly)
                args = [
                    "--standalone",
                    f"--nproc_per_node={self.num_gpus}",
                    "--nnodes=1",
                    str(script_path),
                ]

                print("Running torchrun with args: ", args)
                os.environ["IS_TORCHRUN"] = "1"
                try:
                    torchrun(args=args)
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 0
                    sys.exit(code)

    def run_workflow(self):
        """Run the complete workflow."""
        try:
            logger.info("Starting GR00T fine-tuning...")

            # Step 1: Validate dataset was prepared by entrypoint script
            self.validate_dataset()

            # Step 2: Run training
            self.run_training()

            logger.info("GR00T fine-tuning completed successfully!")

        except Exception as e:
            logger.error(f"Workflow failed: {str(e)}")
            sys.exit(1)


def main():
    """Main entry point."""
    workflow = FinetuneWorkflow()
    workflow.run_workflow()


if __name__ == "__main__":
    main()
