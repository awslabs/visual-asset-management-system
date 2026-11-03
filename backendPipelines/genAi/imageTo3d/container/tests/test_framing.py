# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Framing has to recentre, rescale and composite, and has to leave mask-consuming models alone.

The failure this guards against is silent: an unframed cutout still reconstructs, still passes the
print gate and still exports, but the mesh is an undifferentiated blob because the subject reached
the model at the wrong scale, off centre and on the wrong background. Nothing downstream can detect
that, so the framing itself is asserted here.
"""

import os
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import framing  # noqa: E402
from models.sam3d import Sam3dAdapter  # noqa: E402
from models.triposr import TripoSRAdapter  # noqa: E402


def _cutout(size=100, box=(0, 0, 10, 10), colour=(255, 0, 0)):
    """An RGBA image that is transparent apart from one opaque rectangle."""
    array = np.zeros((size, size, 4), dtype=np.uint8)
    x1, y1, x2, y2 = box
    array[y1:y2, x1:x2, :3] = colour
    array[y1:y2, x1:x2, 3] = 255
    return Image.fromarray(array)


def _alpha_bbox(image):
    alpha = np.array(image.convert("RGBA"))[..., 3]
    rows, cols = np.where(alpha > 0)
    return cols.min(), rows.min(), cols.max(), rows.max()


class TestResizeForeground:
    def test_subject_is_scaled_to_the_requested_fraction_of_the_frame(self):
        """The whole point of the ratio: the subject must fill it, not whatever the photo had."""
        framed = framing.resize_foreground(_cutout(box=(0, 0, 10, 10)), 0.5)
        x1, y1, x2, y2 = _alpha_bbox(framed)
        subject = max(x2 - x1, y2 - y1) + 1
        assert subject / framed.width == pytest.approx(0.5, abs=0.08), (
            f"subject spans {subject} of {framed.width} px")

    def test_an_off_centre_subject_is_recentred(self):
        """A corner subject must end up centred; a model framed on centred subjects needs it."""
        framed = framing.resize_foreground(_cutout(size=200, box=(0, 0, 20, 20)), 0.8)
        x1, y1, x2, y2 = _alpha_bbox(framed)
        assert (x1 + x2) / 2 == pytest.approx(framed.width / 2, abs=2)
        assert (y1 + y2) / 2 == pytest.approx(framed.height / 2, abs=2)

    def test_a_ratio_change_changes_the_framing(self):
        """Guards the assertions above against a ratio that is accepted and then ignored."""
        tight = framing.resize_foreground(_cutout(box=(0, 0, 10, 10)), 0.9)
        loose = framing.resize_foreground(_cutout(box=(0, 0, 10, 10)), 0.3)
        assert loose.width > tight.width

    def test_a_fully_transparent_cutout_is_rejected(self):
        blank = Image.fromarray(np.zeros((64, 64, 4), dtype=np.uint8))
        with pytest.raises(framing.FramingError, match="fully transparent"):
            framing.resize_foreground(blank, 0.85)

    def test_an_image_without_an_alpha_channel_is_rejected(self):
        with pytest.raises(framing.FramingError, match="RGBA"):
            framing.resize_foreground(Image.new("RGB", (64, 64), (10, 20, 30)), 0.85)


class TestCompositeBackground:
    def test_transparent_pixels_take_the_background_colour(self):
        flat = np.array(framing.composite_background(_cutout(), "gray"))
        assert flat.shape[-1] == 3, "the model expects a flattened RGB image"
        assert flat[50, 50, 0] in (127, 128), f"background sampled {flat[50, 50]}"

    def test_black_and_white_backgrounds_differ_from_grey(self):
        cut = _cutout()
        assert np.array(framing.composite_background(cut, "black"))[50, 50, 0] == 0
        assert np.array(framing.composite_background(cut, "white"))[50, 50, 0] == 255

    def test_the_subject_survives_compositing(self):
        flat = np.array(framing.composite_background(_cutout(colour=(255, 0, 0)), "gray"))
        assert tuple(flat[5, 5]) == (255, 0, 0), "an opaque subject pixel must be unchanged"

    def test_an_unknown_background_is_rejected(self):
        with pytest.raises(framing.FramingError, match="Unknown framing background"):
            framing.composite_background(_cutout(), "chartreuse")


class TestFrameImage:
    def test_a_none_background_still_crops_the_subject_but_keeps_the_alpha(self, tmp_path):
        """The background choice governs compositing only -- cropping always happens.

        Returning the input untouched here let TRELLIS.2 receive a full frame with the subject filling
        a fraction of it, and it reconstructed the background plane as a flat slab with a small mug
        attached across 3739 bodies. The model's own preprocessing is off by design (it downloads a
        gated repository), so this is the only stage that isolates the subject.
        """
        from PIL import Image

        source = tmp_path / "cut.png"
        _cutout().save(source)

        result = framing.frame_image(str(source), str(tmp_path), 0.85, "none")
        assert result != str(source), "the input was passed through without being cropped"

        with Image.open(result) as framed:
            assert framed.mode == "RGBA", "a none background must keep the alpha channel as a mask"
            assert framed.size[0] == framed.size[1], "the subject should sit in a square frame"
            alpha = framed.split()[-1]
            assert alpha.getextrema() == (0, 255), (
                "the alpha channel should still distinguish subject from background")

    def test_a_flat_background_composites_and_drops_the_alpha(self, tmp_path):
        """The contrast with the case above: a named colour is painted behind the cropped subject."""
        from PIL import Image

        source = tmp_path / "cut.png"
        _cutout().save(source)
        result = framing.frame_image(str(source), str(tmp_path), 0.85, "gray")
        with Image.open(result) as framed:
            assert framed.mode == "RGB", "compositing a background should flatten the alpha away"

    def test_framing_writes_a_new_flattened_file(self, tmp_path):
        source = tmp_path / "cut.png"
        _cutout().save(source)
        out = framing.frame_image(str(source), str(tmp_path), 0.85, "gray")
        assert out != str(source) and os.path.getsize(out) > 0
        with Image.open(out) as framed:
            assert framed.mode == "RGB"

    @pytest.mark.parametrize("ratio", [0, -0.5, 1.5, "abc"])
    def test_an_unusable_ratio_is_rejected(self, tmp_path, ratio):
        source = tmp_path / "cut.png"
        _cutout().save(source)
        with pytest.raises(framing.FramingError):
            framing.frame_image(str(source), str(tmp_path), ratio, "gray")


class TestAdapterDeclarations:
    def test_sam3d_is_never_framed(self):
        """SAM 3D reads the alpha channel AS the object mask; flattening it destroys the mask."""
        assert Sam3dAdapter.FRAMING_BACKGROUND == "none"

    def test_triposr_is_framed_on_grey(self):
        """Upstream's --no-remove-bg skips its own framing, so the pipeline must supply it."""
        assert TripoSRAdapter.FRAMING_BACKGROUND == "gray"
        assert 0.0 < TripoSRAdapter.FRAMING_RATIO <= 1.0

    def test_every_declared_background_is_one_framing_understands(self):
        from models.step1x3d import Step1X3dAdapter
        from models.trellis2 import Trellis2Adapter

        for adapter in (Sam3dAdapter, Trellis2Adapter, TripoSRAdapter, Step1X3dAdapter):
            assert adapter.FRAMING_BACKGROUND in framing.FRAMING_BACKGROUNDS, (
                f"{adapter.KEY} declares an unusable framing background")
