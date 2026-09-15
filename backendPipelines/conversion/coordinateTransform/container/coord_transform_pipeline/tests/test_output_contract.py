# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the container publishes, and where.

Run from the container directory:

    cd backendPipelines/conversion/coordinateTransform/container
    python -m pytest coord_transform_pipeline/tests -q

Three properties are pinned, and each is observable only in the S3 keys the stage writes plus the
task-token callback it fires — a status-only test would pass against a build that publishes nothing:

* a run that produced no output file, or whose upload failed, fails the stage. Otherwise the workflow
  step is green with nothing in the bucket, and the only record of the failure is a CloudWatch line.
* each output keeps the input file's own subdirectory within the asset, sliced at the assetId threaded
  through the pipeline definition. Collapsed to the output prefix root, two inputs that share a stem in
  different folders overwrite each other's result.
* the run report and the metadata sidecars go to the metadata path only, so they do not also become
  versioned asset files, and no object is written twice.

`coord_xform.pipeline` is stubbed: it needs pyproj, open3d and laspy, and this container declares
pydantic>=2.0 while the interpreter runs the backend's pydantic 1.10.13. `coord_xform.config` is NOT
stubbed — it imports cleanly under pydantic v1, so the real `PipelineConfig` the handler builds is
exercised. `_validate_transform_outputs` is not stubbed either: the stubbed transform writes real LAS
headers and real E57 bytes, which is what makes the upload assertions below assertions about a run that
got that far.

The stub writes into the output directory the handler chose (`config.output.directory`) rather than into
a directory a test picked, so the walk that produces the S3 keys is the real one.
"""

import io
import os
import struct
import sys
import types
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _CONTAINER)

from coord_transform_pipeline import core  # noqa: E402

ASSET_ID = "xd130a6d6ab"
FILES_PREFIX = "pipelines/Coordinate Transform/CoordXform_1/output/exec-1/files"
METADATA_PREFIX = "pipelines/Coordinate Transform/CoordXform_1/output/exec-1/metadata"
PARAMS = '{"sourceCrs":"EPSG:4326","targetCrs":"EPSG:27700"}'

# (maxx, minx, maxy, miny, maxz, minz) — the LAS header's alternating max/min order.
FINITE_BOUNDS = (482763.8, 482060.5, 4392000.0, 4391000.0, 2000.0, 1800.0)
E57_WKT = 'PROJCRS["OSGB36 / British National Grid",ID["EPSG",27700]]'


def _write_las(path):
    """A minimal LAS 1.2 file whose header carries finite, non-inverted bounds.

    Only the fields `_validate_transform_outputs` reads are meaningful: the LASF signature, the version,
    the legacy point count, and the six bounding-box doubles from offset 179.
    """
    header = bytearray(227)
    header[0:4] = b"LASF"
    header[24] = 1
    header[25] = 2
    struct.pack_into("<H", header, 94, 227)
    struct.pack_into("<I", header, 107, 1000)
    struct.pack_into("<6d", header, 179, *FINITE_BOUNDS)
    path.write_bytes(bytes(header))


def _write_e57(path, crs=E57_WKT):
    """A minimal E57 whose root records `crs`, or nothing when `crs` is blank.

    One physical page, so no checksum interleaving to build: the paged case is pinned in
    `tests/test_output_validation.py`. Only what `_validate_transform_outputs` reads is meaningful — the
    `ASTM-E57` signature, the xmlPhysicalOffset at 24, the xmlLogicalLength at 32, and the element.
    """
    element = (
        f'<coordinateMetadata type="String"><![CDATA[{crs}]]></coordinateMetadata>'
        if crs
        else '<coordinateMetadata type="String"/>'
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<e57Root type="Structure">\n  {element}\n</e57Root>\n'
    ).encode("utf-8")

    header = bytearray(48)
    header[0:8] = b"ASTM-E57"
    struct.pack_into("<Q", header, 24, len(header))
    struct.pack_into("<Q", header, 32, len(xml))

    logical = bytes(header) + xml
    assert len(logical) <= 1020, "this helper only builds a single-page file"
    path.write_bytes(logical.ljust(1020, b"\0") + b"\xde\xad\xbe\xef")


class _Report:
    """The subset of coord_xform's PipelineReport that the handler reads."""

    def __init__(self, output_files, errors=()):
        self.output_files = list(output_files)
        self.errors = list(errors)
        self.total_points_processed = 1000 * len(self.output_files)
        self.residual_error_mm = 0.0


