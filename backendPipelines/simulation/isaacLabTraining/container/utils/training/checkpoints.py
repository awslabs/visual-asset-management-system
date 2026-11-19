#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Where each RL library writes its training checkpoints, in one table (issues #1315, #1669).

Isaac Lab's ``isaaclab train --rl_library <library>`` writes each run under ``<cwd>/logs/<library>/...``
and the three libraries lay a run out differently. The layouts below were read from the Isaac Lab
``3.0.0`` image the pipeline is pinned to (``nvcr.io/nvidia/isaac-lab:3.0.0``) by training Cartpole
for three iterations with each library on both physics engines:

* ``rsl_rl``:   ``logs/rsl_rl/<experiment>/<timestamp>[_<run_name>]/model_<iteration>.pt``, with the
  TensorBoard events in the same folder
* ``rl_games``: ``logs/rl_games/<experiment>/<timestamp>/nn/last_<experiment>_ep_<epoch>_rew__<reward>_.pth``
  (periodic and final; the TensorBoard events are in ``.../summaries/``)
* ``skrl``:     ``logs/skrl/<experiment>/<timestamp>_<algorithm>_<framework>[_<name>]/checkpoints/``
  ``agent_<timestep>.pt`` (and ``best_agent.pt`` when the run writes one)

Playback (``isaaclab play``) writes no TensorBoard events, so an evaluation run has no
``metrics.csv`` of its own to export (issue #1316).

A new Isaac Lab release changes this table and nothing else: edit ``CHECKPOINT_PATTERNS`` and the
matching table in ``lambda/openPipeline.py`` (``CHECKPOINT_RANKING``; a lambda test asserts that the two
agree), then re-read the layouts from that release. ``tests/test_checkpoint_layouts.py`` builds a log
tree per library from the same layouts.
"""

import glob
import os

# Glob patterns, relative to the Isaac Lab log root (``/workspace/isaaclab/logs``) and to the EFS
# checkpoint folder, that match the checkpoint files each library writes. ``**`` spans the
# library's experiment and run folders so a task that nests them deeper still matches.
CHECKPOINT_PATTERNS = {
    "rsl_rl": ("**/model_*.pt",),
    "rl_games": ("rl_games/**/nn/*.pth",),
    "skrl": ("skrl/**/checkpoints/*.pt",),
}


def find_checkpoints(roots, rl_library: str) -> list:
    """The checkpoint files ``rl_library`` wrote under any of ``roots``, oldest first.

    A file reached through more than one root or pattern is returned once. The order is the
    modification time, then the path, so a caller that uploads by file name leaves the newest file
    in place when two runs wrote the same name.
    """
    if rl_library not in CHECKPOINT_PATTERNS:
        raise ValueError(
            f"Unsupported rlLibrary '{rl_library}'. Supported libraries: "
            f"{', '.join(sorted(CHECKPOINT_PATTERNS))}"
        )

    found = {}
    for root in roots:
        for pattern in CHECKPOINT_PATTERNS[rl_library]:
            for path in glob.glob(os.path.join(root, pattern), recursive=True):
                if os.path.isfile(path):
                    found[os.path.realpath(path)] = path

    return sorted(found.values(), key=lambda p: (os.path.getmtime(p), p))
