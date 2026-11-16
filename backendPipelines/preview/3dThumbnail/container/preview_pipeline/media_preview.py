# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Media previews: a preview file for an image, a TGA or TIFF, or a video.

The decision table (what the run writes) is the one in the pipeline README:

| Input                                         | Option off              | Option on                        |
| --------------------------------------------- | ----------------------- | -------------------------------- |
| displayable image, under the size limit       | nothing (web shows it)  | a JPEG when it is meaningfully smaller |
| displayable still image, at or over the limit | JPEG under the limit    | same                             |
| animated GIF/WebP at or over the limit        | smaller GIF, else JPEG  | same                             |
| TGA/TIFF, any size                            | JPEG under the limit    | same                             |
| video                                         | GIF under the limit, else JPEG | same                      |
| audio-only / no frames                        | success, no preview     | same                             |

"Option" is ``mediaPreviews.downscaleSelfPreviewableImages``, which reaches the container as the
``VAMS_DOWNSCALE_SELF_PREVIEWABLE_IMAGES`` environment variable. Every file written is strictly under
the preview size limit (``image_utils.MAX_FILE_SIZE_BYTES``).
"""

import os
from dataclasses import dataclass
from typing import Optional

from PIL import Image

from .format_handlers import image_handler, video_handler
from .format_handlers.image_handler import MediaError
from .utils import image_utils
from .utils.logging import get_logger

logger = get_logger()

DOWNSCALE_ENV = "VAMS_DOWNSCALE_SELF_PREVIEWABLE_IMAGES"
# A downscaled preview of an image the web already shows must be smaller than the original by this
# share, unless the original is larger than DOWNSCALE_LONG_EDGE_TRIGGER on its long edge.
MIN_DOWNSCALE_SAVING = 0.25
DOWNSCALE_LONG_EDGE_TRIGGER = image_utils.DOWNSCALED_SELF_PREVIEW_LONG_EDGE


@dataclass
class MediaResult:
    """What a media run produced: the preview file's path, or None with the message to report."""
    path: Optional[str]
    message: Optional[str] = None


def media_extensions():
    """Every extension the media previews accept (images and video)."""
    return image_handler.SUPPORTED_EXTENSIONS | video_handler.SUPPORTED_EXTENSIONS


def is_media(ext: str) -> bool:
    return (ext or "").lower() in media_extensions()


def downscale_option_enabled(environ=None) -> bool:
    """Whether ``mediaPreviews.downscaleSelfPreviewableImages`` is on for this task (off when the
    variable is missing or anything but ``true``)."""
    value = (environ if environ is not None else os.environ).get(DOWNSCALE_ENV, "")
    return str(value).strip().lower() == "true"


def self_preview_message(name: str) -> str:
    return f"'{name}' is shown as its own preview; no preview written."


def _limit() -> int:
    """The size every preview must be strictly under (read at call time)."""
    return image_utils.MAX_FILE_SIZE_BYTES


def _output_path(output_dir: str, basename: str, extension: str) -> str:
    return os.path.join(output_dir, f"{basename}.previewFile.{extension}")


def _write_still(img: Image.Image, output_dir: str, basename: str, output_type: str,
                 max_long_edge: int) -> str:
    """A still preview of ``img``: a PNG for ``outputType`` png when one fits the limit, else a JPEG."""
    if output_type == "png":
        written = image_utils.save_png_under_limit(img, _output_path(output_dir, basename, "png"),
                                                   max_bytes=_limit(), max_long_edge=max_long_edge)
        if written:
            return written
        logger.info("No PNG fits the size limit; writing a JPEG")
    return image_utils.save_jpeg_under_limit(img, _output_path(output_dir, basename, "jpg"),
                                             max_bytes=_limit(), max_long_edge=max_long_edge)


def generate(local_filepath: str, ext: str, output_type: str, output_dir: str, basename: str,
             pixel_budget: int, original_size: Optional[int] = None,
             downscale_self_previewable: bool = False) -> MediaResult:
    """Write the preview for the media file at ``local_filepath`` into ``output_dir`` and return it.

    ``basename`` is the input file's name (the preview is ``<basename>.previewFile.<ext>``).
    ``original_size`` is the input's size in bytes, which decides whether the web shows the file as its
    own preview. Raises MediaError (ImageTooLarge included) for a file that cannot be read."""
    ext = ext.lower()
    if video_handler.can_handle(ext):
        return _video_preview(local_filepath, ext, output_type, output_dir, basename)
    if not image_handler.can_handle(ext):
        raise MediaError(f"Unsupported media format: {ext}")
    if original_size is None:
        original_size = os.path.getsize(local_filepath)
    return _image_preview(local_filepath, ext, output_type, output_dir, basename, pixel_budget,
                          original_size, downscale_self_previewable)


