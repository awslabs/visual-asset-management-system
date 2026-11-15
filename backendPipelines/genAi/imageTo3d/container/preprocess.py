"""
Image validation and background removal.

Runs before any GPU work: a file that is not a decodable image, or is too small to reconstruct
from, is rejected here rather than after a model has been loaded.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Formats Pillow must report for the file to be accepted
ALLOWED_PILLOW_FORMATS = ("PNG", "JPEG", "MPO")
# Reconstruction quality collapses below this; the models downsample to 518-1024 px internally
MIN_EDGE_PIXELS = 256
# Guards against a decompression-bomb style input before any model sees it
MAX_EDGE_PIXELS = 16384

BACKGROUND_REMOVAL_BACKENDS = ("none", "rembg", "birefnet", "rmbg2", "sam")

# Segment Anything, used as a PROMPTED backend. The other three are salient-object detectors: they decide
# for themselves which object the photograph is of, and on a frame holding several they pick one and there
# is no way to ask for a different one. SAM answers a prompt instead, so an operator can point at the
# object to reconstruct. That is the capability it adds; it is not an edge-quality upgrade -- see
# _remove_background_sam.
#
# sam-vit-huge is Apache-2.0 and UNGATED, which is why it and not SAM 3. SAM 3's weights need a manually
# reviewed access request, and a gated repository is a hard stop rather than a slow start: measured in this
# deployment, selecting rmbg2 fails with `GatedRepoError: 403` on briaai/RMBG-2.0 because the token's
# account has not accepted that licence. Overridable to the base or large checkpoint, which share this
# class and processor and trade quality for speed and memory.
#
# Loaded through the transformers each model image already carries, which is not all of them: TripoSR's is
# older than SamModel needs, and it cannot be raised in the shared layer without breaking that model's own
# checkpoint -- see requirements.txt. Selecting sam there raises rather than falling back.
SAM_MODEL_REPO = "facebook/sam-vit-huge"

# HuggingFace repositories each background-removal backend needs. Declaring one here does two things: the
# weight-cache CHECK asks about it, so a cache holding the model's weights but not the backend's reports a
# miss and restores from the S3 backup rather than reporting a hit; and the cache stage FETCHES it before
# preprocessing runs, through model_manager.fetch_repo.
#
# That second part matters more than it looks. Left to transformers, the download happens lazily from
# inside this module, streaming a multi-gigabyte blob onto the shared NFS cache in the middle of a job --
# which produced `OSError: [Errno 116] Stale file handle` three times, twice with concurrent jobs fetching
# one repository and once on a job running alone, and corrupted a neighbouring model's state_dict on the
# way. Fetching it in the cache stage puts it under a retry that discards the partial download instead of
# resuming into it.
#
# rembg and birefnet are absent because they run rembg sessions, whose ONNX weights come from a GitHub
# release rather than HuggingFace; BACKGROUND_REMOVAL_SESSIONS covers those.
BACKGROUND_REMOVAL_REPOS = {
    "rmbg2": ("briaai/RMBG-2.0",),
    "sam": (SAM_MODEL_REPO,),
}

# The rembg session each of the two salient-object backends runs, with the weight file it keeps under
# U2NET_HOME. The cache stage fetches that file into the model's cache directory before preprocessing,
# through model_manager.fetch_rembg_session, so the S3 backup carries it and a named session is downloaded
# once per model rather than on every run -- 973 MB for BiRefNet, 45 to 55 s of every run that selects
# it. The backends name their session from here rather than taking rembg's default, so the file fetched is
# the file read.
BACKGROUND_REMOVAL_SESSIONS = {
    "rembg": ("u2net", "u2net.onnx"),
    "birefnet": ("birefnet-general", "birefnet-general.onnx"),
}

# A prompted mask that covers almost the whole frame, or almost none of it, means the prompt missed: it
# names the background, or a speck. Either produces a reconstruction of the wrong thing, so it is rejected
# rather than reconstructed.
SAM_MIN_MASK_SHARE = 0.001
SAM_MAX_MASK_SHARE = 0.9


def backend_repos(backend: str) -> tuple:
    """The HuggingFace repositories a background-removal backend needs, for the weight cache.

    The cache stage reads this twice -- once to decide whether the cache holds what this run needs, and
    once to fetch anything missing before preprocessing reaches for it. See BACKGROUND_REMOVAL_REPOS.
    """
    return BACKGROUND_REMOVAL_REPOS.get(str(backend or "").strip().lower(), ())


def backend_session(backend: str) -> tuple:
    """``(session name, weight file name)`` of the rembg session a backend runs, or ``()`` for the rest.

    The cache stage fetches the file before preprocessing and asks the weight cache about it, the same
    two uses backend_repos serves for the HuggingFace backends. See BACKGROUND_REMOVAL_SESSIONS.
    """
    return BACKGROUND_REMOVAL_SESSIONS.get(str(backend or "").strip().lower(), ())


class PreprocessError(RuntimeError):
    """An input image that cannot be used for reconstruction."""


def validate_image(image_path: str) -> dict:
    """Verify the file is a decodable PNG/JPEG of usable resolution.

    Pillow's ``verify()`` reads the signature and structure without decoding pixel data, so a file
    renamed to ``.png`` is caught here. Returns the image's ``{format, mode, width, height}``.
    """
    from PIL import Image

    if not os.path.isfile(image_path):
        raise PreprocessError(f"Input image not found: {image_path}")
    if os.path.getsize(image_path) == 0:
        raise PreprocessError(f"Input image is empty: {image_path}")

    try:
        with Image.open(image_path) as probe:
            probe.verify()
    except Exception as e:
        raise PreprocessError(f"Input file is not a readable image: {image_path} ({e})") from e

    # verify() leaves the handle unusable, so reopen to read the header fields
    with Image.open(image_path) as img:
        image_format = (img.format or "").upper()
        info = {"format": image_format, "mode": img.mode,
                "width": img.width, "height": img.height}

    if image_format not in ALLOWED_PILLOW_FORMATS:
        raise PreprocessError(
            f"Unsupported image format '{image_format}' for {image_path}; "
            f"expected one of {', '.join(ALLOWED_PILLOW_FORMATS)}")

    shortest = min(info["width"], info["height"])
    longest = max(info["width"], info["height"])
    if shortest < MIN_EDGE_PIXELS:
        raise PreprocessError(
            f"Input image is {info['width']}x{info['height']}; the shortest edge must be at least "
            f"{MIN_EDGE_PIXELS} px for reconstruction")
    if longest > MAX_EDGE_PIXELS:
        raise PreprocessError(
            f"Input image is {info['width']}x{info['height']}; the longest edge must be at most "
            f"{MAX_EDGE_PIXELS} px")

    logger.info(f"Input image validated: {info}")
    return info


def _remove_background_rembg(image_path: str, output_path: str, params: dict) -> None:
    """rembg's u2net session, named so the weights the cache stage fetched are the weights read. The
    permissive default backend."""
    from PIL import Image
    from rembg import new_session, remove

    with Image.open(image_path) as img:
        cut = remove(img.convert("RGBA"), session=new_session(BACKGROUND_REMOVAL_SESSIONS["rembg"][0]))
    cut.save(output_path)


def _remove_background_birefnet(image_path: str, output_path: str, params: dict) -> None:
    """BiRefNet through rembg's named session. An escalation backend for hair and thin structure."""
    from PIL import Image
    try:
        from rembg import new_session, remove
    except ImportError as e:
        raise PreprocessError(
            "Background removal backend 'birefnet' is unavailable: rembg is not installed") from e

    try:
        session = new_session(BACKGROUND_REMOVAL_SESSIONS["birefnet"][0])
    except Exception as e:
        raise PreprocessError(
            f"Background removal backend 'birefnet' could not load the birefnet-general "
            f"session: {e}") from e

    with Image.open(image_path) as img:
        cut = remove(img.convert("RGBA"), session=session)
    cut.save(output_path)


