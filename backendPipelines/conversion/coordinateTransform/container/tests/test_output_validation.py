# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S33-CDK-015: a corrupt reprojection must fail the stage, not be reported as a conversion.

Run from this directory:  python -m pytest tests/test_output_validation.py -q

Found by execution, not by reading code. Running the shipped `coordinate-transform-wgs84-to-osgb36-laz`
template against an existing 9.72 MB point cloud whose own CRS was a metre-based projection — not the
EPSG:4326 the template declares — produced this on a live deployment:

    input   red-rocks.laz              LAS 1.2  4,004,326 points  X [482,060.5 .. 482,763.8]
    output  red-rocks_EPSG_27700.laz   LAS 1.2  4,004,326 points  X [1.797e308 .. inf]   payload 0.04 MB

The execution reported SUCCEEDED with an empty error, and the output was attached to the asset. Two
defaults let that through: `onMismatch` defaulted to `warn`, so the declared-CRS contradiction only logged;
and nothing inspected the written file, so coordinates outside double-precision range were published.

The bounds check is the load-bearing half, because it catches a corrupt reprojection whatever the cause,
not only a CRS mismatch. The point count is deliberately NOT compared against the input: a transform may
legitimately drop points outside the target CRS's area of use, so a lower count is not by itself wrong —
and in the observed case the count was IDENTICAL while the data was gone, so a count comparison would have
missed it entirely.

An E57 output is checked for the one thing its format records and a LAS header does not: the target CRS on
the E57Root as `coordinateMetadata`. The value is written through libe57, which cannot replace an existing
child element, so the write is structural rather than a plain assignment — reading the file back is what
turns a dropped write into a FAILED execution instead of an E57 full of coordinates with no record of the
system they are in.
"""

import os
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from coord_transform_pipeline.core import (  # noqa: E402
    _E57_PAGE_PAYLOAD,
    _E57_PAGE_SIZE,
    _validate_transform_outputs,
)


def _write_las(path, bounds, point_count=1000):
    """A minimal LAS 1.2 file carrying the given bounds, enough for the header validation to read.

    Only the fields the validator reads are meaningful: the LASF signature, the version, the legacy point
    count and the six bounding-box doubles from offset 179. The rest is zero-filled, which is what makes
    this a test of the validator rather than of laspy.
    """
    header = bytearray(227)
    header[0:4] = b"LASF"
    header[24] = 1  # version major
    header[25] = 2  # version minor
    struct.pack_into("<H", header, 94, 227)  # header size
    struct.pack_into("<I", header, 107, point_count)
    struct.pack_into("<6d", header, 179, *bounds)
    with open(path, "wb") as fh:
        fh.write(header)
    return path


E57_WKT = 'PROJCRS["NAD83 / UTM zone 13N",ID["EPSG",26913]]'
# The E57 the coordinate-transform container produced before the CRS was written: pye57 seeds
# coordinateMetadata with an empty string, so the element exists and records nothing.
E57_PLACEHOLDER = '<coordinateMetadata type="String"/>'


def _write_e57(path, coordinate_metadata=E57_PLACEHOLDER, filler=0):
    """A minimal E57 file: a 48-byte header plus an XML section, laid out in physical pages.

    Only what the validator reads is meaningful — the `ASTM-E57` signature, the xmlPhysicalOffset at
    offset 24 and the xmlLogicalLength at offset 32, and the `coordinateMetadata` element itself. The
    physical layout is: 1024-byte pages, each carrying 1020 logical bytes followed by a 4-byte checksum.
    `filler` pads the XML ahead of the element so a caller can place it across a page boundary, which is
    where a validator that scanned the raw bytes would read checksum bytes as part of the value.
    """
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<e57Root type="Structure">\n'
        f'  <padding type="String"><![CDATA[{"P" * filler}]]></padding>\n'
        f"  {coordinate_metadata}\n"
        '  <data3D type="Vector" allowHeterogeneousChildren="1"/>\n'
        "</e57Root>\n"
    ).encode("utf-8")

    header = bytearray(48)
    header[0:8] = b"ASTM-E57"
    struct.pack_into("<I", header, 8, 1)  # versionMajor
    struct.pack_into("<I", header, 12, 0)  # versionMinor
    # xmlPhysicalOffset is a physical offset, so it counts the checksum bytes before it. The XML starts
    # immediately after the header, which is inside the first page.
    struct.pack_into("<Q", header, 24, len(header))
    struct.pack_into("<Q", header, 32, len(xml))

    logical = bytes(header) + xml
    physical = bytearray()
    for offset in range(0, len(logical), _E57_PAGE_PAYLOAD):
        page = logical[offset : offset + _E57_PAGE_PAYLOAD]
        physical += page.ljust(_E57_PAGE_PAYLOAD, b"\0") + b"\xde\xad\xbe\xef"
    struct.pack_into("<Q", physical, 16, len(physical))  # filePhysicalLength

    with open(path, "wb") as fh:
        fh.write(bytes(physical))
    return path


def _e57_with_crs(path, crs=E57_WKT, filler=0):
    return _write_e57(
        path,
        f'<coordinateMetadata type="String"><![CDATA[{crs}]]></coordinateMetadata>',
        filler=filler,
    )


# (maxx, minx, maxy, miny, maxz, minz) — the LAS header's alternating max/min order.
FINITE = (482763.8, 482060.5, 4392000.0, 4391000.0, 2000.0, 1800.0)
# The bounds actually observed on the corrupt output.
OBSERVED_CORRUPT = (float("inf"), 1.7976931348623157e308, float("inf"), 1.7976931348623157e308,
                    float("inf"), 1.7976931348623157e308)
INVERTED = (100.0, 900.0, 100.0, 900.0, 10.0, 90.0)


class _Report:
    """The fields `_validate_transform_outputs` reads off a pipeline report."""

    def __init__(self, errors=None):
        self.errors = errors or []
        self.total_points_processed = 0
        self.output_files = []


def test_a_finite_bounding_box_passes(tmp_path):
    """The positive control. Without it, a validator that rejected everything would satisfy the
    rejection tests below while failing every real conversion."""
    _write_las(tmp_path / "good.laz", FINITE)
    _validate_transform_outputs(_Report(), str(tmp_path))


def test_the_observed_corrupt_output_is_rejected(tmp_path):
    """The exact bounds read off the live deployment's output."""
    _write_las(tmp_path / "red-rocks_EPSG_27700.laz", OBSERVED_CORRUPT)
    with pytest.raises(RuntimeError) as excinfo:
        _validate_transform_outputs(_Report(), str(tmp_path))
    message = str(excinfo.value)
    assert "red-rocks_EPSG_27700.laz" in message, message
    assert "not finite" in message, message


