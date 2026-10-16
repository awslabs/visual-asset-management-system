# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import numpy as np
from PIL import Image
from .logging import get_logger

logger = get_logger()

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB

# GIF palette slot that marks a pixel transparent. A frame is quantised to at most this many colours
# (indices 0 .. _GIF_TRANSPARENT_INDEX - 1) and its palette is always written with all 256 entries,
# so the transparent index lies inside every frame's palette.
_GIF_TRANSPARENT_INDEX = 255
_GIF_PALETTE_ENTRIES = 256
# A pixel whose alpha is below this is written transparent; GIF transparency is binary.
_GIF_ALPHA_THRESHOLD = 128


def _rgba_to_gif_frame(rgba: np.ndarray) -> Image.Image:
    """One RGBA frame as a palette image whose transparent pixels use _GIF_TRANSPARENT_INDEX.

    The colours are quantised from the opaque pixels' RGB only, so the background does not take
    palette slots from the model, and the palette is padded to the full 256 entries so the
    transparent index is a valid entry of the frame's own palette.
    """
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    transparent = rgba[..., 3] < _GIF_ALPHA_THRESHOLD
    rgb = rgba[..., :3].copy()
    if transparent.any() and not transparent.all():
        # Fill the background with an opaque pixel's colour so it adds no colour to the palette.
        rgb[transparent] = rgb[~transparent][0]
    quantised = Image.fromarray(rgb).quantize(colors=_GIF_TRANSPARENT_INDEX)
    palette = (quantised.getpalette() or [])[: _GIF_TRANSPARENT_INDEX * 3]
    palette += [0] * (_GIF_PALETTE_ENTRIES * 3 - len(palette))

    indices = np.array(quantised, dtype=np.uint8)
    indices[transparent] = _GIF_TRANSPARENT_INDEX
    frame = Image.frombytes("P", (indices.shape[1], indices.shape[0]), indices.tobytes())
    frame.putpalette(palette)
    frame.info["transparency"] = _GIF_TRANSPARENT_INDEX
    return frame


def save_gif(frames, output_path, duration_ms=100, loop=0):
    """
    Save a list of numpy arrays as an animated GIF with transparency.
    frames: list of numpy arrays (H, W, 3) or (H, W, 4) uint8
    output_path: path to write .gif file
    duration_ms: per-frame duration in milliseconds
    loop: 0 = infinite loop

    RGBA frames keep their transparency on every frame. Palette optimisation stays off for them:
    Pillow's optimiser shrinks each frame's local palette to the colours it uses (4, 8, 128 entries)
    while still declaring index 255 transparent, an index outside that palette, which decoders
    treat inconsistently -- some show the background as opaque on those frames.
    """
    if not frames:
        raise ValueError("No frames provided to save_gif")

    has_alpha = frames[0].ndim == 3 and frames[0].shape[2] == 4

    if has_alpha:
        pil_frames = [_rgba_to_gif_frame(f) for f in frames]
    else:
        pil_frames = [Image.fromarray(f).convert("P", palette=Image.ADAPTIVE, colors=256)
                      for f in frames]

    save_kwargs = {
        "save_all": True,
        "append_images": pil_frames[1:],
        "duration": duration_ms,
        "loop": loop,
        "optimize": not has_alpha,
    }

    if has_alpha:
        save_kwargs["transparency"] = _GIF_TRANSPARENT_INDEX
        save_kwargs["disposal"] = 2  # Restore to background between frames

    pil_frames[0].save(output_path, **save_kwargs)
    logger.info(f"Saved GIF with {len(frames)} frames to {output_path} "
                f"({os.path.getsize(output_path) / 1024:.1f} KB)")


def save_png(image_array, output_path):
    """
    Save a single numpy array as a PNG image with transparency support.
    """
    img = Image.fromarray(image_array)
    img.save(output_path, "PNG", optimize=True)
    logger.info(f"Saved PNG to {output_path} ({os.path.getsize(output_path) / 1024:.1f} KB)")


def save_jpeg(image_array, output_path, quality=85):
    """
    Save a single numpy array as a JPEG image.
    JPEG does not support transparency -- alpha channel is composited onto white.
    """
    img = Image.fromarray(image_array)
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.save(output_path, "JPEG", quality=quality, optimize=True)
    logger.info(f"Saved JPEG to {output_path} ({os.path.getsize(output_path) / 1024:.1f} KB)")


def ensure_under_size_limit(frames, output_path, max_bytes=MAX_FILE_SIZE_BYTES, still_frame=None):
    """
    Attempt to save frames as GIF under the size limit. If GIF is too large,
    progressively reduce quality and ultimately fall back to a single PNG or JPEG.

    still_frame is the image the PNG/JPEG fallback writes (the caller passes the front
    three-quarter view); without it the fallback takes the middle frame.

    Returns the final output path (may change extension if falling back).
    """
    # Strategy 1: Full GIF with all frames
    save_gif(frames, output_path, duration_ms=100)
    if os.path.getsize(output_path) <= max_bytes:
        return output_path

    logger.info("GIF exceeds size limit, reducing frame count...")

    # Strategy 2: Reduce frame count progressively
    for target_count in [18, 12, 8]:
        if len(frames) <= target_count:
            continue
        step = max(1, len(frames) // target_count)
        reduced_frames = frames[::step]
        save_gif(reduced_frames, output_path, duration_ms=150)
        if os.path.getsize(output_path) <= max_bytes:
            return output_path

    # Strategy 3: Reduce resolution of frames
    logger.info("Reducing resolution...")
    for scale in [0.75, 0.5, 0.375]:
        reduced_frames = frames[::max(1, len(frames) // 8)]
        resized = []
        for f in reduced_frames:
            img = Image.fromarray(f)
            new_size = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new_size, Image.LANCZOS)
            resized.append(np.array(img))
        save_gif(resized, output_path, duration_ms=150)
        if os.path.getsize(output_path) <= max_bytes:
            return output_path

    # Strategy 4: Fall back to single PNG (preserves transparency)
    logger.info("Falling back to single PNG frame...")
    middle_frame = frames[len(frames) // 2] if still_frame is None else still_frame
    png_path = os.path.splitext(output_path)[0] + ".png"
    save_png(middle_frame, png_path)
    if os.path.getsize(png_path) <= max_bytes:
        if os.path.exists(output_path) and output_path != png_path:
            os.remove(output_path)
        return png_path

    # Strategy 5: Fall back to single JPEG (no transparency but smaller)
    logger.info("PNG too large, falling back to single JPEG frame...")
    jpeg_path = os.path.splitext(output_path)[0] + ".jpg"
    for quality in [85, 70, 55, 40]:
        save_jpeg(middle_frame, jpeg_path, quality=quality)
        if os.path.getsize(jpeg_path) <= max_bytes:
            for p in [output_path, png_path]:
                if os.path.exists(p) and p != jpeg_path:
                    os.remove(p)
            return jpeg_path

    # Final: aggressively resize the JPEG
    img = Image.fromarray(middle_frame)
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    img = img.resize((320, 240), Image.LANCZOS)
    save_jpeg(np.array(img), jpeg_path, quality=40)
    for p in [output_path, png_path]:
        if os.path.exists(p) and p != jpeg_path:
            os.remove(p)
    return jpeg_path
