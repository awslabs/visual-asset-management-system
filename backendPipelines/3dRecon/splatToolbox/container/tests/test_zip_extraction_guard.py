#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""FIX-052 / S4-PIPELINES-067: tests for the splatToolbox container's archive extraction limits.

Upstream `src/main.py` extracts the input archive with `zipfile.ZipFile.extractall` after checking
only for zip slip, so an archive that expands past the job volume exhausts it — and the volume is the
Batch instance's root disk, so it takes the ECS agent and any co-located job down with the run — or
exhausts its inode budget, before any other check runs.

Two things are bounded, and only one of them is bounded on a number the archive supplies. Entry count
comes off the central directory because an entry has to be listed there to be extracted at all. Size is
bounded on the bytes extraction actually PRODUCES, accumulated as the members are copied: the sizes in
the central directory are the archive's own account of itself, so a declared-size total is kept only as
a cheap advisory reject and is not what protects the volume. Both ceilings answer to the hardware — the
instance has one 200 GiB gp3 root volume and the container's `/tmp` is a bind mount on it — so the byte
ceiling is that volume: above it nothing is bounded, and at it no input that could have finished is
refused. Sizes here are sized from that volume and from real capture shapes, never from what these
tests happen to write.
"""

import importlib.util
import os
import re
import subprocess  # nosec B404 - launches the container's own guarded launcher under test
import sys
import zipfile
from unittest.mock import MagicMock

import pytest

_KIB = 1024
_MIB = 1024 ** 2
_GIB = 1024 ** 3


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file (its name is ``__main__.py``).

    Its ``vams_utils`` / ``boto3`` imports are stubbed: the limits are a pure function over archive
    entry metadata and a member handle, and must not need the container's AWS dependencies installed.
    """
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stubbed = {}
    for name in ("boto3", "vams_utils", "vams_utils.manifest_io"):
        if name not in sys.modules:
            stubbed[name] = MagicMock()
    sys.modules.update(stubbed)
    try:
        spec = importlib.util.spec_from_file_location(
            "splat_container_main", os.path.join(container_dir, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
    return module


def _entry(name, file_size, compress_size=None):
    """A `ZipInfo` as `zipfile` hands it to the limits: real central-directory metadata, no file."""
    info = zipfile.ZipInfo(name)
    info.file_size = file_size
    info.compress_size = file_size if compress_size is None else compress_size
    return info


def _image_capture(frame_count, frame_bytes):
    """A capture of poorly-compressing frames: `compress_size` is within a few percent of the size."""
    return [_entry(f"images/frame_{i:06d}.jpg", frame_bytes, int(frame_bytes * 0.97))
            for i in range(frame_count)]


class _SizedBlock:
    """A stand-in for a block of extracted data.

    Only its length is ever read — by the budget that charges for it and by the copy loop's
    end-of-member test — so a test moves hundreds of gigabytes through extraction without allocating
    any of it.
    """

    def __init__(self, length):
        self._length = length

    def __len__(self):
        return self._length


class _FakeMember:
    """An archive member handle that produces a given number of bytes in fixed blocks.

    What it produces is independent of what its central-directory entry declares, which is the whole
    point: an archive's declaration and its behaviour are separate things.
    """

    def __init__(self, produced_bytes, block_bytes):
        self.remaining_bytes = produced_bytes
        self._block_bytes = block_bytes

    def read(self, *_args, **_kwargs):
        if self.remaining_bytes <= 0:
            return b""
        served = min(self._block_bytes, self.remaining_bytes)
        self.remaining_bytes -= served
        return _SizedBlock(served)

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        return False


class _FakeArchive:
    """The `ZipFile` surface an extraction uses: the entry list, and `open` per member."""

    def __init__(self, entries, produced_bytes, block_bytes=64 * _MIB):
        self.entries = entries
        self._produced_bytes = produced_bytes
        self._block_bytes = block_bytes
        self.opened = []

    def infolist(self):
        return self.entries

    def open(self, member, *_args, **_kwargs):
        name = getattr(member, "filename", member)
        self.opened.append(name)
        produced = self._produced_bytes(member) if callable(self._produced_bytes) \
            else self._produced_bytes
        return _FakeMember(produced, self._block_bytes)


class _FakeVolume:
    """The extraction target: records how many bytes reached the disk."""

    def __init__(self):
        self.written_bytes = 0
        self.files = []

    def write(self, block):
        self.written_bytes += len(block)


def _copying_extractall(archive, volume, block_bytes=64 * _MIB):
    """`ZipFile._extract_member`'s copy loop, against a fake volume instead of the real one.

    `zipfile` opens each member and copies it with `shutil.copyfileobj`, so every byte read out of a
    member handle is a byte written to its target file — which is what makes a charge on the read side
    a charge on what the volume holds.
    """
    def extractall(*_args, **_kwargs):
        for entry in archive.infolist():
            volume.files.append(entry.filename)
            with archive.open(entry) as source:
                while True:
                    block = source.read(block_bytes)
                    if not block:
                        break
                    volume.write(block)
    return extractall


def _extract(main_module, archive, volume):
    """One guarded extraction of a fake archive onto a fake volume."""
    return main_module.extract_within_limits(
        archive, _copying_extractall(archive, volume))


def _entry_point_source():
    """The entry module's own source, for the wiring assertions a behavioural test cannot make."""
    source_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "__main__.py")
    with open(source_path, encoding="utf-8") as handle:
        return handle.read()


def _batch_gpu_pipeline_source():
    """The CDK construct that declares the volume this container's `/tmp` lives on."""
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    construct_path = os.path.normpath(os.path.join(
        container_dir, "..", "..", "..", "..", "infra", "lib", "nestedStacks", "pipelines",
        "constructs", "batch-gpu-pipeline.ts"))
    with open(construct_path, encoding="utf-8") as handle:
        return construct_path, handle.read()


def _write_child_workspace(tmp_path, stand_in_main):
    """A directory the entry module can be launched against: a stand-in `main.py` plus stubs for the
    container's AWS dependencies, so loading the real entry module needs nothing installed."""
    (tmp_path / "boto3.py").write_text("", encoding="utf-8")
    stub_package = tmp_path / "vams_utils"
    stub_package.mkdir(exist_ok=True)
    (stub_package / "__init__.py").write_text("", encoding="utf-8")
    (stub_package / "manifest_io.py").write_text(
        "def fetch_metadata(location):\n    return {}\n\n\n"
        "def fetch_input_configuration(location):\n    return {}\n",
        encoding="utf-8")
    (tmp_path / "main.py").write_text("\n".join(stand_in_main) + "\n", encoding="utf-8")


def _run_child(main_module, tmp_path, guarded):
    """The stand-in `main.py`, run either through the entry point's launcher or plainly."""
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if guarded:
        argv = [sys.executable, "-c", main_module._GUARDED_MAIN_LAUNCHER.format(
            entry_path=os.path.join(container_dir, "__main__.py"), script_path="main.py")]
    else:
        argv = [sys.executable, "main.py"]
    return subprocess.run(  # nosec B603 - fixed interpreter, argument list, no shell
        argv, cwd=str(tmp_path), env=dict(os.environ, PYTHONPATH=str(tmp_path)),
        capture_output=True, text=True, timeout=120, check=False)


@pytest.mark.unit
class TestWhatIsExtractedIsWhatIsBounded:
    """The declared sizes are the archive's own account of itself, so the ceiling is enforced on the
    bytes extraction produces. These are the assertions a guard reading only the central directory
    cannot make."""

    def test_an_archive_declaring_a_benign_size_is_still_stopped_at_the_ceiling(self, main_module):
        """The case a declared-size guard cannot see: four entries declaring 64 MiB apiece — a total no
        ceiling would object to — whose members each go on producing 96 GiB."""
        entries = [_entry(f"images/frame_{i:06d}.jpg", 64 * _MIB, 8 * _MIB) for i in range(4)]
        archive = _FakeArchive(entries, 96 * _GIB)
        volume = _FakeVolume()

        with pytest.raises(ValueError) as excinfo:
            _extract(main_module, archive, volume)

        assert "extraction exceeds" in str(excinfo.value)
        assert volume.written_bytes <= main_module.MAX_ARCHIVE_EXTRACTED_BYTES

    def test_the_declared_size_axis_alone_accepts_that_same_archive(self, main_module):
        """The other half of the assertion above: read off the central directory, that archive is a
        quarter of a gigabyte and passes every declared-metadata check there is."""
        entries = [_entry(f"images/frame_{i:06d}.jpg", 64 * _MIB, 8 * _MIB) for i in range(4)]
        entry_count, declared_bytes = main_module.enforce_archive_extraction_limits(entries)
        assert (entry_count, declared_bytes) == (4, 256 * _MIB)

    def test_the_rest_of_the_payload_is_never_written(self, main_module):
        """The refusal has to land on the read that crosses the ceiling, not after the archive has had
        its way with the volume: 288 GiB of payload, and the volume never holds more than 200 GiB."""
        entries = [_entry(f"images/frame_{i:06d}.jpg", 64 * _MIB, 8 * _MIB) for i in range(3)]
        archive = _FakeArchive(entries, 96 * _GIB, block_bytes=64 * _MIB)
        volume = _FakeVolume()

        with pytest.raises(ValueError):
            _extract(main_module, archive, volume)

        ceiling = main_module.MAX_ARCHIVE_EXTRACTED_BYTES
        assert ceiling - 64 * _MIB <= volume.written_bytes <= ceiling

    def test_an_entry_declaring_nothing_is_charged_for_what_it_writes(self, main_module):
        """Declared size zero is what the metadata fallback also produces, so it must buy nothing."""
        archive = _FakeArchive([_entry("bomb/expanded.bin", 0, 0)], 400 * _GIB)
        volume = _FakeVolume()

        with pytest.raises(ValueError):
            _extract(main_module, archive, volume)

        assert volume.written_bytes <= main_module.MAX_ARCHIVE_EXTRACTED_BYTES

    def test_the_budget_runs_across_members_rather_than_per_member(self, main_module):
        """Splitting the payload over entries that are each individually acceptable must not help: no
        one member here writes more than 80 GiB, and the three of them together cross the ceiling."""
        entries = [_entry(f"chunk/{i}.bin", 64 * _MIB, 8 * _MIB) for i in range(3)]
        archive = _FakeArchive(entries, 80 * _GIB)
        volume = _FakeVolume()

        with pytest.raises(ValueError):
            _extract(main_module, archive, volume)

        # Refused during the third member, so the first two were each accepted on their own.
        assert len(archive.opened) == 3

    def test_the_refusal_names_the_entry_it_stopped_on(self, main_module):
        entries = [_entry("images/frame_000000.jpg", 8 * _MIB, 8 * _MIB),
                   _entry("bomb/expanded.bin", 8 * _MIB, 8 * _MIB)]
        archive = _FakeArchive(
            entries, lambda member: 8 * _MIB if member.filename.startswith("images/") else 400 * _GIB)

        with pytest.raises(ValueError) as excinfo:
            _extract(main_module, archive, _FakeVolume())

        assert "bomb/expanded.bin" in str(excinfo.value)

    def test_the_budget_starts_at_zero_for_each_extraction(self, main_module):
        """`main.py` unpacks into one temp directory, so the volume holds the largest single extraction
        rather than the sum — a budget carried between calls would refuse a legitimate re-extract."""
        entries = _image_capture(4, 25 * _MIB)
        for _attempt in range(4):
            archive = _FakeArchive(entries, 25 * _MIB)
            volume = _FakeVolume()
            _extract(main_module, archive, volume)
            assert volume.written_bytes == 4 * 25 * _MIB

    def test_the_metering_does_not_outlive_the_extraction(self, main_module):
        """The metered handle is installed on the archive for the call only. Left behind, it would
        charge later reads to a spent budget and refuse them."""
        archive = _FakeArchive(_image_capture(2, 8 * _MIB), 8 * _MIB)
        _extract(main_module, archive, _FakeVolume())
        assert "open" not in archive.__dict__

        over_ceiling = _FakeArchive([_entry("bomb/expanded.bin", 0, 0)], 400 * _GIB)
        with pytest.raises(ValueError):
            _extract(main_module, over_ceiling, _FakeVolume())
        assert "open" not in over_ceiling.__dict__


@pytest.mark.unit
class TestRealInputIsAccepted:
    """The failure mode that matters most here is a ceiling that rejects a legitimate dataset."""

    def test_the_largest_capture_the_volume_holds_extracts_to_completion(self, main_module):
        """6,000 frames at 25 MiB each — about 146 GiB of barely-compressible image data, the largest
        photogrammetry capture a 200 GiB volume can take, extracted byte for byte."""
        entries = _image_capture(6000, 25 * _MIB)
        archive = _FakeArchive(entries, 25 * _MIB)
        volume = _FakeVolume()

        _extract(main_module, archive, volume)

        assert volume.written_bytes == 6000 * 25 * _MIB
        assert volume.written_bytes > 140 * _GIB
        assert len(volume.files) == 6000

    def test_a_colmap_bundle_of_that_capture_is_accepted(self, main_module):
        """Images plus masks plus per-image sparse records — the entry count a real bundle reaches."""
        entries = _image_capture(6000, 25 * _MIB)
        entries += [_entry(f"masks/frame_{i:06d}.png", 512 * _KIB) for i in range(6000)]
        entries += [_entry(f"sparse/0/images/{i:06d}.bin", 4096) for i in range(6000)]
        entry_count, declared_bytes = main_module.enforce_archive_extraction_limits(entries)
        assert entry_count == 18000
        assert declared_bytes > 145 * _GIB

    def test_a_hundred_gigabyte_video_is_accepted(self, main_module):
        entries = [_entry("capture.mov", 100 * _GIB, int(100 * _GIB * 0.99))]
        archive = _FakeArchive(entries, 100 * _GIB)
        volume = _FakeVolume()

        _extract(main_module, archive, volume)

        assert volume.written_bytes == 100 * _GIB

    def test_an_ordinary_small_archive_extracts_normally(self, main_module):
        """Positive control: nothing about a plain input archive is disturbed by the limits."""
        entries = [_entry("transforms.json", 4 * _MIB, 4 * _KIB)]
        entries += _image_capture(40, 6 * _MIB)
        archive = _FakeArchive(entries, 6 * _MIB)
        volume = _FakeVolume()

        assert _extract(main_module, archive, volume) is None
        assert volume.written_bytes == 41 * 6 * _MIB
        assert len(volume.files) == 41

    def test_a_highly_compressible_legitimate_archive_is_accepted(self, main_module):
        """COLMAP text bundles and ASCII point clouds compress by far more than image data does, so
        nothing may turn a stored-to-expanded ratio into a rejection."""
        entries = [_entry("sparse/0/points3D.txt", 60 * _GIB, 60 * _MIB)]
        archive = _FakeArchive(entries, 60 * _GIB)
        volume = _FakeVolume()

        _extract(main_module, archive, volume)

        assert volume.written_bytes == 60 * _GIB


@pytest.mark.unit
class TestTheCeilingIsTheJobVolume:
    """A cap above the disk is not a cap, and a cap below a real capture rejects real work. Both ends
    are pinned here so neither can drift, and neither end is derived from what these tests write."""

    def test_the_byte_ceiling_does_not_exceed_the_batch_instance_volume(self, main_module):
        """The container's `/tmp` is a bind mount of a directory on the instance's single root volume,
        so the volume the CDK launch template declares is the whole budget there is."""
        construct_path, source = _batch_gpu_pipeline_source()
        declared = re.search(r"volumeSize:\s*(\d+)", source)
        assert declared, f"no volumeSize found in {construct_path} to reconcile the ceiling against"
        volume_bytes = int(declared.group(1)) * _GIB
        assert main_module.MAX_ARCHIVE_EXTRACTED_BYTES <= volume_bytes

    def test_the_byte_ceiling_stays_large_enough_for_a_real_capture(self, main_module):
        """Tens to hundreds of gigabytes of images and video are legitimate input."""
        assert main_module.MAX_ARCHIVE_EXTRACTED_BYTES >= 100 * _GIB

    def test_the_entry_ceiling_stays_far_above_a_real_bundle(self, main_module):
        assert main_module.MAX_ARCHIVE_ENTRY_COUNT >= 500000

    def test_an_archive_overrunning_the_volume_at_an_unremarkable_ratio_is_rejected(
            self, main_module):
        """3 TiB stored at 10:1 declares nothing suspicious about how it compresses and still fills the
        volume fifteen times over, so what refuses it has to be the volume, not the ratio."""
        entries = [_entry(f"capture/part_{i:03d}.mov", 300 * _GIB, 30 * _GIB) for i in range(10)]
        with pytest.raises(ValueError) as excinfo:
            main_module.enforce_archive_extraction_limits(entries)
        assert "extraction limit" in str(excinfo.value)

        archive = _FakeArchive(entries, 300 * _GIB)
        volume = _FakeVolume()
        with pytest.raises(ValueError):
            _extract(main_module, archive, volume)
        assert volume.written_bytes <= main_module.MAX_ARCHIVE_EXTRACTED_BYTES


@pytest.mark.unit
class TestPathologicalArchiveIsRejected:
    def test_a_pathological_entry_count_is_rejected(self, main_module):
        """Many tiny files: small compressed, small extracted, and it exhausts inodes rather than
        bytes. The count is the one number the archive cannot understate — every entry it lists is an
        entry it needs listed to have that entry extracted."""
        limit = main_module.MAX_ARCHIVE_ENTRY_COUNT
        yielded = [0]

        def entries():
            template = _entry("tiny/f", 0, 0)
            for _ in range(limit * 10):
                yielded[0] += 1
                yield template

        with pytest.raises(ValueError) as excinfo:
            main_module.enforce_archive_extraction_limits(entries())
        assert "entries" in str(excinfo.value)
        # Refused on the entry that crossed the ceiling, rather than after walking the whole archive.
        assert yielded[0] == limit + 1

    def test_zero_byte_entries_are_still_counted(self, main_module):
        """Byte accounting alone would accept this archive: every entry declares nothing."""
        entries = [_entry(f"tiny/{i}", 0, 0) for i in range(10)]
        with pytest.raises(ValueError):
            main_module.enforce_archive_extraction_limits(entries, max_entry_count=5)

    def test_a_small_archive_declaring_an_absurd_size_is_rejected_before_extraction(
            self, main_module):
        """The advisory pre-check earning its keep: the classic bomb declares its intent, and refusing
        it off the central directory costs no disk and no extraction."""
        entries = [_entry(f"bomb/{i}.bin", 4 * 1024 ** 5, 1024) for i in range(3)]
        archive = _FakeArchive(entries, 0)
        with pytest.raises(ValueError) as excinfo:
            _extract(main_module, archive, _FakeVolume())
        assert "declares more than" in str(excinfo.value)
        assert archive.opened == []

    def test_the_pre_check_rejection_names_the_entry_that_crossed_the_ceiling(self, main_module):
        entries = [_entry("images/frame_000000.jpg", 25 * _MIB),
                   _entry("bomb/expanded.bin", 8 * 1024 ** 5, 2048)]
        with pytest.raises(ValueError) as excinfo:
            main_module.enforce_archive_extraction_limits(entries)
        assert "bomb/expanded.bin" in str(excinfo.value)


@pytest.mark.unit
class TestCeilingBoundaries:
    def test_an_archive_exactly_at_each_ceiling_is_accepted(self, main_module):
        entries = [_entry("a", 60), _entry("b", 40)]
        assert main_module.enforce_archive_extraction_limits(
            entries, max_entry_count=2, max_declared_bytes=100) == (2, 100)

    def test_one_byte_over_the_declared_byte_ceiling_is_rejected(self, main_module):
        entries = [_entry("a", 60), _entry("b", 41)]
        with pytest.raises(ValueError):
            main_module.enforce_archive_extraction_limits(
                entries, max_entry_count=2, max_declared_bytes=100)

    def test_one_entry_over_the_entry_ceiling_is_rejected(self, main_module):
        entries = [_entry("a", 1), _entry("b", 1), _entry("c", 1)]
        with pytest.raises(ValueError):
            main_module.enforce_archive_extraction_limits(
                entries, max_entry_count=2, max_declared_bytes=100)

    def test_an_empty_archive_is_accepted(self, main_module):
        assert main_module.enforce_archive_extraction_limits([]) == (0, 0)

    def test_metadata_without_a_declared_size_counts_as_nothing(self, main_module):
        """`getattr` fallback: an entry the archive left without a size must not abort the walk, and
        what it actually extracts is charged as it is written."""
        class _Bare:
            filename = "unknown"

        assert main_module.enforce_archive_extraction_limits([_Bare()]) == (1, 0)

    def test_extraction_exactly_at_the_byte_ceiling_is_accepted(self, main_module):
        budget = main_module.ExtractionByteBudget(100)
        budget.charge(60, "a")
        budget.charge(40, "b")
        assert budget.extracted_bytes == 100

    def test_one_byte_over_the_extracted_byte_ceiling_is_rejected(self, main_module):
        budget = main_module.ExtractionByteBudget(100)
        budget.charge(100, "a")
        with pytest.raises(ValueError):
            budget.charge(1, "b")


@pytest.mark.unit
class TestRejectionReportsTheTaskToken:
    """A rejection has to reach Step Functions, or the workflow task stays RUNNING for its whole
    taskTimeout — 72 hours on this pipeline — which is a worse outcome than the unguarded extract."""

    def test_the_cause_stays_within_the_length_the_peer_pipelines_report(self, main_module):
        """The launched command is now the launcher source, so a cause built from the command text
        would be hundreds of characters of bootstrap with the exit status pushed off the end."""
        error = subprocess.CalledProcessError(
            795, [sys.executable, "-c", "x" * 4000])
        cause = main_module.failure_cause(error)
        assert len(cause) <= 256
        assert "795" in cause
        assert "importlib" not in cause and "x" * 50 not in cause

    def test_the_cause_survives_an_error_without_a_return_code(self, main_module):
        assert main_module.failure_cause(object()) == (
            "Pipeline execution failed with exit status unknown")

    def test_the_failure_callback_is_sent_before_the_process_exits(self, main_module):
        source = _entry_point_source()
        failure_branch = source[source.index("except subprocess.CalledProcessError"):]
        assert failure_branch.index("send_task_failure(") < failure_branch.index("sys.exit(1)")
        assert "cause=failure_cause(e)" in failure_branch


@pytest.mark.unit
class TestGuardedLauncherIsWhatRunsMainPy:
    """`main.py` extracts inside the upstream-synced tree, so the limits are installed around
    `zipfile.ZipFile.extractall` in the child process the entry point launches. These assert the
    wiring, not the policy: a guard the launcher never installs would pass every test above."""

    def test_the_entry_point_launches_main_py_through_the_guarded_launcher(self, main_module):
        source = _entry_point_source()
        assert "_GUARDED_MAIN_LAUNCHER.format(" in source
        assert "subprocess.run([sys.executable, '-c', launcher]" in source
        assert "subprocess.run([sys.executable, 'main.py']" not in source

    def test_the_launcher_routes_extraction_through_the_limits(self, main_module):
        launcher = main_module._GUARDED_MAIN_LAUNCHER.format(
            entry_path="/opt/ml/code/__main__.py", script_path="main.py")
        compile(launcher, "<launcher>", "exec")
        assert "extract_within_limits(self," in launcher
        assert "zipfile.ZipFile.extractall = _limited_extractall" in launcher
        assert "run_name='__main__'" in launcher

    def test_the_launcher_really_meters_a_real_extraction_and_still_extracts(
            self, main_module, tmp_path):
        """Runs the launcher for real against a stand-in script: the limits must be installed on
        `zipfile.ZipFile.extractall`, the script must run as ``__main__``, a normal archive must still
        extract, and what `zipfile` copies each member from must be the metered handle — which is what
        makes the byte ceiling apply to a real extraction and not just to a fake one. Without the
        launcher the same script fails on its own checks, so the assertions are load-bearing."""
        stand_in_main = [
            "import os",
            "import shutil",
            "import zipfile",
            "",
            "if __name__ != '__main__':",
            "    raise AssertionError('the script must not run under a name other than __main__')",
            "if zipfile.ZipFile.extractall.__name__ != '_limited_extractall':",
            "    raise AssertionError('the extraction limits are not installed on extractall')",
            "copied_from = []",
            "_copyfileobj = shutil.copyfileobj",
            "",
            "",
            "def _probe(source, target, *args, **kwargs):",
            "    copied_from.append(type(source).__name__)",
            "    return _copyfileobj(source, target, *args, **kwargs)",
            "",
            "",
            "shutil.copyfileobj = _probe",
            "with zipfile.ZipFile('payload.zip', 'w') as archive:",
            "    archive.writestr('frame.txt', 'x' * 64)",
            "    archive.writestr('nested/frame.txt', 'y' * 64)",
            "with zipfile.ZipFile('payload.zip') as archive:",
            "    archive.extractall('out')",
            "for expected in ('frame.txt', os.path.join('nested', 'frame.txt')):",
            "    if not os.path.exists(os.path.join('out', expected)):",
            "        raise AssertionError('a normal archive no longer extracts: ' + expected)",
            "if open(os.path.join('out', 'frame.txt')).read() != 'x' * 64:",
            "    raise AssertionError('extracted content was altered')",
            "if copied_from != ['_MeteredArchiveMember', '_MeteredArchiveMember']:",
            "    raise AssertionError('extraction did not copy through the metered handle: '",
            "                         + repr(copied_from))",
            "print('GUARDED_LAUNCH_OK')",
        ]
        _write_child_workspace(tmp_path, stand_in_main)

        guarded = _run_child(main_module, tmp_path, guarded=True)
        assert guarded.returncode == 0, guarded.stderr
        assert "Archive extraction limits active" in guarded.stdout
        assert "GUARDED_LAUNCH_OK" in guarded.stdout

        unguarded = _run_child(main_module, tmp_path, guarded=False)
        assert unguarded.returncode != 0
        assert "not installed on extractall" in unguarded.stderr

    def test_an_archive_declaring_an_absurd_size_is_refused_at_the_real_extractall(
            self, main_module, tmp_path):
        """The whole chain, at the call site upstream uses: a `ZipFile` whose central directory
        declares a bomb-sized payload is refused by `extractall` itself, and nothing is written."""
        stand_in_main = [
            "import os",
            "import zipfile",
            "",
            "",
            "class _LyingArchive(zipfile.ZipFile):",
            "    def infolist(self):",
            "        info = zipfile.ZipInfo('bomb.bin')",
            "        info.file_size = 8 * 1024 ** 5",
            "        return [info]",
            "",
            "",
            "with zipfile.ZipFile('payload.zip', 'w') as archive:",
            "    archive.writestr('frame.txt', 'x' * 64)",
            "try:",
            "    with _LyingArchive('payload.zip') as archive:",
            "        archive.extractall('out')",
            "except ValueError as error:",
            "    print('REJECTED_AT_EXTRACTALL')",
            "else:",
            "    raise AssertionError('a bomb-shaped archive was extracted')",
            "if os.path.exists('out'):",
            "    raise AssertionError('the extraction target was created before the refusal')",
        ]
        _write_child_workspace(tmp_path, stand_in_main)

        completed = _run_child(main_module, tmp_path, guarded=True)
        assert completed.returncode == 0, completed.stderr
        assert "REJECTED_AT_EXTRACTALL" in completed.stdout
