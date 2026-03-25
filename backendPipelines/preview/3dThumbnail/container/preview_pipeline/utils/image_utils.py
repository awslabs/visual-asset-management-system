# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import numpy as np
from PIL import Image
from .logging import get_logger

logger = get_logger()

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


def _threshold_alpha(a):
    """Return 255 if alpha is below 128, else 0. Used for GIF transparency mask."""
    return 255 if a < 128 else 0


def save_gif(frames, output_path, duration_ms=100, loop=0):
    """
    Save a list of numpy arrays as an animated GIF with transparency.
    frames: list of numpy arrays (H, W, 3) or (H, W, 4) uint8
    output_path: path to write .gif file
    duration_ms: per-frame duration in milliseconds
    loop: 0 = infinite loop
    """
    if not frames:
        raise ValueError("No frames provided to save_gif")

    has_alpha = frames[0].ndim == 3 and frames[0].shape[2] == 4

    pil_frames = []
    for f in frames:
        img = Image.fromarray(f)
        if img.mode == "RGBA" and has_alpha:
            # Convert RGBA to palette mode with transparency
            alpha = img.split()[3]
            p_img = img.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
            # Set transparency for pixels where alpha < 128
            mask = Image.fromarray(np.array(alpha.point(_threshold_alpha), dtype=np.uint8), mode="L")
            p_img.paste(255, mask)  # Index 255 = transparent
            pil_frames.append(p_img)
        else:
            pil_frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=256))

    save_kwargs = {
        "save_all": True,
        "append_images": pil_frames[1:],
        "duration": duration_ms,
        "loop": loop,
        "optimize": True,
    }

    if has_alpha:
        save_kwargs["transparency"] = 255
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


def ensure_under_size_limit(frames, output_path, max_bytes=MAX_FILE_SIZE_BYTES):
    """
    Attempt to save frames as GIF under the size limit. If GIF is too large,
    progressively reduce quality and ultimately fall back to a single PNG or JPEG.

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
    middle_frame = frames[len(frames) // 2]
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
