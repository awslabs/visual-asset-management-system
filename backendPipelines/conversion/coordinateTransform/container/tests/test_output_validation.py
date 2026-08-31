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
"""

import os
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from coord_transform_pipeline.core import _validate_transform_outputs  # noqa: E402


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
