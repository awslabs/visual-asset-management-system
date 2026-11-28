#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/3dBasic: an input too large for the function's memory or working disk fails before it is
downloaded, naming its size, the limit and the resource that sets it.

``size_check.py`` derives the limit from the running function (``AWS_LAMBDA_FUNCTION_MEMORY_SIZE`` and
the free space of /tmp) and a per-format expansion factor; ``lambda.convert_input_output`` reads the
input's size with a HeadObject at the run's launch version and refuses before any download."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inprocess_worker import in_process_worker  # noqa: E402
from botocore.exceptions import ClientError

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import size_check  # noqa: E402
from test_conversion_contract import _exporting_trimesh, _load  # noqa: E402

MB = size_check.MB
OUTPUT = "s3://obkt/pipelines/p1/JOB/output/E1/files/"
FUNCTION_MEMORY_MB = 5308
BIG_DISK = 4096 * MB


MEGABYTE = 1000 ** 2


@pytest.mark.unit
class TestTheLimitDependsOnTheOutput:
    """A USD or COLLADA output needs more memory per input byte than the other outputs (the reviewer
    measured a 330 MB STL to USDA peaking at 5,307 of 5,308 MB). Each pair that needs clearly more
    has its own factor, so every output keeps the same headroom at its limit."""

    def test_a_330_mb_stl_to_usda_is_refused_naming_the_output(self):
        with pytest.raises(size_check.InputTooLargeError,
                           match=r"near\.stl is 314\.7 MB, over the [\d.]+ MB limit for converting \.stl "
                                 r"to \.usda\. The limit is set by the function memory\..* A conversion to "
                                 r"another format may accept it\."):
            size_check.check_size("near.stl", ".stl", 330 * MEGABYTE, FUNCTION_MEMORY_MB * MB, BIG_DISK * 100,
                                  output_extension=".usda")

    @pytest.mark.parametrize("output", [".glb", ".gltf", ".obj", ".ply", ".stl", ".off", ".3mf"])
    def test_the_same_stl_to_another_format_is_admitted(self, output):
        assert size_check.check_size("near.stl", ".stl", 330 * MEGABYTE, FUNCTION_MEMORY_MB * MB,
                                     BIG_DISK * 100, output_extension=output) is not None

    @pytest.mark.parametrize("output", [".usdc", ".usda", ".usdz", ".dae"])
    def test_every_heavier_output_is_refused_for_it(self, output):
        with pytest.raises(size_check.InputTooLargeError, match=f"to \\{output}"):
            size_check.check_size("near.stl", ".stl", 330 * MEGABYTE, FUNCTION_MEMORY_MB * MB, BIG_DISK * 100,
                                  output_extension=output)

    def test_the_measured_peak_at_each_limit_keeps_the_same_headroom(self):
        # The measured peaks per input byte (built image, 163,842-vertex sphere) of the pairs with an
        # entry: at the admitted maximum the peak stays at or under 85 percent of the function memory.
        measured = {(".stl", ".usda"): 16.5, (".stl", ".usdc"): 17.1, (".stl", ".dae"): 19.9,
                    (".glb", ".usdc"): 32.6, (".ply", ".usda"): 32.5, (".3mf", ".dae"): 118.1,
                    (".usdc", ".usdz"): 42.4, (".off", ".usdc"): 18.9}
        memory = FUNCTION_MEMORY_MB * MB
        for (source, output), per_byte in measured.items():
            limit, _ = size_check.limits(source, memory, None, output)
            peak = size_check.BASE_MEMORY_BYTES + per_byte * limit
            assert peak <= 0.85 * memory, (source, output, peak / memory)

    def test_the_pr1_conversions_keep_their_limits(self):
        for source in (".stl", ".obj", ".ply", ".glb", ".gltf", ".xyz"):
            for output in (".stl", ".obj", ".ply", ".glb", ".gltf"):
                assert size_check.expansion_factor(source, output) == size_check.MEMORY_EXPANSION_FACTORS[source]

    def test_the_input_only_message_is_unchanged_without_an_output(self):
        with pytest.raises(size_check.InputTooLargeError, match=r"limit for \.stl inputs\. The limit"):
            size_check.check_size("x.stl", ".stl", 10 ** 12, FUNCTION_MEMORY_MB * MB, None)

    def test_every_entry_is_at_least_the_input_factor(self):
        for output, entries in size_check.OUTPUT_EXPANSION_FACTORS.items():
            for source, factor in entries.items():
                assert factor >= size_check.MEMORY_EXPANSION_FACTORS[source], (source, output)


