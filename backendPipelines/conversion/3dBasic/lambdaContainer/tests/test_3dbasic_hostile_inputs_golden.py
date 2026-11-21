#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Hostile inputs against the BUILT IMAGE, on whichever compute tier the golden run targets (#1732).

``tests/golden/run_golden.sh`` runs this module beside the round-trip suite, against the Lambda
function (the runtime interface emulator) or, with ``GOLDEN_TARGET=batch``, against the image's AWS
Batch target. The same cases and the same expected text apply to both, so a refusal one tier makes and
the other does not fails here. Each case stages a hostile file, runs the image, and checks that the run
failed with the named message, wrote no output, and left the next run working (a clean conversion
follows every hostile case).

Not here: an instancing bomb (a COLLADA or nested-USD scene that places 2^30 copies). This container bounds a
USD scene with ``scene_limits`` but a COLLADA scene only by the worker's address-space limit, and a nested
USD bomb takes minutes to reach its node bound, so a case would run for minutes and tens of GiB; it is
recorded as a finding in the handoff, not asserted here."""

import io
import json
import os
import sys
import zipfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import test_3dbasic_golden_round_trip as golden  # noqa: E402

trimesh = pytest.importorskip("trimesh")
needs_image = golden.needs_image

_DOCTYPE = b'<?xml version="1.0"?><!DOCTYPE COLLADA [<!ENTITY a "aaaa">]><COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema"/>'


def _gltf(uri):
    """A glTF whose one buffer names ``uri``."""
    return json.dumps({
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                       "min": [0, 0, 0], "max": [1, 1, 0]}],
        "bufferViews": [{"buffer": 0, "byteLength": 36}],
        "buffers": [{"uri": uri, "byteLength": 36}],
    }).encode("utf-8")


def _threemf(mutate):
    data = trimesh.creation.box().export(file_type="3mf")
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            result = mutate(info.filename, source.read(info.filename))
            if result is not None:
                target.writestr(*result)
    return out.getvalue()


def _doctype_3mf():
    return _threemf(lambda name, content: (name, content.replace(
        b"<model", b'<!DOCTYPE model [<!ENTITY a "b">]><model', 1) if name.endswith(".model") else content))


def _zip_bomb_3mf():
    return _threemf(lambda name, content: (name, b"\0" * (80 * 1024 ** 2)) if name.endswith(".model") else (name, content))


# (id, input file name, content, output type, expected text in the error)
HOSTILE = [
    ("dae-doctype", "bomb.dae", _DOCTYPE, ".glb", "not allowed in a 3D model file"),
    ("3mf-doctype", "bomb.3mf", _doctype_3mf, ".glb", "not allowed in a 3D model file"),
    ("3mf-zip-bomb", "bomb.3mf", _zip_bomb_3mf, ".glb", "compression ratio"),
    ("gltf-parent-path", "part.gltf", lambda: _gltf("../outside.bin"), ".glb", "path relative to"),
    ("gltf-absolute-path", "part.gltf", lambda: _gltf("/etc/passwd"), ".glb", "path relative to"),
    ("gltf-url", "part.gltf", lambda: _gltf("s3://another-bucket/other/b.bin"), ".glb", "path relative to"),
    ("gltf-missing-buffer", "part.gltf", lambda: _gltf("gone.bin"), ".glb", "gone.bin"),
    ("stl-truncated", "cut.stl", b"solid x\nfacet normal 0 0 1\n outer loop\n vertex 0 0", ".glb", ""),
    ("glb-garbage", "junk.glb", b"glTF" + b"\xff" * 64, ".stl", ""),
    ("ply-garbage", "junk.ply", b"ply\nformat binary_little_endian 1.0\nelement vertex 999999999\n"
                                b"property float x\nend_header\n\0\0", ".glb", ""),
    ("usdz-garbage", "junk.usdz", b"PK\x03\x04" + b"\0" * 64, ".glb", ""),
]


def _run_case(s3, case_id, name, content, output_type):
    data = content() if callable(content) else content
    folder = f"hostile-{case_id}"
    s3.put_object(Bucket=golden.ASSET_BUCKET, Key=f"{folder}/{name}", Body=data)
    prefix = f"{golden.FILES_PREFIX}{case_id}/"
    manifest = {"inputFiles": [{"relativePath": f"/{name}", "bucket": golden.ASSET_BUCKET, "key": f"{folder}/{name}"}],
                "outputs": {"bucket": golden.OUTPUT_BUCKET, "files": prefix}}
    s3.put_object(Bucket=golden.ASSET_BUCKET, Key=f"manifests/{case_id}.json", Body=json.dumps(manifest).encode())
    s3.put_object(Bucket=golden.ASSET_BUCKET, Key=f"configs/{case_id}.json",
                  Body=json.dumps({"outputType": output_type}).encode())
    response = golden._invoke({"inputManifestS3Location": f"s3://{golden.ASSET_BUCKET}/manifests/{case_id}.json",
                               "inputConfigurationS3Location": f"s3://{golden.ASSET_BUCKET}/configs/{case_id}.json",
                               "workflowExecutionId": f"hostile-{case_id}"})
    written = s3.list_objects_v2(Bucket=golden.OUTPUT_BUCKET, Prefix=prefix).get("Contents", [])
    return response, written


@pytest.fixture(scope="module")
def s3():
    import boto3
    client = boto3.client("s3", endpoint_url=golden.S3_ENDPOINT, region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    for bucket in (golden.ASSET_BUCKET, golden.OUTPUT_BUCKET):
        try:
            client.create_bucket(Bucket=bucket)
        except client.exceptions.BucketAlreadyOwnedByYou:
            pass
    return client


@needs_image
@pytest.mark.golden
@pytest.mark.parametrize("case_id,name,content,output_type,expected", HOSTILE, ids=[case[0] for case in HOSTILE])
def test_a_hostile_input_is_refused_with_its_message_and_writes_nothing(s3, case_id, name, content, output_type, expected):
    response, written = _run_case(s3, case_id, name, content, output_type)
    assert isinstance(response, dict) and "errorMessage" in response, response
    message = response["errorMessage"]
    assert message, response
    if expected:
        assert any(part in message for part in expected.split("|")), message
    # Whatever the hostile file held, nothing of it is in the error: no object key of another bucket and
    # no credential material, and no output was uploaded.
    assert "AKIA" not in message and "/var/task" not in message
    assert not written


@needs_image
@pytest.mark.golden
def test_a_clean_conversion_still_works_after_the_hostile_ones(s3):
    # The container (or its Batch task) is reused or fresh; either way the next run is unaffected.
    cell = "hostile_after"
    response, written = golden._convert(s3, cell, ".stl", ".glb")
    assert response == {"statusCode": 200, "body": "Success"}
    assert written
