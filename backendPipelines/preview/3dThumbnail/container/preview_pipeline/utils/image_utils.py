# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import time
import numpy as np
from PIL import Image
from .logging import get_logger

logger = get_logger()

# The size limit of a preview file: 5 MiB, and a preview must be STRICTLY UNDER it. It is the same limit
# as PREVIEW_FILE_SIZE_LIMIT_BYTES in backend/backend/common/s3PathPatterns.py and in
# web/src/common/constants/fileFormats.ts, where a file under it is shown as its own preview. A drift
# test keeps the three equal.
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024

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
    Attempt to save frames as GIF under the size limit (strictly under: ``< max_bytes``). If GIF is too large,
    progressively reduce quality and ultimately fall back to a single PNG or JPEG.

    still_frame is the image the PNG/JPEG fallback writes (the caller passes the front
    three-quarter view); without it the fallback takes the middle frame.

    Returns the final output path (may change extension if falling back).
    """
    # Strategy 1: Full GIF with all frames
    save_gif(frames, output_path, duration_ms=100)
    if os.path.getsize(output_path) < max_bytes:
        return output_path

    logger.info("GIF exceeds size limit, reducing frame count...")

    # Strategy 2: Reduce frame count progressively
    for target_count in [18, 12, 8]:
        if len(frames) <= target_count:
            continue
        step = max(1, len(frames) // target_count)
        reduced_frames = frames[::step]
        save_gif(reduced_frames, output_path, duration_ms=150)
        if os.path.getsize(output_path) < max_bytes:
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
        if os.path.getsize(output_path) < max_bytes:
            return output_path

    # Strategy 4: Fall back to single PNG (preserves transparency)
    logger.info("Falling back to single PNG frame...")
    middle_frame = frames[len(frames) // 2] if still_frame is None else still_frame
    png_path = os.path.splitext(output_path)[0] + ".png"
    save_png(middle_frame, png_path)
    if os.path.getsize(png_path) < max_bytes:
        if os.path.exists(output_path) and output_path != png_path:
            os.remove(output_path)
        return png_path

    # Strategy 5: Fall back to single JPEG (no transparency but smaller)
    logger.info("PNG too large, falling back to single JPEG frame...")
    jpeg_path = os.path.splitext(output_path)[0] + ".jpg"
    for quality in [85, 70, 55, 40]:
        save_jpeg(middle_frame, jpeg_path, quality=quality)
        if os.path.getsize(jpeg_path) < max_bytes:
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


# ---------------------------------------------------------------------------------------------------
# Media previews: stills (images, TGA/TIFF, a video's middle frame) and animated GIFs from images and
# video. Every output is STRICTLY UNDER the size limit (``< max_bytes``).
# ---------------------------------------------------------------------------------------------------
# The long edge a still preview starts from, and the one a downscaled copy of a self-previewable image
# starts from (the ``downscaleSelfPreviewableImages`` option).
STILL_MAX_LONG_EDGE = 4096
DOWNSCALED_SELF_PREVIEW_LONG_EDGE = 2048
# A still is tried at each long edge in turn (never above the image's own), and at each edge at these
# JPEG qualities, best first: quality drops before the size does.
_STILL_LONG_EDGES = (4096, 3072, 2048, 1536, 1024, 768, 512, 384, 256, 192, 128, 96, 64)
_JPEG_QUALITIES = (88, 75, 60)

# The animated ladder (reduce frames first, then size, then palette). Each rung is
# (frames, long edge, palette colours); the first output strictly under the limit is kept.
ANIMATED_MAX_LONG_EDGE = 800
ANIMATED_LADDER = (
    (None, 800, 256), (36, 800, 256), (24, 800, 256), (12, 800, 256), (8, 800, 256),
    (8, 640, 256), (8, 480, 256), (8, 320, 256),
    (8, 320, 128), (8, 320, 64),
)
MAX_ANIMATED_ENCODES = 12
MAX_ANIMATED_ENCODE_SECONDS = 60.0
# GIF stores each delay in hundredths of a second, and decoders treat a delay under 20 ms as 100 ms.
MIN_GIF_FRAME_MS = 20


def flatten_on_white(img: Image.Image) -> Image.Image:
    """The image as 8-bit RGB, with any transparency composited on white."""
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA", "La", "RGBa", "PA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def fit_long_edge(img: Image.Image, long_edge: int) -> Image.Image:
    """The image scaled down so its long edge is at most ``long_edge`` (high-quality resampling); the
    image itself when it already fits. Never scales up."""
    width, height = img.size
    longest = max(width, height)
    if longest <= long_edge:
        return img
    scale = long_edge / longest
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    if img.mode in ("P", "1"):
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    return img.resize(size, Image.LANCZOS, reducing_gap=2.0)


def save_jpeg_under_limit(img: Image.Image, output_path: str, max_bytes: int = MAX_FILE_SIZE_BYTES,
                          max_long_edge: int = STILL_MAX_LONG_EDGE) -> str:
    """Write ``img`` as a JPEG strictly under ``max_bytes``: transparency on white, the long edge at
    most ``max_long_edge``, then quality steps at each smaller size until it fits. Returns
    ``output_path``; raises ValueError when not even the smallest size fits (a pathological limit)."""
    base = flatten_on_white(img)
    for edge in [max_long_edge] + [e for e in _STILL_LONG_EDGES if e < max_long_edge]:
        candidate = fit_long_edge(base, edge)
        for quality in _JPEG_QUALITIES:
            candidate.save(output_path, "JPEG", quality=quality, optimize=True)
            size = os.path.getsize(output_path)
            if size < max_bytes:
                logger.info(f"Saved JPEG {candidate.width}x{candidate.height} quality {quality} "
                            f"({size / 1024:.1f} KB) to {output_path}")
                return output_path
    raise ValueError(f"No JPEG under {max_bytes} bytes could be written")


def save_png_under_limit(img: Image.Image, output_path: str, max_bytes: int = MAX_FILE_SIZE_BYTES,
                         max_long_edge: int = STILL_MAX_LONG_EDGE):
    """Write ``img`` as a PNG strictly under ``max_bytes`` (transparency kept), trying the long edge
    ``max_long_edge`` then three smaller ones. Returns ``output_path``, or None when none fits (the
    caller writes a JPEG instead)."""
    for edge in (max_long_edge, max_long_edge // 2, max_long_edge // 4, max_long_edge // 8):
        candidate = fit_long_edge(img, max(1, edge))
        if candidate.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            candidate = candidate.convert("RGBA" if "transparency" in candidate.info else "RGB")
        candidate.save(output_path, "PNG", optimize=True)
        if os.path.getsize(output_path) < max_bytes:
            logger.info(f"Saved PNG {candidate.width}x{candidate.height} "
                        f"({os.path.getsize(output_path) / 1024:.1f} KB) to {output_path}")
            return output_path
    if os.path.exists(output_path):
        os.remove(output_path)
    return None


def sample_frames(count: int, wanted: int):
    """Indices of ``wanted`` of ``count`` frames, evenly spaced and always including the first.
    ``wanted`` of None, or at least ``count``, takes every frame."""
    if wanted is None or wanted >= count:
        return list(range(count))
    if wanted <= 1:
        return [0]
    return sorted({int(round(i * (count - 1) / (wanted - 1))) for i in range(wanted)})


def sample_with_durations(frames, durations, wanted):
    """``wanted`` of the frames, evenly spaced, each carrying the summed duration of the frames from it
    up to the next one chosen, so the animation's loop length is kept."""
    indices = sample_frames(len(frames), wanted)
    if len(indices) == len(frames):
        return list(frames), list(durations)
    chosen, summed = [], []
    for position, index in enumerate(indices):
        end = indices[position + 1] if position + 1 < len(indices) else len(frames)
        chosen.append(frames[index])
        summed.append(sum(durations[index:end]))
    return chosen, summed


