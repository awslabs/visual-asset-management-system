# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""PNG normalisation for the vision model: long edge within VISION_MAX_LONG_EDGE_PX, encoded size within
VISION_MAX_BYTES, 8-bit RGB or RGBA."""

import io
import os
from typing import Tuple

from PIL import Image

from .common import VISION_MAX_BYTES, VISION_MAX_LONG_EDGE_PX

_DOWNSCALE_STEP = 0.8
_MIN_LONG_EDGE_PX = 64


def flatten_mode(img: Image.Image) -> Image.Image:
    """8-bit RGB, or RGBA when the source carries transparency. Palette, greyscale, CMYK, 16-bit and
    floating-point modes are converted so the PNG encoder receives a mode the vision model reads."""
    if img.mode in ("RGB", "RGBA"):
        return img
    if img.mode in ("LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        return img.convert("RGBA")
    return img.convert("RGB")


def fit_long_edge(img: Image.Image, max_long_edge: int) -> Image.Image:
    width, height = img.size
    longest = max(width, height)
    if longest <= max_long_edge:
        return img
    scale = max_long_edge / float(longest)
    return img.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)


def encode_png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def normalise_for_vision(
    img: Image.Image,
    max_long_edge: int = VISION_MAX_LONG_EDGE_PX,
    max_bytes: int = VISION_MAX_BYTES,
) -> Tuple[bytes, Tuple[int, int]]:
    """PNG bytes within both bounds, plus the emitted (width, height). The long edge is fitted first; the byte
    cap is then met by repeated 0.8x downscales, which converge because PNG size falls with pixel count. The
    loop stops at a 64 px long edge, below which the image carries nothing the model can use."""
    work = fit_long_edge(flatten_mode(img), max_long_edge)
    while True:
        data = encode_png(work)
        if len(data) <= max_bytes or max(work.size) <= _MIN_LONG_EDGE_PX:
            return data, work.size
        width, height = work.size
        work = work.resize(
            (max(1, int(width * _DOWNSCALE_STEP)), max(1, int(height * _DOWNSCALE_STEP))),
            Image.Resampling.LANCZOS,
        )


def write_png(data: bytes, directory: str, name: str) -> str:
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path