def test_an_inverted_bounding_box_is_rejected(tmp_path):
    """The other corrupt signature: a header whose bounds were never updated because nothing was
    written, so min still exceeds max."""
    _write_las(tmp_path / "inverted.las", INVERTED)
    with pytest.raises(RuntimeError) as excinfo:
        _validate_transform_outputs(_Report(), str(tmp_path))
    assert "inverted" in str(excinfo.value)


def test_a_non_las_output_is_rejected(tmp_path):
    """A file that is not a LAS/LAZ after writing means the writer produced something unusable."""
    (tmp_path / "broken.laz").write_bytes(b"not a las file at all")
    with pytest.raises(RuntimeError) as excinfo:
        _validate_transform_outputs(_Report(), str(tmp_path))
    assert "not a LAS/LAZ file" in str(excinfo.value)


def test_non_point_cloud_files_are_ignored(tmp_path):
    """The report JSON and any metadata sit in the same output directory and must not be validated as
    point clouds — otherwise every run fails on its own report."""
    (tmp_path / "report.json").write_text('{"ok": true}')
    (tmp_path / "notes.txt").write_text("nothing to see")
    _write_las(tmp_path / "good.laz", FINITE)
    _validate_transform_outputs(_Report(), str(tmp_path))


def test_the_point_count_is_not_compared_against_the_input(tmp_path):
    """Pinned deliberately, because it is the non-obvious design choice.

    A transform may legitimately drop points that fall outside the target CRS's area of use, so a lower
    output count is not an error. In the case that prompted this validator the count was IDENTICAL to the
    input while the payload was empty, so a count comparison would have passed — the bounds are what
    distinguish corrupt from merely smaller.
    """
    _write_las(tmp_path / "fewer-points.laz", FINITE, point_count=1)
    _validate_transform_outputs(_Report(), str(tmp_path))


def test_a_directory_with_no_outputs_passes(tmp_path):
    """No output files is not this validator's failure to report: an empty output directory means the
    transform produced nothing, which the report's own errors cover."""
    _validate_transform_outputs(_Report(), str(tmp_path))


def test_every_offending_file_is_named(tmp_path):
    """A run writes one file per requested format, so the message must name all of them rather than
    stopping at the first — otherwise a second format's corruption is invisible until the first is fixed."""
    _write_las(tmp_path / "a.laz", OBSERVED_CORRUPT)
    _write_las(tmp_path / "b.las", INVERTED)
    with pytest.raises(RuntimeError) as excinfo:
        _validate_transform_outputs(_Report(), str(tmp_path))
    message = str(excinfo.value)
    assert "a.laz" in message and "b.las" in message, message


# --- an E57 output must record the CRS it was reprojected into ---------------------------------------


def test_an_e57_recording_the_crs_passes(tmp_path):
    """The positive control for the E57 arm."""
    _e57_with_crs(tmp_path / "cloud_EPSG_26913.e57")
    _validate_transform_outputs(_Report(), str(tmp_path))


