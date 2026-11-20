# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The HuggingFace commit every repository this pipeline loads is pinned to, and the guard that holds
every download to it.

A repository named without a revision resolves to its default branch's head WHEN THE JOB RUNS, so two
runs of one image could load different weights, and a repository that ships code loaded with
``trust_remote_code`` could run different code. Pinning only the calls this pipeline writes is not
enough: the upstream libraries fetch further repositories themselves -- TripoSR reads the
``facebook/dino-vitb16`` configuration, Step1X-3D's texture stage loads the SDXL base model and its VAE,
TRELLIS.2 loads a decoder from ``microsoft/TRELLIS-image-large`` -- and those calls carry no revision.

``install()`` therefore wraps huggingface_hub's two download entry points. A call with no revision, or
with ``main``, gets the pinned commit; a call naming the pinned commit passes; a repository with no pin,
or a call asking for any other revision, is refused with an error naming the repository. It also wraps
the cache lookup ``try_to_load_from_cache``, which transformers uses after a download to find the file
again by the caller's revision, so a lookup by ``main`` reads the pinned commit too. The main
process installs it before anything imports transformers or diffusers, and every upstream process gets it
through the ``sitecustomize`` beside this file, which ``run_upstream`` puts on that process's
``PYTHONPATH``. A cached file under a pinned commit is read without a network call, so a warm cache costs
nothing.

