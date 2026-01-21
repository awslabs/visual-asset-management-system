# NVIDIA Isaac Lab VAMS Pipeline - User Guide

## Overview

The Isaac Lab VAMS pipeline enables you to train reinforcement learning policies using NVIDIA Isaac Lab environments on AWS infrastructure. This guide covers how to use built-in environments and upload custom environments and policies.

## Table of Contents

1. [Using Built-in Environments](#using-built-in-environments)
2. [Creating Custom Environments](#creating-custom-environments)
3. [Uploading Custom Environments to VAMS](#uploading-custom-environments-to-vams)
4. [Training with Custom Environments](#training-with-custom-environments)
5. [Using Pre-trained Policies](#using-pre-trained-policies)
6. [Configuration Reference](#configuration-reference)

---

## Using Built-in Environments

Isaac Lab includes 40+ pre-configured environments. The pipeline supports both training new policies and evaluating existing ones.

### Training Mode

Train a new policy from scratch:

```json
{
    "trainingConfig": {
        "mode": "train",
        "task": "Isaac-Ant-v0",
        "numEnvs": 4096,
        "maxIterations": 1500,
        "rlLibrary": "rsl_rl"
    },
    "computeConfig": {
        "numNodes": 1
    }
}
```

### Evaluation Mode

Evaluate a pre-trained policy. You can specify the checkpoint in three ways:

**Option 1: Relative checkpoint path (recommended)**

Reference a checkpoint within the same asset using a relative path:

```json
{
    "trainingConfig": {
        "mode": "evaluate",
        "task": "Isaac-Cartpole-Direct-v0",
        "checkpointPath": "checkpoints/model_300.pt",
        "numEnvs": 100,
        "numEpisodes": 50,
        "recordVideo": true,
        "rlLibrary": "rsl_rl"
    }
}
```

**Option 2: Full S3 URI**

Specify the complete S3 path to any checkpoint:

```json
{
    "trainingConfig": {
        "mode": "evaluate",
        "task": "Isaac-Ant-v0",
        "policyS3Uri": "s3://vams-assets/policies/ant_trained.pt",
        "numEnvs": 100,
        "numEpisodes": 50,
        "rlLibrary": "rsl_rl"
    }
}
```

**Option 3: Auto-discovery (legacy)**

Place a `.pt` file in the same directory as the evaluation config. The pipeline will automatically discover and use it.

**Key Differences:**

-   **Training**: Produces a trained policy (`.pt` file)
-   **Evaluation**: Requires an existing policy, produces metrics and optional videos

### Available Built-in Environments

Common environments include:

-   `Isaac-Cartpole-v0` - Classic cart-pole balancing
-   `Isaac-Ant-v0` - Quadruped locomotion
-   `Isaac-Humanoid-v0` - Humanoid walking
-   `Isaac-Reach-Franka-v0` - Robot arm reaching
-   `Isaac-Lift-Cube-Franka-v0` - Object manipulation

**List all environments:**

```bash
python scripts/environments/list_envs.py
```

---

## Creating Custom Environments

### Step 1: Generate Environment Template

Use Isaac Lab's template generator to create a new environment:

```bash
cd /path/to/IsaacLab
./isaaclab.sh --new
```

**Configuration options:**

-   **Location**: Choose "External" (for standalone package)
-   **Workflow**: Choose "Direct" (simpler) or "Manager" (modular)
-   **Framework**: Select RL libraries (rsl_rl, skrl, rl_games)

This creates a project structure:

```
my_custom_env/
├── setup.py
├── my_custom_env/
│   ├── __init__.py
│   ├── my_env.py
│   └── my_env_cfg.py
└── agents/
    └── rsl_rl_ppo_cfg.py
```

### Step 2: Implement Your Environment

Edit `my_env.py` to define your custom task:

```python
from isaaclab.envs import DirectRLEnv, DirectRLEnvCfg

class MyCustomEnv(DirectRLEnv):
    def __init__(self, cfg: DirectRLEnvCfg, **kwargs):
        super().__init__(cfg, **kwargs)
        # Initialize your environment

    def _pre_physics_step(self, actions):
        # Apply actions
        pass

    def _get_observations(self):
        # Return observations
        pass

    def _get_rewards(self):
        # Calculate rewards
        pass

    def _get_dones(self):
        # Determine episode termination
        pass
```

### Step 3: Register Your Environment

In `__init__.py`:

```python
import gymnasium as gym

gym.register(
    id="MyCustom-Robot-v0",
    entry_point=f"{__name__}.my_env:MyCustomEnv",
    disable_env_checker=True,
    kwargs={
        "env_cfg_entry_point": f"{__name__}.my_env_cfg:MyCustomEnvCfg",
        "rsl_rl_cfg_entry_point": f"{agents.__name__}.rsl_rl_ppo_cfg:PPORunnerCfg",
    },
)
```

### Step 4: Test Locally

```bash
# Install in development mode
pip install -e .

# Test training
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/train.py \
    --task MyCustom-Robot-v0 \
    --num_envs 512 \
    --headless
```

---

## Uploading Custom Environments to VAMS

### Step 1: Package Your Environment

Create a distributable package:

```bash
cd my_custom_env/

# Option A: Source distribution
python -m build --sdist

# Option B: Tar archive
tar -czf my_custom_env.tar.gz \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    .
```

This creates `dist/my_custom_env-0.1.0.tar.gz`

### Step 2: Upload to VAMS

**Via Web UI:**

1. Navigate to **Assets** in VAMS
2. Click **Upload Asset**
3. Select your package file (`my_custom_env.tar.gz`)
4. Add metadata:
    - **Name**: `my-custom-isaaclab-env`
    - **Type**: `isaaclab-environment`
    - **Description**: Brief description of your environment
5. Click **Upload**

**Via VAMS CLI:**

```bash
# Upload custom environment package to an existing asset
vamscli files upload \
    --database-id <database-id> \
    --asset-id <asset-id> \
    --file dist/my_custom_env-0.1.0.tar.gz \
    --relative-path my_custom_env.tar.gz
```

### Step 3: Note the Asset S3 Path

After upload, note the S3 URI (e.g., `s3://vams-assets-bucket/asset-123/`)

---

## Training with Custom Environments

### Execute Workflow with Custom Environment

```json
{
    "trainingConfig": {
        "task": "MyCustom-Robot-v0",
        "numEnvs": 4096,
        "maxIterations": 5000,
        "rlLibrary": "rsl_rl",
        "seed": 42
    },
    "computeConfig": {
        "numNodes": 1
    },
    "inputS3AssetFilePath": "s3://vams-assets-bucket/asset-123/"
}
```

**What happens:**

1. Pipeline downloads your custom environment package from S3
2. Installs it in the container: `pip install -e my_custom_env.tar.gz`
3. Your environment registers with Gymnasium
4. Training executes with your custom task
5. Trained policy uploads to S3 output path

### Multi-GPU Training

For larger environments, use multiple GPUs:

```json
{
    "trainingConfig": {
        "task": "MyCustom-Robot-v0",
        "numEnvs": 16384
    },
    "computeConfig": {
        "numNodes": 2
    }
}
```

---

## Using Pre-trained Policies

### Upload Pre-trained Policy to VAMS

If you have a pre-trained policy to continue training or evaluate:

**Via VAMS Web UI:**

1. Navigate to your asset in VAMS
2. Click **Upload Files**
3. Select your policy checkpoint file (`my_policy.pt`)
4. Upload to a `checkpoints/` folder for organization

**Via VAMS CLI:**

```bash
# Upload policy checkpoint to an existing asset
vamscli files upload \
    --database-id <database-id> \
    --asset-id <asset-id> \
    --file my_policy.pt \
    --relative-path checkpoints/my_policy.pt
```

### Resume Training from Checkpoint

```json
{
    "trainingConfig": {
        "task": "Isaac-Ant-v0",
        "numEnvs": 4096,
        "maxIterations": 5000,
        "resumeCheckpoint": "s3://vams-assets-bucket/policies/my_policy.pt"
    }
}
```

### Evaluate Policy (Play Mode)

To evaluate a trained policy without training:

```json
{
    "trainingConfig": {
        "task": "Isaac-Ant-v0",
        "mode": "play",
        "policyPath": "s3://vams-assets-bucket/policies/my_policy.pt",
        "numEnvs": 100
    }
}
```

---

## Configuration Reference

### Training Configuration

| Parameter   | Type    | Default             | Description                                |
| ----------- | ------- | ------------------- | ------------------------------------------ |
| `mode`      | string  | `train`             | Execution mode: `train` or `evaluate`      |
| `task`      | string  | `Isaac-Cartpole-v0` | Environment task name                      |
| `rlLibrary` | string  | `rsl_rl`            | RL framework: `rsl_rl`, `skrl`, `rl_games` |
| `seed`      | integer | null                | Random seed for reproducibility            |

**Training Mode Parameters:**

| Parameter       | Type    | Default | Description                     |
| --------------- | ------- | ------- | ------------------------------- |
| `numEnvs`       | integer | 4096    | Number of parallel environments |
| `maxIterations` | integer | 1500    | Training iterations             |

**Evaluation Mode Parameters:**

| Parameter        | Type    | Default | Description                                                                 |
| ---------------- | ------- | ------- | --------------------------------------------------------------------------- |
| `checkpointPath` | string  | -       | Relative path to checkpoint within asset (e.g., `checkpoints/model_300.pt`) |
| `policyS3Uri`    | string  | -       | Full S3 URI to trained policy (`.pt` file)                                  |
| `numEnvs`        | integer | 100     | Number of parallel environments                                             |
| `numEpisodes`    | integer | 50      | Number of episodes to evaluate                                              |
| `recordVideo`    | boolean | false   | Record evaluation videos                                                    |

> **Note:** Specify either `checkpointPath` (recommended) or `policyS3Uri`. If neither is provided, the pipeline auto-discovers `.pt` files in the config directory.

### Compute Configuration

| Parameter  | Type    | Default | Description                                   |
| ---------- | ------- | ------- | --------------------------------------------- |
| `numNodes` | integer | 1       | Number of compute nodes (multi-node training) |

### Input/Output Paths

| Parameter              | Type   | Description                                          |
| ---------------------- | ------ | ---------------------------------------------------- |
| `inputS3AssetFilePath` | string | S3 path to custom environment package                |
| `outputS3Path`         | string | S3 path for trained policy and logs (auto-generated) |

### Output Files

After training completes, the following files are uploaded to the output S3 path, organized under the job UUID for easy identification:

| Path                            | Description                                     |
| ------------------------------- | ----------------------------------------------- |
| `{uuid}/checkpoints/model_*.pt` | Training checkpoints saved at regular intervals |
| `{uuid}/metrics.csv`            | Training metrics exported from TensorBoard      |
| `{uuid}/training-config.json`   | Copy of input configuration                     |
| `{uuid}/*.txt`                  | Converted log files (e.g., git diff files)      |

For evaluation jobs:

| Path                            | Description                                  |
| ------------------------------- | -------------------------------------------- |
| `{uuid}/metrics.csv`            | Evaluation metrics exported from TensorBoard |
| `{uuid}/evaluation-config.json` | Copy of input configuration                  |
| `{uuid}/videos/*.mp4`           | Recorded evaluation videos                   |
| `{uuid}/*.txt`                  | Converted log files                          |

The `{uuid}` prefix matches the job execution UUID, making it easy to identify and organize outputs from multiple jobs within the same asset.

---

## Best Practices

### Environment Development

1. **Test locally first** - Validate your environment works before uploading to VAMS
2. **Use small `numEnvs`** during development (512-1024) for faster iteration
3. **Include dependencies** - Add all required packages to `setup.py`
4. **Version your environments** - Use semantic versioning in package names

### Training Configuration

1. **Start small** - Begin with fewer environments and iterations to validate
2. **Monitor GPU usage** - Check CloudWatch metrics to optimize `numEnvs`
3. **Use seeds** - Set random seeds for reproducible experiments
4. **Save checkpoints** - Enable checkpoint saving for long training runs

### Package Structure

Ensure your package includes:

```
my_custom_env/
├── setup.py              # Package metadata and dependencies
├── README.md             # Documentation
├── my_custom_env/
│   ├── __init__.py       # Environment registration
│   ├── my_env.py         # Environment implementation
│   ├── my_env_cfg.py     # Configuration classes
│   └── assets/           # USD files, meshes, etc.
└── agents/
    └── rsl_rl_ppo_cfg.py # RL algorithm config
```

---

## Troubleshooting

### Environment Not Found

**Error:** `gymnasium.error.UnregisteredEnv: No registered env with id: MyCustom-Robot-v0`

**Solution:**

-   Verify `gym.register()` is called in `__init__.py`
-   Check task name matches exactly (case-sensitive)
-   Ensure package installed successfully (check container logs)

### Import Errors

**Error:** `ModuleNotFoundError: No module named 'my_dependency'`

**Solution:**

-   Add missing dependencies to `setup.py`:

```python
setup(
    name="my_custom_env",
    install_requires=[
        "numpy>=1.20.0",
        "my_dependency>=1.0.0",
    ],
)
```

### GPU Out of Memory

**Error:** `CUDA out of memory`

**Solution:**

-   Reduce `numEnvs` (try 2048 or 1024)
-   Simplify environment (fewer objects, lower resolution)
-   Use multi-node training to distribute load

### Package Installation Failed

**Error:** `Failed to install custom environment package`

**Solution:**

-   Verify package structure is correct
-   Test installation locally: `pip install -e my_custom_env.tar.gz`
-   Check container logs for detailed error messages

---

## Examples

### Example 1: Simple Custom Cartpole Variant

```python
# my_cartpole/my_cartpole/my_env.py
from isaaclab_tasks.direct.cartpole import CartpoleEnv, CartpoleEnvCfg

class MyCartpoleEnvCfg(CartpoleEnvCfg):
    # Modify default configuration
    episode_length_s = 10.0  # Longer episodes
    max_cart_pos = 5.0       # Wider track

class MyCartpoleEnv(CartpoleEnv):
    cfg: MyCartpoleEnvCfg

    def __init__(self, cfg: MyCartpoleEnvCfg, **kwargs):
        super().__init__(cfg, **kwargs)
```

### Example 2: Custom Reward Function

```python
def _get_rewards(self):
    # Custom reward: penalize cart velocity more heavily
    pole_angle = self.joint_pos[:, self.pole_dof_idx]
    cart_vel = self.joint_vel[:, self.cart_dof_idx]

    rewards = {
        "pole_upright": -torch.abs(pole_angle),
        "cart_stationary": -torch.abs(cart_vel) * 2.0,  # Increased penalty
        "alive": 1.0,
    }

    return sum(rewards.values())
```

### Example 3: Workflow Execution

```json
{
    "workflowName": "isaac-lab-training",
    "parameters": {
        "trainingConfig": {
            "task": "MyCartpole-v0",
            "numEnvs": 8192,
            "maxIterations": 3000,
            "rlLibrary": "rsl_rl",
            "seed": 123
        },
        "computeConfig": {
            "numNodes": 1
        },
        "inputS3AssetFilePath": "s3://vams-assets-bucket/my-cartpole-env/"
    }
}
```

---

## Additional Resources

-   [Isaac Lab Documentation](https://isaac-sim.github.io/IsaacLab/main/)
-   [Isaac Lab Quickstart](https://isaac-sim.github.io/IsaacLab/main/source/setup/quickstart.html)
-   [Creating Custom Environments Tutorial](https://isaac-sim.github.io/IsaacLab/main/source/tutorials/03_envs/create_direct_rl_env.html)
-   [VAMS Documentation](../../../README.md)

---

## Support

For issues or questions:

1. Check CloudWatch logs for detailed error messages
2. Review Isaac Lab documentation for environment development
3. Verify package structure matches template generator output
4. Test custom environments locally before uploading to VAMS
