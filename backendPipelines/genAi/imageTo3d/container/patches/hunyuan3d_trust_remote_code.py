# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Let the texture stage load the multi-view pipeline that ships inside the repository.

The paint stage's diffusion pipeline is not a stock ``diffusers`` class: upstream keeps it in the repo at
``hy3dgen/texgen/hunyuanpaint`` and asks ``diffusers`` to load it as a custom pipeline. Current
``diffusers`` refuses to execute custom code without ``trust_remote_code=True`` -- and upstream's call
does not pass it, so the stage dies with::

    ValueError: The directory .../hunyuanpaint contains custom code in pipeline.py which must be
    executed to correctly load the model. Pass `trust_remote_code=True` to allow loading remote code.

which upstream's own ``from_pretrained`` then swallows into ``RuntimeError: Something wrong while
loading <path>``, naming the checkpoint rather than the cause.

The flag cannot be passed from outside: ``Hunyuan3DPaintPipeline.from_pretrained(model_path, subfolder)``
takes no ``trust_remote_code`` and has no ``**kwargs``, and the load happens two layers down in
``Multiview_Diffusion_Net``. So it is set at the call site.

"Remote" is misleading here. The code being trusted is the ``hunyuanpaint`` directory of the repository
this image clones at the commit pinned by ``HUNYUAN3D_COMMIT`` -- it is baked into the image and reviewed
like the rest of it, and nothing is fetched at run time. The flag is scoped to that one call, so no other
load in the image gains it.

Run against a cloned repo at build time. Exits non-zero when the target line is absent, so a pinned
revision that has moved on fails the image build rather than shipping a texture stage that fails on a GPU.
"""

import sys

# Each entry is (description, exact source text, replacement). The leading whitespace is part of the
# target: upstream indents these continuation lines by 12 spaces, and a shorter prefix would still MATCH
# by substring while writing a misaligned replacement. Taken from the pinned revision's bytes.
REPLACEMENTS = (
    (
        "trust the hunyuanpaint custom pipeline that ships in this repository",
        "            custom_pipeline=custom_pipeline_path, torch_dtype=torch.float16)\n",
        "            custom_pipeline=custom_pipeline_path, torch_dtype=torch.float16,\n"
        "            trust_remote_code=True)\n",
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
        print(f"usage: {sys.argv[0]} <path to hy3dgen/texgen/utils/multiview_utils.py>",
              file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
