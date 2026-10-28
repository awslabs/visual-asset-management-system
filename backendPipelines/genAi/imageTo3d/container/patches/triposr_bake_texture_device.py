# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Place the texture bake's tensors on the device the model is loaded onto.

Upstream's ``positions_to_colors`` builds its sample positions as a CPU tensor and hands them to a
triplane query whose weights are on the GPU, then calls ``.numpy()`` on the CUDA tensor that comes
back. Both fail on any GPU run, so ``--bake-texture`` yields an untextured mesh whatever the GL
configuration; the UV atlas itself rasterizes correctly before either fault is reached.

Run against a cloned repo at build time. Exits non-zero when a target line is absent, so a pinned
revision that has moved on fails the image build rather than shipping a silently unpatched bake.
"""

import sys

# Each entry is (description, exact source text, replacement).
REPLACEMENTS = (
    (
        "move the sampled positions onto the model's device",
        "    positions = torch.tensor(positions_texture.reshape(-1, 4)[:, :-1])\n",
        "    positions = torch.tensor(positions_texture.reshape(-1, 4)[:, :-1]).to(\n"
        "        next(model.parameters()).device\n"
        "    )\n",
    ),
    (
        "read the queried colours back from the device before converting to numpy",
        '    rgb_f = queried_grid["color"].numpy().reshape(-1, 3)\n',
        '    rgb_f = queried_grid["color"].detach().cpu().numpy().reshape(-1, 3)\n',
    ),
)


def main(path: str) -> int:
    with open(path, encoding="utf-8") as handle:
        source = handle.read()

    for description, old, new in REPLACEMENTS:
        if new in source:
            print(f"already applied: {description}")
            continue
        if source.count(old) != 1:
            print(
                f"ERROR: cannot patch {path} to {description}: expected exactly one occurrence of "
                f"the target text, found {source.count(old)}. The pinned upstream revision has "
                f"changed shape; re-derive this patch against it.",
                file=sys.stderr,
            )
            return 1
        source = source.replace(old, new, 1)
        print(f"patched: {description}")

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(source)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path to tsr/bake_texture.py>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
