#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The LAS point data record format of a .las/.laz input, read from the first bytes of its header.

PotreeConverter (the pinned commit in container/Dockerfile_Potree) reads LAS point data record formats
0 to 7 only: computeOutputAttributes (Converter/include/PotreeConverter.h) exits with "currently
unsupported LAS format" for any other, and the chunker's formatToExtraIndex map
(Converter/src/chunker_countsort_laszip.cpp) has the same eight entries. A LAS 1.4 file in format 8
(RGB + NIR), 9 or 10 (waveform) is therefore converted by the PDAL stage to LAS 1.2 / format 3 first.

The format is read from the file's header bytes, never from its extension: the header is a fixed
layout, so a ranged GET of its first bytes is enough and the point data is never read.

A file in a format PotreeConverter reads is also sent through PDAL when an Extra Bytes descriptor's
name or description fills its 32 bytes with no NUL, which PotreeConverter reads past (#783); the VLR
block is read with a second ranged GET for that."""

import struct
from typing import Optional
from customLogging.logger import safeLogger

# Point data record formats the pinned PotreeConverter reads directly.
POTREE_CONVERTER_POINT_FORMATS = frozenset(range(8))

# The public header's "Point Data Format ID" is the unsigned byte at offset 104 (the same offset in
# LAS 1.0 to 1.4). A LAZ file sets the top bit (LASzip; older writers the next bit as well) to mark
# compression, so the format is its lower six bits.
LAS_SIGNATURE = b"LASF"
POINT_FORMAT_OFFSET = 104
POINT_FORMAT_MASK = 0x3F
HEADER_PREFIX_BYTES = POINT_FORMAT_OFFSET + 1

LAS_INPUT_EXTENSIONS = (".las", ".laz")

logger = safeLogger(service="LasHeader")


def point_format_from_header(prefix: bytes) -> Optional[int]:
    """The point data record format in a LAS header's first bytes, or None when ``prefix`` is not the
    start of a LAS header (too short, or no LASF signature)."""
    if not prefix or len(prefix) < HEADER_PREFIX_BYTES or prefix[:4] != LAS_SIGNATURE:
        return None
    return prefix[POINT_FORMAT_OFFSET] & POINT_FORMAT_MASK


def needs_pdal_conversion(point_format: Optional[int]) -> bool:
    """True when a LAS/LAZ input's format is known and PotreeConverter cannot read it. An unknown
    format (the header could not be read) keeps the direct path, which is the behaviour before the
    header was read at all."""
    return point_format is not None and point_format not in POTREE_CONVERTER_POINT_FORMATS


def _ranged_read(s3_client, bucket_name: str, object_key: str, version_id: str, first: int, last: int) -> bytes:
    """Bytes ``first`` to ``last`` (inclusive) of the object; raises on any S3 failure."""
    kwargs = {
        "Bucket": bucket_name,
        "Key": object_key,
        "Range": f"bytes={first}-{last}",
    }
    if version_id:
        kwargs["VersionId"] = version_id
    response = s3_client.get_object(**kwargs)
    body = response["Body"]
    try:
        return body.read(last - first + 1)
    finally:
        body.close()


def read_point_format(s3_client, bucket_name: str, object_key: str, version_id: str = "") -> Optional[int]:
    """The point data record format of the object, from a ranged GET of its first header bytes.

    ``version_id`` reads the run's launch version (empty reads the current object). Any failure is
    logged and returns None, so the caller keeps the direct path instead of failing the run here."""
    return read_routing_facts(s3_client, bucket_name, object_key, version_id, walk_vlrs=False).get("pointFormat")


# --- Extra Bytes descriptors PotreeConverter reads past (#783) -------------------------------------
#
# PotreeConverter's parseExtraAttributes (Converter/include/PotreeConverter.h) copies each Extra Bytes
# descriptor's 32-byte name and 32-byte description into 32-byte stack buffers and builds a
# std::string from each, which reads until a NUL. The LAS specification lets both fields fill all 32
# bytes with no NUL, so such a file makes PotreeConverter read past the buffer: undefined behaviour,
# seen as a nondeterministic crash (exit 139). PDAL reads these files, so they are converted first.
#
# The VLRs sit between the public header and the point data. Public header fields used: header size
# (uint16 at 94), offset to point data (uint32 at 96), number of VLRs (uint32 at 100). Each VLR has a
# 54-byte header: reserved (2), user ID (16, at 2), record ID (uint16 at 18), record length after
# header (uint16 at 20), description (32). The Extra Bytes VLR is user ID "LASF_Spec", record ID 4,
# holding 192-byte descriptors with the name at 4 and the description at 160, 32 bytes each.
HEADER_SIZE_OFFSET = 94
POINT_DATA_OFFSET_OFFSET = 96
VLR_COUNT_OFFSET = 100
VLR_HEADER_BYTES = 54
EXTRA_BYTES_USER_ID = b"LASF_Spec"
EXTRA_BYTES_RECORD_ID = 4
EXTRA_BYTES_DESCRIPTOR_BYTES = 192
EXTRA_BYTES_TEXT_FIELDS = ((4, 32), (160, 32))  # (offset, length): name, description
# The VLR block read is capped: a header whose VLRs run past this keeps the direct path.
VLR_BLOCK_MAX_BYTES = 64 * 1024


def vlr_block_range(prefix: bytes) -> Optional[tuple]:
    """(first, last) inclusive byte range of the VLR block named by a header prefix, or None when the
    header names no VLRs, its fields are inconsistent, or the block exceeds VLR_BLOCK_MAX_BYTES."""
    if point_format_from_header(prefix) is None:
        return None
    header_size = struct.unpack_from("<H", prefix, HEADER_SIZE_OFFSET)[0]
    point_data_offset = struct.unpack_from("<I", prefix, POINT_DATA_OFFSET_OFFSET)[0]
    vlr_count = struct.unpack_from("<I", prefix, VLR_COUNT_OFFSET)[0]
    if vlr_count == 0 or header_size < HEADER_PREFIX_BYTES or point_data_offset <= header_size:
        return None
    if point_data_offset - header_size > VLR_BLOCK_MAX_BYTES:
        return None
    return header_size, point_data_offset - 1


def has_unterminated_extra_bytes_text(vlr_block: bytes, vlr_count: int) -> Optional[bool]:
    """True when an Extra Bytes descriptor in ``vlr_block`` (the bytes from the end of the public
    header to the point data) has a name or description without a NUL, False when every one has
    both, None when the block is malformed (a VLR or the declared count runs past the block)."""
    position = 0
    for _ in range(vlr_count):
        if position + VLR_HEADER_BYTES > len(vlr_block):
            return None
        user_id = vlr_block[position + 2:position + 18].split(b"\0", 1)[0]
        record_id, length = struct.unpack_from("<HH", vlr_block, position + 18)
        data_start = position + VLR_HEADER_BYTES
        data_end = data_start + length
        if data_end > len(vlr_block):
            return None
        if user_id == EXTRA_BYTES_USER_ID and record_id == EXTRA_BYTES_RECORD_ID:
            data = vlr_block[data_start:data_end]
            # PotreeConverter reads whole descriptors only (size // 192), as is done here.
            for index in range(len(data) // EXTRA_BYTES_DESCRIPTOR_BYTES):
                descriptor = data[index * EXTRA_BYTES_DESCRIPTOR_BYTES:(index + 1) * EXTRA_BYTES_DESCRIPTOR_BYTES]
                for offset, size in EXTRA_BYTES_TEXT_FIELDS:
                    if b"\0" not in descriptor[offset:offset + size]:
                        return True
        position = data_end
    return False


def read_routing_facts(s3_client, bucket_name: str, object_key: str, version_id: str = "",
                       walk_vlrs: bool = True) -> dict:
    """What the header says about routing a LAS/LAZ input, as state-machine input fields:
    ``pointFormat`` (int) when the header could be read, and ``unterminatedExtraBytes`` (True) when an
    Extra Bytes descriptor lacks a NUL (#783). A fact that could not be established is left out.

    The first ranged GET is the 105-byte prefix. The VLR block is read with a second ranged GET only
    when the format is one PotreeConverter reads (a format 8-10 input goes through PDAL anyway) and
    the header declares VLRs. Every failure is logged and leaves the fact out, so the caller keeps
    the route it would have taken without it."""
    facts = {}
    location = f"s3://{bucket_name}/{object_key}"
    try:
        prefix = _ranged_read(s3_client, bucket_name, object_key, version_id, 0, HEADER_PREFIX_BYTES - 1)
    except Exception as e:  # nosec B110 - the header read is advisory; the run keeps the direct path
        logger.warning(f"Could not read the LAS header of {location}: {e}")
        return facts
    point_format = point_format_from_header(prefix)
    if point_format is None:
        logger.warning(f"{location} does not start with a LAS header")
        return facts
    facts["pointFormat"] = point_format
    if not walk_vlrs or needs_pdal_conversion(point_format):
        return facts
    block_range = vlr_block_range(prefix)
    if block_range is None:
        return facts
    first, last = block_range
    try:
        vlr_block = _ranged_read(s3_client, bucket_name, object_key, version_id, first, last)
    except Exception as e:  # nosec B110 - advisory, as above
        logger.warning(f"Could not read the LAS VLRs of {location}: {e}")
        return facts
    vlr_count = struct.unpack_from("<I", prefix, VLR_COUNT_OFFSET)[0]
    unterminated = has_unterminated_extra_bytes_text(vlr_block, vlr_count)
    if unterminated is None:
        logger.warning(f"{location} has a malformed VLR block; keeping the direct path")
    elif unterminated:
        facts["unterminatedExtraBytes"] = True
    return facts
