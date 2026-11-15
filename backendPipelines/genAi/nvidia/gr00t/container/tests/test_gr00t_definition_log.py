#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""What the GR00T container logs about its pipeline definition.

The Batch job passes the whole definition as ``sys.argv[1]``. The job log says how it was passed (inline
JSON or a file path) and its length, and lists the parsed definition's top-level keys. A definition that
does not parse is reported by its length, in the log and in the raised error.
"""

import importlib.util
import json
import logging
import os
import sys

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

_CALLBACK_VALUE = "CALLBACK-VALUE-e5309c"
_METADATA_LOCATION = "s3://vams-auxiliary-bucket/METADATA-VALUE-6f2a/metadata.json"


def _load_container_entrypoint():
    """``__main__.py`` cannot be imported under that name — it belongs to the running interpreter — so
    it is loaded from its path under an alias, which also leaves its ``if __name__`` guard inert."""
    spec = importlib.util.spec_from_file_location(
        "gr00t_container_entrypoint_definition_log", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


container = _load_container_entrypoint()


def _definition():
    """A pipeline definition shaped like the one ``constructPipeline`` writes."""
    return {
        "jobName": "gr00t-finetune-job",
        "mode": "finetune",
        "inputS3AssetPath": "s3://vams-assets-bucket/xASSET1/",
        "outputS3AssetFilesPath": "s3://vams-assets-bucket/pipelines/gr00t/JOB/output/E1/files/",
        "assetId": "xASSET1",
        "databaseId": "db1",
        "gr00tConfig": "{}",
        "inputMetadataS3Location": _METADATA_LOCATION,
        "inputConfigurationS3Location": "",
        "externalSfnTaskToken": _CALLBACK_VALUE,
    }


def _unparseable_definition():
    """Inline text that is not valid JSON, longer than a path name can be and with no '/'."""
    text = json.dumps({"externalSfnTaskToken": _CALLBACK_VALUE, "gr00tConfig": "a" * 300, "jobName": "job"})
    return text[:-1]


@pytest.mark.unit
def test_main_logs_the_definition_keys_and_none_of_its_values(monkeypatch, caplog):
    """With no model bucket ``main()`` stops after it has logged the definition."""
    monkeypatch.delenv("S3_MODEL_BUCKET", raising=False)
    monkeypatch.setenv("HF_HOME", "/tmp/hf-home-before")
    argument = json.dumps(_definition())
    monkeypatch.setattr(sys, "argv", ["__main__.py", argument])
    caplog.set_level(logging.INFO)

    with pytest.raises(ValueError, match="S3_MODEL_BUCKET"):
        container.main()

    assert _CALLBACK_VALUE not in caplog.text
    assert "METADATA-VALUE-6f2a" not in caplog.text
    assert f"Loading pipeline definition: inline JSON, {len(argument)} characters" in caplog.text
    assert f"Pipeline definition loaded: keys {list(_definition())}" in caplog.text


@pytest.mark.unit
def test_a_definition_that_does_not_parse_is_reported_by_its_length(monkeypatch, caplog):
    text = _unparseable_definition()
    monkeypatch.setattr(sys, "argv", ["__main__.py", text])
    caplog.set_level(logging.INFO)

    with pytest.raises(ValueError) as raised:
        container.load_pipeline_definition()

    assert f"inline JSON, {len(text)} characters" in str(raised.value)
    assert _CALLBACK_VALUE not in str(raised.value)
    assert _CALLBACK_VALUE not in caplog.text


@pytest.mark.unit
def test_the_definition_source_is_described_by_kind_and_length():
    argument = json.dumps(_definition())

    assert container.describe_definition_source(argument) == f"inline JSON, {len(argument)} characters"
    assert container.describe_definition_source("/tmp/definition.json") == "file path, 20 characters"


@pytest.mark.unit
def test_a_definition_that_is_not_an_object_is_described_by_its_type():
    assert container.describe_definition([_CALLBACK_VALUE]) == "a JSON list"
