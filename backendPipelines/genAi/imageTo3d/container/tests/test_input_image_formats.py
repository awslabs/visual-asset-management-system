#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Input images are decoded as PNG or JPEG only, whatever the file is named.

The pipeline's input filter matches file names, so a file named ``photo.png`` can hold any format. Pillow picks
its decoder from the file's content, so every ``Image.open`` of an input names the decoders it may use
(``preprocess.INPUT_IMAGE_FORMATS``) and Pillow tries no other. ``validate_image`` runs first on every input file
and refuses anything else with a job error before a model loads.

The two opens that read model output are held to the formats the model writes: the image-editing reply is decoded
as PNG, the format the request asks for, and a model's baked texture as PNG or JPEG.

Two kinds of test: the behaviour of ``validate_image``, ``frame_image``, the image-edit broker and the texture
binding on real files of other formats, and a source check that every ``PIL.Image.open`` in the container code and
in the adapters' driver scripts, however it is spelled, names PNG or JPEG decoders. The source check is the guard
for a new call site.
"""

import ast
import base64
import io
import json
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

import agent_broker  # noqa: E402
import framing  # noqa: E402
import texturing  # noqa: E402

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DRIVER_NAME = re.compile(r"^[A-Z_]*DRIVER[A-Z_]*$")


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


class _ReplyingBedrock:
    """An image-editing model that replies with a fixed encoded image."""

    def __init__(self, encoded):
        self.encoded = encoded

    def invoke_model(self, modelId, body, contentType, accept):
        assert json.loads(body)["output_format"] == "png"
        return {"body": io.BytesIO(json.dumps({"images": [base64.b64encode(self.encoded).decode()],
                                               "finish_reasons": [None]}).encode())}


def _encoded(fmt):
    buffer = io.BytesIO()
    Image.new("RGB", (256, 192), (40, 200, 60)).save(buffer, format=fmt)
    return buffer.getvalue()


class TestModelOutput:
    def _edit(self, tmp_path, encoded):
        (tmp_path / "textures").mkdir()
        (tmp_path / "requests").mkdir()
        Image.new("RGB", (256, 192), (120, 80, 40)).save(tmp_path / "textures" / "atlas.png")
        broker = agent_broker.ImageEditBroker(tmp_path, "us-west-2", client=_ReplyingBedrock(encoded))
        (tmp_path / "requests" / "r1.json").write_text(json.dumps({
            "operation": "structure", "image": "textures/atlas.png", "prompt": "brushed steel",
            "output": "textures/edited.png"}), encoding="utf-8")
        broker.serve_pending()
        return json.loads((tmp_path / "requests" / "r1.result.json").read_text(encoding="utf-8"))

    def test_the_image_edit_reply_is_decoded_as_png(self, tmp_path):
        assert self._edit(tmp_path, _encoded("PNG"))["status"] == "ok"

    @pytest.mark.parametrize("fmt", ["JPEG", "TIFF", "GIF"])
    def test_an_image_edit_reply_in_another_format_is_refused(self, tmp_path, decoders_used, fmt):
        result = self._edit(tmp_path, _encoded(fmt))
        assert result["status"] == "error", result
        assert not (tmp_path / "textures" / "edited.png").exists()
        assert set(decoders_used) <= {"PNG", "JPEG"}, decoders_used

    @staticmethod
    def _uv_mesh():
        import trimesh

        mesh = trimesh.creation.box()
        uv = mesh.vertices[:, :2] - mesh.vertices[:, :2].min(axis=0)
        mesh.visual = trimesh.visual.TextureVisuals(uv=uv / uv.max())
        return mesh

    @pytest.mark.parametrize("fmt, name", [("PNG", "texture.png"), ("JPEG", "material_0.jpeg")])
    def test_a_baked_texture_in_png_or_jpeg_is_bound(self, tmp_path, fmt, name):
        path = tmp_path / name
        Image.new("RGB", (64, 64), (255, 0, 0)).save(path, format=fmt)
        mesh = texturing.attach_generated_texture(self._uv_mesh(), [str(path)])
        assert texturing.real_material_image(mesh.visual.material) is not None

    def test_a_baked_texture_in_another_format_is_refused(self, tmp_path, decoders_used):
        path = _other_format_files(str(tmp_path))["TIFF"]
        with pytest.raises(texturing.TextureTransferError, match="could not be read"):
            texturing.attach_generated_texture(self._uv_mesh(), [path])
        assert set(decoders_used) <= {"PNG", "JPEG"}, decoders_used


def _python_files():
    for root, dirs, names in os.walk(_CONTAINER_DIR):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", "locks")]
        for name in names:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def _image_open_calls(tree):
    """``(function name, call)`` for each call of ``PIL.Image.open`` in ``tree``, however it is spelled: through
    ``Image``, ``PILImage`` or any other name an import binds to the module (``from PIL import Image as X``,
    ``import PIL.Image as X``), as ``PIL.Image.open`` (``import PIL``, ``import PIL.Image``), or through a name bound
    to the function itself (``from PIL.Image import open as X``). Imports anywhere in the module count, a function's
    own included."""
    modules, packages, functions = {"Image", "PILImage"}, {"PIL"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "PIL":
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == "Image")
        elif isinstance(node, ast.ImportFrom) and node.module == "PIL.Image":
            functions.update(alias.asname or alias.name for alias in node.names if alias.name == "open")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "PIL.Image" and alias.asname:
                    modules.add(alias.asname)
                elif alias.name in ("PIL", "PIL.Image"):
                    packages.add(alias.asname or "PIL")

    def is_open(func):
        if isinstance(func, ast.Name):
            return func.id in functions
        if not (isinstance(func, ast.Attribute) and func.attr == "open"):
            return False
        owner = func.value
        if isinstance(owner, ast.Name):
            return owner.id in modules
        return (isinstance(owner, ast.Attribute) and owner.attr == "Image"
                and isinstance(owner.value, ast.Name) and owner.value.id in packages)

    found = []

    def visit(node, function):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, ast.Call) and is_open(child.func):
                found.append((function, child))
            visit(child, name)

    visit(tree, "<module>")
    return found


def _formats_value(call):
    """The ``formats`` argument of an ``Image.open`` call: a name, a tuple of format names, or None."""
    keyword = next((k for k in call.keywords if k.arg == "formats"), None)
    value = keyword.value if keyword is not None else (call.args[2] if len(call.args) > 2 else None)
    if value is None:
        return None
    if isinstance(value, ast.Name):
        return value.id
    return tuple(ast.literal_eval(value))


# Every spelling the guard has to see, each with one unrestricted open.
_SPELLINGS = (
    "from PIL import Image\nImage.open(p)",
    "from PIL import Image as I\nI.open(p)",
    "import PIL.Image as pim\npim.open(p)",
    "import PIL.Image\nPIL.Image.open(p)",
    "import PIL\nPIL.Image.open(p)",
    "import PIL as P\nP.Image.open(p)",
    "from PIL.Image import open as pil_open\npil_open(p)",
    "def f(p):\n    from PIL import Image as _Img\n    return _Img.open(p)",
)


class TestEveryImageOpenNamesTheDecoders:
    @pytest.mark.parametrize("source", _SPELLINGS)
    def test_the_guard_sees_an_open_however_it_is_spelled(self, source):
        calls = _image_open_calls(ast.parse(source))
        assert len(calls) == 1 and _formats_value(calls[0][1]) is None, source

    def test_the_guard_reads_formats_by_keyword_or_position(self):
        calls = _image_open_calls(ast.parse(
            "from PIL import Image\nImage.open(p, formats=INPUT_IMAGE_FORMATS)\nImage.open(p, 'r', ('PNG',))\n"
            "open(p)\nhandle.open()"))
        assert [_formats_value(c) for _, c in calls] == ["INPUT_IMAGE_FORMATS", ("PNG",)]

    def test_container_modules(self):
        seen = 0
        allowed = set(preprocess.INPUT_IMAGE_FORMATS)
        for path in _python_files():
            relative = os.path.relpath(path, _CONTAINER_DIR)
            for function, call in _image_open_calls(ast.parse(open(path, encoding="utf-8").read())):
                seen += 1
                value = _formats_value(call)
                restricted = value == "INPUT_IMAGE_FORMATS" or (
                    isinstance(value, tuple) and bool(value) and set(value) <= allowed)
                assert restricted, (
                    f"{relative}:{call.lineno} ({function}) opens an image without formats=INPUT_IMAGE_FORMATS or "
                    f"a subset of {preprocess.INPUT_IMAGE_FORMATS}")
        assert seen >= 16

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