class _Uploads:
    """Every s3.upload call the stage made, and the failure mode utils/aws/s3.py has."""

    def __init__(self):
        self.calls = []
        self.fail_all = False

    @property
    def keys(self):
        return [key for _bucket, key in self.calls]

    def keys_under(self, prefix):
        return sorted(key for key in self.keys if key.startswith(prefix))


@pytest.fixture
def uploads(monkeypatch):
    recorded = _Uploads()

    def upload(bucket, object_key, file_path):
        recorded.calls.append((bucket, object_key))
        # None is what utils/aws/s3.py returns when the PutObject raises a ClientError.
        return None if recorded.fail_all else object_key

    monkeypatch.setattr(core.s3, "upload", upload)
    return recorded


@pytest.fixture
def callbacks(monkeypatch):
    """Which task-token callback run() fires, in order."""
    calls = []
    monkeypatch.setattr(core.sfn, "send_task_heartbeat", lambda token: None)
    monkeypatch.setattr(
        core.sfn, "send_task_failure", lambda msg="": calls.append(("failure", msg))
    )
    monkeypatch.setattr(
        core.sfn, "send_task_success", lambda out: calls.append(("success", out))
    )
    return calls


@pytest.fixture(autouse=True)
def stub_download(monkeypatch):
    """Make the S3 download succeed without S3, so every test reaches the transform."""
    monkeypatch.setattr(core.s3, "download", lambda bucket, key, dest: dest)


@pytest.fixture
def transform(monkeypatch):
    """Install a stand-in for coord_xform.pipeline.run_pipeline that writes the named files.

    `container/tests/conftest.py` installs its own stand-in for this module at collection time. This
    reuses that module object when it is present rather than replacing it, so both suites read the same
    module `core.py` imports; monkeypatch restores the attribute afterwards, so neither suite observes
    the other's behaviour.
    """
    module = sys.modules.get("coord_xform.pipeline")
    if module is None:
        module = types.ModuleType("coord_xform.pipeline")
        monkeypatch.setitem(sys.modules, "coord_xform.pipeline", module)

    def install(written_names=(), reported=None, errors=(), e57_crs=E57_WKT):
        def run_pipeline(config, inputs):
            output_dir = Path(config.output.directory)
            for name in written_names:
                target = output_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.suffix.lower() in (".las", ".laz"):
                    _write_las(target)
                elif target.suffix.lower() == ".e57":
                    _write_e57(target, crs=e57_crs)
                else:
                    target.write_text("{}", encoding="utf-8")
            # coord_xform reports the point-cloud files it wrote; the sidecars are not among them.
            names = (
                reported
                if reported is not None
                else [
                    name
                    for name in written_names
                    if Path(name).suffix.lower() in (".las", ".laz", ".e57", ".ply")
                ]
            )
            return _Report([output_dir / name for name in names], errors)

        monkeypatch.setattr(module, "run_pipeline", run_pipeline, raising=False)

    return install


def _definition(object_key, asset_id=ASSET_ID, metadata_prefix=METADATA_PREFIX):
    """The pipeline definition constructPipeline builds, as the container receives it on argv.

    The stage carries no `transformConfig` and no metadata document: the transform configuration
    reaches the container once, as `inputParameters`, and the resolved asset metadata is merged into it
    upstream. Omitting `transformConfig` here also exercises the `PipelineStage` default that makes the
    absent key valid. The producer side of this shape is pinned by the lambda's own
    `test_definition_satisfies_the_container_dataclasses`, which instantiates these dataclasses from the
    emitted definition.
    """
    return {
        "jobName": "CoordXform_1",
        "stages": [
            {
                "type": "COORD_TRANSFORM",
                "inputFile": {
                    "bucketName": "asset-bucket",
                    "objectKey": object_key,
                    "fileExtension": ".laz",
                },
                "outputFiles": {
                    "bucketName": "asset-bucket",
                    "objectDir": f"{FILES_PREFIX}/",
                },
                "outputMetadata": {
                    "bucketName": "asset-bucket" if metadata_prefix else "",
                    "objectDir": f"{metadata_prefix}/" if metadata_prefix else "",
                },
            }
        ],
        "inputMetadata": "",
        "inputParameters": PARAMS,
        "externalSfnTaskToken": "external-token",
        "localTest": "False",
        "assetId": asset_id,
        "databaseId": "smoke-db",
    }


