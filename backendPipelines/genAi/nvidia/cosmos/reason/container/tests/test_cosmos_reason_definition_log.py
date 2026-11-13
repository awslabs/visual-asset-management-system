#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""What the Cosmos Reason container logs about its pipeline definition.

The Batch job passes the whole definition as ``sys.argv[1]``. The job log says how it was passed (inline
JSON or a file path) and its length, and lists the parsed definition's top-level keys. A definition that
does not parse is reported by its length, in the log and in the raised error. The container is loaded
from its path under an alias, with its bare-name sibling modules saved and restored, so the load is
hermetic against the other NVIDIA containers' modules of the same names."""

import importlib.util
import json
import logging
import os
import sys

import pytest

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIBLING_MODULES = ("inference", "model_manager", "manifest_io", "manifestHelper")

_CALLBACK_VALUE = "CALLBACK-VALUE-c7a419"
_METADATA_LOCATION = "s3://vams-auxiliary-bucket/METADATA-VALUE-0e6b/metadata.json"


@pytest.fixture(scope="module")
def entrypoint():
    saved = {name: sys.modules.get(name) for name in _SIBLING_MODULES}
    sys.path.insert(0, CONTAINER_DIR)
    try:
        for name in _SIBLING_MODULES:
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(
            "cosmos_reason_definition_log_entrypoint", os.path.join(CONTAINER_DIR, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(CONTAINER_DIR)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module


def _definition():
    """A pipeline definition shaped like the one ``constructPipeline`` writes."""
    return {
        "jobName": "cosmos-reason-job",
        "modelSize": "2B",
        "cosmosPrompt": "describe the scene",
        "inputS3AssetFilePath": "s3://vams-assets-bucket/xASSET1/clip.mp4",
        "outputS3AssetFilesPath": "s3://vams-assets-bucket/pipelines/reason/JOB/output/E1/files/",
        "outputS3AssetMetadataPath": "s3://vams-assets-bucket/pipelines/reason/JOB/output/E1/metadata/",
        "assetId": "xASSET1",
        "databaseId": "db1",
        "inputMetadataS3Location": _METADATA_LOCATION,
        "inputConfigurationS3Location": "",
        "externalSfnTaskToken": _CALLBACK_VALUE,
    }


def _unparseable_definition():
    """Inline text that is not valid JSON, longer than a path name can be and with no '/'."""
    text = json.dumps({"externalSfnTaskToken": _CALLBACK_VALUE, "cosmosPrompt": "a" * 300, "jobName": "job"})
    return text[:-1]


def test_main_logs_the_definition_keys_and_none_of_its_values(entrypoint, monkeypatch, caplog):
    """With no model bucket ``main()`` stops after it has logged the definition."""
    monkeypatch.delenv("S3_MODEL_BUCKET", raising=False)
    monkeypatch.setenv("HF_HOME", "/tmp/hf-home-before")
    argument = json.dumps(_definition())
    monkeypatch.setattr(sys, "argv", ["__main__.py", argument])
    caplog.set_level(logging.INFO)

    with pytest.raises(ValueError, match="S3_MODEL_BUCKET"):
        entrypoint.main()

    assert _CALLBACK_VALUE not in caplog.text
    assert "METADATA-VALUE-0e6b" not in caplog.text
    assert f"Loading pipeline definition: inline JSON, {len(argument)} characters" in caplog.text
    assert f"Pipeline definition loaded: keys {list(_definition())}" in caplog.text


def test_a_definition_that_does_not_parse_is_reported_by_its_length(entrypoint, monkeypatch, caplog):
    text = _unparseable_definition()
    monkeypatch.setattr(sys, "argv", ["__main__.py", text])
    caplog.set_level(logging.INFO)

    with pytest.raises(ValueError) as raised:
        entrypoint.load_pipeline_definition()

    assert f"inline JSON, {len(text)} characters" in str(raised.value)
    assert _CALLBACK_VALUE not in str(raised.value)
    assert _CALLBACK_VALUE not in caplog.text


def test_the_definition_source_is_described_by_kind_and_length(entrypoint):
    argument = json.dumps(_definition())

    assert entrypoint.describe_definition_source(argument) == f"inline JSON, {len(argument)} characters"
    assert entrypoint.describe_definition_source("/tmp/definition.json") == "file path, 20 characters"


def test_a_definition_that_is_not_an_object_is_described_by_its_type(entrypoint):
    assert entrypoint.describe_definition([_CALLBACK_VALUE]) == "a JSON list"
