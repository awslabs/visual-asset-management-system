#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The LAS point data record format of a .las/.laz input, read from the first bytes of its header.

PotreeConverter (the pinned commit in container/Dockerfile_Potree) reads LAS point data record formats
0 to 7 only: computeOutputAttributes (Converter/include/PotreeConverter.h) exits with "currently
unsupported LAS format" for any other, and the chunker's formatToExtraIndex map
(Converter/src/chunker_countsort_laszip.cpp) has the same eight entries. A LAS 1.4 file in format 8
(RGB + NIR), 9 or 10 (waveform) is therefore converted by the PDAL stage to LAS 1.2 / format 3 first.

The format is read from the file's header bytes, never from its extension: the header is a fixed
layout, so a ranged GET of its first bytes is enough and the point data is never read."""

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


def read_point_format(s3_client, bucket_name: str, object_key: str, version_id: str = "") -> Optional[int]:
    """The point data record format of the object, from a ranged GET of its first header bytes.

    ``version_id`` reads the run's launch version (empty reads the current object). Any failure is
    logged and returns None, so the caller keeps the direct path instead of failing the run here."""
    kwargs = {
        "Bucket": bucket_name,
        "Key": object_key,
        "Range": f"bytes=0-{HEADER_PREFIX_BYTES - 1}",
    }
    if version_id:
        kwargs["VersionId"] = version_id
    try:
        response = s3_client.get_object(**kwargs)
        body = response["Body"]
        try:
            prefix = body.read(HEADER_PREFIX_BYTES)
        finally:
            body.close()
    except Exception as e:  # nosec B110 - the header read is advisory; the run keeps the direct path
        logger.warning(f"Could not read the LAS header of s3://{bucket_name}/{object_key}: {e}")
        return None
    point_format = point_format_from_header(prefix)
    if point_format is None:
        logger.warning(f"s3://{bucket_name}/{object_key} does not start with a LAS header")
    return point_format
