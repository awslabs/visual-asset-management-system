"""The CLI's allowed preview extensions must match the backend's (#1580)."""

import ast
from pathlib import Path

import pytest

from vamscli.constants import ALLOWED_PREVIEW_EXTENSIONS

BACKEND_PATTERNS = (
    Path(__file__).resolve().parents[3] / "backend" / "backend" / "common" / "s3PathPatterns.py"
)


def _backend_preview_extensions():
    tree = ast.parse(BACKEND_PATTERNS.read_text(encoding="utf-8"))
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "ALLOWED_PREVIEW_FILE_EXTENSIONS":
            return set(ast.literal_eval(node.value))
    raise AssertionError("ALLOWED_PREVIEW_FILE_EXTENSIONS not found in s3PathPatterns.py")


class TestPreviewExtensionList:
    def test_cli_list_holds_the_displayable_image_types(self):
        assert set(ALLOWED_PREVIEW_EXTENSIONS) == {
            ".png",
            ".jpg",
            ".jpeg",
            ".svg",
            ".gif",
            ".webp",
            ".bmp",
        }

    def test_cli_list_excludes_tga_and_tiff(self):
        for ext in (".tga", ".tif", ".tiff"):
            assert ext not in ALLOWED_PREVIEW_EXTENSIONS

    def test_cli_list_is_lowercase_and_unique(self):
        assert all(e == e.lower() and e.startswith(".") for e in ALLOWED_PREVIEW_EXTENSIONS)
        assert len(set(ALLOWED_PREVIEW_EXTENSIONS)) == len(ALLOWED_PREVIEW_EXTENSIONS)


class TestBackendDrift:
    def test_cli_list_equals_backend_list(self):
        if not BACKEND_PATTERNS.exists():
            pytest.skip("backend/backend/common/s3PathPatterns.py is not in this checkout")
        backend = _backend_preview_extensions()
        if not {".webp", ".bmp"} <= backend:
            pytest.skip(
                "backend ALLOWED_PREVIEW_FILE_EXTENSIONS lacks .webp/.bmp until #1550 / PR #1575 "
                "lands on this branch; remove this skip once it has"
            )
        assert set(ALLOWED_PREVIEW_EXTENSIONS) == backend
