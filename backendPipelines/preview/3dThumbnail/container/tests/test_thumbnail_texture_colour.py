#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A textured glTF renders in its texture's colours, whatever the texture's image mode (#476).

The Khronos Duck rendered black in its glTF, GLB and Draco variants: its base-colour texture is a
palette PNG, and the mesh loader handed ``np.array(image)`` -- the 2-D array of palette indices, all
below 54 -- to VTK, which read it as a dark greyscale intensity. Textures are now converted to RGB
before they reach VTK.

The conversion tests need the real Pillow; the glTF render test needs the real trimesh, pyvista and
VTK and runs inside the pipeline image.
"""

import numpy as np
import pytest

from conftest import requires_real

_YELLOW = (240, 200, 20)


def _palette_image(rgb=_YELLOW, size=16):
    from PIL import Image

    image = Image.new("RGB", (size, size), rgb).convert("P", palette=Image.ADAPTIVE, colors=4)
    assert image.mode == "P"
    return image


@pytest.mark.unit
@requires_real("PIL", "PIL.Image")
class TestTextureRgbArray:
    def _convert(self, image):
        from preview_pipeline.format_handlers.mesh_handler import texture_rgb_array

        return texture_rgb_array(image)

    def test_a_palette_texture_becomes_its_colours_not_its_indices(self):
        rgb = self._convert(_palette_image())
        assert rgb.shape == (16, 16, 3) and rgb.dtype == np.uint8
        assert tuple(int(v) for v in rgb[0, 0]) == _YELLOW

    def test_greyscale_rgba_and_16_bit_textures_become_rgb(self):
        from PIL import Image

        grey = self._convert(Image.new("L", (4, 4), 200))
        assert grey.shape == (4, 4, 3) and int(grey.min()) == 200
        rgba = self._convert(Image.new("RGBA", (4, 4), (10, 20, 30, 40)))
        assert rgba.shape == (4, 4, 3) and tuple(int(v) for v in rgba[0, 0]) == (10, 20, 30)
        sixteen = self._convert(Image.fromarray(np.full((4, 4), 65535, dtype=np.uint16)))
        assert sixteen.shape == (4, 4, 3) and int(sixteen.min()) == 255

    def test_an_array_texture_is_accepted(self):
        rgb = self._convert(np.full((4, 4, 3), 90, dtype=np.uint8))
        assert rgb.shape == (4, 4, 3) and int(rgb.min()) == 90


@pytest.mark.unit
@requires_real("trimesh", "pyvista", "vtk", "PIL", "PIL.Image")
class TestTexturedGltfRendersInColour:
    def _textured_box_glb(self, path):
        import trimesh

        box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        # A planar projection is enough: the texture is one colour.
        uv = np.column_stack([box.vertices[:, 0] + 0.5, box.vertices[:, 1] + 0.5])
        # The Duck's material: a base-colour texture, metallic 0, roughness 1, no colour factor.
        material = trimesh.visual.material.PBRMaterial(
            baseColorTexture=_palette_image(), metallicFactor=0.0, roughnessFactor=1.0)
        box.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
        box.export(str(path))
        return path

    def test_the_rendered_subject_is_not_near_black(self, tmp_path):
        from preview_pipeline import renderer
        from preview_pipeline.format_handlers import mesh_handler

        data = mesh_handler.load(str(self._textured_box_glb(tmp_path / "box.glb")))
        assert getattr(data, "_preview_texture", None) is not None, "the texture path was not taken"

        frame = renderer.generate_static_frame(data, resolution=(160, 120))
        subject = frame[..., :3][frame[..., 3] == 255].astype(float)
        assert len(subject) > 500, "the box did not render"
        mean = subject.mean(axis=0)
        # Lit yellow: bright red and green, little blue. The palette indices rendered at about 5.
        assert mean[0] > 90 and mean[1] > 70, f"subject mean RGB {mean.round(1).tolist()} is near black"
        assert mean[0] > mean[2] + 40, f"subject mean RGB {mean.round(1).tolist()} lost the texture hue"
