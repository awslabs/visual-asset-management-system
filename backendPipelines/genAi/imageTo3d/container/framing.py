# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Subject framing and background compositing between segmentation and generation.

A single-view reconstruction model sees its subject the way its training data framed it: cropped to
the subject, padded to a square, scaled so the subject occupies a fixed fraction of the frame, and
composited onto one flat colour. An alpha cutout handed over unframed shifts the scale, the centring
and the background at once, and the reconstruction degrades into an undifferentiated blob.

Each upstream repo performs this step inside its own background-removal branch, which a pipeline that
supplies its own mask necessarily skips. The framing is therefore applied here instead, from values
each adapter declares, because the models do not agree on them: TripoSR composites onto mid-grey at a
0.85 subject ratio, while the TRELLIS family premultiplies onto black.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Flat background colours a model may expect behind the subject, as fractions of full intensity.
# ``none`` leaves the RGBA cutout untouched for a model that consumes the alpha channel itself.
BACKGROUND_VALUES = {"gray": 0.5, "grey": 0.5, "black": 0.0, "white": 1.0}
FRAMING_BACKGROUNDS = tuple(sorted(BACKGROUND_VALUES)) + ("none",)


class FramingError(RuntimeError):
    """A cutout that cannot be framed for reconstruction."""


def resize_foreground(image, ratio: float):
    """Crop to the subject, pad to a square, then pad again so the subject fills ``ratio``.

    A port of the upstream implementation the models were evaluated against, including its
    half-open bounding-box slice: reproducing the framing the weights were trained on matters more
    than reclaiming the final row and column of the subject.
    """
    import numpy as np
    from PIL import Image

    array = np.array(image)
    if array.ndim != 3 or array.shape[-1] != 4:
        raise FramingError(
            f"Framing needs an RGBA cutout to locate the subject; received an image of shape "
            f"{array.shape}. Run a background-removal or segmentation backend first.")

    opaque = np.where(array[..., 3] > 0)
    if opaque[0].size == 0:
        raise FramingError(
            "The cutout is fully transparent, so it carries no subject to frame. The "
            "background-removal or segmentation stage removed the entire image.")

    y1, y2 = opaque[0].min(), opaque[0].max()
    x1, x2 = opaque[1].min(), opaque[1].max()
    foreground = array[y1:y2, x1:x2]
    if foreground.shape[0] == 0 or foreground.shape[1] == 0:
        raise FramingError(
            f"The subject's bounding box is degenerate ({foreground.shape[1]}x"
            f"{foreground.shape[0]} px), so there is nothing to reconstruct.")

    # Pad the subject to a square, keeping it centred
    size = max(foreground.shape[0], foreground.shape[1])
    ph0, pw0 = (size - foreground.shape[0]) // 2, (size - foreground.shape[1]) // 2
    ph1, pw1 = size - foreground.shape[0] - ph0, size - foreground.shape[1] - pw0
    padded = np.pad(foreground, ((ph0, ph1), (pw0, pw1), (0, 0)), mode="constant",
                    constant_values=0)

    # Pad the square so the subject occupies `ratio` of the final frame
    new_size = int(padded.shape[0] / ratio)
    ph0, pw0 = (new_size - size) // 2, (new_size - size) // 2
    ph1, pw1 = new_size - size - ph0, new_size - size - pw0
    framed = np.pad(padded, ((ph0, ph1), (pw0, pw1), (0, 0)), mode="constant", constant_values=0)

    return Image.fromarray(framed)


def composite_background(image, background: str):
    """Flatten an RGBA cutout onto a flat colour, premultiplying by alpha as the models expect."""
    import numpy as np
    from PIL import Image

    key = str(background or "").strip().lower()
    if key not in BACKGROUND_VALUES:
        raise FramingError(
            f"Unknown framing background '{background}'; expected one of "
            f"{', '.join(FRAMING_BACKGROUNDS)}")

    array = np.array(image).astype(np.float32) / 255.0
    alpha = array[:, :, 3:4]
    flattened = array[:, :, :3] * alpha + (1.0 - alpha) * BACKGROUND_VALUES[key]
    return Image.fromarray((flattened * 255.0).astype(np.uint8))


def frame_image(image_path: str, work_dir: str, ratio: float, background: str) -> str:
    """Frame a cutout for reconstruction, returning the path the model should consume.

    Two separate operations, and the background choice governs only the second:

    1. **Cropping and scaling the subject** to fill ``ratio`` of a square frame. This always happens.
    2. **Compositing a flat colour** behind it. A ``none`` background skips this and keeps the alpha
       channel, which a model that reads alpha as an object mask requires.

    Skipping step 1 for a ``none`` background was a real defect: TRELLIS.2 was handed a full 768x768
    frame with the subject occupying a fraction of it and reconstructed the **background plane as a
    flat slab** with a small mug attached, over 3739 separate bodies. Nothing else cropped it -- the
    model's own preprocessing is deliberately off, because it downloads a gated repository -- so the
    subject has to be isolated here. Cropping to the alpha bounding box is what tells the model where
    the object ends, whether or not a colour is painted behind it.
    """
    key = str(background or "").strip().lower() or "none"

    try:
        value = float(ratio)
    except (TypeError, ValueError) as e:
        raise FramingError(f"Framing ratio '{ratio}' is not a number") from e
    if not 0.0 < value <= 1.0:
        raise FramingError(
            f"Framing ratio {value} is out of range; the subject must occupy a positive fraction "
            f"of the frame, so the ratio has to fall in (0, 1]. A value above 1 would crop the "
            f"subject rather than pad around it.")

    from PIL import Image

    os.makedirs(work_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(image_path))[0]
    output_path = os.path.join(work_dir, f"{stem}_framed.png")

    with Image.open(image_path) as source:
        cutout = source.convert("RGBA")
        framed = resize_foreground(cutout, value)
        if key != "none":
            framed = composite_background(framed, key)
        else:
            logger.info("Framing kept the alpha channel; no background colour was composited")
    framed.save(output_path)

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise FramingError(f"Framing produced no output at {output_path}")

    logger.info(
        f"Framed the subject to {value} of the frame on a {key} background: "
        f"{image_path} -> {output_path}")
    return output_path
