#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A LAS/LAZ input in a point data record format PotreeConverter cannot read goes through PDAL (#1215).

PotreeConverter (the commit container/Dockerfile_Potree pins) reads point formats 0 to 7 and exits with
"unsupported LAS format: 8" for a LAS 1.4 format 8 file. Before this change every .las/.laz went
straight to the POTREE stage by extension, so such a file always failed. openPipeline now reads the
format from the header's first bytes (a ranged GET, never the extension) and passes it in the state
machine input; constructPipeline sends a format outside 0-7 through the PDAL stage, which writes LAS
1.2 / format 3. Formats 0-7, an unreadable header and an execution started before the field keep the
direct path, with the same definition as before."""

import importlib
import importlib.util
import io
import json
import os
import struct
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:111122223333:stateMachine:sm",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".laz,.las,.e57,.ply",
}.items():
    os.environ.setdefault(_k, _v)

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


def las_header(point_format, compressed=False, version=(1, 4), signature=b"LASF"):
    """The first 227 bytes of a LAS public header with the given point data record format. A LAZ
    file sets bit 7 of the format byte."""
    header = bytearray(227)
    header[0:4] = signature
    header[24], header[25] = version
    header[104] = point_format | (0x80 if compressed else 0)
    struct.pack_into("<H", header, 105, 34)
    return bytes(header)


def _load_las_header():
    if "lasHeader" in sys.modules:
        return importlib.reload(sys.modules["lasHeader"])
    return importlib.import_module("lasHeader")


def _load_construct():
    if "constructPipeline" in sys.modules:
        return importlib.reload(sys.modules["constructPipeline"])
    return importlib.import_module("constructPipeline")


def _load_open_pipeline():
    """This pipeline's openPipeline by path under a unique name (every pipeline ships one)."""
    path = os.path.join(_LAMBDA_DIR, "openPipeline.py")
    spec = importlib.util.spec_from_file_location("pcPotreeViewer_openPipeline_las_routing", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _construct_event(file_key="xid1/scan.laz", **extra):
    event = {
        "jobName": "PipelineJob_20261107_010203_004_abcd1234",
        "inputS3AssetFilePath": f"s3://asset-bkt/{file_key}",
        "outputS3AssetFilesPath": "s3://asset-bkt/xid1/",
        "outputS3AssetPreviewPath": "s3://asset-bkt/xid1/",
        "outputS3AssetMetadataPath": "s3://asset-bkt/xid1/",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://aux-bkt/db1/{file_key}/preview/PotreeViewer",
        "externalSfnTaskToken": "tok",
    }
    event.update(extra)
    return event


def _open_event(file_key="xid1/scan.laz", **extra):
    event = {
        "inputS3AssetFilePath": f"s3://asset-bkt/{file_key}",
        "outputS3AssetFilesPath": "s3://asset-bkt/xid1/",
        "outputS3AssetPreviewPath": "s3://asset-bkt/xid1/",
        "outputS3AssetMetadataPath": "s3://asset-bkt/xid1/",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://aux-bkt/db1/{file_key}/preview/PotreeViewer",
    }
    event.update(extra)
    return event


def _s3_returning(prefix):
    client = MagicMock()
    client.get_object.return_value = {"Body": io.BytesIO(prefix)}
    return client


@pytest.mark.unit
class TestHeaderSniff:
    @pytest.mark.parametrize("point_format", range(11))
    def test_reads_the_format_byte_of_an_uncompressed_header(self, point_format):
        las = _load_las_header()
        assert las.point_format_from_header(las_header(point_format)) == point_format

    @pytest.mark.parametrize("point_format", [3, 7, 8])
    def test_clears_the_laz_compression_bits(self, point_format):
        las = _load_las_header()
        assert las.point_format_from_header(las_header(point_format, compressed=True)) == point_format
        # Older LAZ writers set bit 6 as well as bit 7.
        assert las.point_format_from_header(las_header(point_format | 0x40, compressed=True)) == point_format

    def test_only_the_first_105_bytes_are_needed(self):
        las = _load_las_header()
        assert las.HEADER_PREFIX_BYTES == 105
        assert las.point_format_from_header(las_header(8)[:105]) == 8

    @pytest.mark.parametrize("prefix", [b"", b"LASF", las_header(8)[:104], las_header(8, signature=b"PK\x03\x04")])
    def test_a_short_or_foreign_prefix_is_unknown(self, prefix):
        assert _load_las_header().point_format_from_header(prefix) is None

    def test_potree_converter_formats_are_0_to_7(self):
        las = _load_las_header()
        assert sorted(las.POTREE_CONVERTER_POINT_FORMATS) == list(range(8))
        assert [f for f in range(11) if las.needs_pdal_conversion(f)] == [8, 9, 10]
        assert las.needs_pdal_conversion(None) is False

    def test_reads_a_ranged_prefix_of_the_launch_version(self):
        las = _load_las_header()
        client = _s3_returning(las_header(8, compressed=True)[:105])
        assert las.read_point_format(client, "asset-bkt", "xid1/scan.laz", VERSION) == 8
        client.get_object.assert_called_once_with(
            Bucket="asset-bkt", Key="xid1/scan.laz", Range="bytes=0-104", VersionId=VERSION)

    def test_no_version_reads_the_current_object(self):
        las = _load_las_header()
        client = _s3_returning(las_header(3)[:105])
        assert las.read_point_format(client, "asset-bkt", "xid1/scan.las") == 3
        assert "VersionId" not in client.get_object.call_args.kwargs

    def test_a_failed_read_is_unknown_not_an_error(self):
        las = _load_las_header()
        client = MagicMock()
        client.get_object.side_effect = Exception("AccessDenied")
        assert las.read_point_format(client, "asset-bkt", "xid1/scan.laz") is None


@pytest.mark.unit
class TestConstructPipelineRouting:
    @pytest.mark.parametrize("file_key", ["xid1/scan.laz", "xid1/scan.las", "xid1/SCAN.LAZ"])
    @pytest.mark.parametrize("point_format", [8, 9, 10])
    def test_an_unreadable_format_goes_through_pdal(self, file_key, point_format):
        """Fails on the extension-only routing, which sent every .las/.laz to POTREE."""
        construct = _load_construct()
        result = construct.lambda_handler(
            _construct_event(file_key, inputLasPointFormat=point_format), None)
        definition = json.loads(result["definition"][0])

        assert result["currentStageType"] == "PDAL"
        assert [s["type"] for s in definition["stages"]] == ["PDAL", "POTREE"]
        pdal, potree = definition["stages"]
        # The extension is carried as written, as for an E57/PLY input; the PDAL stage matches it
        # case-insensitively.
        assert pdal["inputFile"] == {"bucketName": "asset-bkt", "objectKey": file_key,
                                     "fileExtension": os.path.splitext(file_key)[1]}
        stem = os.path.splitext(os.path.basename(file_key))[0]
        assert potree["inputFile"]["objectKey"] == pdal["outputFiles"]["objectDir"] + stem + ".laz"
        assert potree["outputFiles"]["objectDir"] == f"db1/{file_key}/preview/PotreeViewer/"

    def test_the_launch_version_goes_to_the_pdal_stage(self):
        construct = _load_construct()
        result = construct.lambda_handler(
            _construct_event(inputLasPointFormat=8, inputVersionId=VERSION), None)
        assert json.loads(result["definition"][1]) == {"inputVersions": {"PDAL": VERSION}}

    @pytest.mark.parametrize("point_format", range(8))
    def test_a_readable_format_keeps_the_direct_path_unchanged(self, point_format):
        construct = _load_construct()
        before = construct.lambda_handler(_construct_event(), None)
        after = construct.lambda_handler(_construct_event(inputLasPointFormat=point_format), None)

        assert after["currentStageType"] == "POTREE"
        assert after == before

    @pytest.mark.parametrize("value", [None, "8", True, 8.0, [8]])
    def test_a_missing_or_malformed_format_keeps_the_direct_path(self, value):
        construct = _load_construct()
        event = _construct_event()
        if value is not None:
            event["inputLasPointFormat"] = value
        assert construct.lambda_handler(event, None)["currentStageType"] == "POTREE"

    def test_e57_and_ply_are_not_affected(self):
        construct = _load_construct()
        for key in ("xid1/scan.e57", "xid1/scan.ply"):
            before = construct.lambda_handler(_construct_event(key), None)
            after = construct.lambda_handler(_construct_event(key, inputLasPointFormat=3), None)
            assert after == before
            assert after["currentStageType"] == "PDAL"


@pytest.mark.unit
class TestOpenPipelineReadsTheHeader:
    def _start(self, module, event, s3_client):
        sfn = MagicMock()
        sfn.start_execution.return_value = {"executionArn": "arn:x",
                                            "startDate": __import__("datetime").datetime(2026, 11, 7)}
        with patch.object(module, "sfn", sfn), \
                patch.object(module, "s3_client_for_region", MagicMock(return_value=s3_client)) as for_region:
            response = module.lambda_handler(event, None)
        assert response["statusCode"] == 200
        return json.loads(sfn.start_execution.call_args.kwargs["input"]), for_region

    def test_a_format_8_laz_is_named_in_the_state_machine_input(self):
        module = _load_open_pipeline()
        client = _s3_returning(las_header(8, compressed=True)[:105])
        sfn_input, for_region = self._start(
            module, _open_event(inputBucketRegion="us-west-2", inputVersionId=VERSION), client)

        assert sfn_input["inputLasPointFormat"] == 8
        for_region.assert_called_once_with("us-west-2")
        client.get_object.assert_called_once_with(
            Bucket="asset-bkt", Key="xid1/scan.laz", Range="bytes=0-104", VersionId=VERSION)

    def test_a_format_3_las_is_named_too(self):
        module = _load_open_pipeline()
        sfn_input, _ = self._start(module, _open_event("xid1/scan.las"), _s3_returning(las_header(3)[:105]))
        assert sfn_input["inputLasPointFormat"] == 3

    def test_an_unreadable_header_leaves_the_input_as_before(self):
        module = _load_open_pipeline()
        client = MagicMock()
        client.get_object.side_effect = Exception("timeout")
        sfn_input, _ = self._start(module, _open_event(), client)
        assert "inputLasPointFormat" not in sfn_input

    def test_a_failing_client_does_not_block_the_launch(self):
        module = _load_open_pipeline()
        sfn = MagicMock()
        sfn.start_execution.return_value = {"executionArn": "arn:x",
                                            "startDate": __import__("datetime").datetime(2026, 11, 7)}
        with patch.object(module, "sfn", sfn), \
                patch.object(module, "s3_client_for_region", MagicMock(side_effect=ValueError("bad region"))):
            assert module.lambda_handler(_open_event(), None)["statusCode"] == 200
        assert "inputLasPointFormat" not in json.loads(sfn.start_execution.call_args.kwargs["input"])

    def test_e57_input_reads_no_header(self):
        module = _load_open_pipeline()
        client = MagicMock()
        sfn_input, _ = self._start(module, _open_event("xid1/scan.e57"), client)
        client.get_object.assert_not_called()
        assert "inputLasPointFormat" not in sfn_input
