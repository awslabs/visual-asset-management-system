#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The LAS/LAZ reader is bounded by the render cap, not by the file's size.

`laspy.read()` decodes every point before returning, and the handler then built two further full copies
of the coordinates (`np.vstack` produces a (3, N) array, and `.T.astype(np.float64)` copies it again
because the transpose is not contiguous). Peak memory was therefore roughly three times the cloud's
coordinate size, and the `MAX_POINTS_FOR_RENDER` cap was applied AFTERWARDS — so it bounded render cost
and not memory, and a cloud large enough to matter exhausted the container before it could be capped.

The assertions below are about the READ, because that is what changed: the whole-file decode must not
happen at all, the file must be consumed in chunks, and the returned set must respect the cap. Asserting
only "the result is <= the cap" would have passed on the old code too — it capped afterwards.
"""

import sys
import types

import pytest

# numpy is a real dependency of the handler under test, and the only one here that is NOT stubbed by
# tests/conftest.py — the striding and the colour normalisation are numpy operations, so replacing it
# with a mock would leave these assertions checking nothing. It is the first test in this container to
# need it, so skip rather than error where it is absent: an ImportError at collection would take the
# whole container suite down on an environment that previously ran it fine.
np = pytest.importorskip("numpy", reason="the LAS streaming assertions operate on real numpy arrays")


def _load_handler():
    """Import the handler with the real numpy and the conftest's stubs in place."""
    from preview_pipeline.format_handlers import pointcloud_handler
    return pointcloud_handler


class _FakeChunk:
    """One decoded chunk, shaped like laspy's ScaleAwarePointRecord for what the handler touches."""

    def __init__(self, start, count, with_colour):
        self.x = np.arange(start, start + count, dtype=np.float64)
        self.y = self.x + 1000.0
        self.z = self.x + 2000.0
        if with_colour:
            # 16-bit colour values, so the handler's normalisation branch is the one exercised.
            self.red = ((self.x % 256) * 257).astype(np.uint16)
            self.green = ((self.x % 128) * 257).astype(np.uint16)
            self.blue = ((self.x % 64) * 257).astype(np.uint16)

    def __len__(self):
        return len(self.x)


class _FakeReader:
    def __init__(self, total, chunk_size, with_colour):
        dimension_names = ["X", "Y", "Z"] + (["red", "green", "blue"] if with_colour else [])
        self.header = types.SimpleNamespace(
            point_count=total,
            point_format=types.SimpleNamespace(dimension_names=dimension_names),
        )
        self._total = total
        self._chunk_size = chunk_size
        self._with_colour = with_colour
        self.requested_chunk_sizes = []
        self.max_points_decoded_at_once = 0

    def chunk_iterator(self, points_per_iteration):
        self.requested_chunk_sizes.append(points_per_iteration)
        # Serve the file in chunks of the size the CALLER asked for, so the test measures the handler's
        # request rather than a size the fake chose.
        served = 0
        while served < self._total:
            count = min(points_per_iteration, self._total - served)
            self.max_points_decoded_at_once = max(self.max_points_decoded_at_once, count)
            yield _FakeChunk(served, count, self._with_colour)
            served += count

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _install_fake_laspy(total, with_colour=False, chunk_size=None):
    """Replace the laspy stub with a fake whose `read` is forbidden. Returns (restore, reader_box)."""
    box = {}

    def _open(path):
        reader = _FakeReader(total, chunk_size or total, with_colour)
        box["reader"] = reader
        return reader

    def _read(path):  # noqa: ARG001
        raise AssertionError(
            "laspy.read() decodes the WHOLE file — the handler must stream with laspy.open() instead")

    fake = types.ModuleType("laspy")
    fake.open = _open
    fake.read = _read
    previous = sys.modules.get("laspy")
    sys.modules["laspy"] = fake
    return (lambda: sys.modules.__setitem__("laspy", previous)), box


