#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every preview frame has a transparent background, and the GIF keeps it on every frame (#463).

Two defects made an animated preview flicker between a transparent and an opaque grey background:

* the renderer took its alpha mask from a second render's depth buffer, whose float32 conversion
  failed to recognise the far plane on some camera angles of a scene with a wide depth range (a few
  primitives far from the subject), so the whole background of those frames came out opaque; the
  mask also disagreed with the colour image along edges because the two renders used different
  clipping ranges;
* the GIF writer let Pillow shrink each frame's local palette to the colours it used while still
  declaring index 255 transparent -- an index outside that palette, which decoders handle
  inconsistently.

The renderer now reads colour and alpha from one render, and every GIF frame's transparent index is
an entry of its own palette. The GIF tests need the real Pillow and the render tests the real
pyvista/VTK, so they run inside the pipeline image and skip elsewhere.
"""

import struct

import numpy as np
import pytest

from conftest import requires_real

H, W = 120, 160
# The GIF tests encode at the pipeline's output size: Pillow only rewrites a frame's palette when the
# frame -- after it is cropped to the region that changed -- is under 512x512 pixels, which is what
# happens to the later frames of a full-size orbit.
GIF_H, GIF_W = 600, 800


def _gif_frame_records(path):
    """(transparency flag, transparent index, palette length) for every image in a GIF file.

    A literal walk of the GIF block structure, the way a strict decoder reads it: the palette that
    applies to a frame is its local colour table when present, else the global one.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    assert data[:6] in (b"GIF87a", b"GIF89a")
    flags = data[10]
    pos = 13
    global_len = 0
    if flags & 0x80:
        global_len = 2 ** ((flags & 7) + 1)
        pos += 3 * global_len
    records, gce = [], None
    while pos < len(data):
        block = data[pos]
        if block == 0x21:  # extension
            label = data[pos + 1]
            pos += 2
            if label == 0xF9:
                packed, _delay, index = struct.unpack("<BHB", data[pos + 1:pos + 5])
                gce = (bool(packed & 1), index)
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
        elif block == 0x2C:  # image descriptor
            local = data[pos + 9]
            pos += 10
            palette_len = global_len
            if local & 0x80:
                palette_len = 2 ** ((local & 7) + 1)
                pos += 3 * palette_len
            pos += 1  # LZW minimum code size
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
            flag, index = gce if gce else (False, None)
            records.append((flag, index, palette_len))
            gce = None
        elif block == 0x3B:  # trailer
            break
        else:
            raise AssertionError(f"unexpected GIF block 0x{block:02x} at byte {pos}")
    return records


def _orbit_like_frames(n=36, flat=True):
    """RGBA frames of a disc whose size changes per frame on a transparent background.

    ``flat`` shades the disc with a handful of colours -- the case that made Pillow's optimiser
    write 4- and 8-entry local palettes.
    """
    yy, xx = np.mgrid[0:GIF_H, 0:GIF_W]
    frames, masks = [], []
    for i in range(n):
        t = 2 * np.pi * i / n
        mask = (xx - GIF_W / 2) ** 2 + (yy - GIF_H / 2) ** 2 <= (150 + 75 * np.sin(t)) ** 2
        shade = 0.4 + 0.6 * (xx / GIF_W)
        if flat:
            shade = np.round(shade * 3) / 3
        rgba = np.zeros((GIF_H, GIF_W, 4), np.uint8)
        rgba[..., :3] = 128
        for channel, base in enumerate((173, 216, 230)):
            rgba[..., channel] = np.where(mask, base * shade, 128).astype(np.uint8)
        rgba[..., 3] = np.where(mask, 255, 0)
        frames.append(rgba)
        masks.append(mask)
    return frames, masks


