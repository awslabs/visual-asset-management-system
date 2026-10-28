# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A face the unwrap could not chart must cost one face of colour, not the whole texture.

The first Blender-agent model to exceed the face budget was decimated, re-baked, and shipped GREY: the
unwrap returned NaN for a vertex of a degenerate face, NaN passed the rasteriser's area test (a NaN
comparison is False), and ``int(floor(NaN))`` raised "cannot convert float NaN to integer" out of the whole
re-bake. The fallback then found no raw texture files and the mesh went out untextured, while the log said
the export had carried an image. Measured on the shipped model: no material at all, and the blind judge
scored its colour zero.
"""

import numpy as np
import pytest

import texturing


def _shade_red(_number, _face, weights):
    return np.tile([200.0, 30.0, 30.0], (len(weights), 1))


@pytest.mark.unit
class TestTheRasteriserSkipsWhatItCannotPlace:
    def test_a_face_with_a_nan_corner_is_skipped_and_the_rest_is_painted(self):
        uv = np.array([[0.1, 0.1], [0.9, 0.1], [0.1, 0.9],      # a good face
                       [0.5, 0.5], [np.nan, 0.6], [0.6, 0.6]])  # a face the unwrap could not place
        faces = np.array([[0, 1, 2], [3, 4, 5]])
        image = texturing._rasterise_atlas(uv, faces, _shade_red, 64)
        pixels = np.asarray(image.convert("RGB"))
        # The good face's interior is painted; the flood colours the rest from it, so nothing is black.
        assert tuple(pixels[40, 20]) == (200, 30, 30)
        assert (pixels.reshape(-1, 3).sum(axis=1) > 0).all()

    def test_every_face_unplaced_still_returns_an_image_rather_than_raising(self):
        uv = np.full((3, 2), np.nan)
        image = texturing._rasterise_atlas(uv, np.array([[0, 1, 2]]), _shade_red, 16)
        assert image.size == (16, 16)
