#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The named result of the Lambda tier, against the BUILT IMAGE under the runtime interface emulator
(``tests/golden/run_golden.sh``, Lambda target only; the Batch target never returns one).

An input over the function's limit is refused from its size alone (a HeadObject, before any download), so
the object the case stages is zeros named for a STEP file: nothing reads it."""

import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import test_3dbasic_golden_round_trip as golden  # noqa: E402

lambda_only = pytest.mark.skipif(
    not (golden.RIE_URL and golden.S3_ENDPOINT and not golden.BATCH_IMAGE and golden.STEP_IMAGE),
    reason="needs the Lambda target under the emulator, built with STEP input")

# The emulator runs the function with AWS_LAMBDA_FUNCTION_MEMORY_SIZE=5308: a STEP file to .usdc
# (factor 109) is limited to (0.9 x 5308 - 300) MB / 109 = 41 MB.
OVER_THE_LIMIT_BYTES = 60 * 1024 ** 2


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


def _run(s3, cell, name, size, output_type, extra):
    folder = f"escalate-{cell}"
    s3.put_object(Bucket=golden.ASSET_BUCKET, Key=f"{folder}/{name}", Body=b"\0" * size)
    prefix = f"{golden.FILES_PREFIX}{cell}/"
    manifest = {"inputFiles": [{"relativePath": f"/{name}", "bucket": golden.ASSET_BUCKET, "key": f"{folder}/{name}"}],
                "outputs": {"bucket": golden.OUTPUT_BUCKET, "files": prefix,
                            "results": f"pipelines/golden/JOB/output/E1/results/{cell}/"}}
    s3.put_object(Bucket=golden.ASSET_BUCKET, Key=f"manifests/{cell}.json", Body=json.dumps(manifest).encode())
    s3.put_object(Bucket=golden.ASSET_BUCKET, Key=f"configs/{cell}.json", Body=json.dumps({"outputType": output_type}).encode())
    body = {"inputManifestS3Location": f"s3://{golden.ASSET_BUCKET}/manifests/{cell}.json",
            "inputConfigurationS3Location": f"s3://{golden.ASSET_BUCKET}/configs/{cell}.json",
            "workflowExecutionId": f"golden-{cell}"}
    body.update(extra)
    return golden._invoke(body)


@lambda_only
@pytest.mark.golden
def test_an_input_over_the_limit_returns_the_named_result_when_the_state_machine_asks(s3):
    response = _run(s3, "size", "big.step", OVER_THE_LIMIT_BYTES, ".usdc", {"allowEscalation": True})
    assert response["escalate"] is True and response["reason"] == "size"
    assert response["inputName"] == "big.step" and response["inputBytes"] == OVER_THE_LIMIT_BYTES
    assert response["formatPair"] == ".step to .usdc"
    assert response["resource"] == "function memory"
    assert 5308 < response["requiredMemoryMiB"] < 30 * 1024
    assert sorted(response["limitBytesByTaskMemoryMiB"]) == ["122880", "30720", "61440"]
    assert response["minTaskMemoryMiB"] == 5308
    assert "limit for converting .step to .usdc" in response["message"]


@lambda_only
@pytest.mark.golden
def test_without_the_flag_the_same_input_fails_with_the_text_it_always_had(s3):
    named = _run(s3, "size-named", "big.step", OVER_THE_LIMIT_BYTES, ".usdc", {"allowEscalation": True})
    plain = _run(s3, "size-plain", "big.step", OVER_THE_LIMIT_BYTES, ".usdc", {})
    assert "escalate" not in plain
    assert plain["errorMessage"] == named["message"]


def _stage_fixture(s3, cell, extension, output_type):
    names, _, _ = golden.FIXTURES[extension]
    folder = f"escalate-{cell}"
    for name, data in names.items():
        s3.put_object(Bucket=golden.ASSET_BUCKET, Key=f"{folder}/{name}", Body=data)
    input_name = next(iter(names))
    results = f"pipelines/golden/JOB/output/E1/results/{cell}/"
    manifest = {"inputFiles": [{"relativePath": f"/{input_name}", "bucket": golden.ASSET_BUCKET,
                                "key": f"{folder}/{input_name}"}],
                "outputs": {"bucket": golden.OUTPUT_BUCKET, "files": f"{golden.FILES_PREFIX}{cell}/",
                            "results": results}}
    s3.put_object(Bucket=golden.ASSET_BUCKET, Key=f"manifests/{cell}.json", Body=json.dumps(manifest).encode())
    s3.put_object(Bucket=golden.ASSET_BUCKET, Key=f"configs/{cell}.json", Body=json.dumps({"outputType": output_type}).encode())
    return results, {"inputManifestS3Location": f"s3://{golden.ASSET_BUCKET}/manifests/{cell}.json",
                     "inputConfigurationS3Location": f"s3://{golden.ASSET_BUCKET}/configs/{cell}.json",
                     "workflowExecutionId": f"golden-{cell}"}


@lambda_only
@pytest.mark.golden
def test_a_run_that_fits_writes_the_run_document_only_when_the_state_machine_sent_the_flag(s3):
    results, body = _stage_fixture(s3, "doc-on", ".stl", ".glb")
    assert golden._invoke({**body, "allowEscalation": True}) == {"statusCode": 200, "body": "Success"}
    document = json.loads(s3.get_object(Bucket=golden.OUTPUT_BUCKET, Key=results + "conversion-run.json")["Body"].read())
    assert document["schema"] == "vams.conversion3dBasic.run/v1"
    assert document["tier"] == "lambda" and document["taskMemoryMiB"] == 5308
    assert set(document["phaseSeconds"]) >= {"head", "download", "references", "convert", "upload"}
    assert "escalatedFrom" not in document

    results, body = _stage_fixture(s3, "doc-off", ".stl", ".glb")
    assert golden._invoke(body) == {"statusCode": 200, "body": "Success"}
    listing = s3.list_objects_v2(Bucket=golden.OUTPUT_BUCKET, Prefix=results).get("Contents", [])
    assert not [item for item in listing if item["Key"].endswith("conversion-run.json")]
