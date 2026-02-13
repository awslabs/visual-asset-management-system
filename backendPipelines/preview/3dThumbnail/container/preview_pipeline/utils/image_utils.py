# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import numpy as np
from PIL import Image
from .logging import get_logger

logger = get_logger()

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


def save_gif(frames, output_path, duration_ms=100, loop=0):
    """
    Save a list of numpy arrays as an animated GIF.
    frames: list of numpy arrays (H, W, 3) or (H, W, 4) uint8
    output_path: path to write .gif file
    duration_ms: per-frame duration in milliseconds
    loop: 0 = infinite loop
    """
    if not frames:
        raise ValueError("No frames provided to save_gif")

    # Convert frames to PIL images for optimization control
    pil_frames = [Image.fromarray(f) for f in frames]

    # Quantize to 256 colors per frame for smaller GIF
    pil_frames = [f.convert("P", palette=Image.ADAPTIVE, colors=256) for f in pil_frames]

    pil_frames[0].save(
        output_path,
        save_all=True,
        append_images=pil_frames[1:],
        duration=duration_ms,
        loop=loop,
        optimize=True,
    )
    logger.info(f"Saved GIF with {len(frames)} frames to {output_path} "
                f"({os.path.getsize(output_path) / 1024:.1f} KB)")


def save_jpeg(image_array, output_path, quality=85):
    """
    Save a single numpy array as a JPEG image.
    """
    img = Image.fromarray(image_array)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(output_path, "JPEG", quality=quality, optimize=True)
    logger.info(f"Saved JPEG to {output_path} ({os.path.getsize(output_path) / 1024:.1f} KB)")


def ensure_under_size_limit(frames, output_path, max_bytes=MAX_FILE_SIZE_BYTES):
    """
    Attempt to save frames as GIF under the size limit. If GIF is too large,
    progressively reduce quality and ultimately fall back to a single JPEG.

    Returns the final output path (may change extension if falling back to JPEG).
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

    # Strategy 4: Fall back to single optimized JPEG
    logger.info("Falling back to single JPEG frame...")
    # Use the middle frame for the best representative view
    middle_frame = frames[len(frames) // 2]
    jpeg_path = os.path.splitext(output_path)[0] + ".jpg"
    for quality in [85, 70, 55, 40]:
        save_jpeg(middle_frame, jpeg_path, quality=quality)
        if os.path.getsize(jpeg_path) <= max_bytes:
            # Remove the oversized GIF
            if os.path.exists(output_path) and output_path != jpeg_path:
                os.remove(output_path)
            return jpeg_path

    # Final: aggressively resize the JPEG
    img = Image.fromarray(middle_frame)
    img = img.resize((320, 240), Image.LANCZOS)
    save_jpeg(np.array(img), jpeg_path, quality=40)
    if os.path.exists(output_path) and output_path != jpeg_path:
        os.remove(output_path)
    return jpeg_path
