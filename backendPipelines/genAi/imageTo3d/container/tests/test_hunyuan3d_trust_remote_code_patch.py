# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The trust_remote_code patch has to be wired into the build and has to fail loudly.

Hunyuan3D-2's paint stage is a custom diffusers pipeline that ships inside the repository at
``hy3dgen/texgen/hunyuanpaint``, and current diffusers refuses to execute custom code without
``trust_remote_code=True``. Upstream's call does not pass it and offers no way to pass it from outside, so
the patch sets it at the call site.

Unlike the TripoSR bake patch, a no-op here does NOT ship a quietly degraded result -- it ships a texture
stage that raises, which the live run proved: diffusers' ValueError naming the directory, wrapped by
upstream's own ``RuntimeError: Something wrong while loading <path>``. That is a loud failure, but it costs
a GPU instance and the whole shape stage to discover, which is what these tests replace.
"""

import os
import subprocess
import sys

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(CONTAINER_DIR, "patches", "hunyuan3d_trust_remote_code.py")

# The upstream text the patch rewrites, COPIED from the revision pinned by HUNYUAN3D_COMMIT in
# Dockerfile.hunyuan3d rather than reconstructed. WHITESPACE IS PART OF THE TEST. A first version of this
# fixture was written from a summary of the file with the continuation lines indented 8 spaces where
# upstream uses 12, and every test passed: the fixture matched the patch, the patch did not match the real
# file's alignment, and a substring match hid it. Take the bytes from the pinned revision.
UPSTREAM = '''import os
import torch
from diffusers import DiffusionPipeline, EulerAncestralDiscreteScheduler, LCMScheduler


class Multiview_Diffusion_Net():
    def __init__(self, config) -> None:
        self.device = config.device
        self.view_size = 512
        multiview_ckpt_path = config.multiview_ckpt_path

        current_file_path = os.path.abspath(__file__)
        custom_pipeline_path = os.path.join(os.path.dirname(current_file_path), '..', 'hunyuanpaint')

        pipeline = DiffusionPipeline.from_pretrained(
            multiview_ckpt_path,
            custom_pipeline=custom_pipeline_path, torch_dtype=torch.float16)

        if config.pipe_name in ['hunyuanpaint']:
            pipeline.scheduler = EulerAncestralDiscreteScheduler.from_config(pipeline.scheduler.config,
                                                                             timestep_spacing='trailing')
        pipeline.set_progress_bar_config(disable=True)
        self.pipeline = pipeline.to(self.device)
'''


def _run(path):
    return subprocess.run([sys.executable, PATCH, path], capture_output=True, text=True)


def _write(tmp_path, body):
    target = tmp_path / "multiview_utils.py"
    target.write_text(body, encoding="utf-8", newline="")
    return str(target)


def test_the_patch_script_exists_and_is_referenced_by_the_build():
    assert os.path.isfile(PATCH), "the patch script is missing"
    with open(os.path.join(CONTAINER_DIR, "Dockerfile.hunyuan3d"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    assert "patches/hunyuan3d_trust_remote_code.py" in dockerfile, (
        "Dockerfile.hunyuan3d does not copy the patch script")
    assert "multiview_utils.py" in dockerfile, (
        "Dockerfile.hunyuan3d copies the patch but never runs it against the upstream file")


def test_it_trusts_the_bundled_custom_pipeline(tmp_path):
    target = _write(tmp_path, UPSTREAM)
    result = _run(target)
    assert result.returncode == 0, result.stderr
    patched = open(target, encoding="utf-8").read()
    assert "trust_remote_code=True" in patched, "the custom pipeline is still refused"
    # The rest of the call must survive: the custom pipeline path and the dtype are what select the
    # bundled pipeline and keep it in half precision.
    assert "custom_pipeline=custom_pipeline_path" in patched
    assert "torch_dtype=torch.float16" in patched


def test_it_fails_when_the_pinned_revision_has_moved_on(tmp_path):
    """The whole safety property: no silent no-op may reach an image build."""
    target = _write(tmp_path, "class Multiview_Diffusion_Net:\n    pass\n")
    result = _run(target)
    assert result.returncode != 0, "an unpatchable source must fail the build, not pass silently"
    assert "changed shape" in result.stderr


def test_it_fails_when_a_target_appears_more_than_once(tmp_path):
    """Two occurrences mean the patch cannot know which load the paint stage actually uses."""
    target = _write(tmp_path, UPSTREAM + UPSTREAM)
    assert _run(target).returncode != 0


def test_it_is_idempotent(tmp_path):
    """A rebuilt layer, or a revision that has already fixed this, must not fail the build."""
    target = _write(tmp_path, UPSTREAM)
    assert _run(target).returncode == 0
    first = open(target, encoding="utf-8").read()
    assert _run(target).returncode == 0
    assert open(target, encoding="utf-8").read() == first, "a second run changed the file again"