@pytest.mark.unit
class TestTheDeployedFunctionMemory:
    """The function runs at 10,240 MB (CONVERSION_MEMORY_SIZE_MB in conversion3dBasicFunctions.ts): the
    limits the pipeline page lists at that memory, and the peaks measured there in the built image with
    the worker's limits, scaled to each pair's limit, at or under 85 percent of the function memory."""

    MEMORY = 10240 * MB

    @pytest.mark.parametrize("source,output,limit_mb", [
        (".step", ".usdc", 81.8), (".stl", ".usda", 424.6), (".glb", ".glb", 330.2), (".stl", ".stl", 636.9),
        (".obj", ".obj", 990.7), (".3mf", ".usdc", 84.1), (".usdc", ".dae", 99.1),
    ])
    def test_the_documented_limits(self, source, output, limit_mb):
        limit, resource = size_check.limits(source, self.MEMORY, None, output)
        assert round(limit / MB, 1) == limit_mb and resource == size_check.MEMORY_RESOURCE

    def test_six_gib_of_disk_keeps_memory_the_binding_limit(self):
        disk = 6 * 1024 * MB - 200 * MB  # less what the image and the runtime keep in /tmp
        for source in size_check.MEMORY_EXPANSION_FACTORS:
            assert size_check.limits(source, self.MEMORY, disk)[1] == size_check.MEMORY_RESOURCE, source

    @pytest.mark.parametrize("source,output,input_mb,peak_mb", [
        # Measured at 10,240 MB with the worker's address-space and file-size limits (built image);
        # peak_mb includes the interpreter and library baseline of about 100 MB.
        (".stl", ".stl", 617.8, 7063), (".stl", ".usda", 411.8, 4764), (".glb", ".glb", 384.4, 2017),
        (".step", ".usdc", 54.6, 1681), (".obj", ".obj", 550.2, 3249), (".obj", ".usdc", 378.3, 4071),
    ])
    def test_the_measured_peak_scaled_to_the_limit_keeps_the_headroom(self, source, output, input_mb, peak_mb):
        limit, _ = size_check.limits(source, self.MEMORY, None, output)
        per_byte = peak_mb / input_mb
        assert per_byte * limit <= 0.85 * self.MEMORY, (source, output, per_byte * limit / self.MEMORY)
        assert peak_mb * MB <= size_check.address_space_limit_bytes({"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "10240"})


@pytest.mark.unit
class TestLimits:
    def test_the_memory_limit_is_the_usable_share_less_the_base_over_the_format_factor(self):
        limit, resource = size_check.limits(".ply", FUNCTION_MEMORY_MB * MB, BIG_DISK * 100)
        usable = FUNCTION_MEMORY_MB * MB * size_check.MEMORY_USABLE_FRACTION - size_check.BASE_MEMORY_BYTES
        assert limit == int(usable / size_check.MEMORY_EXPANSION_FACTORS[".ply"])
        assert resource == size_check.MEMORY_RESOURCE

    def test_the_disk_limit_leaves_room_for_the_input_and_its_output(self):
        limit, resource = size_check.limits(".stl", FUNCTION_MEMORY_MB * MB * 100, 1000 * MB)
        assert limit == int((1000 * MB - size_check.DISK_HEADROOM_BYTES) / (1 + size_check.OUTPUT_DISK_FACTOR))
        assert resource == size_check.DISK_RESOURCE

    def test_the_lower_of_the_two_binds(self):
        small_memory, _ = size_check.limits(".glb", 1024 * MB, None)
        small_disk, _ = size_check.limits(".glb", None, 600 * MB)
        both, resource = size_check.limits(".glb", 1024 * MB, 600 * MB)
        assert both == min(small_memory, small_disk)
        assert resource == (size_check.MEMORY_RESOURCE if small_memory < small_disk else size_check.DISK_RESOURCE)

    @pytest.mark.parametrize("extension", sorted(size_check.MEMORY_EXPANSION_FACTORS))
    def test_every_format_the_pipeline_reads_has_a_measured_factor(self, extension):
        assert size_check.expansion_factor(extension) == size_check.MEMORY_EXPANSION_FACTORS[extension]

    def test_the_factors_cover_exactly_the_accepted_input_formats(self):
        assert set(size_check.MEMORY_EXPANSION_FACTORS) == _load().SUPPORTED_INPUT_FORMATS

    @pytest.mark.parametrize("extension", [".usd", ".usda", ".usdc", ".usdz", ".dae", ".3mf", ".off"])
    def test_every_new_input_format_has_its_own_measured_factor(self, extension):
        assert extension in size_check.MEMORY_EXPANSION_FACTORS
        # COLLADA output needs more than the input factor for every new input except the generic .usd,
        # whose factor (that of a binary layer) already covers a text layer to COLLADA.
        assert extension in size_check.OUTPUT_EXPANSION_FACTORS[".dae"] or extension == ".usd"

    def test_a_collada_output_never_lowers_the_factor_and_raises_it_where_measured(self):
        for extension, base in size_check.MEMORY_EXPANSION_FACTORS.items():
            assert size_check.expansion_factor(extension, ".dae") >= base, extension
            assert size_check.expansion_factor(extension, ".glb") == base, extension
        assert size_check.expansion_factor(".glb", ".dae") > size_check.expansion_factor(".glb", ".obj")
        assert size_check.expansion_factor(".glb") == 27, "an existing format keeps its factor for other outputs"

    def test_the_collada_limit_is_lower_for_the_same_input(self):
        plain, _ = size_check.limits(".glb", FUNCTION_MEMORY_MB * MB, BIG_DISK * 100)
        collada, _ = size_check.limits(".glb", FUNCTION_MEMORY_MB * MB, BIG_DISK * 100, ".dae")
        assert collada < plain

    def test_a_larger_function_converts_a_larger_input(self):
        small, _ = size_check.limits(".obj", 2048 * MB, None)
        large, _ = size_check.limits(".obj", 8192 * MB, None)
        assert large > small

    def test_a_tiny_function_has_a_zero_limit_rather_than_a_negative_one(self):
        assert size_check.limits(".obj", 100 * MB, None)[0] == 0

    def test_nothing_known_sets_no_limit(self):
        assert size_check.limits(".obj", None, None) == (None, None)


@pytest.mark.unit
class TestMemoryLimitFromTheEnvironment:
    def test_megabytes_become_bytes(self):
        assert size_check.memory_limit_bytes({"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "5308"}) == 5308 * MB

    @pytest.mark.parametrize("value", [None, "", "abc", "0", "-5"])
    def test_an_absent_or_invalid_value_is_no_limit(self, value):
        environ = {} if value is None else {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": value}
        assert size_check.memory_limit_bytes(environ) is None


@pytest.mark.unit
class TestAddressSpaceLimit:
    def test_it_is_the_function_memory_plus_the_parsers_overhead(self):
        assert size_check.address_space_limit_bytes({"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "5308"}) == \
            5308 * MB + size_check.ADDRESS_SPACE_OVERHEAD_BYTES

    def test_it_is_absent_outside_lambda(self):
        assert size_check.address_space_limit_bytes({}) is None


@pytest.mark.unit
class TestCheckSize:
    def test_an_input_within_the_limit_passes_and_the_limit_is_returned(self):
        limit = size_check.check_size("m.stl", ".stl", 10 * MB, FUNCTION_MEMORY_MB * MB, BIG_DISK)
        assert limit > 10 * MB

    def test_the_message_names_the_size_the_limit_and_the_binding_resource(self):
        with pytest.raises(size_check.InputTooLargeError) as excinfo:
            size_check.check_size("huge.ply", ".PLY", 900 * MB, FUNCTION_MEMORY_MB * MB, BIG_DISK)
        message = str(excinfo.value)
        limit = size_check.limits(".ply", FUNCTION_MEMORY_MB * MB, BIG_DISK)[0]
        assert "huge.ply" in message
        assert "900.0 MB" in message
        assert f"{limit / MB:,.1f} MB" in message
        assert size_check.MEMORY_RESOURCE in message
        assert ".ply" in message

    def test_a_full_disk_names_the_disk(self):
        with pytest.raises(size_check.InputTooLargeError, match=r"working disk \(/tmp\)"):
            size_check.check_size("m.glb", ".glb", 300 * MB, FUNCTION_MEMORY_MB * MB * 50, 500 * MB)

    def test_the_declared_size_of_referenced_files_counts_with_the_input(self):
        limit = size_check.limits(".gltf", FUNCTION_MEMORY_MB * MB, BIG_DISK)[0]
        assert size_check.check_size("m.gltf", ".gltf", 1000, FUNCTION_MEMORY_MB * MB, BIG_DISK,
                                     extra_bytes=limit - 2000)
        with pytest.raises(size_check.InputTooLargeError):
            size_check.check_size("m.gltf", ".gltf", 1000, FUNCTION_MEMORY_MB * MB, BIG_DISK,
                                  extra_bytes=limit)

    def test_with_no_known_limit_nothing_is_refused(self):
        assert size_check.check_size("m.stl", ".stl", 10 ** 12) is None


@pytest.mark.unit
class TestHeadSize:
    def test_the_launch_version_is_sent(self):
        client = MagicMock()
        client.head_object.return_value = {"ContentLength": 42}
        assert size_check.head_size(client, "b", "k", "v1") == 42
        client.head_object.assert_called_once_with(Bucket="b", Key="k", VersionId="v1")

    def test_no_version_reads_the_current_object(self):
        client = MagicMock()
        client.head_object.return_value = {"ContentLength": 7}
        size_check.head_size(client, "b", "k")
        client.head_object.assert_called_once_with(Bucket="b", Key="k")


def _client(size, error=None):
    client = MagicMock()
    if error:
        client.head_object.side_effect = error
    else:
        client.head_object.return_value = {"ContentLength": size}
    return client


def _convert(mod, client, key="xid/model.ply", version="", memory_mb=FUNCTION_MEMORY_MB, free=BIG_DISK):
    download = MagicMock(side_effect=lambda bucket, k, path, **kw: open(path, "wb").close() or path)
    with patch.object(mod, "_s3_client_for_bucket", lambda bucket: client), \
            patch.object(mod, "download", download), \
            patch.object(mod, "uploadV2", MagicMock(side_effect=lambda bucket, k, path: k)), \
            patch.object(mod, "run_worker", in_process_worker(_exporting_trimesh())), \
            patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": str(memory_mb)}), \
            patch.object(size_check, "free_disk_bytes", lambda path: free):
        try:
            mod.convert_input_output(f"s3://abkt/{key}", OUTPUT, ".glb", "", version)
        finally:
            convert_download = download
    return convert_download


@pytest.mark.unit
class TestTheConversionRefusesBeforeDownloading:
    def test_a_declared_large_object_fails_before_any_download(self):
        # A sparse object: its declared size is large, its body is not. Only the HEAD is made.
        mod = _load()
        client = _client(2000 * MB)
        download = MagicMock()
        with patch.object(mod, "_s3_client_for_bucket", lambda bucket: client), \
                patch.object(mod, "download", download), \
                patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": str(FUNCTION_MEMORY_MB)}), \
                patch.object(size_check, "free_disk_bytes", lambda path: BIG_DISK):
            with pytest.raises(size_check.InputTooLargeError) as excinfo:
                mod.convert_input_output("s3://abkt/xid/model.ply", OUTPUT, ".glb", "", "")
        download.assert_not_called()
        assert "model.ply" in str(excinfo.value) and "2,000.0 MB" in str(excinfo.value)
        client.head_object.assert_called_once_with(Bucket="abkt", Key="xid/model.ply")

    @pytest.mark.parametrize("output", [".usda", ".usdc", ".dae"])
    def test_a_330_mb_stl_to_a_heavier_output_is_refused_before_download_naming_it(self, output):
        mod = _load()
        client = _client(330 * 1000 ** 2)
        download = MagicMock()
        with patch.object(mod, "_s3_client_for_bucket", lambda bucket: client), \
                patch.object(mod, "download", download), \
                patch.dict(os.environ, {"AWS_LAMBDA_FUNCTION_MEMORY_SIZE": str(FUNCTION_MEMORY_MB)}), \
                patch.object(size_check, "free_disk_bytes", lambda path: BIG_DISK * 100):
            with pytest.raises(size_check.InputTooLargeError, match=f"limit for converting \\.stl to \\{output}"):
                mod.convert_input_output("s3://abkt/xid/near.stl", OUTPUT, output, "", "")
        download.assert_not_called()

    def test_the_same_stl_to_glb_is_still_downloaded(self):
        mod = _load()
        download = _convert(mod, _client(330 * 1000 ** 2), key="xid/near.stl", free=BIG_DISK * 100)
        download.assert_called_once()

    def test_the_head_reads_the_launch_version(self):
        mod = _load()
        client = _client(1000)
        _convert(mod, client, version="v-launch")
        client.head_object.assert_any_call(Bucket="abkt", Key="xid/model.ply", VersionId="v-launch")

    def test_an_input_that_fits_is_downloaded(self):
        mod = _load()
        download = _convert(mod, _client(1 * MB))
        download.assert_called_once()

    def test_a_small_function_refuses_what_a_large_one_converts(self):
        mod = _load()
        _convert(mod, _client(20 * MB), memory_mb=FUNCTION_MEMORY_MB)
        with pytest.raises(size_check.InputTooLargeError, match="function memory"):
            _convert(mod, _client(20 * MB), memory_mb=512)

    def test_a_deleted_version_fails_naming_the_file_and_version(self):
        mod = _load()
        error = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        with pytest.raises(Exception) as excinfo:
            _convert(mod, _client(0, error), version="v-gone")
        assert "xid/model.ply" in str(excinfo.value) and "v-gone" in str(excinfo.value)

    def test_other_head_failures_keep_a_generic_message(self):
        mod = _load()
        error = ClientError({"Error": {"Code": "403"}}, "HeadObject")
        with pytest.raises(Exception, match="Could not read the input file's size"):
            _convert(mod, _client(0, error))

    def test_the_files_an_input_references_are_bounded_by_the_remaining_limit(self):
        mod = _load()
        client = _client(1000)
        captured = {}

        def _fetch(*args, **kwargs):
            captured["limits"] = kwargs["limits"]
            return MagicMock(by_reference={})

        with patch.object(mod.referenced_files, "fetch_referenced_files", _fetch), \
                patch.object(mod.referenced_files, "ConfinedResolver", MagicMock()):
            _convert(mod, client, key="xid/model.gltf")
        limit = size_check.limits(".gltf", FUNCTION_MEMORY_MB * MB, BIG_DISK)[0]
        # Files that carry geometry are bounded by what is left of the input's limit; textures are
        # bounded apart, by decoded pixels.
        assert captured["limits"].max_geometry_bytes == limit - 1000
        assert captured["limits"].max_total_bytes == mod.referenced_files.DEFAULT_MAX_TOTAL_BYTES
        assert captured["limits"].image_budget(1000) == size_check.texture_budget_bytes(
            ".gltf", 1000, FUNCTION_MEMORY_MB * MB)
