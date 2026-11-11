#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A LAS/LAZ input whose Extra Bytes descriptor PotreeConverter reads past goes through PDAL (#783).

PotreeConverter's parseExtraAttributes copies each Extra Bytes descriptor's 32-byte name and
description into 32-byte buffers and builds a std::string from each, which reads until a NUL. The
LAS specification lets both fields fill all 32 bytes, so a file with such a descriptor (the #783 file's
OriginId description) makes PotreeConverter read past the buffer and crash on some runs (exit 139).

openPipeline now reads the VLR block with a second ranged GET (bounded by the point data offset and a
64 KiB cap) and flags such a descriptor; constructPipeline sends a flagged file through PDAL. A
well-formed Extra Bytes file, a malformed or truncated VLR block, a failed read and an execution
without the field keep the direct path. Before this change none of this was read and every format
0-7 file went straight to PotreeConverter."""

import datetime
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
HEADER_SIZE = 227


def descriptor(name=b"OriginId", description=b"origin file source id"):
    """One 192-byte Extra Bytes descriptor; a 32-byte field is stored as given, NUL-padded if shorter."""
    record = bytearray(192)
    record[2] = 5  # data type: int32
    record[4:4 + len(name)] = name
    record[160:160 + len(description)] = description
    return bytes(record)


def vlr(user_id, record_id, data):
    header = bytearray(54)
    header[2:2 + len(user_id)] = user_id
    struct.pack_into("<HH", header, 18, record_id, len(data))
    return bytes(header) + data


def extra_bytes_vlr(*descriptors):
    return vlr(b"LASF_Spec", 4, b"".join(descriptors))


def las_file(vlrs=(), point_format=2, compressed=True, vlr_count=None, point_data_offset=None):
    """A LAS header, its VLRs and a few bytes of point data, with the header fields describing them."""
    block = b"".join(vlrs)
    header = bytearray(HEADER_SIZE)
    header[0:4] = b"LASF"
    header[24], header[25] = 1, 2
    struct.pack_into("<H", header, 94, HEADER_SIZE)
    struct.pack_into("<I", header, 96, HEADER_SIZE + len(block) if point_data_offset is None else point_data_offset)
    struct.pack_into("<I", header, 100, len(vlrs) if vlr_count is None else vlr_count)
    header[104] = point_format | (0x80 if compressed else 0)
    return bytes(header) + block + b"\x01" * 64


def ranged_s3(data):
    """An S3 client stub serving ranged GETs of ``data`` and recording each Range."""
    client = MagicMock()

    def get_object(**kwargs):
        first, last = (int(x) for x in kwargs["Range"][len("bytes="):].split("-"))
        return {"Body": io.BytesIO(data[first:last + 1])}

    client.get_object.side_effect = get_object
    return client


LAZIP_VLR = vlr(b"laszip encoded", 22204, b"\x00" * 52)
WELL_FORMED = las_file([extra_bytes_vlr(descriptor(), descriptor(b"Height", b"height above ground")), LAZIP_VLR])
# The #783 shape: name NUL-terminated, description fills all 32 bytes.
UNTERMINATED_DESCRIPTION = las_file([extra_bytes_vlr(descriptor(description=b"A file source ID from which the ")),
                                     LAZIP_VLR])
UNTERMINATED_NAME = las_file([LAZIP_VLR, extra_bytes_vlr(descriptor(), descriptor(name=b"N" * 32))])


def _las():
    if "lasHeader" in sys.modules:
        return importlib.reload(sys.modules["lasHeader"])
    return importlib.import_module("lasHeader")


def _construct():
    if "constructPipeline" in sys.modules:
        return importlib.reload(sys.modules["constructPipeline"])
    return importlib.import_module("constructPipeline")


def _open_pipeline():
    path = os.path.join(_LAMBDA_DIR, "openPipeline.py")
    spec = importlib.util.spec_from_file_location("pcPotreeViewer_openPipeline_extra_bytes", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _construct_event(**extra):
    event = {
        "jobName": "PipelineJob_20261107_010203_004_abcd1234",
        "inputS3AssetFilePath": "s3://asset-bkt/xid1/lion.laz",
        "outputS3AssetFilesPath": "s3://asset-bkt/xid1/",
        "outputS3AssetPreviewPath": "s3://asset-bkt/xid1/",
        "outputS3AssetMetadataPath": "s3://asset-bkt/xid1/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux-bkt/db1/xid1/lion.laz/preview/PotreeViewer",
        "externalSfnTaskToken": "tok",
    }
    event.update(extra)
    return event


@pytest.mark.unit
class TestVlrWalk:
    def _walk(self, data):
        las = _las()
        prefix = data[:las.HEADER_PREFIX_BYTES]
        first, last = las.vlr_block_range(prefix)
        return las.has_unterminated_extra_bytes_text(data[first:last + 1], struct.unpack_from("<I", data, 100)[0])

    def test_well_formed_descriptors_are_not_flagged(self):
        assert self._walk(WELL_FORMED) is False

    def test_an_unterminated_description_is_flagged(self):
        assert self._walk(UNTERMINATED_DESCRIPTION) is True

    def test_an_unterminated_name_in_a_later_vlr_is_flagged(self):
        assert self._walk(UNTERMINATED_NAME) is True

    def test_an_unterminated_text_outside_extra_bytes_is_ignored(self):
        """Only the LASF_Spec record 4 descriptors are read by parseExtraAttributes."""
        other = las_file([vlr(b"LASF_Projection", 34735, descriptor(name=b"N" * 32))])
        assert self._walk(other) is False

    def test_a_vlr_count_past_the_block_is_malformed(self):
        data = las_file([extra_bytes_vlr(descriptor())], vlr_count=3)
        assert self._walk(data) is None

    def test_a_vlr_longer_than_the_block_is_malformed(self):
        """The declared VLR length runs past the point data offset."""
        block = extra_bytes_vlr(descriptor(description=b"D" * 32))
        data = las_file([block], point_data_offset=HEADER_SIZE + len(block) - 100)
        assert self._walk(data) is None

    def test_a_truncated_vlr_header_is_malformed(self):
        las = _las()
        assert las.has_unterminated_extra_bytes_text(extra_bytes_vlr(descriptor())[:30], 1) is None

    def test_no_vlrs_or_an_oversized_block_reads_nothing(self):
        las = _las()
        assert las.vlr_block_range(las_file([])[:105]) is None
        huge = las_file([extra_bytes_vlr(descriptor())], point_data_offset=HEADER_SIZE + 64 * 1024 + 1)
        assert las.vlr_block_range(huge[:105]) is None
        bad = las_file([extra_bytes_vlr(descriptor())], point_data_offset=100)
        assert las.vlr_block_range(bad[:105]) is None


@pytest.mark.unit
class TestReadRoutingFacts:
    def test_flags_the_783_shape_with_two_ranged_reads_of_the_launch_version(self):
        las = _las()
        client = ranged_s3(UNTERMINATED_DESCRIPTION)
        facts = las.read_routing_facts(client, "asset-bkt", "xid1/lion.laz", VERSION)

        assert facts == {"pointFormat": 2, "unterminatedExtraBytes": True}
        ranges = [c.kwargs["Range"] for c in client.get_object.call_args_list]
        point_data_offset = struct.unpack_from("<I", UNTERMINATED_DESCRIPTION, 96)[0]
        assert ranges == ["bytes=0-104", f"bytes={HEADER_SIZE}-{point_data_offset - 1}"]
        assert all(c.kwargs["VersionId"] == VERSION for c in client.get_object.call_args_list)

    def test_a_well_formed_file_is_not_flagged(self):
        assert _las().read_routing_facts(ranged_s3(WELL_FORMED), "b", "k.laz") == {"pointFormat": 2}

    def test_a_format_8_file_skips_the_vlr_read(self):
        las = _las()
        client = ranged_s3(las_file([extra_bytes_vlr(descriptor(name=b"N" * 32))], point_format=8))
        assert las.read_routing_facts(client, "b", "k.laz") == {"pointFormat": 8}
        assert client.get_object.call_count == 1

    def test_a_failed_vlr_read_keeps_the_format_only(self):
        las = _las()
        client = MagicMock()
        client.get_object.side_effect = [{"Body": io.BytesIO(UNTERMINATED_DESCRIPTION[:105])}, Exception("timeout")]
        assert las.read_routing_facts(client, "b", "k.laz") == {"pointFormat": 2}

    def test_a_vlr_block_cut_short_by_the_object_is_not_flagged(self):
        """The object ends before the declared point data offset: the block read is short and the walk
        reports it malformed, so the file keeps the direct path."""
        assert _las().read_routing_facts(ranged_s3(UNTERMINATED_DESCRIPTION[:300]), "b", "k.laz") == {"pointFormat": 2}

    def test_read_point_format_still_reads_105_bytes_only(self):
        las = _las()
        client = ranged_s3(UNTERMINATED_DESCRIPTION)
        assert las.read_point_format(client, "b", "k.laz") == 2
        assert client.get_object.call_count == 1


@pytest.mark.unit
class TestRouting:
    def test_a_flagged_file_goes_through_pdal(self):
        """Fails on the old routing, which sent every format 0-7 file straight to POTREE."""
        result = _construct().lambda_handler(
            _construct_event(inputLasPointFormat=2, inputLasUnterminatedExtraBytes=True), None)
        definition = json.loads(result["definition"][0])
        assert result["currentStageType"] == "PDAL"
        assert [s["type"] for s in definition["stages"]] == ["PDAL", "POTREE"]
        assert definition["stages"][0]["inputFile"]["objectKey"] == "xid1/lion.laz"

    @pytest.mark.parametrize("value", [None, False, "true", 1])
    def test_anything_but_true_keeps_the_direct_path_unchanged(self, value):
        construct = _construct()
        before = construct.lambda_handler(_construct_event(inputLasPointFormat=2), None)
        event = _construct_event(inputLasPointFormat=2)
        if value is not None:
            event["inputLasUnterminatedExtraBytes"] = value
        after = construct.lambda_handler(event, None)
        assert after["currentStageType"] == "POTREE"
        assert after == before

    def _start(self, data):
        module = _open_pipeline()
        sfn = MagicMock()
        sfn.start_execution.return_value = {"executionArn": "arn:x", "startDate": datetime.datetime(2026, 11, 7)}
        event = {
            "inputS3AssetFilePath": "s3://asset-bkt/xid1/lion.laz",
            "outputS3AssetFilesPath": "s3://asset-bkt/xid1/",
            "outputS3AssetPreviewPath": "s3://asset-bkt/xid1/",
            "outputS3AssetMetadataPath": "s3://asset-bkt/xid1/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux-bkt/db1/xid1/lion.laz/preview/PotreeViewer",
        }
        with patch.object(module, "sfn", sfn), \
                patch.object(module, "s3_client_for_region", MagicMock(return_value=ranged_s3(data))):
            assert module.lambda_handler(event, None)["statusCode"] == 200
        return json.loads(sfn.start_execution.call_args.kwargs["input"])

    def test_open_pipeline_names_the_flag_for_the_783_shape(self):
        sfn_input = self._start(UNTERMINATED_DESCRIPTION)
        assert sfn_input["inputLasPointFormat"] == 2
        assert sfn_input["inputLasUnterminatedExtraBytes"] is True

    def test_open_pipeline_leaves_a_well_formed_file_unflagged(self):
        sfn_input = self._start(WELL_FORMED)
        assert sfn_input["inputLasPointFormat"] == 2
        assert "inputLasUnterminatedExtraBytes" not in sfn_input
