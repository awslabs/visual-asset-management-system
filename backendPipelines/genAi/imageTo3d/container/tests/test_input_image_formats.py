#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Input images are decoded as PNG or JPEG only, whatever the file is named.

The pipeline's input filter matches file names, so a file named ``photo.png`` can hold any format. Pillow picks
its decoder from the file's content, so every ``Image.open`` of an input names the decoders it may use
(``preprocess.INPUT_IMAGE_FORMATS``) and Pillow tries no other. ``validate_image`` runs first on every input file
and refuses anything else with a job error before a model loads.

Two kinds of test: the behaviour of ``validate_image`` and ``frame_image`` on real files of other formats, and a
source check that every ``Image.open`` in the container code and in the adapters' driver scripts passes the same
decoders. The source check is the guard for a new call site.
"""

import ast
import os
import re
import shutil
from unittest.mock import MagicMock

import pytest

import preprocess

PIL = pytest.importorskip("PIL")
if isinstance(PIL, MagicMock):  # pragma: no cover - the conftest stub stands in when Pillow is absent
    pytest.skip("Pillow is not installed; these tests decode real files", allow_module_level=True)

from PIL import Image  # noqa: E402

import framing  # noqa: E402

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DRIVER_NAME = re.compile(r"^[A-Z_]*DRIVER[A-Z_]*$")

# Image.open calls that do not read a pipeline input, so they keep Pillow's default decoders.
# (file relative to the container, enclosing function) -> why
_NOT_INPUT = {
    ("agent_broker.py", "execute"): "the image the image-editing model returned, which the request asks for as PNG",
    ("texturing.py", "attach_generated_texture"): "the texture the model itself wrote beside its mesh",
}


def _other_format_files(directory):
    """``{format: path}`` for each format this Pillow can write that is neither PNG nor JPEG, every file named
    ``.png`` and large enough to pass the size checks."""
    image = Image.new("RGB", (300, 300), (40, 160, 90))
    files = {}
    for fmt, suffix in (("TIFF", "tiff"), ("GIF", "gif"), ("BMP", "bmp"), ("WEBP", "webp"), ("PPM", "ppm"),
                        ("TGA", "tga"), ("PCX", "pcx")):
        real = os.path.join(directory, f"real.{suffix}")
        try:
            image.save(real, format=fmt)
        except (KeyError, OSError):
            continue
        named = os.path.join(directory, f"{fmt.lower()}_named.png")
        shutil.copy(real, named)
        files[fmt] = named
    return files


@pytest.fixture
def decoders_used(monkeypatch):
    """The Pillow decoders asked to open a file while the test runs."""
    Image.init()
    used = []
    for fmt, (factory, accept) in list(Image.OPEN.items()):
        def recording(fp, filename=None, *args, _fmt=fmt, _factory=factory, **kwargs):
            used.append(_fmt)
            return _factory(fp, filename, *args, **kwargs)
        monkeypatch.setitem(Image.OPEN, fmt, (recording, accept))
    return used


class TestValidateImage:
    def test_the_decoders_are_png_and_jpeg(self):
        assert preprocess.INPUT_IMAGE_FORMATS == ("PNG", "JPEG")

    def test_a_file_of_another_format_is_refused_whatever_its_name(self, tmp_path, decoders_used):
        files = _other_format_files(str(tmp_path))
        assert "TIFF" in files and "GIF" in files
        for fmt, path in files.items():
            decoders_used.clear()
            with pytest.raises(preprocess.PreprocessError, match="not a PNG or JPEG image"):
                preprocess.validate_image(path)
            # Refused by the open itself: no decoder but PNG's and JPEG's read the file.
            assert set(decoders_used) <= {"PNG", "JPEG"}, (fmt, decoders_used)

    @pytest.mark.parametrize("fmt, suffix, expected", [("PNG", "png", "PNG"), ("JPEG", "jpg", "JPEG")])
    def test_png_and_jpeg_are_accepted(self, tmp_path, fmt, suffix, expected):
        path = os.path.join(str(tmp_path), f"photo.{suffix}")
        Image.new("RGB", (300, 300), (40, 160, 90)).save(path, format=fmt)
        assert preprocess.validate_image(path)["format"] == expected

    def test_a_multi_picture_jpeg_from_a_camera_is_accepted(self, tmp_path):
        path = os.path.join(str(tmp_path), "camera.jpg")
        frame = Image.new("RGB", (300, 300), (40, 160, 90))
        frame.save(path, format="MPO", save_all=True, append_images=[frame])
        assert preprocess.validate_image(path)["format"] == "MPO"


class TestFraming:
    def test_framing_decodes_png_and_jpeg_only(self, tmp_path, decoders_used):
        path = _other_format_files(str(tmp_path))["TIFF"]
        with pytest.raises(Image.UnidentifiedImageError):
            framing.frame_image(path, str(tmp_path / "work"), 0.85, "none")
        assert set(decoders_used) <= {"PNG", "JPEG"}, decoders_used


def _python_files():
    for root, dirs, names in os.walk(_CONTAINER_DIR):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", "locks")]
        for name in names:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def _image_open_calls(tree):
    """``(function name, call)`` for each ``Image.open`` / ``PILImage.open`` call in ``tree``."""
    found = []

    def visit(node, function):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "open"
                    and isinstance(child.func.value, ast.Name) and child.func.value.id in ("Image", "PILImage")):
                found.append((function, child))
            visit(child, name)

    visit(tree, "<module>")
    return found


def _formats_value(call):
    keyword = next((k for k in call.keywords if k.arg == "formats"), None)
    if keyword is None:
        return None
    if isinstance(keyword.value, ast.Name):
        return keyword.value.id
    return ast.literal_eval(keyword.value)


class TestEveryImageOpenNamesTheDecoders:
    def test_container_modules(self):
        seen = 0
        for path in _python_files():
            relative = os.path.relpath(path, _CONTAINER_DIR)
            for function, call in _image_open_calls(ast.parse(open(path, encoding="utf-8").read())):
                seen += 1
                value = _formats_value(call)
                if value is None and (relative, function) in _NOT_INPUT:
                    continue
                assert value == "INPUT_IMAGE_FORMATS", (
                    f"{relative}:{call.lineno} ({function}) opens an image without formats=INPUT_IMAGE_FORMATS")
        assert seen >= 12

    def test_driver_scripts(self):
        seen = 0
        for path in _python_files():
            module = ast.parse(open(path, encoding="utf-8").read())
            for node in module.body:
                if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                        and any(isinstance(t, ast.Name) and _DRIVER_NAME.match(t.id) for t in node.targets)):
                    continue
                for _, call in _image_open_calls(ast.parse(node.value.value)):
                    seen += 1
                    # The driver runs in the upstream interpreter, so it carries the tuple itself.
                    assert _formats_value(call) == preprocess.INPUT_IMAGE_FORMATS, (
                        f"{os.path.relpath(path, _CONTAINER_DIR)}: a driver opens an image without "
                        f"formats={preprocess.INPUT_IMAGE_FORMATS}")
        assert seen >= 5
