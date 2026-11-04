"""Tests for how a stage export's asset paths are rewritten for the asset it is uploaded to.

Isaac Sim opens a VAMS asset from a private download directory, and exporting the stage flattens it,
so every texture, reference and sublayer path in the export is absolute into such a directory. Uploaded
as is, the export resolves on no other machine, and on this one only until the directory is cleared. A
path to a file the target asset's own download left is rewritten relative to the asset root, where
the export lands; any other local path is left as it is and reported; a URL or a relative path is left
alone.
"""

import os

import pytest

from vams.connector.isaacsim.connector import _Download, _portable_asset_path

TARGET = ("db", "a1")
OTHER = ("db", "a2")


def _path(root, *parts):
    """An absolute asset path as USD authors it: forward slashes, whatever the platform."""
    return root.joinpath(*parts).as_posix()


def _roots(*downloads):
    """Download directories from (root, owner, file, ...) tuples."""
    return {str(root): _Download(owner, frozenset(files)) for root, owner, *files in downloads}


class TestFilesTheTargetAssetsDownloadLeft:
    def test_become_relative_to_the_asset_root(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = _path(root, "house", "textures", "color.jpg")
        roots = _roots((root, TARGET, "house/textures/color.jpg"))
        assert _portable_asset_path(path, roots, TARGET) == ("./house/textures/color.jpg", True)

    def test_native_separators_still_give_posix_separators(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = str(root / "house" / "textures" / "color.jpg")
        roots = _roots((root, TARGET, "house/textures/color.jpg"))
        assert _portable_asset_path(path, roots, TARGET) == ("./house/textures/color.jpg", True)

    def test_a_file_url_is_a_local_path(self, tmp_path):
        root = tmp_path / "vams_ab"
        url = (root / "house" / "textures" / "color.jpg").as_uri()
        roots = _roots((root, TARGET, "house/textures/color.jpg"))
        assert _portable_asset_path(url, roots, TARGET) == ("./house/textures/color.jpg", True)

    def test_a_path_inside_a_downloaded_package_keeps_its_package_part(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = _path(root, "pkg", "robot.usdz") + "[textures/metal.png]"
        roots = _roots((root, TARGET, "pkg/robot.usdz"))
        assert _portable_asset_path(path, roots, TARGET) == (
            "./pkg/robot.usdz[textures/metal.png]", True)


class TestLocalPathsOutsideTheTargetAsset:
    def test_a_file_the_download_did_not_leave_is_not_portable(self, tmp_path):
        """Importer output, or a layer saved into the download directory, is not in VAMS."""
        root = tmp_path / "vams_ab"
        path = _path(root, "robot", "configuration", "robot_physics.usd")
        roots = _roots((root, TARGET, "robot.urdf", "meshes/base.stl"))
        assert _portable_asset_path(path, roots, TARGET) == (path, False)

    def test_a_path_into_a_package_the_download_did_not_leave_is_not_portable(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = _path(root, "pkg", "baked.usdz") + "[textures/metal.png]"
        roots = _roots((root, TARGET, "pkg/robot.usdz"))
        assert _portable_asset_path(path, roots, TARGET) == (path, False)

    def test_a_path_into_another_assets_download_is_not_portable(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = _path(root, "house", "textures", "color.jpg")
        roots = _roots((root, OTHER, "house/textures/color.jpg"))
        assert _portable_asset_path(path, roots, TARGET) == (path, False)

    def test_a_path_into_the_same_asset_in_another_database_is_not_portable(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = _path(root, "color.jpg")
        roots = _roots((root, ("db2", "a1"), "color.jpg"))
        assert _portable_asset_path(path, roots, TARGET) == (path, False)

    def test_an_unrecorded_local_path_is_not_portable(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = _path(tmp_path, "home", "textures", "color.jpg")
        roots = _roots((root, TARGET, "home/textures/color.jpg"))
        assert _portable_asset_path(path, roots, TARGET) == (path, False)

    def test_with_no_recorded_roots_every_local_path_is_not_portable(self, tmp_path):
        path = _path(tmp_path, "color.jpg")
        assert _portable_asset_path(path, {}, TARGET) == (path, False)

    def test_an_export_to_a_new_asset_matches_no_root(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = _path(root, "color.jpg")
        roots = _roots((root, TARGET, "color.jpg"))
        assert _portable_asset_path(path, roots, ("db", None)) == (path, False)

    def test_a_root_that_held_two_assets_matches_no_target(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = _path(root, "color.jpg")
        roots = _roots((root, None, "color.jpg"))
        assert _portable_asset_path(path, roots, TARGET) == (path, False)

    def test_the_root_directory_itself_is_not_a_file_of_the_asset(self, tmp_path):
        root = tmp_path / "vams_ab"
        roots = _roots((root, TARGET, "color.jpg"))
        assert _portable_asset_path(root.as_posix(), roots, TARGET) == (root.as_posix(), False)


class TestRootBoundaries:
    def test_a_root_that_is_a_prefix_string_of_another_does_not_match_it(self, tmp_path):
        short, longer = tmp_path / "vams_ab", tmp_path / "vams_abc"
        path = _path(longer, "color.jpg")
        # A string-prefix match would read the path as c/color.jpg under the short root; its files
        # include that, so only the directory boundary can refuse the match.
        roots = _roots((short, TARGET, "color.jpg", "c/color.jpg"))
        assert _portable_asset_path(path, roots, TARGET) == (path, False)
        # Control: the longer root, recorded for the target, does match.
        roots = _roots((short, OTHER, "color.jpg"), (longer, TARGET, "color.jpg"))
        assert _portable_asset_path(path, roots, TARGET) == ("./color.jpg", True)

    def test_the_innermost_root_owns_a_path_in_nested_roots(self, tmp_path):
        outer = tmp_path / "vams_ab"
        inner = outer / "nested"
        roots = _roots((outer, TARGET, "nested/color.jpg", "house/color.jpg"),
                       (inner, OTHER, "color.jpg"))
        in_inner = _path(inner, "color.jpg")
        assert _portable_asset_path(in_inner, roots, TARGET) == (in_inner, False)
        assert _portable_asset_path(_path(outer, "house", "color.jpg"), roots, TARGET) == (
            "./house/color.jpg", True)
        swapped = _roots((outer, OTHER, "nested/color.jpg"), (inner, TARGET, "color.jpg"))
        assert _portable_asset_path(in_inner, swapped, TARGET) == ("./color.jpg", True)

    def test_a_path_that_escapes_a_root_through_dotdot_is_not_inside_it(self, tmp_path):
        root = tmp_path / "vams_ab"
        escaping = root.as_posix() + "/../elsewhere/color.jpg"
        roots = _roots((root, TARGET, "elsewhere/color.jpg"))
        assert _portable_asset_path(escaping, roots, TARGET) == (escaping, False)
        into_other = root.as_posix() + "/../vams_abc/color.jpg"
        roots = _roots((root, TARGET, "vams_abc/color.jpg"),
                       (tmp_path / "vams_abc", OTHER, "color.jpg"))
        assert _portable_asset_path(into_other, roots, TARGET) == (into_other, False)

    def test_a_dotdot_that_stays_inside_the_root_is_resolved(self, tmp_path):
        root = tmp_path / "vams_ab"
        path = root.as_posix() + "/snowman/../house/color.jpg"
        roots = _roots((root, TARGET, "house/color.jpg"))
        assert _portable_asset_path(path, roots, TARGET) == ("./house/color.jpg", True)


@pytest.mark.parametrize("path", [
    "https://example.com/textures/color.jpg",
    "http://example.com/textures/color.jpg",
    "omniverse://nucleus.example.com/Projects/house/color.jpg",
    "s3://bucket/house/color.jpg",
    "./house/textures/color.jpg",
    "house/textures/color.jpg",
    "../textures/color.jpg",
    "",
])
def test_a_url_or_a_relative_path_is_left_alone_and_not_counted(tmp_path, path):
    roots = _roots((tmp_path / "vams_ab", TARGET, "house/textures/color.jpg"))
    assert _portable_asset_path(path, roots, TARGET) == (path, True)


def test_a_path_is_never_rewritten_to_an_empty_one(tmp_path):
    """ModifyAssetPaths removes a sublayer, reference or payload whose callback returns ''."""
    root = tmp_path / "vams_ab"
    roots = _roots((root, TARGET, "a.jpg"))
    for path in (_path(root, "a.jpg"), _path(tmp_path, "b.jpg"), "https://example.com/c.jpg"):
        new_path, _portable = _portable_asset_path(path, roots, TARGET)
        assert new_path
        assert os.path.basename(new_path) in ("a.jpg", "b.jpg", "c.jpg")