def test_an_e57_with_the_empty_placeholder_is_rejected(tmp_path):
    """The regression proper.

    `pye57.E57.write_default_header` creates `coordinateMetadata` as an empty string, so this is exactly
    the file the container produced while `E57Writer` accepted `crs_wkt` and discarded it — reprojected
    coordinates with no record of the system they are in. It is also what makes the pipeline's own E57
    output unusable as its own input, because source-CRS enforcement finds no CRS to check.
    """
    _write_e57(tmp_path / "cloud_EPSG_26913.e57")
    with pytest.raises(RuntimeError) as excinfo:
        _validate_transform_outputs(_Report(), str(tmp_path))
    message = str(excinfo.value)
    assert "cloud_EPSG_26913.e57" in message, message
    assert "no coordinate reference system" in message, message


def test_an_e57_whose_crs_element_is_present_but_blank_is_rejected(tmp_path):
    """A blank value reads as "not recorded", matching `E57Reader._read_crs`.

    A validator that only checked for the element's presence would pass every pre-fix file, since the
    placeholder is present in all of them.
    """
    _write_e57(
        tmp_path / "cloud.e57",
        '<coordinateMetadata type="String"><![CDATA[   ]]></coordinateMetadata>',
    )
    with pytest.raises(RuntimeError):
        _validate_transform_outputs(_Report(), str(tmp_path))


def test_a_crs_spanning_a_page_boundary_is_read_intact(tmp_path):
    """What makes the page de-paging load-bearing rather than decoration.

    E57 stores its XML in 1024-byte physical pages, each ending with a 4-byte checksum. A compound CRS
    WKT is routinely longer than a page's 1020 usable bytes, so a scan over the raw file reads checksum
    bytes as part of the value. Measured on a real pye57-written file: a 1,561-character WKT came back as
    1,565 bytes containing non-text characters. This asserts the raw file really is spliced, so the test
    fails against an implementation that skips the de-paging.
    """
    long_crs = 'COMPOUNDCRS["' + "L" * 1500 + '",ID["EPSG",9999]]'
    path = _e57_with_crs(tmp_path / "cloud.e57", crs=long_crs)

    raw = path.read_bytes()
    assert len(raw) > _E57_PAGE_SIZE, "the file must span more than one page for this to mean anything"
    assert long_crs.encode("utf-8") not in raw, (
        "the CRS must be split across pages in the raw file, or this test proves nothing"
    )

    _validate_transform_outputs(_Report(), str(tmp_path))


def test_a_crs_element_whose_opening_tag_spans_a_page_boundary_is_found(tmp_path):
    """The element's own tag can be the part that straddles, not just its value.

    A false rejection here would fail every legitimate E57 run whose XML happened to land that way, which
    is worse than the defect being fixed.
    """
    for filler in range(_E57_PAGE_PAYLOAD - 200, _E57_PAGE_PAYLOAD - 100):
        target = tmp_path / f"cloud_{filler}.e57"
        _e57_with_crs(target, filler=filler)
        _validate_transform_outputs(_Report(), str(tmp_path))
        target.unlink()


def test_a_file_named_e57_that_is_not_one_is_rejected(tmp_path):
    """The read-back must be able to say no, so a pass means the CRS was found rather than that the
    check gave up."""
    (tmp_path / "cloud.e57").write_bytes(b"LASF" + b"\0" * 500)
    with pytest.raises(RuntimeError) as excinfo:
        _validate_transform_outputs(_Report(), str(tmp_path))
    assert "not an E57 file" in str(excinfo.value)


def test_an_e57_with_an_empty_xml_section_is_rejected(tmp_path):
    """A zero xmlLogicalLength means nothing was written to look in."""
    header = bytearray(48)
    header[0:8] = b"ASTM-E57"
    struct.pack_into("<Q", header, 24, 48)
    struct.pack_into("<Q", header, 32, 0)
    (tmp_path / "cloud.e57").write_bytes(bytes(header).ljust(_E57_PAGE_SIZE, b"\0"))
    with pytest.raises(RuntimeError) as excinfo:
        _validate_transform_outputs(_Report(), str(tmp_path))
    assert "XML section is empty" in str(excinfo.value)


def test_a_bad_e57_and_a_bad_las_are_both_named(tmp_path):
    """A run writing both formats must report both, the same way two bad LAS files do."""
    _write_e57(tmp_path / "cloud.e57")
    _write_las(tmp_path / "cloud.laz", OBSERVED_CORRUPT)
    with pytest.raises(RuntimeError) as excinfo:
        _validate_transform_outputs(_Report(), str(tmp_path))
    message = str(excinfo.value)
    assert "cloud.e57" in message and "cloud.laz" in message, message


def test_a_ply_output_is_not_checked_for_a_crs(tmp_path):
    """PLY has no CRS field, so `PlyWriter` correctly writes none and the validator must not ask."""
    (tmp_path / "cloud.ply").write_bytes(b"ply\nformat ascii 1.0\nend_header\n")
    _e57_with_crs(tmp_path / "cloud.e57")
    _validate_transform_outputs(_Report(), str(tmp_path))
