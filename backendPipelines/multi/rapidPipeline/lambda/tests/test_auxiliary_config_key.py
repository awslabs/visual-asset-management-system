#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The rp_config object must be written under the run's auxiliary working prefix.

`inputOutputS3AssetAuxiliaryFilesPath` resolves to `s3://{auxBucket}/pipelines/{pipelineName}/
{executionId}/` — a per-execution prefix. constructPipeline parses that URI into a bucket and a
bucket-relative key, and the config object belongs under the key half: it is a temporary working
object, and the prefix is where the cleanup and uninstall paths look for one. Written at the bucket
root instead, it accumulates without bound in a location nothing lists and nothing deletes.

Two properties are separable and both are asserted, because the per-execution namespacing already
in the file name makes the placement look settled when it is not: the key must carry the prefix AND
keep the jobName. The container reads the config from a URI built out of the same variable, so the
download target is asserted against the recorded key rather than a second literal.

The S3 double is a recording fake rather than a MagicMock. put_object is a WRITER — a MagicMock
accepts any call and reports success, so an assertion made against the mock's mere existence would
hold even if nothing were written. Every assertion here reads what the fake recorded.
"""

import os
import sys
import json
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(_k, _v)


def _module():
    if "constructPipeline" in sys.modules:
        return importlib.reload(sys.modules["constructPipeline"])
    return importlib.import_module("constructPipeline")


class _Body:
    def __init__(self, raw):
        self._raw = raw

    def read(self):
        return self._raw


class RecordingS3:
    """Serves the input configuration and records every put_object call."""

    def __init__(self, config):
        self._config = config
        self.puts = []

    def get_object(self, **kwargs):
        return {"Body": _Body(json.dumps(self._config).encode("utf-8"))}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {}


AUX_BUCKET = "auxbkt"
AUX_PREFIX = "pipelines/RapidPipeline/E1/"
JOB_NAME = "PipelineJob_1234567890123_ab12"


def _event(**overrides):
    event = {
        "jobName": JOB_NAME,
        "inputS3AssetFilePath": "s3://abkt/xidM/parts/housing/model.obj",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/RapidPipeline/MJOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://{AUX_BUCKET}/{AUX_PREFIX}",
        "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/p1/config.json",
        "outputFileType": ".glb",
    }
    event.update(overrides)
    return event


@pytest.mark.unit
class TestAuxiliaryObjectKey:
    """The join, driven directly: the property is a composition of two bucket-relative parts."""

    @pytest.mark.parametrize("prefix, expected", [
        ("pipelines/RapidPipeline/E1/", "pipelines/RapidPipeline/E1/rp_config.json"),
        # A prefix without the trailing slash still gets exactly one separator.
        ("pipelines/RapidPipeline/E1", "pipelines/RapidPipeline/E1/rp_config.json"),
        # A leading slash would otherwise make the key a distinct object under an empty segment.
        ("/pipelines/RapidPipeline/E1/", "pipelines/RapidPipeline/E1/rp_config.json"),
        # A direct invocation naming the bucket root has no prefix to apply.
        ("", "rp_config.json"),
        (None, "rp_config.json"),
    ])
    def test_exactly_one_separator_joins_the_parts(self, prefix, expected):
        assert _module().auxiliary_object_key(prefix, "rp_config.json") == expected

    def test_no_key_gains_an_empty_path_segment(self):
        """Positive control for the separator handling. An implementation that concatenated the two
        parts unconditionally would satisfy the trailing-slash case above by producing '...E1//
        rp_config.json', which is a DIFFERENT object from the one under the prefix."""
        key = _module().auxiliary_object_key("pipelines/RapidPipeline/E1/", "rp_config.json")
        assert "//" not in key


@pytest.mark.unit
class TestRpConfigIsWrittenUnderTheAuxiliaryPrefix:
    def test_the_written_key_carries_the_prefix_and_the_job_name(self):
        mod = _module()
        config = {"settings": {"quality": "high"}}
        s3 = RecordingS3(config)
        with patch.object(mod, "s3", s3):
            mod.lambda_handler(_event(), MagicMock())

        assert len(s3.puts) == 1, "the config was not written exactly once"
        put = s3.puts[0]
        assert put["Bucket"] == AUX_BUCKET
        assert put["Key"] == f"{AUX_PREFIX}rp_config_{JOB_NAME}.json", (
            "the config object must sit under the run's auxiliary working prefix, not at the "
            "auxiliary bucket root where nothing lists or deletes it"
        )
        assert "//" not in put["Key"]
        assert json.loads(put["Body"]) == config

    def test_the_container_downloads_the_key_that_was_written(self):
        """The put and the container's `aws s3 cp` are built from one variable; asserting the
        command against the RECORDED key is what proves they cannot drift apart."""
        mod = _module()
        s3 = RecordingS3({"settings": {"quality": "high"}})
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(_event(), MagicMock())

        assert len(s3.puts) == 1
        written_uri = f"s3://{AUX_BUCKET}/{s3.puts[0]['Key']}"
        command = out["commands"][2]
        assert f"aws s3 cp {written_uri} rp_config.json" in command
        assert "--read_config rp_config.json" in command

    def test_a_bucket_root_auxiliary_path_still_writes_the_named_object(self):
        """A direct invocation may name the auxiliary bucket root, leaving no prefix to apply. The
        jobName namespacing is then the only thing keeping concurrent runs apart, so it must remain
        in the file name rather than being folded into a prefix that does not exist."""
        mod = _module()
        s3 = RecordingS3({"settings": {"quality": "high"}})
        with patch.object(mod, "s3", s3):
            mod.lambda_handler(_event(inputOutputS3AssetAuxiliaryFilesPath=f"s3://{AUX_BUCKET}/"),
                               MagicMock())

        assert len(s3.puts) == 1
        assert s3.puts[0]["Key"] == f"rp_config_{JOB_NAME}.json"
