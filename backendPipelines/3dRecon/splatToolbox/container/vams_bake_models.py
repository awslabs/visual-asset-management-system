#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Bake the Splat Toolbox pipeline's models into the container image at the paths that read them.

WHY THIS IS A SEPARATE FILE, and why it must stay one.

`splatToolbox-construct.ts` syncs the pinned upstream Splat Toolbox repository into this directory at
synth time, and its copy overwrites every file that also exists upstream — `build_models_tar.py` is one
of those. Model-baking logic added to that file therefore survives exactly until the next `cdk synth`,
at which point the Dockerfile's injected bake line runs against a script that no longer implements it:
`error: unrecognized arguments: --bake`, exit 2, and the `docker build` layer fails.

That is not hypothetical. It happened: the bake was implemented inside `build_models_tar.py`, passed its
tests, then a later synth restored the upstream file and the staged asset shipped a Dockerfile whose
`RUN ... --bake` could not succeed. With `useCodeBuild: false` the deploy fails outright; with
`useCodeBuild: true` the deploy exits 0, the CodeBuild build fails, the content-addressed tag is never
pushed, and every splat execution dies with `CannotPullContainerError`.

This filename does not exist upstream, so the sync preserves it ("only files present in the source are
written/overwritten"). It is also deliberately SELF-CONTAINED — it imports nothing from
`build_models_tar.py` — because that file's contents track the upstream pin, so importing from it would
reintroduce the same coupling through a different door.

WHAT IT BAKES, and why each one is needed at build time rather than at run time. Every model here has a
SOFT failure mode: the library fetches it itself on first use, so the image builds clean and the
download reappears on the data path — which is a hard failure in a deployment whose Batch subnets have
no egress, and a hard failure after the GPU node is already up.

    u2net .pth weights     backgroundremover loads these directly from U2NET_PATH's directory
    rembg .onnx models     resolved through rembg's OWN API, which supplies the URL and the hash
    SAM2 checkpoint        the SAM2 wrapper composes its path from MODEL_PATH
    torch-hub checkpoints  torch.hub reads $TORCH_HOME/hub/checkpoints

Stable Diffusion XL is deliberately NOT baked: it is ~7 GB and is still fetched at run time by the
default object-removal action, so that path is not egress-free regardless. The documented egress-free
object-removal route is the other action.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# --- what the image reads -------------------------------------------------------------------------
# Each of these is also set as ENV by the Dockerfile, so the build supplies the real value and these
# are only the fallbacks.

# U2NET_PATH names the FILE; backgroundremover reads the directory containing it.
DEFAULT_IMAGE_U2NET_PATH = "/root/.u2net/u2net.pth"
DEFAULT_IMAGE_MODEL_DIR = "/opt/ml/input/data/model"
# torch.hub.load_state_dict_from_url reads $TORCH_HOME/hub/checkpoints, and TORCH_HOME defaults to
# ~/.cache/torch. The image declares no USER, so HOME is /root at build time and at run time.
DEFAULT_IMAGE_TORCH_HUB_DIR = "/root/.cache/torch/hub/checkpoints"

SAM2_CHECKPOINT_NAME = "sam2.1_hiera_large.pt"
SAM2_CHECKPOINT_URL = "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt"

# The .pth weights backgroundremover assembles from split parts.
U2NET_WEIGHT_FILES = ("u2net.pth", "u2netp.pth", "u2net_human_seg.pth")
U2NET_PART_BASE = "https://github.com/nadermx/backgroundremover/raw/main/models"

# The rembg models the human-segmentation path opens. `u2net_human_seg` and `birefnet-portrait` are
# BOTH opened for one background-removal call, so a bake covering only the first still fetches the
# second mid-run. Resolved through rembg's own API rather than a guessed URL: rembg verifies each
# file's MD5 and re-downloads on a mismatch, so a file placed by hand bakes and is then ignored.
REMBG_MODEL_NAMES = ("u2net", "u2net_human_seg", "birefnet-portrait")

# torch-hub checkpoints the reconstruction and its perceptual metrics load. Each URL already carries
# the content hash torch matches on, so these are pinned by name.
TORCH_HUB_CHECKPOINTS = (
    (
        "https://download.pytorch.org/models/mobilenet_v3_large-8738ca79.pth",
        "mobilenet_v3_large-8738ca79.pth",
    ),
    (
        "https://download.pytorch.org/models/fasterrcnn_resnet50_fpn_coco-258fb6c6.pth",
        "fasterrcnn_resnet50_fpn_coco-258fb6c6.pth",
    ),
    ("https://download.pytorch.org/models/vgg16-397923af.pth", "vgg16-397923af.pth"),
    ("https://download.pytorch.org/models/alexnet-owt-7be5be79.pth", "alexnet-owt-7be5be79.pth"),
)


def download(url: str, dest: Path) -> None:
    """Download url to dest, reporting progress every 10 percent and exiting non-zero on failure."""
    print(f"  Downloading {dest.name} ...", flush=True)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=60) as resp:  # nosemgrep: dynamic-urllib-use-detected
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            last_pct = -1
            start = time.time()
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(512 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = int(downloaded / total * 100)
                        if pct >= last_pct + 10:
                            elapsed = time.time() - start
                            speed = downloaded / elapsed / 1024 / 1024 if elapsed else 0
                            print(
                                f"    {pct}%  {downloaded / 1024 / 1024:.1f}/"
                                f"{total / 1024 / 1024:.1f} MB  {speed:.1f} MB/s",
                                flush=True,
                            )
                            last_pct = pct
    except (HTTPError, URLError) as e:
        print(f"  ERROR downloading {url}: {e}")
        sys.exit(1)
    print(f"  Done: {dest.name} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")


def download_u2net_weights(u2net_dir: Path) -> Path:
    """Assemble the backgroundremover .pth weights into u2net_dir from their split parts."""
    u2net_dir.mkdir(parents=True, exist_ok=True)

    for target, parts in (
        ("u2net.pth", ("u2aa", "u2ab", "u2ac", "u2ad")),
        ("u2net_human_seg.pth", ("u2haa", "u2hab", "u2hac", "u2had")),
    ):
        part_paths = []
        for part in parts:
            p = u2net_dir / f"{target}.{part}"
            download(f"{U2NET_PART_BASE}/{part}", p)
            part_paths.append(p)
        with open(u2net_dir / target, "wb") as out:
            for p in part_paths:
                out.write(p.read_bytes())
                p.unlink()

    download(f"{U2NET_PART_BASE}/u2netp.pth", u2net_dir / "u2netp.pth")
    return u2net_dir


def bake_into_image(u2net_dir: Path, model_dir: Path) -> None:
    """The two weight sets that are fetched by URL: the u2net .pth files and the SAM2 checkpoint.

    Separate from the rembg and torch steps because those two resolve their own downloads through
    their libraries' APIs rather than from a URL this module holds.
    """
    print("\n--- U2NET ---")
    download_u2net_weights(u2net_dir)
    print("\n--- SAM2 ---")
    download_sam2_checkpoint(model_dir)


def _new_rembg_session(name: str):
    """Resolve one rembg model through rembg's own API, which supplies both the URL and the hash.

    Imported inside the function: rembg is installed by the image build, so a caller (or a test) that
    only needs the rest of this module must not require it at import time.
    """
    from rembg import new_session  # noqa: PLC0415 - only the image build has rembg

    return new_session(name, providers=["CPUExecutionProvider"])


def warm_rembg_sessions(u2net_dir: Path, session_factory=None) -> list:
    """Resolve each rembg ONNX model into the directory rembg reads, and return their paths.

    Opening a session is what downloads the model, so this leaves each file where rembg itself looks
    (`U2NET_HOME`, which it defaults to ~/.u2net) with the hash rembg expects. Pointed at the same
    directory as the .pth weights so one directory holds both.
    """
    # Looked up here rather than bound as a default argument so a test can substitute it by patching
    # this module, without rembg installed.
    factory = session_factory or _new_rembg_session
    u2net_dir.mkdir(parents=True, exist_ok=True)
    os.environ["U2NET_HOME"] = str(u2net_dir)
    for name in REMBG_MODEL_NAMES:
        print(f"  Resolving rembg model {name} ...", flush=True)
        factory(name)
    return [u2net_dir / f"{name}.onnx" for name in REMBG_MODEL_NAMES]


def download_sam2_checkpoint(model_dir: Path) -> Path:
    """Place the SAM2 checkpoint where the SAM2 wrapper composes its path from MODEL_PATH."""
    model_dir.mkdir(parents=True, exist_ok=True)
    dest = model_dir / SAM2_CHECKPOINT_NAME
    download(SAM2_CHECKPOINT_URL, dest)
    return dest


def download_torch_hub_checkpoints(torch_dir: Path) -> list:
    """Download the torch-hub checkpoints into the directory torch.hub loads them from.

    These are named by content hash, so torch finds them by filename and skips its own download. The
    failure mode without them is SOFT — torch re-downloads at run time rather than erroring — which is
    why the caller verifies the files exist rather than relying on the build to fail.
    """
    torch_dir.mkdir(parents=True, exist_ok=True)
    for url, name in TORCH_HUB_CHECKPOINTS:
        download(url, torch_dir / name)
    return [torch_dir / name for _url, name in TORCH_HUB_CHECKPOINTS]


def verify_baked_models(u2net_dir: Path, model_dir: Path, torch_dir: Path) -> None:
    """Exit non-zero naming every model the bake was supposed to leave behind and did not.

    Build time is the only place this is cheap to report: every one of these fails soft at run time by
    re-downloading, so without this check the image builds and pushes clean and the download reappears
    on the data path.
    """
    expected = [u2net_dir / name for name in U2NET_WEIGHT_FILES]
    expected += [u2net_dir / f"{name}.onnx" for name in REMBG_MODEL_NAMES]
    expected.append(model_dir / SAM2_CHECKPOINT_NAME)
    expected += [torch_dir / name for _url, name in TORCH_HUB_CHECKPOINTS]

    missing = [str(path) for path in expected if not path.exists()]
    if missing:
        print("\nERROR: the bake did not leave these models in the image:")
        for path in missing:
            print(f"  {path}")
        sys.exit(1)
    print(f"\nVerified {len(expected)} baked model files")


def resolve_directories(u2net_dir=None, model_dir=None, torch_dir=None):
    """The three directories the bake writes to, from the build's ENV unless overridden."""
    resolved_u2net = (
        Path(u2net_dir)
        if u2net_dir
        else Path(os.environ.get("U2NET_PATH", DEFAULT_IMAGE_U2NET_PATH)).parent
    )
    resolved_model = (
        Path(model_dir) if model_dir else Path(os.environ.get("MODEL_PATH", DEFAULT_IMAGE_MODEL_DIR))
    )
    if torch_dir:
        resolved_torch = Path(torch_dir)
    elif os.environ.get("TORCH_HOME"):
        resolved_torch = Path(os.environ["TORCH_HOME"]) / "hub" / "checkpoints"
    else:
        resolved_torch = Path(DEFAULT_IMAGE_TORCH_HUB_DIR)
    return resolved_u2net, resolved_model, resolved_torch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Bake the Splat Toolbox models into this container image."
    )
    parser.add_argument("--u2net-dir", default=None, help="Override the u2net directory")
    parser.add_argument("--model-dir", default=None, help="Override the SAM2 model directory")
    parser.add_argument("--torch-dir", default=None, help="Override the torch-hub directory")
    args = parser.parse_args(argv)

    u2net_dir, model_dir, torch_dir = resolve_directories(
        args.u2net_dir, args.model_dir, args.torch_dir
    )

    print("=== Baking models into the image ===")
    print(f"U2NET directory: {u2net_dir}")
    print(f"Model directory: {model_dir}")
    print(f"Torch hub directory: {torch_dir}")

    # Each step is called through its module-level name so a test can substitute one and have the
    # substitution take effect -- calling download_u2net_weights/download_sam2_checkpoint directly
    # here instead would bypass a patched bake_into_image and make the test fetch ~200 MB for real.
    bake_into_image(u2net_dir, model_dir)
    print("\n--- rembg ONNX models ---")
    warm_rembg_sessions(u2net_dir)
    print("\n--- PyTorch hub checkpoints ---")
    download_torch_hub_checkpoints(torch_dir)

    verify_baked_models(u2net_dir, model_dir, torch_dir)
    print("\n=== Done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
