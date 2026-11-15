# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""TORCH_CUDA_ARCH_LIST has to cover every GPU family the pipeline can schedule onto.

An extension compiled without the running GPU's architecture fails at run time with "no kernel image
is available for execution on the device" -- after the instance is provisioned, the image pulled, and
the model loaded. Widening the configured instance types without widening this list is what produces
that, and neither change looks wrong on its own.

The mapping is deliberately spelled out rather than derived: reading a compute capability off an
instance type needs an AWS call, and this file is a unit test.
"""

import json
import os
import re

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.abspath(os.path.join(CONTAINER_DIR, "..", "..", "..", ".."))


def _dockerfiles_declaring_an_arch_list():
    """Every model image that declares an architecture list, read off disk rather than listed.

    A hardcoded list excludes a new model's Dockerfile, and a new model is exactly where a missing
    architecture would first bite. Selection is by whether the file declares TORCH_CUDA_ARCH_LIST at
    all, which is the right condition whether or not the image compiles an extension itself: a wheel
    built during the image build may consult it too, and Stable3DGen sets one for that reason while
    compiling nothing of its own.
    """
    found = []
    for name in sorted(os.listdir(CONTAINER_DIR)):
        if not name.startswith("Dockerfile."):
            continue
        with open(os.path.join(CONTAINER_DIR, name), encoding="utf-8") as handle:
            if "TORCH_CUDA_ARCH_LIST" in handle.read():
                found.append(name)
    assert len(found) >= 4, f"expected several models to compile extensions, found {found}"
    return tuple(found)


DOCKERFILES = _dockerfiles_declaring_an_arch_list()

# Instance family -> CUDA compute capability of its GPU.
FAMILY_COMPUTE_CAPABILITY = {
    "g4dn": "7.5",   # T4, Turing
    "g5": "8.6",     # A10G, Ampere
    "g6": "8.9",     # L4, Ada
    "g6e": "8.9",    # L40S, Ada
    "p4d": "8.0",    # A100, Ampere
    "p4de": "8.0",
    "p5": "9.0",     # H100, Hopper
    "p5e": "9.0",
}


def _arch_list(dockerfile):
    body = open(os.path.join(CONTAINER_DIR, dockerfile), encoding="utf-8").read()
    m = re.search(r'ENV TORCH_CUDA_ARCH_LIST="([^"]+)"', body)
    assert m, f"{dockerfile} declares no TORCH_CUDA_ARCH_LIST"
    return {a.strip() for a in m.group(1).split(";") if a.strip()}


def _configured_instance_types():
    """The instance types the shipped commercial template offers this pipeline."""
    path = os.path.join(REPO_ROOT, "infra", "config", "config.template.commercial.json")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    block = cfg["app"]["pipelines"]["useImageTo3d"]
    return block["instanceTypes"]


def test_the_scan_finds_both_sides():
    """Neither half may be silently empty, or the coverage assertion proves nothing."""
    types = _configured_instance_types()
    assert types, "no instanceTypes configured for useImageTo3d"
    for d in DOCKERFILES:
        assert _arch_list(d), f"{d} has an empty arch list"


def test_every_configured_instance_family_is_covered():
    types = _configured_instance_types()
    families = {t.split(".")[0] for t in types}
    unknown = sorted(f for f in families if f not in FAMILY_COMPUTE_CAPABILITY)
    assert not unknown, (
        f"instance families with no known compute capability: {unknown}. "
        "Add them to FAMILY_COMPUTE_CAPABILITY with the GPU's capability."
    )
    required = {FAMILY_COMPUTE_CAPABILITY[f] for f in families}

    gaps = {}
    for d in DOCKERFILES:
        missing = sorted(required - _arch_list(d))
        if missing:
            gaps[d] = missing
    assert not gaps, (
        f"arch list does not cover every configured instance family: {gaps}. "
        f"Configured types: {sorted(types)}"
    )


def test_all_dockerfiles_declare_the_same_arch_list():
    """A model built for fewer architectures fails only on the instances it was not built for."""
    lists = {d: sorted(_arch_list(d)) for d in DOCKERFILES}
    distinct = {tuple(v) for v in lists.values()}
    assert len(distinct) == 1, f"arch lists diverge between images: {lists}"
