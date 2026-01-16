# Isaac Lab CLI Reference Guide

This guide provides a complete reference for Isaac Lab command-line arguments used in training and evaluation pipelines.

## Table of Contents

-   [Quick Start](#quick-start)
-   [Important: Evaluation Termination](#important-evaluation-termination)
-   [Play Script Arguments](#play-script-arguments-playpy)
-   [Train Script Arguments](#train-script-arguments-trainpy)
-   [RSL-RL Specific Arguments](#rsl-rl-specific-arguments)
-   [AppLauncher Arguments](#applauncher-arguments)
-   [Environment Configuration](#environment-configuration)
-   [Examples](#examples)

---

## Quick Start

### Training

```bash
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/train.py \
    --task Isaac-Ant-Direct-v0 \
    --num_envs 4096 \
    --max_iterations 1000 \
    --headless
```

### Evaluation

```bash
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/play.py \
    --task Isaac-Ant-Direct-v0 \
    --num_envs 100 \
    --checkpoint /path/to/model.pt \
    --headless \
    --video \
    --video_length 5000
```

---

## Important: Evaluation Termination

> **Critical**: The `play.py` script runs in an **infinite loop** by default. Without the `--video` flag, evaluation will never terminate on its own.

### How Termination Works

From the Isaac Lab source code (`play.py` lines 127-140):

```python
while simulation_app.is_running():
    # ... inference loop ...

    if args_cli.video:
        timestep += 1
        if timestep == args_cli.video_length:
            break  # Only exits when --video is enabled
```

### Solution for Batch Jobs

Always use `--video` with `--video_length` to ensure evaluation terminates:

```bash
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/play.py \
    --task Isaac-Ant-Direct-v0 \
    --checkpoint /path/to/model.pt \
    --headless \
    --video \
    --video_length 45000  # Controls when evaluation stops
```

### Calculating video_length

The `video_length` parameter is in **simulation steps**, not episodes.

```
video_length = num_episodes × steps_per_episode
steps_per_episode = episode_length_s / (decimation × physics_dt)
```

| Task                     | Typical Steps/Episode | 50 Episodes |
| ------------------------ | --------------------- | ----------- |
| Isaac-Ant-Direct-v0      | ~900                  | 45,000      |
| Isaac-Cartpole-Direct-v0 | ~500                  | 25,000      |
| Isaac-Humanoid-Direct-v0 | ~1000                 | 50,000      |

**Default**: 1000 steps/episode if not specified.

---

## Play Script Arguments (`play.py`)

Arguments specific to policy evaluation/inference.

| Argument                      | Type | Default                  | Description                                                                                |
| ----------------------------- | ---- | ------------------------ | ------------------------------------------------------------------------------------------ |
| `--video`                     | flag | `False`                  | Record videos during evaluation. **Required for auto-termination in batch jobs.**          |
| `--video_length`              | int  | `200`                    | Length of recorded video in simulation steps. Also controls when evaluation terminates.    |
| `--disable_fabric`            | flag | `False`                  | Disable Fabric and use USD I/O operations. Slower but more compatible with some workflows. |
| `--num_envs`                  | int  | `None`                   | Number of parallel environments to simulate. Overrides task default.                       |
| `--task`                      | str  | `None`                   | Name of the task/environment (e.g., `Isaac-Ant-Direct-v0`). **Required.**                  |
| `--agent`                     | str  | `rsl_rl_cfg_entry_point` | Name of the RL agent configuration entry point.                                            |
| `--seed`                      | int  | `None`                   | Random seed for reproducibility.                                                           |
| `--use_pretrained_checkpoint` | flag | `False`                  | Use pre-trained checkpoint from NVIDIA Nucleus instead of local file.                      |
| `--real-time`                 | flag | `False`                  | Run simulation in real-time by adding sleep delays between steps.                          |

---

## Train Script Arguments (`train.py`)

Arguments specific to policy training.

| Argument                  | Type | Default                  | Description                                                                               |
| ------------------------- | ---- | ------------------------ | ----------------------------------------------------------------------------------------- |
| `--video`                 | flag | `False`                  | Record videos during training at specified intervals.                                     |
| `--video_length`          | int  | `200`                    | Length of each recorded video in simulation steps.                                        |
| `--video_interval`        | int  | `2000`                   | Number of steps between video recordings.                                                 |
| `--num_envs`              | int  | `None`                   | Number of parallel environments. More environments = faster training but more GPU memory. |
| `--task`                  | str  | `None`                   | Task/environment name. **Required.**                                                      |
| `--agent`                 | str  | `rsl_rl_cfg_entry_point` | Agent configuration entry point.                                                          |
| `--seed`                  | int  | `None`                   | Random seed. Use `-1` for random seed selection.                                          |
| `--max_iterations`        | int  | `None`                   | Maximum training iterations. Overrides task default.                                      |
| `--distributed`           | flag | `False`                  | Enable multi-GPU or multi-node distributed training.                                      |
| `--export_io_descriptors` | flag | `False`                  | Export IO descriptors for deployment (manager-based envs only).                           |
| `--ray-proc-id`           | int  | `None`                   | Ray integration process ID. Auto-configured by Ray, do not set manually.                  |

---

## RSL-RL Specific Arguments

Arguments for the RSL-RL library, available in both `train.py` and `play.py`.

| Argument             | Type | Default | Description                                                          |
| -------------------- | ---- | ------- | -------------------------------------------------------------------- |
| `--experiment_name`  | str  | `None`  | Name of the experiment folder where logs are stored.                 |
| `--run_name`         | str  | `None`  | Suffix appended to the log directory name.                           |
| `--resume`           | flag | `False` | Resume training from a previous checkpoint.                          |
| `--load_run`         | str  | `None`  | Name of the run folder to resume from (e.g., `2024-01-15_10-30-00`). |
| `--checkpoint`       | str  | `None`  | Path to specific checkpoint file (e.g., `/path/to/model_500.pt`).    |
| `--logger`           | str  | `None`  | Logger backend: `wandb`, `tensorboard`, or `neptune`.                |
| `--log_project_name` | str  | `None`  | Project name for Weights & Biases or Neptune logging.                |

### Checkpoint Loading Priority

1. `--use_pretrained_checkpoint` - Downloads from NVIDIA Nucleus
2. `--checkpoint` - Uses specified file path
3. `--load_run` - Finds latest checkpoint in specified run folder
4. Default - Finds latest checkpoint in experiment directory

---

## AppLauncher Arguments

Common arguments available to all Isaac Lab scripts. These control the simulation application.

### Display & Rendering

| Argument           | Type | Default    | Description                                                                        |
| ------------------ | ---- | ---------- | ---------------------------------------------------------------------------------- |
| `--headless`       | flag | `False`    | Run without GUI. **Required for batch/cloud jobs.**                                |
| `--livestream`     | int  | `-1`       | Enable livestreaming: `0`=disabled, `1`=WebRTC public, `2`=WebRTC private.         |
| `--enable_cameras` | flag | `False`    | Enable camera sensors and rendering extensions. **Required when using `--video`.** |
| `--rendering_mode` | str  | `balanced` | Rendering quality preset: `performance`, `balanced`, or `quality`.                 |

### Device & Compute

| Argument   | Type | Default  | Description                                                      |
| ---------- | ---- | -------- | ---------------------------------------------------------------- |
| `--device` | str  | `cuda:0` | Compute device: `cpu`, `cuda`, or `cuda:N` where N is GPU index. |

### Logging & Debug

| Argument    | Type | Default | Description                                         |
| ----------- | ---- | ------- | --------------------------------------------------- |
| `--verbose` | flag | `False` | Enable verbose-level log output from SimulationApp. |
| `--info`    | flag | `False` | Enable info-level log output from SimulationApp.    |

### Advanced

| Argument       | Type | Default | Description                                                               |
| -------------- | ---- | ------- | ------------------------------------------------------------------------- |
| `--experience` | str  | `""`    | Custom experience file path. Auto-selected based on other flags if empty. |
| `--xr`         | flag | `False` | Enable XR mode for VR/AR applications.                                    |
| `--kit_args`   | str  | `""`    | Additional Omniverse Kit arguments as space-separated string.             |

### Experience File Auto-Selection

When `--experience` is empty, Isaac Lab selects the appropriate experience file:

| Conditions                        | Experience File                          |
| --------------------------------- | ---------------------------------------- |
| `--headless` + `--enable_cameras` | `isaaclab.python.headless.rendering.kit` |
| `--headless` only                 | `isaaclab.python.headless.kit`           |
| `--enable_cameras` only           | `isaaclab.python.rendering.kit`          |
| Neither                           | `isaaclab.python.kit`                    |

---

## Environment Configuration

Key environment configuration parameters that affect episode behavior.

| Parameter           | Type  | Description                                                   |
| ------------------- | ----- | ------------------------------------------------------------- |
| `episode_length_s`  | float | Duration of an episode in seconds.                            |
| `decimation`        | int   | Number of simulation steps per policy step.                   |
| `seed`              | int   | Random seed for environment initialization.                   |
| `is_finite_horizon` | bool  | Whether task is finite horizon (no bootstrapping at timeout). |

### Episode Length Calculation

```python
episode_length_steps = ceil(episode_length_s / (decimation * physics_dt))
```

Example for `Isaac-Ant-Direct-v0`:

-   `episode_length_s` = 15.0
-   `decimation` = 2
-   `physics_dt` = 0.00833 (120 Hz)
-   `episode_length_steps` = ceil(15.0 / (2 × 0.00833)) ≈ 900 steps

---

## Examples

### Basic Training

```bash
# Train Ant locomotion for 500 iterations
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/train.py \
    --task Isaac-Ant-Direct-v0 \
    --num_envs 4096 \
    --max_iterations 500 \
    --headless
```

### Training with Video Recording

```bash
# Train with periodic video recording
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/train.py \
    --task Isaac-Cartpole-Direct-v0 \
    --num_envs 1024 \
    --max_iterations 300 \
    --headless \
    --video \
    --video_length 200 \
    --video_interval 1000 \
    --enable_cameras
```

### Training with Weights & Biases Logging

```bash
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/train.py \
    --task Isaac-Ant-Direct-v0 \
    --num_envs 4096 \
    --max_iterations 1000 \
    --headless \
    --logger wandb \
    --log_project_name my-isaaclab-project \
    --experiment_name ant-training
```

### Resume Training

```bash
# Resume from specific checkpoint
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/train.py \
    --task Isaac-Ant-Direct-v0 \
    --num_envs 4096 \
    --headless \
    --resume \
    --checkpoint /path/to/logs/rsl_rl/ant_direct/2024-01-15_10-30-00/model_500.pt
```

### Evaluation with Termination (Batch Jobs)

```bash
# Evaluate for approximately 50 episodes
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/play.py \
    --task Isaac-Ant-Direct-v0 \
    --num_envs 100 \
    --checkpoint /path/to/model.pt \
    --headless \
    --video \
    --video_length 45000 \
    --enable_cameras
```

### Evaluation with Pre-trained Checkpoint

```bash
# Use NVIDIA's pre-trained model
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/play.py \
    --task Isaac-Ant-Direct-v0 \
    --num_envs 32 \
    --use_pretrained_checkpoint \
    --headless \
    --video \
    --video_length 5000
```

### Multi-GPU Distributed Training

```bash
# Launch with torchrun for multi-GPU
torchrun --nnodes=1 --nproc_per_node=4 \
    scripts/reinforcement_learning/rsl_rl/train.py \
    --task Isaac-Ant-Direct-v0 \
    --num_envs 4096 \
    --max_iterations 1000 \
    --headless \
    --distributed
```

### Real-Time Visualization (Local Development)

```bash
# Run with GUI for debugging
./isaaclab.sh -p scripts/reinforcement_learning/rsl_rl/play.py \
    --task Isaac-Ant-Direct-v0 \
    --num_envs 16 \
    --checkpoint /path/to/model.pt \
    --real-time
```

---

## VAMS Pipeline Integration

When using Isaac Lab through the VAMS pipeline, the container automatically constructs commands based on your configuration.

### Training Mode Configuration

```json
{
    "trainingConfig": {
        "mode": "train",
        "task": "Isaac-Ant-Direct-v0",
        "numEnvs": 4096,
        "maxIterations": 1000,
        "rlLibrary": "rsl_rl"
    }
}
```

### Evaluation Mode Configuration

```json
{
    "trainingConfig": {
        "mode": "evaluate",
        "task": "Isaac-Ant-Direct-v0",
        "numEnvs": 100,
        "numEpisodes": 50,
        "stepsPerEpisode": 900,
        "rlLibrary": "rsl_rl"
    }
}
```

| Parameter         | Type | Default | Description                                   |
| ----------------- | ---- | ------- | --------------------------------------------- |
| `numEpisodes`     | int  | 50      | Number of episodes to evaluate                |
| `stepsPerEpisode` | int  | 1000    | Simulation steps per episode (task-dependent) |

The pipeline converts these to `video_length = numEpisodes × stepsPerEpisode`.

---

## Troubleshooting

### Evaluation Job Runs Forever

**Cause**: Missing `--video` flag.

**Solution**: Always include `--video` and `--video_length` for batch evaluation jobs.

### Out of GPU Memory

**Cause**: Too many environments for available VRAM.

**Solution**: Reduce `--num_envs`. Start with 1024 and increase gradually.

### Video Not Recording

**Cause**: Missing `--enable_cameras` flag.

**Solution**: Add `--enable_cameras` when using `--video`.

### Checkpoint Not Found

**Cause**: Incorrect path or missing `--load_run` specification.

**Solution**: Use absolute paths with `--checkpoint` or specify `--load_run` with the run folder name.

---

## References

-   [Isaac Lab Documentation](https://isaac-sim.github.io/IsaacLab/)
-   [RSL-RL Library](https://github.com/leggedrobotics/rsl_rl)
-   [Isaac Lab GitHub Repository](https://github.com/isaac-sim/IsaacLab)