To move a pin: read the new commit's changes (code files above all, for a repository loaded with
``trust_remote_code``), update the commit here, and run the model once. The HuggingFace cache keys its
snapshots by commit, so the new snapshot lands beside the old one and only files that changed download.
"""

import functools

# Repository id -> 40-hex commit. Looked up case-insensitively: StableNormal names BiRefNet
# ``zhengpeng7/BiRefNet``, the hub's own spelling is ``ZhengPeng7/BiRefNet``, and both are one repository.
HF_REVISIONS = {
    # TripoSR, and the DINO configuration its image tokenizer downloads.
    "stabilityai/TripoSR": "5b521936b01fbe1890f6f9baed0254ab6351c04a",
    "facebook/dino-vitb16": "f205d5d8e640a89a2b8ef0369670dfc37cc07fc2",
    # Hunyuan3D-2: shape and paint models, and the multi-view shape model.
    "tencent/Hunyuan3D-2": "9cd649ba6913f7a852e3286bad86bfa9a2d83dcf",
    "tencent/Hunyuan3D-2mv": "3a761b539b29fe4ff64714813aa9560fd66f5de0",
    # The super-resolution model the Hunyuan3D-2 paint stack can load.
    "stabilityai/stable-diffusion-x4-upscaler": "572c99286543a273bfd17fac263db5a77be12c4c",
    # SAM 3D Objects (gated) and the MoGe depth model its pipeline configuration names.
    "facebook/sam-3d-objects": "2e73555018d2741ccd486e56c24fac41155a1dc6",
    "Ruicheng/moge-vitl": "ad326bfb61facd6c52b5a825bc1e34d7c97d9672",
    # The Blender agent's MoGe-2 refit model.
    "Ruicheng/moge-2-vitl-normal": "cb0e8bbd6b1e243589717c78e750b1ba4c093acf",
    # Stable3DGen: geometry, normal predictor, and BiRefNet (also TRELLIS.2's and Step1X-3D's own
    # background remover). BiRefNet is loaded with trust_remote_code, so its code runs at this commit.
    "Stable-X/trellis-normal-v0-1": "fdfac1d0e0cb3f86adcac69239c1abdd82228e17",
    "Stable-X/yoso-normal-v1-8-1": "4dd7d000d76febb68254ebe27251c2cb79dc1c2a",
    "ZhengPeng7/BiRefNet": "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4",
    # Step1X-3D, the encoder configurations its geometry stage downloads, and the SDXL base model and
    # VAE its texture stage loads.
    "stepfun-ai/Step1X-3D": "bf7084495b3a72222f36549b7942948aa4d9daa7",
    "facebook/dinov2-with-registers-large": "e4c89a4e05589de9b3e188688a303d0f3c04d0f3",
    "openai/clip-vit-large-patch14": "32bd64288804d66eefd0ccbe215aa642df71cc41",
    "stabilityai/stable-diffusion-xl-base-1.0": "462165984030d82259a11f4367a4eed129e94a7b",
    "madebyollin/sdxl-vae-fp16-fix": "207b116dae70ace3637169f1ddd2434b91b3a8cd",
    # TRELLIS.2, its DINOv3 encoder (gated), and the TRELLIS decoder its pipeline.json names.
    "microsoft/TRELLIS.2-4B": "af44b45f2e35a493886929c6d786e563ec68364d",
    "facebook/dinov3-vitl16-pretrain-lvd1689m": "ea8dc2863c51be0a264bab82070e3e8836b02d51",
    "microsoft/TRELLIS-image-large": "25e0d31ffbebe4b5a97464dd851910efc3002d96",
    # Background-removal backends: the SAM checkpoints SAM_MODEL_REPO may name, and RMBG-2.0 (gated,
    # loaded with trust_remote_code).
    "facebook/sam-vit-huge": "87aecf0df4ce6b30cd7de76e87673c49644bdf67",
    "facebook/sam-vit-large": "6851e0441005b0fb96f2cc4dfac472f3d1b14af1",
    "facebook/sam-vit-base": "70c1a07f894ebb5b307fd9eaaee97b9dfc16068f",
    "briaai/RMBG-2.0": "6a88d6a65119856a743be87ec1e6bee6f08e1f93",
}

_BY_LOWER = {repo.lower(): revision for repo, revision in HF_REVISIONS.items()}


class UnpinnedRepositoryError(RuntimeError):
    """A HuggingFace download this pipeline has no pin for, or one asking for a revision other than its pin."""


def revision_for(repo_id: str) -> str:
    """The pinned commit for ``repo_id``. Raises UnpinnedRepositoryError for a repository with no pin."""
    revision = _BY_LOWER.get(str(repo_id or "").strip().lower())
    if revision is None:
        raise UnpinnedRepositoryError(
            f"HuggingFace repository '{repo_id}' has no pinned commit, so it is not downloaded. Every "
            f"repository this pipeline loads is pinned in hfguard/hf_pins.py (HF_REVISIONS); add one there "
            f"after reviewing that commit.")
    return revision


def is_pinned(repo_id: str) -> bool:
    """Whether ``repo_id`` has a pin."""
    return str(repo_id or "").strip().lower() in _BY_LOWER


def resolve_revision(repo_id: str, revision=None, repo_type=None) -> str:
    """The revision a download of ``repo_id`` is allowed to use.

    No revision or ``main`` becomes the pin, and the pin itself passes. Any other revision -- another
    branch, a tag, another commit -- and any repository that is not a model repository are refused.
    """
    if repo_type not in (None, "model"):
        raise UnpinnedRepositoryError(
            f"HuggingFace {repo_type} repository '{repo_id}' is not pinned; only model repositories are "
            f"downloaded, each at the commit in hfguard/hf_pins.py.")
    pinned = revision_for(repo_id)
    if revision in (None, "", "main", pinned):
        return pinned
    raise UnpinnedRepositoryError(
        f"A download asked for revision '{revision}' of '{repo_id}', which is pinned to {pinned}; only the "
        f"pinned commit is downloaded.")


def _pinned(download):
    """Wrap a huggingface_hub download function so it uses the pinned commit."""
    if getattr(download, "_vams_pinned", False):
        return download

    @functools.wraps(download)
    def wrapper(repo_id, *args, revision=None, repo_type=None, **kwargs):
        return download(repo_id, *args, revision=resolve_revision(repo_id, revision, repo_type),
                        repo_type=repo_type, **kwargs)

    wrapper._vams_pinned = True
    return wrapper


def _pinned_lookup(lookup):
    """Wrap huggingface_hub's ``try_to_load_from_cache`` so a lookup by ``main`` finds the pinned commit.

    A pinned download writes ``snapshots/<pin>`` and no ``refs/main`` (huggingface_hub records a ref only
    for the revision it was asked for), and transformers ignores what ``hf_hub_download`` returns: it looks
    the file up again by the caller's revision. Without this a model loaded through ``from_pretrained``
    (TRELLIS.2's DINOv3) found nothing, and a cache whose ``refs/main`` predates the pin would serve the
    older commit. Only the lookup is redirected; nothing is downloaded and nothing is refused here.
    """
    if getattr(lookup, "_vams_pinned", False):
        return lookup

    @functools.wraps(lookup)
    def wrapper(repo_id, filename, *args, revision=None, repo_type=None, **kwargs):
        if revision in (None, "", "main") and repo_type in (None, "model") and is_pinned(repo_id):
            revision = revision_for(repo_id)
        return lookup(repo_id, filename, *args, revision=revision, repo_type=repo_type, **kwargs)

    wrapper._vams_pinned = True
    return wrapper


def install() -> bool:
    """Hold every huggingface_hub download in this process to the pins. Idempotent.

    Patched where the functions live and where they are re-exported, because transformers and diffusers
    import them by name. Call it before those libraries are imported; the upstream processes get it at
    interpreter start through ``sitecustomize``. Returns False when huggingface_hub is not installed.
    """
    try:
        import huggingface_hub
        from huggingface_hub import _snapshot_download, file_download
    except ImportError:
        return False

    file_download.hf_hub_download = _pinned(file_download.hf_hub_download)
    _snapshot_download.snapshot_download = _pinned(_snapshot_download.snapshot_download)
    file_download.try_to_load_from_cache = _pinned_lookup(file_download.try_to_load_from_cache)
    huggingface_hub.hf_hub_download = file_download.hf_hub_download
    huggingface_hub.snapshot_download = _snapshot_download.snapshot_download
    huggingface_hub.try_to_load_from_cache = file_download.try_to_load_from_cache
    return True