def _image_preview(path, ext, output_type, output_dir, basename, pixel_budget, original_size,
                   downscale_self_previewable) -> MediaResult:
    if image_handler.is_self_previewable(ext, original_size):
        # An animated GIF/WebP is left alone even with the option on: a JPEG would drop the animation.
        if not downscale_self_previewable or (ext in image_handler.ANIMATED_EXTENSIONS
                                              and image_handler.frame_count(path, ext) > 1):
            return MediaResult(None, self_preview_message(basename))
        return _downscaled_self_preview(path, ext, output_dir, basename, pixel_budget, original_size)

    if output_type in ("auto", "gif") and ext in image_handler.ANIMATED_EXTENSIONS \
            and image_handler.frame_count(path, ext) > 1:
        animated = _animated_preview(path, ext, output_dir, basename, pixel_budget)
        if animated is not None:
            return MediaResult(animated)
    if output_type == "gif" and ext not in image_handler.ANIMATED_EXTENSIONS:
        logger.info(f"outputType gif does not apply to a still {image_handler.type_name(ext)} image; "
                    f"writing a JPEG")
    image = image_handler.load_raster(path, ext, pixel_budget,
                                      draft_edge=image_utils.STILL_MAX_LONG_EDGE)
    return MediaResult(_write_still(image, output_dir, basename, output_type,
                                    image_utils.STILL_MAX_LONG_EDGE))


def _animated_preview(path, ext, output_dir, basename, pixel_budget) -> Optional[str]:
    """A smaller animated GIF of a large animated GIF/WebP, or None when no rung of the ladder fits
    (the caller then writes the first frame as a JPEG)."""
    frames, durations = image_handler.load_animation(path, ext, pixel_budget)
    return image_utils.encode_animated_under_limit(
        frames, durations, _output_path(output_dir, basename, "gif"), max_bytes=_limit())


def _downscaled_self_preview(path, ext, output_dir, basename, pixel_budget, original_size) -> MediaResult:
    """With ``downscaleSelfPreviewableImages`` on: a JPEG of an image the web already shows, written
    only when it saves at least MIN_DOWNSCALE_SAVING of the original's size or the original's long
    edge is over DOWNSCALE_LONG_EDGE_TRIGGER."""
    image = image_handler.load_raster(path, ext, pixel_budget,
                                      draft_edge=image_utils.DOWNSCALED_SELF_PREVIEW_LONG_EDGE)
    # The draft decode may already have reduced a JPEG, so the long edge is read from the file.
    with Image.open(path) as original:
        original_long_edge = max(original.size)
    target = _output_path(output_dir, basename, "jpg")
    image_utils.save_jpeg_under_limit(image, target, max_bytes=_limit(),
                                      max_long_edge=image_utils.DOWNSCALED_SELF_PREVIEW_LONG_EDGE)
    size = os.path.getsize(target)
    saves_enough = size <= original_size * (1 - MIN_DOWNSCALE_SAVING)
    if saves_enough or original_long_edge > DOWNSCALE_LONG_EDGE_TRIGGER:
        return MediaResult(target)
    os.remove(target)
    return MediaResult(None, f"No smaller preview possible for '{basename}'; no preview written.")


def _video_preview(path, ext, output_type, output_dir, basename) -> MediaResult:
    try:
        frames, _duration = video_handler.sample_frames(path, ext)
    except video_handler.NoVideoStream:
        return MediaResult(None, f"No video stream in '{basename}'; no preview written.")
    middle = frames[len(frames) // 2]
    if output_type in ("jpg", "png"):
        return MediaResult(_write_still(middle, output_dir, basename, output_type,
                                        image_utils.STILL_MAX_LONG_EDGE))
    durations = [video_handler.GIF_FRAME_MS] * len(frames)
    animated = None
    if len(frames) > 1:
        animated = image_utils.encode_animated_under_limit(
            frames, durations, _output_path(output_dir, basename, "gif"), max_bytes=_limit())
    elif output_type == "gif":
        logger.info("The video yielded one frame; writing it as a still")
    if animated is not None:
        return MediaResult(animated)
    return MediaResult(_write_still(middle, output_dir, basename, "jpg",
                                    image_utils.STILL_MAX_LONG_EDGE))