def _read_container_source(relative_path):
    return io.open(
        os.path.join(_CONTAINER, *relative_path.split("/")), encoding="utf-8"
    ).read()


# --- a run that published nothing must not report success -------------------------------------------


def test_zero_output_files_reports_failure_not_success(transform, uploads, callbacks):
    """The worst case: a green workflow step with no output.

    A transform can report neither an error nor an output file — coord_xform records an error only for
    a file that raised, so a reader that yields no chunks writes nothing and reports nothing. Before the
    check this run set the stage COMPLETE and fired SendTaskSuccess, which is what makes this the
    positive control for the fix: the unfixed handler produces ["success"] here.
    """
    transform(written_names=(), reported=[])

    core.run(_definition(f"{ASSET_ID}/cloud.laz"))

    assert [kind for kind, _ in callbacks] == ["failure"], (
        "a run that produced no output file must fail the stage, not report a green step"
    )
    assert "no output files" in callbacks[0][1]
    assert uploads.calls == [], "nothing should have been published"


def test_a_failed_upload_reports_failure_not_success(transform, uploads, callbacks):
    """`s3.upload` returns None on a ClientError, and the return used to be discarded.

    An AccessDenied or a KMS denial therefore ended as a completed conversion with an empty asset. The
    unfixed handler produces ["success"] here, which is the positive control.
    """
    transform(written_names=("cloud_EPSG_27700.laz",))
    uploads.fail_all = True

    core.run(_definition(f"{ASSET_ID}/cloud.laz"))

    assert [kind for kind, _ in callbacks] == ["failure"]
    assert "Failed to upload" in callbacks[0][1]
    assert "cloud_EPSG_27700.laz" in callbacks[0][1], (
        "the operator needs to know which object could not be written"
    )


def test_a_clean_run_still_reports_success(transform, uploads, callbacks):
    """The positive control for the two checks above.

    Without it both are satisfied by a container that fails unconditionally, which would be a worse
    regression than the one being fixed.
    """
    transform(written_names=("cloud_EPSG_27700.laz",))

    core.run(_definition(f"{ASSET_ID}/cloud.laz"))

    assert [kind for kind, _ in callbacks] == ["success"], (
        f"a clean run must succeed, got {callbacks}"
    )
    assert uploads.keys_under(FILES_PREFIX) == [
        f"{FILES_PREFIX}/cloud_EPSG_27700.laz"
    ]


def test_an_e57_output_recording_its_crs_is_published(transform, uploads, callbacks):
    """The positive control for the arm below: a well-formed E57 output completes and is uploaded."""
    transform(written_names=("cloud_EPSG_27700.e57",))

    core.run(_definition(f"{ASSET_ID}/cloud.laz"))

    assert [kind for kind, _ in callbacks] == ["success"]
    assert uploads.keys_under(FILES_PREFIX) == [
        f"{FILES_PREFIX}/cloud_EPSG_27700.e57"
    ]


def test_an_e57_output_recording_no_crs_fails_the_stage(transform, uploads, callbacks):
    """An E57 with no CRS on its root must not be published as a successful conversion.

    The CRS is written through libe57, which cannot replace an existing child element, so a dropped
    write would otherwise be silent: the file lands on the asset with reprojected coordinates and no
    record of the system they are in, and a second run over it is rejected for having no detectable CRS.
    The validator runs before the upload, so nothing reaches the bucket.
    """
    transform(written_names=("cloud_EPSG_27700.e57",), e57_crs="")

    core.run(_definition(f"{ASSET_ID}/cloud.laz"))

    assert [kind for kind, _ in callbacks] == ["failure"], (
        f"an E57 recording no CRS must fail the stage, got {callbacks}"
    )
    assert "cloud_EPSG_27700.e57" in callbacks[0][1]
    assert "no coordinate reference system" in callbacks[0][1]
    assert uploads.calls == [], "nothing should have been published"