@pytest.mark.unit
@requires_real("PIL")
class TestGifTransparency:
    @pytest.mark.parametrize("flat", [True, False], ids=["few-colours", "shaded"])
    def test_every_frame_declares_a_transparent_index_inside_its_palette(self, tmp_path, flat):
        from preview_pipeline.utils import image_utils

        frames, _ = _orbit_like_frames(flat=flat)
        path = str(tmp_path / "preview.gif")
        image_utils.save_gif(frames, path)

        records = _gif_frame_records(path)
        assert len(records) == len(frames)
        for number, (flag, index, palette_len) in enumerate(records):
            assert flag, f"frame {number} declares no transparent index"
            assert index < palette_len, \
                f"frame {number}: transparent index {index} is outside its {palette_len}-entry palette"

    def test_each_decoded_frame_is_transparent_exactly_outside_the_model(self, tmp_path):
        from PIL import Image
        from preview_pipeline.utils import image_utils

        frames, masks = _orbit_like_frames()
        path = str(tmp_path / "preview.gif")
        image_utils.save_gif(frames, path)

        with Image.open(path) as gif:
            assert gif.n_frames == len(frames)
            for number, mask in enumerate(masks):
                gif.seek(number)
                opaque = np.array(gif.convert("RGBA"))[..., 3] == 255
                assert np.array_equal(opaque, mask), f"frame {number} alpha differs from the source"

    def test_the_transparent_index_is_reserved_for_the_background(self):
        from preview_pipeline.utils import image_utils

        frames, masks = _orbit_like_frames(n=1, flat=False)
        indices = np.array(image_utils._rgba_to_gif_frame(frames[0]))
        transparent = image_utils._GIF_TRANSPARENT_INDEX
        assert np.array_equal(indices == transparent, ~masks[0])
        assert len(image_utils._rgba_to_gif_frame(frames[0]).getpalette()) == 256 * 3


def _wide_depth_point_cloud():
    """A dense subject plus a few stray points hundreds of units away: the scene shape (a splat
    with stray splats) whose orbit lost its transparent background on several frames."""
    import pyvista as pv

    rng = np.random.default_rng(7)
    points = np.vstack([rng.normal(size=(5000, 3)), rng.uniform(-1, 1, size=(8, 3)) * 800])
    cloud = pv.PolyData(points.astype(np.float32))
    cloud.point_data["RGB"] = rng.integers(0, 256, size=(len(points), 3), dtype=np.uint8)
    return cloud


def _wide_depth_mesh():
    import pyvista as pv

    subject = pv.Sphere(radius=1.0, theta_resolution=60, phi_resolution=60)
    stray = pv.Sphere(radius=0.01, theta_resolution=4, phi_resolution=4, center=(700.0, 0.0, 0.0))
    return subject.merge(stray)


def _assert_transparent_background(frame, label):
    from preview_pipeline import renderer

    assert frame.shape[2] == 4 and frame.dtype == np.uint8, label
    alpha = frame[..., 3]
    corners = [alpha[0, 0], alpha[0, -1], alpha[-1, 0], alpha[-1, -1]]
    assert corners == [0, 0, 0, 0], f"{label}: corner alpha {corners}"
    assert (alpha == 255).any(), f"{label}: nothing rendered opaque"
    # Alpha and colour come from one render: no geometry hides under alpha 0, and no pixel that
    # shows a colour other than the background is transparent.
    background = np.all(frame[..., :3] == np.array(renderer._BG_COLOR, np.uint8), axis=-1)
    assert np.all(background[alpha == 0]), f"{label}: coloured pixels under alpha 0"
    assert np.all(alpha[~background] > 0), f"{label}: transparent pixels showing geometry"


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestRenderedTransparency:
    @pytest.mark.parametrize("scene", [_wide_depth_point_cloud, _wide_depth_mesh],
                             ids=["point-cloud", "mesh"])
    def test_every_orbit_frame_has_a_transparent_background(self, scene):
        from preview_pipeline import renderer

        # Percentile framing puts the camera inside the scene's bounds -- the wide clipping range
        # that broke the depth-buffer mask. (A mesh frames on its full bounds by default.)
        frames = renderer.generate_rotating_frames(scene(), resolution=(W * 2, H * 2),
                                                   use_full_bounds=False)
        assert len(frames) == renderer.DEFAULT_N_FRAMES
        for number, frame in enumerate(frames):
            _assert_transparent_background(frame, f"frame {number}")

    def test_a_still_frame_has_a_transparent_background(self):
        from preview_pipeline import renderer

        frame = renderer.generate_static_frame(_wide_depth_point_cloud(), resolution=(W * 2, H * 2))
        _assert_transparent_background(frame, "still")
