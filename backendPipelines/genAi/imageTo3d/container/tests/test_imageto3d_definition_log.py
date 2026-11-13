# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the Image to 3D container logs about its pipeline definition.

The Batch job passes the whole definition as ``sys.argv[1]``. ``run()`` logs the definition by its
top-level keys, not their values, before it reads anything from Amazon S3.
"""

import importlib.util
import json
import logging
import os
import sys

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CALLBACK_VALUE = "CALLBACK-VALUE-0c93a2"
_METADATA_LOCATION = "s3://aux-bucket/METADATA-VALUE-b417/metadata.json"


@pytest.fixture(scope="module")
def main_module():
    spec = importlib.util.spec_from_file_location(
        "imageto3d_container_main_definition_log", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _definition():
    """A pipeline definition shaped like the one ``constructPipeline`` writes."""
    return {
        "jobName": "imageto3d-job",
        "assetId": "xASSET1",
        "databaseId": "db1",
        "inputFile": {"bucketName": "asset-bucket", "objectKey": "xASSET1/photo.png", "fileExtension": ".png"},
        "outputFiles": {"bucketName": "asset-bucket", "objectDir": "pipelines/imageTo3d/JOB/output/E1/files/"},
        "outputMetadata": {"bucketName": "asset-bucket", "objectDir": "pipelines/imageTo3d/JOB/output/E1/metadata/"},
        "outputResults": {"bucketName": "asset-bucket", "objectDir": "pipelines/imageTo3d/JOB/output/E1/results/"},
        "bucketRegions": {},
        "inputMetadataS3Location": _METADATA_LOCATION,
        "inputConfigurationS3Location": "",
        "externalSfnTaskToken": _CALLBACK_VALUE,
    }


class _RunStopped(Exception):
    """Raised by the stubbed Region registration, which ``run()`` calls right after it logs the definition."""


@pytest.mark.unit
def test_run_logs_the_definition_keys_and_none_of_its_values(main_module, monkeypatch, caplog):
    def stop(_bucket_regions):
        raise _RunStopped()

    monkeypatch.setattr(main_module.s3, "register_bucket_regions", stop)
    monkeypatch.setattr(sys, "argv", ["__main__.py", json.dumps(_definition())])
    caplog.set_level(logging.INFO)

    with pytest.raises(_RunStopped):
        main_module.run()

    assert _CALLBACK_VALUE not in caplog.text
    assert "METADATA-VALUE-b417" not in caplog.text
    assert f"Pipeline definition: keys {list(_definition())}" in caplog.text


@pytest.mark.unit
def test_a_definition_that_is_not_an_object_is_described_by_its_type(main_module):
    assert main_module.describe_definition([_CALLBACK_VALUE]) == "a JSON list"
