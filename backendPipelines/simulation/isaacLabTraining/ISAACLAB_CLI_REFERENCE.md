# Isaac Lab CLI Reference Guide

This guide describes the Isaac Lab 3.0 command line the VAMS pipeline runs for training and evaluation, the physics engine setting, and the task ids. The pipeline builds every command itself from the run configuration. The commands below are for reproducing a run by hand in the Isaac Lab container.

## Table of Contents

-   [Quick Start](#quick-start)
-   [Physics Engine](#physics-engine)
-   [Task Ids](#task-ids)
-   [Task Support by Engine](#task-support-by-engine)
-   [Important: Evaluation Termination](#important-evaluation-termination)
-   [Visualizers](#visualizers)
-   [Logs, Checkpoints and Video](#logs-checkpoints-and-video)
-   [VAMS Pipeline Integration](#vams-pipeline-integration)
-   [Troubleshooting](#troubleshooting)

---

## Quick Start

The image runs Isaac Lab from `/workspace/isaaclab` with Python 3.12 in `/opt/isaaclab-venv`. The `isaaclab.sh` launcher is deprecated upstream, and the pipeline still uses it because the pinned image ships it.

### Training

```bash
./isaaclab.sh train --rl_library rsl_rl \
    --task Isaac-Cartpole \
    --num_envs 4096 \
    --max_iterations 1500 \
    --viz none \
    physics=newton_mjwarp
```

### Evaluation

```bash
./isaaclab.sh play --rl_library rsl_rl \
    --task Isaac-Cartpole \
    --num_envs 100 \
    --checkpoint /path/to/model_1499.pt \
    --video --video_length 50000 \
    --viz newton_gl \
    physics=newton_mjwarp
```

`--rl_library` takes `rsl_rl`, `rl_games` or `skrl`. `--headless` no longer exists: Isaac Lab rejects it as an unrecognized argument. `--viz none` keeps visualizers off for training. The `physics=<engine>` override goes last.

---

## Physics Engine

Isaac Lab 3.0 selects the physics engine with a Hydra override, `physics=<preset>`, after the options and without leading dashes. The pipeline exposes one setting for both training and evaluation, `trainingConfig.physics`:

| Value            | Engine                                                     | Notes                                                |
| ---------------- | ---------------------------------------------------------- | ---------------------------------------------------- |
| `newton_mjwarp`  | Newton with the MuJoCo-Warp solver. **Default.**           | Newton and its packages ship in the image.           |
| `isaacsim_physx` | PhysX on Isaac Sim. The engine earlier VAMS releases used. | Set this to reproduce a run made before the upgrade. |

Other presets exist (`newton_kamino`, `ovphysx`, `physx`) and depend on the task or cannot share a process with Isaac Sim, so the pipeline does not offer them.

Things to know:

-   **Results differ between engines.** A saved configuration, a built-in template or an Isaac Sim connector launch that does not name an engine now runs on Newton. A checkpoint trained before the upgrade was trained on PhysX.
-   **Evaluate on the engine that trained the policy.** Evaluating on the other engine is a transfer test. A checkpoint carries observations and actions and not the engine, so it loads on either, and the behaviour is similar and not identical. When the evaluation can read the training run's `train-config.json` and the engines differ, the run prints a note and records it as `physicsNote` in `evaluate-config.json`. The note never fails the run. Robots with branched kinematics may also need joint and body ordering overrides that the pipeline does not expose.
-   **A task offers the engines it lists.** `./isaaclab.sh list_envs --keyword <task> --show_presets` prints them. A task that does not list the chosen engine fails 11 to 17 seconds after the command starts, before Isaac Sim starts, and the pipeline reports `Task <id> does not offer physics=<value> ... Options for this task: ...`.
-   **Tasks that list no physics presets** (the AutoMate tasks, `IsaacContrib-Velocity-*-AnymalC-Direct` and most custom or ported environments) reject both engines, so the pipeline leaves the `physics=` selector out and the task runs on the physics it registers. The run log says `Task <id> declares no physics presets; it runs on its registered physics. The physics setting is ignored.` and `train-config.json` records `physics` as `task-default`. For a task the pipeline does not know, the container reads Isaac Lab's rejection and runs the task once more without the selector.
-   **`--num_envs` is always passed.** The pipeline sets it for every run (4096 for training, 100 for evaluation unless `numEnvs` is set) so runs are comparable and bounded, instead of using each task's own default.
-   **Checkpoints from Isaac Lab 2.x.** `rl_games` and `skrl` checkpoints load. `rsl_rl` checkpoints do not: rsl_rl 5.x stores the actor and critic in a new format, so the run stops before Isaac Sim starts with a message naming the cause. Retrain on 3.0.
-   **The first run on a new instance compiles kernels.** Warp and Newton compile on first use. A cold Newton training start measured 88 to 112 seconds against 25 seconds warm on PhysX. The cache lives in the container, so every job starts cold.

---

## Task Ids

Isaac Lab 3.0 does not alias the 2.x ids ending in `-v0` (`Isaac-Cartpole-v0` raises a deprecation error), and some ids were renamed rather than only shortened. The pipeline maps these saved ids to their 3.0 ids, logs the mapping, and records the requested id as `taskIdRequested`:

| Isaac Lab 2.x id                   | Isaac Lab 3.0 id               |
| ---------------------------------- | ------------------------------ |
| `Isaac-Cartpole-v0`                | `Isaac-Cartpole`               |
| `Isaac-Cartpole-Direct-v0`         | `Isaac-Cartpole-Direct`        |
| `Isaac-Lift-Cube-Franka-v0`        | `Isaac-Lift-Franka`            |
| `Isaac-Velocity-Rough-Anymal-D-v0` | `Isaac-Velocity-Rough-AnymalD` |

Any other id is passed to Isaac Lab as written. For another saved 2.x id, look the 3.0 id up with `./isaaclab.sh list_envs` and update the configuration. Tasks that moved to the contributed set carry an `IsaacContrib-` prefix.

---

## Task Support by Engine

Three levels of evidence are used. **ran**: a training run finished. **config**: Isaac Lab accepted the engine for the task and Isaac Sim started, and no training ran. **docs**: Isaac Lab's documentation names the pair. A listed preset is not proof that training works: only Cartpole was run on both engines.

| Task                                                                                     | Newton (`newton_mjwarp`)           | PhysX (`isaacsim_physx`)     |
| ---------------------------------------------------------------------------------------- | ---------------------------------- | ---------------------------- |
| `Isaac-Cartpole`, `Isaac-Cartpole-Direct`                                                | ran (rsl_rl, rl_games, skrl)       | ran (rsl_rl, rl_games, skrl) |
| `Isaac-Ant`, `Isaac-Ant-Direct`, `Isaac-Humanoid`, `Isaac-Humanoid-Direct`               | config                             | config                       |
| `Isaac-Velocity-Rough-AnymalD`, `Isaac-Velocity-Rough-G1`, `Isaac-Reorient-Cube-Allegro` | config                             | config                       |
| `Isaac-Lift-Franka`                                                                      | docs                               | docs                         |
| `IsaacContrib-AutoMate-*`                                                                | not offered (no physics preset)    | not measured                 |
| Tasks that list only `isaacsim_physx`                                                    | not offered                        | listed                       |
| Tasks that list no physics preset                                                        | not offered                        | not measured                 |
| A custom environment                                                                     | only with a `newton_mjwarp` preset | as the port declares         |

The AutoMate tasks offer `rl_games` only. The pipeline refuses them on Newton and with any other library before a GPU instance starts.

---

## Important: Evaluation Termination

> **Critical**: `play` runs until it is stopped unless a video length bounds it. Without `--video`, evaluation does not terminate on its own.

Always pass `--video` with `--video_length`, and compute it from the run:

```
video_length = num_episodes × steps_per_episode
```

`video_length` counts simulation steps, not episodes. The pipeline sets it from `numEpisodes` and `stepsPerEpisode` (defaults 50 and 1000). Evaluation records a video on every run, and `recordVideo` decides only whether it is uploaded.

As a second bound, the container stops a `play` process that has not finished within a wall-clock limit (1,800 seconds plus 0.1 seconds per step, never more than 6,600) and fails the run naming the engine and the limit. Some play runs did not terminate during testing: the default Kit visualizer under Newton, and a Newton checkpoint played on PhysX.

---

## Visualizers

`--viz` selects the visualizer. The container has no display.

| Run                     | Option                 | Why                                                                                             |
| ----------------------- | ---------------------- | ----------------------------------------------------------------------------------------------- |
| Training, either engine | `--viz none`           | No window or viewer code runs.                                                                  |
| Evaluation on Newton    | `--viz newton_gl`      | Records through Newton's own viewer. Isaac Lab warns that Kit frames can be black under Newton. |
| Evaluation on PhysX     | the default Kit viewer | Recording through Kit RTX needs a GPU with RT cores on both engines.                            |

Video recording needs MoviePy 1.x and `imageio-ffmpeg`, which the image installs. MoviePy 2.x fails to import in Isaac Lab.

---

## Logs, Checkpoints and Video

Paths are relative to `/workspace/isaaclab`.

| Library    | Run folder                                      | Checkpoint                                  | TensorBoard events                        |
| ---------- | ----------------------------------------------- | ------------------------------------------- | ----------------------------------------- |
| `rsl_rl`   | `logs/rsl_rl/<experiment>/<timestamp>/`         | `model_<n>.pt` in the run folder            | `events.out.tfevents.*` in the run folder |
| `rl_games` | `logs/rl_games/<experiment>/<timestamp>/`       | `nn/last_<experiment>_ep_<n>_rew__<r>_.pth` | `summaries/`                              |
| `skrl`     | `logs/skrl/<experiment>/<timestamp>_ppo_torch/` | `checkpoints/agent_<step>.pt`               | not recorded; read in the live smoke      |

-   `play` writes no TensorBoard events, so an evaluation run has no training-style metrics file of its own.
-   Video goes to `<folder of --checkpoint>/videos/play/` for `rsl_rl`, and one folder higher for `rl_games` and `skrl`. A checkpoint at a flat path such as `/tmp/model.pt` makes those two try `/videos` and fail with a permission error, so the container downloads the policy into a nested folder under its writable log root.
-   `rl_games` and `skrl` break with a very small `numEnvs` (64 failed the `rl_games` minibatch check). Leave the parallel environments at the default unless you know the library's minibatch size.

---

## VAMS Pipeline Integration

### Training Mode Configuration

```json
{
    "trainingConfig": {
        "mode": "train",
        "task": "Isaac-Cartpole-Direct",
        "numEnvs": 4096,
        "maxIterations": 1500,
        "rlLibrary": "rsl_rl",
        "physics": "newton_mjwarp"
    }
}
```

### Evaluation Mode Configuration

```json
{
    "trainingConfig": {
        "mode": "evaluate",
        "task": "Isaac-Cartpole-Direct",
        "numEnvs": 100,
        "numEpisodes": 50,
        "stepsPerEpisode": 1000,
        "rlLibrary": "rsl_rl",
        "physics": "newton_mjwarp"
    }
}
```

| Parameter         | Type | Default         | Description                                                             |
| ----------------- | ---- | --------------- | ----------------------------------------------------------------------- |
| `physics`         | enum | `newton_mjwarp` | `newton_mjwarp` or `isaacsim_physx`. Blank or absent means the default. |
| `numEpisodes`     | int  | 50              | Number of episodes to evaluate                                          |
| `stepsPerEpisode` | int  | 1000            | Simulation steps per episode (task-dependent)                           |

The uploaded `train-config.json` and `evaluate-config.json` carry the resolved `physics`, the `isaacLabVersion` of the image and the `gpuDriverVersion` the run saw.

### GPU driver

NVIDIA tests Isaac Sim 6.2 on driver 595.58.03 or newer on Linux. The pipeline runs on the driver of the ECS GPU AMI, which was 580.178.04 when it was validated, and prints the driver version at the start of every run. The image itself declares 570.169 as its minimum. A different AMI driver is the operator's risk, and no run fails on the driver version.

---

## Troubleshooting

### Evaluation Job Runs Forever or Is Stopped at the Time Limit

**Cause**: `play` did not end after `--video_length` steps, for example on the default Kit visualizer under Newton or with a checkpoint from the other engine.

**Solution**: Check the run log for where it stopped. Evaluate on the engine that trained the policy, and lower `numEpisodes` or `stepsPerEpisode` to shorten the playback.

### `Task <id> does not offer physics=<value>`

**Cause**: The task does not list that engine.

**Solution**: Set "Physics engine" to one of the options in the message. A task that lists no presets does not raise this error: it runs on its registered physics.

### Out of GPU Memory

**Cause**: Too many environments for available VRAM.

**Solution**: Reduce `numEnvs`. Newton used less memory than PhysX for Ant at 4096 environments in testing (about 1.5 GB against 4 GB of GPU memory).

### Video Is Black

**Cause**: Kit frames can be black under Newton.

**Solution**: Evaluation on Newton records with `--viz newton_gl`. If frames are still empty, evaluate with `isaacsim_physx`.

A PhysX clip starts with a few dark frames while the renderer warms up; the rest of the clip shows the scene. The first PhysX evaluation on a new instance also takes several minutes longer while Kit builds its shader cache.

### Checkpoint Does Not Load

**Cause**: The checkpoint was written by another RL library, or by a much older Isaac Lab release.

**Solution**: Select the library that trained it. A checkpoint from before the upgrade was trained on PhysX with Isaac Lab 2.3.0: evaluate it with `isaacsim_physx` and, if it does not load, retrain.

---

## References

-   [Isaac Lab Documentation](https://isaac-sim.github.io/IsaacLab/)
-   [RSL-RL Library](https://github.com/leggedrobotics/rsl_rl)
-   [Isaac Lab GitHub Repository](https://github.com/isaac-sim/IsaacLab)
