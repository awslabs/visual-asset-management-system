# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ask an image-editing model on Amazon Bedrock to work on a texture, without leaving the sandbox.

    python tools/agent_image_edit.py inpaint --image textures/solid_basecolor.png --mask textures/inferred_mask.png \\
        --prompt "black cotton t-shirt fabric and long brown hair continuing from the visible side" \\
        --out textures/filled.png
    python tools/agent_image_edit.py structure --image textures/view_back.png --mask textures/view_back_mask.png \\
        --prompt "a woman seen from behind, long dark brown wavy hair, black t-shirt, light blue jeans" \\
        --control-strength 0.9 --out textures/view_back_filled.png
    python tools/agent_image_edit.py search_replace --image textures/x.png --select "the shoes" --prompt "white low-top sneakers" --out ...
    python tools/agent_image_edit.py style_transfer --image textures/solid_basecolor.png --style refs/<front>_cutout.png \\
        --prompt "the same person, same clothing" --out textures/styled.png
    python tools/agent_image_edit.py search_recolor --image textures/x.png --select "the trousers" --prompt "light blue denim" --out ...
    python tools/agent_image_edit.py erase --image textures/x.png --mask textures/spots.png --out ...

You have no network. This writes a request into ``requests/`` and waits for the pipeline, which runs beside
you with credentials you do not have, to serve it with a Stability AI model and write the result image where
you asked. Inputs and outputs must be inside the workspace. The image is sent at up to 1024 px on its longer
edge and the result comes back at the input's size. With a mask -- required by ``inpaint`` and ``erase``,
optional on the rest -- every unmasked pixel of the result is the input's own, untouched; the model's output
is taken inside the mask only. ``structure`` regenerates the whole picture along the input's outline and
pose with the content your prompt names, which is what the hidden side of a figure needs: the inpainting
model, given a mask that covers most of a figure, redraws the figure.

What it is for: the photograph covers the surface the camera saw and nothing else. The rest is filled through
`bl_view_fill.py`: its ``prepare`` renders the model from a side and masks what was never photographed, you
inpaint that PICTURE here with a prompt describing the subject from that side, and its ``apply`` bakes the
result back onto the faces that view saw. Inpainting the atlas itself, with `bl_uv_mask.py`'s mask, is for a
small patch: an image model completes pictures, and handed uv islands it returned a uniform grey-brown. Look
at every result before you keep it: it is a guess, and a wrong guess is worse than the flat colour.
"""

import argparse
import json
import os
import sys
import time

WORKSPACE = os.path.realpath(os.environ.get("AGENT_WORKSPACE", os.getcwd()))


def inside(path, purpose):
    resolved = os.path.realpath(path)
    if not (resolved == WORKSPACE or resolved.startswith(WORKSPACE + os.sep)):
        raise SystemExit(f"Refusing to {purpose} outside the workspace: {path}")
    return os.path.relpath(resolved, WORKSPACE).replace(os.sep, "/")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("inpaint", "structure", "style_transfer", "search_recolor", "search_replace", "erase"))
    parser.add_argument("--image", required=True)
    parser.add_argument("--mask", default="")
    parser.add_argument("--style", default="")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--negative-prompt", default="")
    parser.add_argument("--select", default="", help="search_recolor / search_replace: what to change, in words")
    parser.add_argument("--control-strength", type=float, default=None,
                        help="structure: how closely to follow the input, 0-1 (the model default is 0.7)")
    parser.add_argument("--style-strength", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--grow-mask", type=int, default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    if args.operation in ("inpaint", "erase") and not args.mask:
        raise SystemExit(f"{args.operation} needs --mask")
    if args.operation == "style_transfer" and not args.style:
        raise SystemExit("style_transfer needs --style")
    if args.operation in ("search_recolor", "search_replace") and not args.select:
        raise SystemExit(f"{args.operation} needs --select")
    if args.operation in ("structure", "search_replace") and not args.prompt:
        raise SystemExit(f"{args.operation} needs --prompt")
    request = {"operation": args.operation, "image": inside(args.image, "read"), "output": inside(args.out, "write"),
               "prompt": args.prompt}
    if args.mask:
        request["mask"] = inside(args.mask, "read")
    if args.style:
        request["style"] = inside(args.style, "read")
    if args.negative_prompt:
        request["negative_prompt"] = args.negative_prompt
    if args.select:
        request["select_prompt"] = args.select
    if args.style_strength is not None:
        request["style_strength"] = args.style_strength
    if args.control_strength is not None:
        request["control_strength"] = args.control_strength
    if args.seed is not None:
        request["seed"] = args.seed
    if args.grow_mask is not None:
        request["grow_mask"] = args.grow_mask

    requests_dir = os.path.join(WORKSPACE, "requests")
    os.makedirs(requests_dir, exist_ok=True)
    name = f"{args.operation}_{int(time.time() * 1000)}"
    request_path = os.path.join(requests_dir, f"{name}.json")
    result_path = os.path.join(requests_dir, f"{name}.result.json")
    with open(request_path, "w", encoding="utf-8") as handle:
        json.dump(request, handle, indent=2)
    print(f"request {name}: {args.operation} on {request['image']} -> {request['output']}; waiting for the pipeline",
          flush=True)

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if os.path.exists(result_path):
            time.sleep(0.2)
            with open(result_path, encoding="utf-8") as handle:
                result = json.load(handle)
            if result.get("status") == "ok":
                kept = "; unmasked pixels untouched" if result.get("unmaskedPreserved") else ""
                print(f"done in {result.get('seconds')}s by {result.get('model')}: {result.get('output')} "
                      f"(edited at {result.get('workingSize')}, returned at the input size{kept}). LOOK AT IT before you keep it.")
                return
            raise SystemExit(f"the edit failed: {result.get('message')}")
        time.sleep(2.0)
    raise SystemExit(f"no result after {args.timeout}s; the broker may not be running -- say so in notes.md and go on "
                     f"without it")


if __name__ == "__main__":
    main()
