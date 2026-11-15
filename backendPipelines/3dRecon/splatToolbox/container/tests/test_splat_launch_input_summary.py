#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for what the splatToolbox container prints about its launch inputs.

``main()`` starts by printing a summary of its command line, where the Batch job passes the whole
pipeline definition as ``sys.argv[1]``, and of the environment variables under
``LAUNCH_ENV_PREFIXES``. The summary names the definition's top-level keys and the variables, and
prints a value only for the variables ``LAUNCH_ENV_VALUES_PRINTED`` lists.
"""

import json
import os
import sys

import pytest

_DEFINITION_VALUE = "DEFINITION-VALUE-1f0c7d2e"
_ENV_VALUES = {
    "TASK_TOKEN": "ENV-VALUE-TASK-6b1e",
    "AWS_EXECUTION_ENV": "ENV-VALUE-AWS-93c4",
    "VAMS_EXAMPLE_SETTING": "ENV-VALUE-VAMS-0d7a",
    "INPUT_EXAMPLE_SETTING": "ENV-VALUE-INPUT-58f2",
}


def _definition():
    """A pipeline definition shaped like the one ``constructPipeline`` writes."""
    return {
        "jobName": "splat-job",
        "stages": [{
            "type": "SPLAT",
            "inputFile": {"bucketName": "asset-bucket", "objectKey": "asset-1/capture.mp4",
                          "fileExtension": ".mp4"},
            "outputFiles": {"bucketName": "asset-bucket", "objectDir": "pipelines/out/files/"},
        }],
        "bucketRegions": {},
        "inputMetadataS3Location": "s3://aux-bucket/run/metadata.json",
        "inputConfigurationS3Location": "s3://aux-bucket/run/configuration.json",
        "externalSfnTaskToken": _DEFINITION_VALUE,
    }


class _LaunchStopped(Exception):
    """Raised by the stubbed metadata read, to stop ``main()`` once its launch summary is printed."""


@pytest.fixture
def launch(container_main, monkeypatch):
    """Run ``main()`` on a definition argument until it reads its input metadata."""
    for key, value in _ENV_VALUES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    class _StoppingManifestIo:
        @staticmethod
        def fetch_metadata(_location):
            raise _LaunchStopped()

    monkeypatch.setattr(container_main, "manifest_io", _StoppingManifestIo)

    def run(definition_argument, expected=_LaunchStopped):
        monkeypatch.setattr(sys, "argv", ["__main__.py", definition_argument])
        with pytest.raises(expected):
            container_main.main()

    return run


@pytest.mark.unit
class TestLaunchInputSummary:
    def test_main_prints_no_definition_or_environment_values(self, launch, capsys):
        launch(json.dumps(_definition()))
        out = capsys.readouterr().out

        assert _DEFINITION_VALUE not in out
        for value in _ENV_VALUES.values():
            assert value not in out

    def test_main_names_the_definition_keys_and_the_variables(self, launch, capsys):
        """The summary still says what the job was given."""
        launch(json.dumps(_definition()))
        out = capsys.readouterr().out

        assert "Command line arguments: 2\n" in out
        assert "Pipeline definition argument: inline JSON with keys [" in out
        for key in _definition():
            assert f"'{key}'" in out
        for key in _ENV_VALUES:
            assert f"  {key}\n" in out

    def test_main_prints_the_values_of_the_listed_variables(self, launch, capsys):
        launch(json.dumps(_definition()))
        out = capsys.readouterr().out

        assert "  AWS_REGION=eu-west-1\n" in out

    def test_every_other_variable_is_printed_without_a_value(self, container_main, launch, capsys):
        """Covers the variables the process already had, not only the ones this test sets."""
        launch(json.dumps(_definition()))
        out = capsys.readouterr().out

        unlisted = [key for key in os.environ
                    if key.startswith(container_main.LAUNCH_ENV_PREFIXES)
                    and key not in container_main.LAUNCH_ENV_VALUES_PRINTED]
        assert unlisted
        for key in unlisted:
            assert f"{key}=" not in out

    def test_a_definition_that_does_not_parse_is_not_printed(self, launch, capsys):
        """The parse failure reports the error and the length, not the start of the text."""
        malformed = json.dumps({"externalSfnTaskToken": _DEFINITION_VALUE, "jobName": "splat-job"})[:-1]

        launch(malformed, expected=SystemExit)
        out = capsys.readouterr().out

        assert "Failed to parse pipeline definition as JSON" in out
        assert f"Pipeline definition length: {len(malformed)} characters\n" in out
        assert _DEFINITION_VALUE not in out


@pytest.mark.unit
class TestDefinitionArgumentSummary:
    def test_a_file_path_is_named(self, container_main):
        assert (container_main.definition_argument_summary(" /tmp/definition.json ")
                == "file path /tmp/definition.json")

    def test_inline_json_is_described_by_its_top_level_keys(self, container_main):
        summary = container_main.definition_argument_summary(json.dumps(_definition()))

        assert summary == f"inline JSON with keys {list(_definition())}"
        assert _DEFINITION_VALUE not in summary
        assert "asset-1/capture.mp4" not in summary

    def test_inline_json_that_is_not_an_object_is_described_by_its_type(self, container_main):
        assert (container_main.definition_argument_summary(json.dumps([_DEFINITION_VALUE]))
                == "inline JSON list")

    def test_text_that_is_not_json_is_described_by_its_length(self, container_main):
        text = f"not json {_DEFINITION_VALUE}"

        assert (container_main.definition_argument_summary(text)
                == f"inline text of {len(text)} characters that is not valid JSON")


@pytest.mark.unit
def test_no_listed_variable_is_a_task_variable(container_main):
    """``TASK_`` variables are the container's own control keys; the summary names them only."""
    assert not [key for key in container_main.LAUNCH_ENV_VALUES_PRINTED if key.startswith("TASK_")]