@pytest.mark.unit
class TestLasReadIsBoundedByTheCapNotTheFile:
    def test_the_whole_file_decode_is_never_used(self):
        """The defect itself: `laspy.read` materialises every point before the cap can apply."""
        handler = _load_handler()
        restore, box = _install_fake_laspy(total=1_000, chunk_size=250)
        try:
            points, _ = handler._load_las("cloud.las", max_points=100)
        finally:
            restore()
        # If read() had been called the fake would have raised, so reaching here is the assertion.
        assert box["reader"].requested_chunk_sizes, "the handler never asked for a chunk iterator"
        assert len(points) <= 100

    def test_only_one_chunk_is_resident_at_a_time(self):
        """The memory bound. The handler must request a fixed chunk size, not the whole point count."""
        handler = _load_handler()
        total = 10_000
        restore, box = _install_fake_laspy(total=total, chunk_size=None)
        try:
            handler._load_las("cloud.las", max_points=1_000)
        finally:
            restore()
        requested = box["reader"].requested_chunk_sizes
        assert requested == [handler._LAS_CHUNK_POINTS], requested
        assert handler._LAS_CHUNK_POINTS < 20_000_000, (
            "the chunk size must be a bound, not the render cap restated")

    def test_a_cloud_over_the_cap_is_bounded(self):
        handler = _load_handler()
        restore, _ = _install_fake_laspy(total=10_000, chunk_size=1_000)
        try:
            points, _ = handler._load_las("cloud.las", max_points=1_000)
        finally:
            restore()
        assert len(points) <= 1_000, len(points)
        assert len(points) >= 900, ("the sample collapsed: %d points kept from 10,000 at a cap of "
                                   "1,000" % len(points))

    def test_a_cloud_under_the_cap_is_returned_whole(self):
        """Control. A cap that also discards small clouds would satisfy the bound above."""
        handler = _load_handler()
        restore, _ = _install_fake_laspy(total=750, chunk_size=250)
        try:
            points, _ = handler._load_las("cloud.las", max_points=1_000)
        finally:
            restore()
        assert len(points) == 750, len(points)
        # Every point, in file order, with the coordinates the fake generated.
        assert points[0].tolist() == [0.0, 1000.0, 2000.0]
        assert points[-1].tolist() == [749.0, 1749.0, 2749.0]

    def test_the_stride_is_uniform_across_chunk_boundaries(self):
        """The sample must span the whole file, not restart inside each chunk.

        With a per-chunk restart the kept x values clump at the start of every chunk, so the last point
        kept is far from the end of the file. Asserting the SPREAD is what distinguishes the two.
        """
        handler = _load_handler()
        restore, _ = _install_fake_laspy(total=1_000, chunk_size=100)
        try:
            points, _ = handler._load_las("cloud.las", max_points=100)
        finally:
            restore()
        xs = points[:, 0]
        assert xs[0] == 0.0, xs[:5]
        assert xs[-1] >= 980.0, (
            "the sample does not reach the end of the file (last x = %s), which is what a stride "
            "restarted per chunk produces" % xs[-1])
        gaps = np.diff(xs)
        assert gaps.min() == gaps.max() == 10.0, (
            "expected an even stride of 10 across the whole file, got gaps %s..%s"
            % (gaps.min(), gaps.max()))

    def test_sixteen_bit_colour_is_normalised_to_bytes(self):
        handler = _load_handler()
        restore, _ = _install_fake_laspy(total=500, with_colour=True, chunk_size=200)
        try:
            points, colors = handler._load_las("cloud.las", max_points=1_000)
        finally:
            restore()
        assert colors is not None, "a point format declaring red/green/blue must yield colours"
        assert colors.dtype == np.uint8, colors.dtype
        assert len(colors) == len(points)
        assert colors.max() <= 255

    def test_no_colour_when_the_point_format_declares_none(self):
        """Control on the colour branch: it must key on the declared dimensions, not always fire."""
        handler = _load_handler()
        restore, _ = _install_fake_laspy(total=500, with_colour=False, chunk_size=200)
        try:
            _, colors = handler._load_las("cloud.las", max_points=1_000)
        finally:
            restore()
        assert colors is None

    def test_an_empty_file_is_refused(self):
        handler = _load_handler()
        restore, _ = _install_fake_laspy(total=0, chunk_size=100)
        try:
            with pytest.raises(ValueError):
                handler._load_las("cloud.las", max_points=1_000)
        finally:
            restore()
