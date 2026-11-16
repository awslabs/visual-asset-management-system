#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""What the Cosmos Predict 2.5 container logs about its pipeline definition.

The Batch job passes the whole definition as ``sys.argv[1]``. The job log says how it was passed (inline
JSON or a file path) and its length, and lists the parsed definition's top-level keys. A definition that
does not parse is reported by its length, in the log and in the raised error.
"""

import json
import logging
import sys

import pytest

from conftest import base_definition

_CALLBACK_VALUE = "CALLBACK-VALUE-8b02e7"
_METADATA_LOCATION = "s3://vams-auxiliary-bucket/METADATA-VALUE-51d3/metadata.json"


def _definition():
    return base_definition(externalSfnTaskToken=_CALLBACK_VALUE, inputMetadataS3Location=_METADATA_LOCATION)


def _unparseable_definition():
    """Inline text that is not valid JSON, longer than a path name can be and with no '/'."""
    text = json.dumps({"externalSfnTaskToken": _CALLBACK_VALUE, "cosmosPrompt": "a" * 300, "jobName": "job"})
    return text[:-1]


@pytest.fixture
def launch(container, monkeypatch, caplog):
    """Run ``main()`` on a definition argument; with no model bucket it stops after logging the definition."""
    monkeypatch.delenv("S3_MODEL_BUCKET", raising=False)
    monkeypatch.setenv("HF_HOME", "/tmp/hf-home-before")
    caplog.set_level(logging.INFO)

    def run(definition_argument):
        monkeypatch.setattr(sys, "argv", ["__main__.py", definition_argument])
        with pytest.raises(ValueError, match="S3_MODEL_BUCKET"):
            container.main()
        return caplog.text

    return run


def test_main_logs_the_definition_keys_and_none_of_its_values(launch):
    argument = json.dumps(_definition())
    log = launch(argument)

    assert _CALLBACK_VALUE not in log
    assert "METADATA-VALUE-51d3" not in log
    assert f"Loading pipeline definition: inline JSON, {len(argument)} characters" in log
    assert f"Pipeline definition loaded: keys {list(_definition())}" in log


def test_a_definition_that_does_not_parse_is_reported_by_its_length(container, monkeypatch, caplog):
    text = _unparseable_definition()
    monkeypatch.setattr(sys, "argv", ["__main__.py", text])
    caplog.set_level(logging.INFO)

    with pytest.raises(ValueError) as raised:
        container.load_pipeline_definition()

    assert f"inline JSON, {len(text)} characters" in str(raised.value)
    assert _CALLBACK_VALUE not in str(raised.value)
    assert _CALLBACK_VALUE not in caplog.text


def test_the_definition_source_is_described_by_kind_and_length(container):
    argument = json.dumps(_definition())

    assert container.describe_definition_source(argument) == f"inline JSON, {len(argument)} characters"
    assert container.describe_definition_source("/tmp/definition.json") == "file path, 20 characters"


def test_a_definition_that_is_not_an_object_is_described_by_its_type(container):
    assert container.describe_definition([_CALLBACK_VALUE]) == "a JSON list"
