# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Raster image reader for the media previews: PNG, JPEG, GIF, WebP, BMP, TGA and TIFF.

Every reader here decodes untrusted files, so the pixel count is read from the header and checked
against the task's budget before anything is decoded (a decompression bomb is refused without
allocating its raster), a JPEG is decoded at reduced size when only a thumbnail is wanted, and a
multi-page TIFF contributes one page. Pillow reads what it can; a TIFF it cannot read (16-bit colour,
float samples, some compressions) falls back to ``tifffile``.

The output of ``load_raster`` is an 8-bit RGB or RGBA Pillow image with the file's EXIF orientation
applied: 16-bit, 32-bit and float samples are scaled to 8 bits from their 1st to 99th percentile, and
CMYK, YCbCr and LAB are converted to RGB (through the embedded ICC profile when there is one).
"""

import io
import os
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageOps

from ..utils import image_utils
from ..utils.logging import get_logger

logger = get_logger()

# The accepted image input extensions. Drift-guarded against the media workflow's filters.
SUPPORTED_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tga", ".tif", ".tiff",
})
TIFF_EXTENSIONS = frozenset({".tif", ".tiff"})
ANIMATED_EXTENSIONS = frozenset({".gif", ".webp"})
# The accepted extensions the web shows as their own preview when the file is under the size limit
# (the backend's ALLOWED_PREVIEW_FILE_EXTENSIONS less .svg, which the pipeline does not accept).
# TGA and TIFF are not here: browsers cannot display them. A drift test pins this to the backend list.
SELF_PREVIEWABLE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

# Frames an animated image contributes: at most this many are kept (evenly spaced across it) and no
# more than this many are walked.
MAX_ANIMATION_FRAMES = 300
MAX_ANIMATION_FRAMES_SCANNED = 5000
ANIMATION_DECODE_SECONDS = 120.0
DEFAULT_FRAME_MS = 100
# A multi-page TIFF's pages looked at for the first one that is not a reduced-resolution copy.
MAX_TIFF_PAGES_CHECKED = 16

_TYPE_NAMES = {
    ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".gif": "GIF", ".webp": "WebP", ".bmp": "BMP",
    ".tga": "TGA", ".tif": "TIFF", ".tiff": "TIFF",
}
# EXIF/TIFF orientation tag -> the Pillow transpose that makes the image upright.
_ORIENTATION_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
_ORIENTATION_TAG = 0x0112
_TIFF_SUBFILE_TYPE_TAG = 254
_TIFF_REDUCED_IMAGE = 1
# Samples looked at to find a high-bit-depth image's percentile range.
_PERCENTILE_SAMPLE = 4_000_000


class MediaError(Exception):
    """A media file the pipeline cannot turn into a preview; the message names the file type."""


class ImageTooLarge(MediaError):
    """The image holds more pixels than the task's budget."""


def type_name(ext: str) -> str:
    return _TYPE_NAMES.get((ext or "").lower(), (ext or "").lstrip(".").upper() or "image")


def can_handle(ext: str) -> bool:
    return (ext or "").lower() in SUPPORTED_EXTENSIONS


def is_self_previewable(ext: str, size: Optional[int]) -> bool:
    """True when the web shows a file of this type and size as its own preview: a displayable format
    whose size is known and strictly under the preview size limit."""
    return (size is not None and (ext or "").lower() in SELF_PREVIEWABLE_EXTENSIONS
            and size < image_utils.MAX_FILE_SIZE_BYTES)


def check_pixels(width: int, height: int, max_pixels: int, ext: str = "") -> None:
    """Raise ImageTooLarge when ``width`` x ``height`` exceeds ``max_pixels``."""
    pixels = int(width) * int(height)
    if pixels > max_pixels:
        raise ImageTooLarge(
            f"The {type_name(ext)} image is {width} x {height} ({pixels:,} pixels), more than the "
            f"{max_pixels:,} pixels this task can decode. Reduce the image size or use a larger "
            f"compute size.")


# ---------------------------------------------------------------------------------------------------
# Sample and colour conversion
# ---------------------------------------------------------------------------------------------------