def resolve_sam_prompt(value, size) -> tuple:
    """``(processor_kwargs, description)`` for a ``SAM_PROMPT`` setting against an image of ``size``.

    Coordinates are NORMALISED to 0-1 rather than given in pixels, so one value keeps its meaning if the
    photograph is re-exported at another resolution, and an operator reading a picture at any zoom can
    describe a position in it. Accepted forms:

        centre          a point at the middle of the frame, which is where a framed subject is
        x,y             a point
        x0,y0,x1,y1     a box, as the two opposite corners

    A point asks SAM "the object here"; a box asks "the object within this". A box is the stronger prompt
    where several objects overlap, because it bounds the answer as well as locating it.

    Anything else raises rather than falling back to the centre: a prompt that was meant to name one object
    among several and is silently ignored produces a confident reconstruction of the wrong one.
    """
    width, height = size
    text = str(value or "").strip().lower()
    if text in ("", "centre", "center", "auto"):
        return ({"input_points": [[[width // 2, height // 2]]]},
                f"a point at the centre of the frame ({width // 2}, {height // 2})")

    parts = [p for p in text.replace(";", ",").split(",") if p.strip()]
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        raise PreprocessError(
            f"SAM_PROMPT '{value}' is not a coordinate; expected 'centre', 'x,y' for a point, or "
            f"'x0,y0,x1,y1' for a box, with every value normalised between 0 and 1")
    if any(n < 0.0 or n > 1.0 for n in numbers):
        raise PreprocessError(
            f"SAM_PROMPT '{value}' has a value outside 0-1; coordinates are normalised fractions of the "
            f"image, not pixels, so that one value holds at any resolution")

    def pixels(x, y):
        return [int(round(x * (width - 1))), int(round(y * (height - 1)))]

    if len(numbers) == 2:
        point = pixels(numbers[0], numbers[1])
        return {"input_points": [[point]]}, f"'{value}', a point at {tuple(point)}"
    if len(numbers) == 4:
        first, second = pixels(numbers[0], numbers[1]), pixels(numbers[2], numbers[3])
        box = [min(first[0], second[0]), min(first[1], second[1]),
               max(first[0], second[0]), max(first[1], second[1])]
        if box[0] == box[2] or box[1] == box[3]:
            raise PreprocessError(
                f"SAM_PROMPT '{value}' describes a box with no area; its two corners must differ in both "
                f"axes")
        return {"input_boxes": [[box]]}, f"'{value}', a box {tuple(box)}"

    raise PreprocessError(
        f"SAM_PROMPT '{value}' has {len(numbers)} coordinate(s); expected 'centre', two for a point, or "
        f"four for a box")


def _remove_background_sam(image_path: str, output_path: str, params: dict) -> None:
    """Segment Anything, prompted. The backend for choosing WHICH object rather than for matting an edge.

    What it adds over the other three is that it takes a prompt. They are salient-object detectors and
    decide the subject themselves, so on a photograph holding several objects they pick one and offer no way
    to ask for another. This one segments what the prompt points at. See resolve_sam_prompt.

    What it does NOT add is edge quality. SAM returns a BINARY mask, so there is no graded alpha through
    hair or a thin structure, where rembg and BiRefNet both produce a soft matte. Measured on a studio
    photograph of a person, BiRefNet carried 5,781 pixels of partial alpha; a SAM mask carries none by
    construction. For a model that reads the mask as a cutout that costs little, but it is the wrong
    instrument for a subject whose edge is the difficult part.

    SAM proposes three masks for one prompt at different scales -- roughly the part, the object and the
    group it belongs to -- and returns its own quality estimate for each, so the highest-scoring one is
    taken rather than the first.
    """
    from PIL import Image
    try:
        import numpy as np
        import torch
        from transformers import SamModel, SamProcessor
    except ImportError as e:
        raise PreprocessError(
            "Background removal backend 'sam' is unavailable: torch and transformers are required. Each "
            "model image carries its own transformers, and TripoSR's is older than SamModel needs -- see "
            "the note in requirements.txt for why one cannot simply be added to the shared layer") from e

    with Image.open(image_path) as img:
        source = img.convert("RGB")
    prompt, described = resolve_sam_prompt(params.get("SAM_PROMPT"), source.size)
    repo = str(params.get("SAM_MODEL_REPO") or SAM_MODEL_REPO).strip() or SAM_MODEL_REPO
    logger.info(f"Segmenting with SAM ({repo}) prompted by {described}")

    model = SamModel.from_pretrained(repo)
    processor = SamProcessor.from_pretrained(repo)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    model.eval()

    inputs = processor(source, return_tensors="pt", **prompt).to(device)
    with torch.no_grad():
        outputs = model(**inputs)
    masks = processor.image_processor.post_process_masks(
        outputs.pred_masks.cpu(), inputs["original_sizes"].cpu(), inputs["reshaped_input_sizes"].cpu())

    # One image, one prompt: (num_masks, height, width) with a score per mask. Checked rather than assumed,
    # because a shape change upstream would otherwise silently select along the wrong axis and return a
    # mask for something else.
    candidates = np.asarray(masks[0][0].numpy() if hasattr(masks[0][0], "numpy") else masks[0][0])
    scores = np.asarray(outputs.iou_scores[0][0].detach().cpu().numpy())

    # Released before the reconstruction model loads, and only once the masks and scores have been copied
    # out of it. This runs in the same container and on the same card, and the vit-huge encoder is about
    # 2.4 GB -- Step1X-3D's texture stage already fails with CUDA out of memory on a 22 GiB card unless its
    # own memory controls are applied, so leaving a segmenter resident through generation would take that
    # margin away. Upstream's own driver deletes its geometry pipeline before loading its texture stack for
    # the same reason.
    del model, outputs, inputs
    if device == "cuda":
        torch.cuda.empty_cache()

    if candidates.ndim != 3 or len(scores) != len(candidates):
        raise PreprocessError(
            f"SAM returned masks shaped {candidates.shape} with {len(scores)} score(s); expected one "
            f"score per mask over a (masks, height, width) array")

    best = int(scores.argmax())
    chosen = candidates[best].astype(bool)
    share = float(chosen.mean())
    logger.info(f"SAM proposed {len(candidates)} masks with scores "
                f"{[round(float(s), 3) for s in scores]}; taking mask {best}, covering {share:.2%} of the "
                f"frame")
    if not SAM_MIN_MASK_SHARE <= share <= SAM_MAX_MASK_SHARE:
        raise PreprocessError(
            f"The SAM mask prompted by {described} covers {share:.2%} of the frame, outside the "
            f"{SAM_MIN_MASK_SHARE:.1%}-{SAM_MAX_MASK_SHARE:.0%} a subject occupies. The prompt is most "
            f"likely on the background or on a speck; set SAM_PROMPT to a point or box on the object")

    cut = source.convert("RGBA")
    cut.putalpha(Image.fromarray((chosen * 255).astype(np.uint8), mode="L"))
    cut.save(output_path)


def _remove_background_rmbg2(image_path: str, output_path: str, params: dict) -> None:
    """BRIA RMBG-2.0 through transformers. An escalation backend for cluttered backgrounds."""
    from PIL import Image
    try:
        import torch
        from torchvision import transforms
        from transformers import AutoModelForImageSegmentation
    except ImportError as e:
        raise PreprocessError(
            "Background removal backend 'rmbg2' is unavailable: torch, torchvision and "
            "transformers are required") from e

    model = AutoModelForImageSegmentation.from_pretrained(
        "briaai/RMBG-2.0", trust_remote_code=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    model.eval()

    transform = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])

    with Image.open(image_path) as img:
        source = img.convert("RGB")
        tensor = transform(source).unsqueeze(0).to(device)
        with torch.no_grad():
            prediction = model(tensor)[-1].sigmoid().cpu()
        mask = transforms.ToPILImage()(prediction[0].squeeze()).resize(source.size)
        cut = source.convert("RGBA")
        cut.putalpha(mask)
    cut.save(output_path)


# Every backend takes ``params`` whether it reads it or not, so the dispatch has one shape. Only SAM uses
# it today, for its prompt.
_BACKENDS = {
    "rembg": _remove_background_rembg,
    "birefnet": _remove_background_birefnet,
    "rmbg2": _remove_background_rmbg2,
    "sam": _remove_background_sam,
}


def remove_background(image_path: str, work_dir: str, backend: str, params: dict = None) -> str:
    """Isolate the subject, returning the path to the image the model should consume.

    ``none`` returns the input path unchanged. A backend that cannot be imported raises rather
    than silently falling through to the raw photo, which would produce a mesh of the background.
    """
    backend = str(backend or "").strip().lower() or "rembg"
    if backend not in BACKGROUND_REMOVAL_BACKENDS:
        raise PreprocessError(
            f"Unknown background removal backend '{backend}'; "
            f"expected one of {', '.join(BACKGROUND_REMOVAL_BACKENDS)}")

    if backend == "none":
        logger.info("Background removal disabled; using the input image as supplied")
        return image_path

    os.makedirs(work_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(image_path))[0]
    output_path = os.path.join(work_dir, f"{stem}_nobg.png")

    logger.info(f"Removing the background with '{backend}': {image_path} -> {output_path}")
    _BACKENDS[backend](image_path, output_path, params or {})

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise PreprocessError(
            f"Background removal backend '{backend}' produced no output at {output_path}")
    logger.info(f"Background removed with '{backend}'")
    return output_path


def prepare_input_image(image_path: str, work_dir: str, backend: str, params: dict = None) -> tuple:
    """Validate then background-remove. Returns ``(prepared_path, image_info)``.

    ``params`` carries the run's settings for a backend that takes options of its own -- SAM reads its
    prompt from there. Optional, so a caller with nothing to pass need not invent a dict.
    """
    info = validate_image(image_path)
    prepared = remove_background(image_path, work_dir, backend, params)
    return prepared, info