def _gif_frame(img: Image.Image, colours: int) -> Image.Image:
    """One frame as a palette image of at most ``colours`` colours (transparency on white)."""
    rgb = flatten_on_white(img)
    return rgb.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)


def write_animated_gif(frames, durations_ms, output_path: str, colours: int = 256):
    """Write the frames (PIL images) as an infinitely looping GIF."""
    if not frames:
        raise ValueError("No frames provided")
    palette_frames = [_gif_frame(f, colours) for f in frames]
    delays = [max(MIN_GIF_FRAME_MS, int(round(d / 10.0)) * 10) for d in durations_ms]
    palette_frames[0].save(output_path, save_all=True, append_images=palette_frames[1:],
                           duration=delays, loop=0, optimize=False, disposal=1)


def encode_animated_under_limit(frames, durations_ms, output_path: str,
                                max_bytes: int = MAX_FILE_SIZE_BYTES,
                                ladder=ANIMATED_LADDER, max_encodes: int = MAX_ANIMATED_ENCODES,
                                max_seconds: float = MAX_ANIMATED_ENCODE_SECONDS,
                                clock=time.monotonic):
    """Write an animated GIF strictly under ``max_bytes`` from ``frames`` (PIL images) and their
    per-frame ``durations_ms``, or return None when no rung of the ladder fits.

    The ladder reduces the frame count first (every frame, then 36, 24, 12, 8 evenly sampled, their
    durations summed so the loop keeps its length), then the long edge (800, 640, 480, 320), then the
    palette (256, 128, 64). It stops at the first rung that fits, after ``max_encodes`` encodes or after
    ``max_seconds`` of encoding. A frame already smaller than a rung's long edge is not scaled up."""
    started = clock()
    encodes = 0
    resized = {}
    tried = set()
    longest = max(max(f.size) for f in frames)
    for wanted, edge, colours in ladder:
        if encodes >= max_encodes or clock() - started > max_seconds:
            logger.info("Animated GIF ladder stopped at its encode or time cap")
            break
        chosen, delays = sample_with_durations(frames, durations_ms, wanted)
        # A rung that would repeat an earlier encode (fewer frames or a longer edge than the animation
        # has) is skipped without counting against the encode cap.
        rung = (len(chosen), min(edge, longest), colours)
        if rung in tried:
            continue
        tried.add(rung)
        if edge not in resized:
            resized[edge] = {}
        scaled = [resized[edge].setdefault(id(f), fit_long_edge(f, edge)) for f in chosen]
        write_animated_gif(scaled, delays, output_path, colours)
        encodes += 1
        size = os.path.getsize(output_path)
        logger.info(f"Animated GIF rung frames={len(scaled)} long_edge={edge} colours={colours}: "
                    f"{size / 1024:.1f} KB")
        if size < max_bytes:
            return output_path
    if os.path.exists(output_path):
        os.remove(output_path)
    return None