def _percentile_range(values: np.ndarray) -> Tuple[float, float]:
    """The 1st and 99th percentile of the finite values (a strided sample of a large array); the
    minimum and maximum when those are equal."""
    flat = values.reshape(-1)
    stride = max(1, flat.size // _PERCENTILE_SAMPLE)
    sample = flat[::stride].astype(np.float32, copy=False)
    sample = sample[np.isfinite(sample)]
    if sample.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(sample, [1.0, 99.0])
    if hi <= lo:
        lo, hi = float(sample.min()), float(sample.max())
    return float(lo), float(hi)


def scale_to_uint8(values: np.ndarray) -> np.ndarray:
    """Samples of any dtype as 8-bit: uint8 as it is; other integers and floats scaled linearly from
    their 1st to 99th percentile to 0..255 (clipped), NaN and infinity as 0."""
    if values.dtype == np.uint8:
        return values
    lo, hi = _percentile_range(values)
    work = np.nan_to_num(values.astype(np.float32), nan=lo, posinf=hi, neginf=lo)
    if hi <= lo:
        return np.full(values.shape, 128, dtype=np.uint8)
    work -= lo
    work *= 255.0 / (hi - lo)
    np.clip(work, 0, 255, out=work)
    return work.astype(np.uint8)


def _icc_to_srgb(img: Image.Image, mode: str = "RGB") -> Optional[Image.Image]:
    """The image converted to sRGB through its embedded ICC profile, or None when it has none or the
    conversion fails."""
    profile = img.info.get("icc_profile")
    if not profile:
        return None
    try:
        from PIL import ImageCms
        source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        return ImageCms.profileToProfile(img, source, ImageCms.createProfile("sRGB"), outputMode=mode)
    except Exception as e:
        logger.warning(f"ICC conversion failed, converting without the profile: {type(e).__name__}")
        return None


def _lab_to_rgb(img: Image.Image) -> Image.Image:
    try:
        from PIL import ImageCms
        return ImageCms.profileToProfile(img, ImageCms.createProfile("LAB"),
                                         ImageCms.createProfile("sRGB"), outputMode="RGB")
    except Exception as e:
        logger.warning(f"LAB conversion failed, showing lightness only: {type(e).__name__}")
        return img.getchannel("L").convert("RGB")


def to_rgb8(img: Image.Image) -> Image.Image:
    """The image as 8-bit RGB or RGBA (RGBA only when it carries transparency)."""
    mode = img.mode
    if mode in ("RGB", "RGBA"):
        return img
    if mode in ("L", "1"):
        return img.convert("RGB")
    if mode in ("LA", "La", "RGBa"):
        return img.convert("RGBA")
    if mode in ("P", "PA"):
        return img.convert("RGBA" if (mode == "PA" or "transparency" in img.info) else "RGB")
    if mode == "CMYK":
        return _icc_to_srgb(img) or img.convert("RGB")
    if mode == "LAB":
        return _lab_to_rgb(img)
    if mode in ("I;16", "I;16L", "I;16B", "I;16N", "I", "F"):
        gray = scale_to_uint8(np.asarray(img))
        return Image.fromarray(gray, "L").convert("RGB")
    return img.convert("RGB")


def apply_orientation(img: Image.Image, orientation: Optional[int]) -> Image.Image:
    """The image made upright from its EXIF/TIFF orientation value (1 or unknown: unchanged)."""
    transpose = _ORIENTATION_TRANSPOSE.get(orientation or 1)
    return img.transpose(transpose) if transpose is not None else img


# ---------------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------------

def _select_tiff_page(img: Image.Image) -> None:
    """Seek ``img`` to the first page of a multi-page TIFF that is not a reduced-resolution copy (a
    thumbnail or a pyramid level); page 0 when every page is marked reduced."""
    pages = getattr(img, "n_frames", 1)
    for index in range(min(pages, MAX_TIFF_PAGES_CHECKED)):
        img.seek(index)
        subfile = img.tag_v2.get(_TIFF_SUBFILE_TYPE_TAG, 0) if hasattr(img, "tag_v2") else 0
        if not (subfile & _TIFF_REDUCED_IMAGE):
            return
    img.seek(0)


def _load_with_pillow(path: str, ext: str, max_pixels: int, draft_edge: Optional[int]) -> Image.Image:
    # Pillow's own bomb check runs inside Image.open and raises before the size can be read, so it is
    # off for the open and back on (at the budget) for the decode; check_pixels states the refusal.
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as img:
        Image.MAX_IMAGE_PIXELS = max_pixels
        check_pixels(img.width, img.height, max_pixels, ext)
        if ext in TIFF_EXTENSIONS:
            _select_tiff_page(img)
            check_pixels(img.width, img.height, max_pixels, ext)
        elif getattr(img, "n_frames", 1) > 1:
            img.seek(0)
        # Pillow's TIFF reader already turns the image upright from the Orientation tag; every other
        # format is turned here from its EXIF orientation.
        orientation = None if ext in TIFF_EXTENSIONS else img.getexif().get(_ORIENTATION_TAG)
        if draft_edge and img.format == "JPEG":
            # Decode at 1/2, 1/4 or 1/8 scale when the result stays at least ``draft_edge`` on its long edge.
            img.draft("RGB" if img.mode in ("RGB", "YCbCr", "L") else None, (draft_edge, draft_edge))
        img.load()
        converted = to_rgb8(img)
        if converted is img:
            converted = img.copy()
    return apply_orientation(converted, orientation)


def _array_to_image(array: np.ndarray, photometric: str, colormap=None) -> Image.Image:
    """A TIFF page's samples as an 8-bit RGB/RGBA Pillow image, from its photometric interpretation."""
    if photometric == "PALETTE" and colormap is not None and array.ndim == 2:
        table = (np.asarray(colormap).astype(np.uint32) >> 8).astype(np.uint8)  # (3, N) 16-bit -> 8-bit
        return Image.fromarray(table.T[np.clip(array, 0, table.shape[1] - 1)], "RGB")
    if array.ndim == 3 and array.shape[0] in (1, 2, 3, 4, 5) and array.shape[0] < min(array.shape[1:]):
        array = np.moveaxis(array, 0, -1)  # planar samples (S, H, W) -> (H, W, S)
    if photometric in ("CIELAB", "ICCLAB", "ITULAB") and array.ndim == 3 and array.shape[2] >= 3:
        lab = array[..., :3].astype(np.uint8)
        if array.dtype == np.uint8:
            lab[..., 1:] = (lab[..., 1:].view(np.int8).astype(np.int16) + 128).astype(np.uint8)
        return _lab_to_rgb(Image.fromarray(lab, "LAB"))
    samples = scale_to_uint8(array)
    if samples.ndim == 3 and samples.shape[2] == 1:
        samples = samples[..., 0]
    if samples.ndim == 2:
        if photometric == "MINISWHITE":
            samples = 255 - samples
        return Image.fromarray(samples, "L").convert("RGB")
    channels = samples.shape[2]
    if channels == 2:
        return Image.fromarray(samples, "LA").convert("RGBA")
    if channels == 3:
        return Image.fromarray(np.ascontiguousarray(samples), "RGB")
    if photometric == "SEPARATED":
        cmyk = Image.fromarray(np.ascontiguousarray(samples[..., :4]), "CMYK")
        return cmyk.convert("RGB")
    return Image.fromarray(np.ascontiguousarray(samples[..., :4]), "RGBA")


def _load_with_tifffile(path: str, max_pixels: int) -> Image.Image:
    import tifffile
    with tifffile.TiffFile(path) as tif:
        page = None
        for index in range(min(len(tif.pages), MAX_TIFF_PAGES_CHECKED)):
            candidate = tif.pages[index]
            if not getattr(candidate, "is_reduced", False):
                page = candidate
                break
        page = page if page is not None else tif.pages[0]
        height, width = page.imagelength, page.imagewidth
        check_pixels(width, height, max_pixels, ".tif")
        array = page.asarray()
        photometric = getattr(page.photometric, "name", str(page.photometric))
        image = _array_to_image(array, photometric, getattr(page, "colormap", None))
        orientation = getattr(page.tags.get("Orientation"), "value", None) if hasattr(page, "tags") else None
    return apply_orientation(image, orientation)


def load_raster(path: str, ext: str, max_pixels: int, draft_edge: Optional[int] = None) -> Image.Image:
    """The first (or first full-resolution) image of ``path`` as an upright 8-bit RGB/RGBA image.

    ``draft_edge`` lets a JPEG decode at reduced size while its long edge stays at least that long.
    Raises ImageTooLarge over the pixel budget, MediaError for a file that cannot be read."""
    ext = (ext or os.path.splitext(path)[1]).lower()
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels  # Pillow's own bomb check, a second line behind check_pixels
    try:
        try:
            return _load_with_pillow(path, ext, max_pixels, draft_edge)
        except ImageTooLarge:
            raise
        except Exception as pillow_error:
            if ext not in TIFF_EXTENSIONS:
                raise MediaError(_read_failure(ext, pillow_error)) from pillow_error
            logger.info(f"Pillow could not read the TIFF ({type(pillow_error).__name__}); trying tifffile")
            try:
                return _load_with_tifffile(path, max_pixels)
            except ImageTooLarge:
                raise
            except Exception as tiff_error:
                raise MediaError(_read_failure(ext, tiff_error)) from tiff_error
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def _read_failure(ext: str, error: Exception) -> str:
    detail = str(error).strip().splitlines()[0][:200] if str(error).strip() else type(error).__name__
    return f"The {type_name(ext)} file could not be read as an image ({type(error).__name__}: {detail})"


# ---------------------------------------------------------------------------------------------------
# Animated GIF and WebP
# ---------------------------------------------------------------------------------------------------

def frame_count(path: str, ext: str) -> int:
    """The number of frames of a GIF or WebP (1 for any other file or one that cannot be read)."""
    if (ext or "").lower() not in ANIMATED_EXTENSIONS:
        return 1
    try:
        with Image.open(path) as img:
            return max(1, int(getattr(img, "n_frames", 1)))
    except Exception:
        return 1


def load_animation(path: str, ext: str, max_pixels: int, max_frames: int = MAX_ANIMATION_FRAMES,
                   long_edge: int = image_utils.ANIMATED_MAX_LONG_EDGE,
                   clock=None) -> Tuple[List[Image.Image], List[int]]:
    """Frames and per-frame durations (ms) of an animated GIF or WebP, as RGB images (transparency on
    white) scaled to ``long_edge``.

    At most ``max_frames`` frames are kept, evenly spaced across the animation; each carries the summed
    duration of the frames up to the next one kept, so the loop keeps its length. Only the first
    MAX_ANIMATION_FRAMES_SCANNED frames are walked, and walking stops after ANIMATION_DECODE_SECONDS."""
    import time
    clock = clock or time.monotonic
    started = clock()
    ext = ext.lower()
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(path) as img:
            Image.MAX_IMAGE_PIXELS = max_pixels
            check_pixels(img.width, img.height, max_pixels, ext)
            total = min(max(1, int(getattr(img, "n_frames", 1))), MAX_ANIMATION_FRAMES_SCANNED)
            keep = set(image_utils.sample_frames(total, max_frames))
            kept, durations = [], []
            walked = 0
            for index in range(total):
                if clock() - started > ANIMATION_DECODE_SECONDS:
                    logger.warning(f"Animation decode stopped at frame {index} of {total} "
                                   f"(time limit {ANIMATION_DECODE_SECONDS:.0f} s)")
                    break
                img.seek(index)
                duration = int(img.info.get("duration") or DEFAULT_FRAME_MS)
                durations.append(max(duration, image_utils.MIN_GIF_FRAME_MS))
                walked += 1
                if index in keep:
                    frame = image_utils.flatten_on_white(img.convert("RGBA"))
                    kept.append((index, image_utils.fit_long_edge(frame, long_edge).copy()))
    except ImageTooLarge:
        raise
    except Exception as e:
        raise MediaError(_read_failure(ext, e)) from e
    finally:
        Image.MAX_IMAGE_PIXELS = previous
    if not kept:
        raise MediaError(f"The {type_name(ext)} file holds no readable frames")
    frames, summed = [], []
    for position, (index, frame) in enumerate(kept):
        end = kept[position + 1][0] if position + 1 < len(kept) else walked
        frames.append(frame)
        summed.append(sum(durations[index:end]))
    return frames, summed