def test_a_reported_error_still_fails_the_stage(transform, uploads, callbacks):
    """The neighbouring check this one sits beside, kept from regressing when the two were combined."""
    transform(
        written_names=("cloud_EPSG_27700.laz",),
        errors=["cloud.laz: ValueError: no readable points"],
    )

    core.run(_definition(f"{ASSET_ID}/cloud.laz"))

    assert [kind for kind, _ in callbacks] == ["failure"]
    assert "no readable points" in callbacks[0][1]


# --- outputs keep the input file's subdirectory within the asset ------------------------------------


def test_output_preserves_the_input_relative_subdirectory(transform, uploads, callbacks):
    """The asset-relative subdirectory of the input is part of the output key.

    Positive control: the unfixed handler wrote `{FILES_PREFIX}/cloud_EPSG_27700.laz`, which the last
    assertion rejects.
    """
    transform(written_names=("cloud_EPSG_27700.laz",))

    core.run(_definition(f"{ASSET_ID}/scans/room1/cloud.laz"))

    assert [kind for kind, _ in callbacks] == ["success"]
    assert uploads.keys_under(FILES_PREFIX) == [
        f"{FILES_PREFIX}/scans/room1/cloud_EPSG_27700.laz"
    ]
    assert f"{FILES_PREFIX}/cloud_EPSG_27700.laz" not in uploads.keys, (
        "the output must not collapse to the output prefix root"
    )


def test_inputs_sharing_a_stem_in_different_folders_do_not_collide(
    transform, uploads, callbacks
):
    """The impact assertion for the collapse: two subfolders' outputs must be different objects."""
    transform(written_names=("cloud_EPSG_27700.laz",))

    core.run(_definition(f"{ASSET_ID}/scanA/cloud.laz"))
    first = uploads.keys_under(FILES_PREFIX)
    uploads.calls.clear()
    core.run(_definition(f"{ASSET_ID}/scanB/cloud.laz"))
    second = uploads.keys_under(FILES_PREFIX)

    assert first and second
    assert first != second, (
        "two inputs sharing a stem in different folders overwrote each other at the asset root"
    )


def test_an_input_at_the_asset_root_gets_no_subdirectory(transform, uploads, callbacks):
    transform(written_names=("cloud_EPSG_27700.laz",))

    core.run(_definition(f"{ASSET_ID}/cloud.laz"))

    assert uploads.keys_under(FILES_PREFIX) == [
        f"{FILES_PREFIX}/cloud_EPSG_27700.laz"
    ]


def test_an_object_key_without_the_asset_id_keeps_the_old_layout(
    transform, uploads, callbacks
):
    """A direct invoke carries no asset, so there is no subdirectory to derive and none is invented."""
    transform(written_names=("cloud_EPSG_27700.laz",))

    core.run(_definition("some/other/path/cloud.laz", asset_id=""))

    assert [kind for kind, _ in callbacks] == ["success"]
    assert uploads.keys_under(FILES_PREFIX) == [
        f"{FILES_PREFIX}/cloud_EPSG_27700.laz"
    ]


@pytest.mark.parametrize(
    "object_key,asset_id,expected",
    [
        (f"{ASSET_ID}/cloud.laz", ASSET_ID, ""),
        (f"{ASSET_ID}/scans/cloud.laz", ASSET_ID, "scans"),
        (f"{ASSET_ID}/scans/room1/cloud.laz", ASSET_ID, "scans/room1"),
        (f"prefix/{ASSET_ID}/scans/cloud.laz", ASSET_ID, "scans"),
        ("scans/cloud.laz", "", ""),
        ("scans/cloud.laz", "not-in-the-key", ""),
    ],
)
def test_relative_subdir_is_sliced_at_the_threaded_asset_id(
    object_key, asset_id, expected
):
    assert core._relative_subdir_from_object_key(object_key, asset_id) == expected


# --- sidecars are metadata, not asset files ---------------------------------------------------------


