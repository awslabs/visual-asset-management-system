# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The upstream bake patch has to be wired into the build and has to fail loudly.

The patch rewrites source in a repo cloned at build time from a revision this tree does not own. Its
value depends entirely on failing the build when the text it targets is gone: a patch that quietly
does nothing produces an image whose texture bake cannot work, and the run still succeeds with an
untextured mesh, so nothing downstream reports the loss.
"""

import os
import subprocess
import sys

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(CONTAINER_DIR, "patches", "triposr_bake_texture_device.py")

# The upstream text the patch rewrites, as it stands in the revision it was derived against.
UPSTREAM = '''import numpy as np
import torch


def positions_to_colors(model, scene_code, positions_texture, texture_resolution):
    positions = torch.tensor(positions_texture.reshape(-1, 4)[:, :-1])
    with torch.no_grad():
        queried_grid = model.renderer.query_triplane(
            model.decoder,
            positions,
            scene_code,
        )
    rgb_f = queried_grid["color"].numpy().reshape(-1, 3)
    return rgb_f
'''


def _run(path):
    return subprocess.run([sys.executable, PATCH, path], capture_output=True, text=True)


def _write(tmp_path, body):
    target = tmp_path / "bake_texture.py"
    target.write_text(body, encoding="utf-8", newline="")
    return str(target)


def test_the_patch_script_exists_and_is_referenced_by_the_build():
    assert os.path.isfile(PATCH), "the patch script is missing"
    with open(os.path.join(CONTAINER_DIR, "Dockerfile.triposr"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    assert "patches/triposr_bake_texture_device.py" in dockerfile, (
        "Dockerfile.triposr does not copy the patch script")
    assert "bake_texture.py" in dockerfile, (
        "Dockerfile.triposr copies the patch but never runs it against the upstream file")


def test_it_moves_both_tensors_off_their_wrong_device(tmp_path):
    target = _write(tmp_path, UPSTREAM)
    result = _run(target)
    assert result.returncode == 0, result.stderr
    patched = open(target, encoding="utf-8").read()
    assert "next(model.parameters()).device" in patched, "positions never reach the model's device"
    assert '.detach().cpu().numpy()' in patched, "colours are still read straight off the device"
    assert '"color"].numpy()' not in patched


def test_it_fails_when_the_pinned_revision_has_moved_on(tmp_path):
    """The whole safety property: no silent no-op may reach an image build."""
    target = _write(tmp_path, "def positions_to_colors(model):\n    pass\n")
    result = _run(target)
    assert result.returncode != 0, "an unpatchable source must fail the build, not pass silently"
    assert "changed shape" in result.stderr


def test_it_fails_when_a_target_appears_more_than_once(tmp_path):
    """Two occurrences mean the patch cannot know which one the bake actually uses."""
    target = _write(tmp_path, UPSTREAM + UPSTREAM)
    assert _run(target).returncode != 0


def test_it_is_idempotent(tmp_path):
    """A rebuilt layer, or a revision that has already fixed this, must not fail the build."""
    target = _write(tmp_path, UPSTREAM)
    assert _run(target).returncode == 0
    first = open(target, encoding="utf-8").read()
    assert _run(target).returncode == 0
    assert open(target, encoding="utf-8").read() == first, "a second run changed the file again"
