#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/3dBasic: texture images are budgeted by their decoded pixels, apart from the files that
carry geometry, and a referenced file left out for size is named in the run's completion message.

Before, the referenced files shared one byte cap derived from the geometry memory factor (27 for glTF
and GLB), so a glTF with large PNG textures lost some of them and the run still reported plain success.
"""

import io
import os
import sys
from unittest.mock import MagicMock

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import referenced_files as rf  # noqa: E402
import size_check  # noqa: E402
from test_3dbasic_referenced_files import FakeS3, ROOT, _fetch, _gltf  # noqa: E402
from test_conversion_contract import _load  # noqa: E402

MB = size_check.MB
FUNCTION_MEMORY = 5308 * MB


def _png(width, height):
    """A PNG of one colour: a few KB on disk, width x height x 4 bytes decoded."""
    PIL = pytest.importorskip("PIL.Image")
    out = io.BytesIO()
    PIL.new("RGB", (width, height), (10, 20, 30)).save(out, format="PNG")
    return out.getvalue()


@pytest.mark.unit
class TestTheTextureBudget:
    def test_a_12000_square_texture_fits_the_5308_mb_function(self):
        budget = size_check.texture_budget_bytes(".glb", 1 * MB, FUNCTION_MEMORY)
        assert budget >= 12000 * 12000 * rf.DECODED_BYTES_PER_PIXEL

    def test_four_4096_square_textures_fit_beside_a_small_gltf(self):
        budget = size_check.texture_budget_bytes(".gltf", 1 * MB, FUNCTION_MEMORY)
        assert budget >= 4 * 4096 * 4096 * rf.DECODED_BYTES_PER_PIXEL

    def test_geometry_takes_its_share_first(self):
        small = size_check.texture_budget_bytes(".glb", 1 * MB, FUNCTION_MEMORY)
        large = size_check.texture_budget_bytes(".glb", 100 * MB, FUNCTION_MEMORY)
        assert abs((small - large) - 99 * MB * size_check.expansion_factor(".glb")
                   / size_check.TEXTURE_MEMORY_FACTOR) <= 1

    def test_a_smaller_function_has_a_smaller_budget_and_none_goes_below_zero(self):
        assert size_check.texture_budget_bytes(".glb", 1 * MB, 1024 * MB) < \
            size_check.texture_budget_bytes(".glb", 1 * MB, FUNCTION_MEMORY)
        assert size_check.texture_budget_bytes(".glb", 10_000 * MB, 1024 * MB) == 0

    def test_without_a_known_memory_there_is_no_budget(self):
        assert size_check.texture_budget_bytes(".glb", 1, None) is None


@pytest.mark.unit
class TestTexturesAreNotBoundedByTheGeometryFactor:
    """The fixture of the review: four ~48 MB textures beside a tiny glTF. With the geometry factor
    applied to file bytes the cap was 173,874,500 bytes and the last texture was dropped."""

    def _model(self, tmp_path, textures, **limits):
        client = FakeS3({ROOT + "b0.bin": b"x" * 4, **{ROOT + name: data for name, data in textures.items()}})
        document = _gltf("b0.bin", images=list(textures))
        return _fetch(tmp_path, client, "m.gltf", document, limits=rf.FetchLimits(**limits))

    def test_large_textures_are_all_fetched_when_the_geometry_cap_is_small(self, tmp_path):
        # 4 x 3 MB of file bytes against a 1 MB geometry cap: the old single cap refused them.
        textures = {f"t{i}.png": b"\x89PNG" + b"0" * (3 * MB) for i in range(4)}
        _, report = self._model(tmp_path, textures, max_geometry_bytes=1 * MB,
                                image_budget=lambda geometry: 10_000 * MB)
        assert sorted(report.fetched) == ["b0.bin"] + sorted(textures)
        assert report.skipped_for_size_count == 0

    def test_a_texture_over_the_decoded_budget_is_dropped_and_reported_for_size(self, tmp_path):
        textures = {"small.png": _png(64, 64), "huge.png": _png(2000, 2000)}
        _, report = self._model(tmp_path, textures, image_budget=lambda geometry: 1 * MB)
        assert "small.png" in report.fetched and "huge.png" not in report.fetched
        assert report.skipped_for_size_count == 1
        assert report.skipped_for_size[0].startswith("huge.png:")
        assert "decodes to 15 MB" in report.skipped_for_size[0]
        # The file the loader cannot use was removed, so it cannot be served.
        assert "huge.png" not in "".join(report.by_reference)

    def test_a_highly_compressed_texture_is_measured_by_its_pixels_not_its_file_size(self, tmp_path):
        data = _png(4000, 4000)
        assert len(data) < 1 * MB
        _, report = self._model(tmp_path, {"flat.png": data}, image_budget=lambda geometry: 32 * MB)
        assert report.skipped_for_size_count == 1

    def test_the_decoded_bytes_of_every_texture_share_one_budget(self, tmp_path):
        one = 1000 * 1000 * rf.DECODED_BYTES_PER_PIXEL
        textures = {f"t{i}.png": _png(1000, 1000) for i in range(3)}
        _, report = self._model(tmp_path, textures, image_budget=lambda geometry: int(one * 2.5))
        assert report.skipped_for_size_count == 1
        assert report.image_decoded_bytes == 2 * one

    def test_the_geometry_bytes_so_far_are_passed_to_the_budget(self, tmp_path):
        seen = []

        def budget(geometry_bytes):
            seen.append(geometry_bytes)
            return 100 * MB

        self._model(tmp_path, {"t.png": _png(8, 8)}, image_budget=budget)
        # The input document plus the 4-byte buffer already fetched.
        assert len(seen) == 1 and seen[0] > 4

    def test_a_buffer_over_the_geometry_cap_still_fails(self, tmp_path):
        client = FakeS3({ROOT + "b0.bin": b"x" * 100})
        with pytest.raises(rf.ReferencedFileError):
            _fetch(tmp_path, client, "m.gltf", _gltf("b0.bin"), limits=rf.FetchLimits(max_geometry_bytes=10))

    def test_an_optional_non_image_over_the_geometry_cap_is_reported_for_size(self, tmp_path):
        client = FakeS3({ROOT + "m.mtl": b"newmtl a\n" + b"#" * 100})
        _, report = _fetch(tmp_path, client, "m.obj", "mtllib m.mtl\nv 0 0 0\n",
                           limits=rf.FetchLimits(max_geometry_bytes=10))
        assert report.skipped_for_size_count == 1 and report.skipped_for_size[0].startswith("m.mtl:")


@pytest.mark.unit
class TestTheCompletionMessageNamesWhatWasLeftOut:
    def test_a_run_that_left_nothing_out_has_no_message(self):
        mod = _load()
        assert mod.incomplete_message("m.gltf", rf.FetchReport()) is None

    def test_the_message_names_the_files_and_says_the_model_is_incomplete(self):
        mod = _load()
        report = rf.FetchReport(skipped_for_size=["a.png: too big", "b.png: too big"], skipped_for_size_count=2)
        message = mod.incomplete_message("m.gltf", report)
        assert "2 referenced files" in message and "a.png: too big" in message
        assert "incomplete" in message

    def test_files_past_the_listed_ones_are_counted(self):
        mod = _load()
        names = [f"t{i}.png: too big" for i in range(mod.INCOMPLETE_MESSAGE_FILES)]
        message = mod.incomplete_message("m.gltf", rf.FetchReport(
            skipped_for_size=names, skipped_for_size_count=mod.INCOMPLETE_MESSAGE_FILES + 3))
        assert "and 3 more" in message

    def test_the_handler_returns_the_message_as_the_body_instead_of_success(self):
        mod = _load()
        mod.convert_input_output = MagicMock(return_value="Converted m.gltf, but 1 referenced file ...")
        mod.fetch_input_configuration = MagicMock(return_value={"outputType": ".glb"})
        mod.resolve_inputs_from_manifest = MagicMock(return_value=("s3://b/k.gltf", "s3://b/o/", "", ""))
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 900000
        result = mod.lambda_handler({"body": {"inputManifestS3Location": ""}}, context)
        assert result["statusCode"] == 200 and result["body"].startswith("Converted m.gltf, but 1")

    def test_a_complete_run_still_answers_success(self):
        mod = _load()
        mod.convert_input_output = MagicMock(return_value=None)
        mod.fetch_input_configuration = MagicMock(return_value={"outputType": ".glb"})
        mod.resolve_inputs_from_manifest = MagicMock(return_value=("s3://b/k.gltf", "s3://b/o/", "", ""))
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 900000
        assert mod.lambda_handler({"body": {"inputManifestS3Location": ""}}, context)["body"] == "Success"