def test_sidecars_go_to_the_metadata_path_only(transform, uploads, callbacks):
    """Each sidecar is written once, under the metadata prefix.

    Positive control: the unfixed handler uploaded `*_scan_metadata.json` and `*_camera.json` to BOTH
    prefixes, so the files-prefix assertion listed three objects rather than one and the call count was
    six rather than four.
    """
    transform(
        written_names=(
            "cloud_EPSG_27700.laz",
            "cloud_EPSG_27700_scan_metadata.json",
            "cloud_camera.json",
            "transform_report.json",
        )
    )

    core.run(_definition(f"{ASSET_ID}/scans/room1/cloud.laz"))

    assert [kind for kind, _ in callbacks] == ["success"]
    assert uploads.keys_under(FILES_PREFIX) == [
        f"{FILES_PREFIX}/scans/room1/cloud_EPSG_27700.laz"
    ], "sidecar JSON must not become a versioned asset file"
    assert uploads.keys_under(METADATA_PREFIX) == sorted(
        [
            f"{METADATA_PREFIX}/scans/room1/cloud_EPSG_27700_scan_metadata.json",
            f"{METADATA_PREFIX}/scans/room1/cloud_camera.json",
            f"{METADATA_PREFIX}/scans/room1/transform_report.json",
        ]
    )
    assert len(uploads.calls) == 4, (
        f"four files written, four uploads expected, got {uploads.keys}"
    )


def test_sidecars_stay_with_the_outputs_when_no_metadata_path_is_configured(
    transform, uploads, callbacks
):
    """With no metadata destination the sidecars are kept rather than silently dropped.

    The run report is still never an asset file: it describes the run, not the asset.
    """
    transform(
        written_names=(
            "cloud_EPSG_27700.laz",
            "cloud_camera.json",
            "transform_report.json",
        )
    )

    core.run(_definition(f"{ASSET_ID}/cloud.laz", metadata_prefix=""))

    assert [kind for kind, _ in callbacks] == ["success"]
    assert uploads.keys_under(FILES_PREFIX) == sorted(
        [
            f"{FILES_PREFIX}/cloud_EPSG_27700.laz",
            f"{FILES_PREFIX}/cloud_camera.json",
        ]
    )
    assert not any(key.endswith("transform_report.json") for key in uploads.keys)


def test_copied_images_are_asset_files_and_not_metadata(transform, uploads, callbacks):
    """A multi-image dataset copies its imagery into the output directory.

    Those files belong with the outputs, and the metadata prefix must receive nothing for them.
    """
    transform(written_names=("cloud_EPSG_27700.laz", "images/room1.jpg"))

    core.run(_definition(f"{ASSET_ID}/cloud.laz"))

    assert uploads.keys_under(FILES_PREFIX) == sorted(
        [
            f"{FILES_PREFIX}/cloud_EPSG_27700.laz",
            f"{FILES_PREFIX}/images/room1.jpg",
        ]
    )
    assert uploads.keys_under(METADATA_PREFIX) == []


# --- guards on the contract the routing reads -------------------------------------------------------


def test_the_sidecar_names_match_what_coord_xform_writes():
    """The routing is by file name, so a rename in coord_xform silently sends a sidecar to the wrong
    prefix while every assertion above stays green — the stub writes the names this test reads.

    Each name must also be written into the output directory the handler walks, or the file is never
    found at all.
    """
    source = _read_container_source("coord_xform/pipeline.py")

    for literal in (
        f'"{core.RUN_REPORT_FILENAME}"',
        *core.METADATA_SIDECAR_SUFFIXES,
    ):
        assert literal in source, (
            f"{literal} is what core.py routes on; coord_xform no longer writes it"
        )
        window = source[max(0, source.index(literal) - 200) : source.index(literal)]
        assert "output_dir" in window, (
            f"{literal} must be written into the pipeline's output directory for the upload walk to "
            f"find it"
        )


def test_the_report_field_the_completeness_check_reads_still_exists():
    """`report.output_files` is what the empty-output check reads; a rename would make it vacuous."""
    source = _read_container_source("coord_xform/models.py")

    assert "output_files" in source, (
        "PipelineReport.output_files is the field core.py checks before publishing"
    )
