#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Helpers for tests that convert real files in this process through ``conversion_worker.convert_model``
(real trimesh, real usd-core), without the S3 flow or a child process."""

import os
import sys

import numpy as np

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)


def textured_cube(extents=(2.0, 3.0, 4.0)):
    import trimesh
    from PIL import Image
    rng = np.random.default_rng(11)
    box = trimesh.creation.box(extents=extents)
    image = Image.fromarray((rng.random((16, 16, 3)) * 255).astype(np.uint8))
    box.visual = trimesh.visual.TextureVisuals(uv=rng.random((len(box.vertices), 2)), image=image)
    return box


def sphere():
    import trimesh
    return trimesh.creation.icosphere(subdivisions=2, radius=1.5)


def write_usd_fixture(directory, name, geometry, meters_per_unit=1.0, up_axis="y"):
    """Write ``geometry`` as USD (by the container's writer) to ``directory/name`` and return its path."""
    import trimesh
    import usd_io
    export_dir = os.path.join(directory, "usdsrc")
    os.makedirs(export_dir, exist_ok=True)
    path = os.path.join(export_dir, name)
    usd_io.write_usd(trimesh.Scene(geometry), path, meters_per_unit, up_axis, os.path.splitext(name)[1])
    return path


def convert(tmp_path, source_path, output_extension, options=None, name=None, run_dir="run"):
    """Stage ``source_path`` as the input of a job, convert it, and return the path of the output
    (relative companions are beside it in ``export``)."""
    import conversion_worker
    work = tmp_path / run_dir
    input_dir = work / "input"
    export_dir = work / "export"
    input_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    name = name or os.path.basename(source_path)
    staged = input_dir / name
    # The input and, when it sits in a fixture folder, the files beside it (a USD file's textures).
    source_dir = os.path.dirname(source_path)
    for root, _, names in os.walk(source_dir):
        for companion in names:
            full = os.path.join(root, companion)
            target = input_dir / os.path.relpath(full, source_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(open(full, "rb").read())
    if name != os.path.basename(source_path):
        staged.write_bytes(open(source_path, "rb").read())
    # Every staged file is served to the loader by the name it is spelled with (as the fetch does).
    by_reference = {}
    for root, _, names in os.walk(input_dir):
        for staged_name in names:
            full = os.path.join(root, staged_name)
            by_reference[os.path.relpath(full, input_dir).replace(os.sep, "/")] = full
    output = export_dir / (os.path.splitext(name)[0] + output_extension)
    conversion_worker.convert_model({
        "input_file": str(staged), "input_dir": str(input_dir), "input_relative_path": name,
        "by_reference": by_reference, "output_file": str(output), "output_filetype": output_extension,
        "input_name": name, "options": options or {},
    })
    return str(output)
