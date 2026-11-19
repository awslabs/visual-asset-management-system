#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Where each RL library writes its training checkpoints, in one table (issue #1315).

Isaac Lab's ``scripts/reinforcement_learning/<library>/train.py`` writes each run under
``<cwd>/logs/<library>/...`` and the three libraries lay a run out differently. The layouts below were
read from the Isaac Lab ``v2.3.0`` scripts, the release the image is pinned to
(``nvcr.io/nvidia/isaac-lab:2.3.0``), and from the libraries' own save code:

* ``rsl_rl``:   ``logs/rsl_rl/<experiment>/<timestamp>[_<run_name>]/model_<iteration>.pt``
* ``rl_games``: ``logs/rl_games/<config name>/<full_experiment_name>/nn/<name>.pth`` (best reward) and
  ``.../nn/last_<name>_ep_<epoch>_rew_<reward>.pth`` (periodic and final)
* ``skrl``:     ``logs/skrl/<experiment directory>/<timestamp>_<algorithm>_<framework>[_<name>]/checkpoints/``
  ``agent_<timestep>.pt`` and ``best_agent.pt``

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
