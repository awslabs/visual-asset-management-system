# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The per-step input-configuration objects an execution launch writes must be parseable JSON.

Each step's config key is declared `application/json` and reaches the step as
`inputConfigurationS3Location` (threaded by the ASL for step 1..n and re-published by the interim
lambda), so an SQS / EventBridge / DeadlineCloud worker running on the customer's own compute calls
`json.loads` on that object. A step with no configuration body — no template and no override, which
resolves `renderedConfig` to "" — must therefore still receive a JSON body rather than a zero-byte
object declared as JSON, which raises `JSONDecodeError: Expecting value: line 1 column 1`
(S21-CUSTOMER-001).

The bodies are asserted through `json.loads` rather than by string comparison: parseability is the
contract, and a string comparison would pass on a body no reader can use. A step WITH a body is
asserted byte-for-byte, because a fix that rewrote every body would corrupt real configurations.
"""

import json
import os
import sys
import types

import pytest
from unittest.mock import patch

# executeWorkflow loads these at import (mirrors test_executeWorkflow.py).
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates")
os.environ.setdefault("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")

# handlers.workflows package __init__ imports get_task_builder at import time; the shared mock package
# does not provide it, so register a lightweight stub before importing the handler.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.common.workflows import executionRecords as er
from backend.backend.handlers.workflows import executeWorkflow as ewv2

MOD = "backend.backend.handlers.workflows.executeWorkflow"

EXEC_ID = "execCFG1"
RUN_BUCKET = "run-bucket"

# A json-format body as a template renders it: exact indentation, a non-ASCII value, trailing newline.
JSON_BODY = '{\n  "quality": "high",\n  "note": "café"\n}\n'
# A body whose declared configFormat is not json (TEMPLATE_CONFIG_FORMATS also allows yaml / openjd /
# xml / raw). Caller content either way, so it travels unchanged.
YAML_BODY = "quality: high\nframes: 30\n"


def _envelope():
    """A grouped metadata envelope carrying one asset and one database."""
    group = er.build_metadata_asset_group(
        "db1", "a1", asset_data={"assetName": "A"},
        files=[er.build_metadata_file_record("/", metadata={"ASSET_KEY": "asset-value"})])
    return er.build_grouped_metadata_envelope(
        [group], databases=[er.build_metadata_database_group("db1", metadata={"DB_KEY": "db-value"})])


def _manifest():
    return {"inputFiles": [], "outputTarget": {"fileBaseExecutionPathExtension": "/"}}


def _write(config_bodies, pipelines_count=None, gates=None):
    """Drive _write_execution_input_files with S3 mocked.

    Returns (locations, puts) where puts maps each written key to its put_object kwargs, so both the
    Body bytes and the declared ContentType can be asserted.
    """
    count = len(config_bodies) if pipelines_count is None else pipelines_count
    with patch(f"{MOD}.s3c") as s3:
        locations = ewv2._write_execution_input_files(
            EXEC_ID, RUN_BUCKET, count, _envelope(), _manifest(), list(config_bodies),
            step_metadata_gates=gates)
        puts = {c.kwargs["Key"]: c.kwargs for c in s3.put_object.call_args_list}
    return locations, puts


def _config_body(puts, step_index):
    return puts[er.pipeline_input_config_key(EXEC_ID, step_index)]["Body"]


@pytest.mark.unit
class TestStepWithNoConfigBody:
    """The customer-reported failure: a step with no configuration was handed a 0-byte object."""

    def test_writes_a_parseable_json_object(self):
        # json.loads is the assertion, not a string comparison: an unparseable body is the defect,
        # whatever bytes it happens to contain.
        _, puts = _write([""])
        assert json.loads(_config_body(puts, 1)) == {}

    def test_a_zero_byte_body_would_not_parse(self):
        # Positive control for the assertion above — this is exactly what the worker saw, and what
        # json.loads does with it. Without this, a test asserting "the body parses" could be passing
        # for a reason unrelated to the body.
        with pytest.raises(ValueError):
            json.loads(b"".decode("utf-8"))

    def test_body_is_non_empty_bytes(self):
        _, puts = _write([""])
        assert _config_body(puts, 1) != b""

    def test_content_type_still_declares_json(self):
        # The body has to match the declaration; the declaration is what workers rely on.
        _, puts = _write([""])
        assert puts[er.pipeline_input_config_key(EXEC_ID, 1)]["ContentType"] == "application/json"

    def test_step_index_past_the_supplied_bodies_also_parses(self):
        # Defensive branch: more steps than resolved bodies. Its object is published like any other,
        # so it must be parseable too.
        _, puts = _write([JSON_BODY], pipelines_count=3)
        assert json.loads(_config_body(puts, 2)) == {}
        assert json.loads(_config_body(puts, 3)) == {}

    def test_every_step_of_a_config_less_run_parses(self):
        _, puts = _write(["", "", ""])
        for step in (1, 2, 3):
            assert json.loads(_config_body(puts, step)) == {}


@pytest.mark.unit
class TestStepWithAConfigBody:
    """Positive control — a real configuration must reach the step byte-for-byte."""

    def test_json_body_is_byte_identical(self):
        _, puts = _write([JSON_BODY])
        assert _config_body(puts, 1) == JSON_BODY.encode("utf-8")

    def test_non_json_format_body_is_byte_identical(self):
        # A yaml / openjd / xml / raw body is caller content and is not rewritten either.
        _, puts = _write([YAML_BODY])
        assert _config_body(puts, 1) == YAML_BODY.encode("utf-8")

    def test_a_body_that_is_already_an_empty_object_is_untouched(self):
        _, puts = _write(["{}"])
        assert _config_body(puts, 1) == b"{}"

    def test_only_the_body_less_step_is_substituted(self):
        _, puts = _write([JSON_BODY, ""])
        assert _config_body(puts, 1) == JSON_BODY.encode("utf-8")
        assert json.loads(_config_body(puts, 2)) == {}


@pytest.mark.unit
class TestConfigKeysAlignment:
    """configKeys[i] is step i+1 — the alignment the published task body depends on.

    Omitting the object for a body-less step (the alternative fix) would break this, so it is pinned
    directly rather than inferred from the write count.
    """

    def test_one_entry_per_step_in_step_order(self):
        locations, _ = _write([JSON_BODY, "", YAML_BODY])
        assert locations["configKeys"] == [
            er.pipeline_input_config_key(EXEC_ID, i) for i in (1, 2, 3)]

    def test_each_published_key_holds_that_steps_body(self):
        bodies = [JSON_BODY, "", YAML_BODY]
        locations, puts = _write(bodies)
        expected = [JSON_BODY.encode("utf-8"), b"{}", YAML_BODY.encode("utf-8")]
        for key, want in zip(locations["configKeys"], expected):
            assert puts[key]["Body"] == want

    @pytest.mark.parametrize("count", [1, 2, 4])
    def test_entry_count_matches_the_step_count(self, count):
        locations, _ = _write([""] * count)
        assert len(locations["configKeys"]) == count

    def test_no_pipelines_publishes_nothing(self):
        locations, puts = _write([], pipelines_count=0)
        assert locations["configKeys"] == []
        assert puts == {}


@pytest.mark.unit
class TestEveryJsonDeclaredObjectParses:
    """The function's other bodies, audited for the same shape.

    The metadata envelope, a step's narrowed metadata file and pipeline 1's manifest are all written
    with ContentType application/json. Asserting across every put_object call — rather than only the
    config keys — is what would catch a future body written from raw text instead of json.dumps.
    """

    def test_all_objects_written_for_a_config_less_run_are_json(self):
        _, puts = _write(["", ""], gates={1: {"databaseMetadata": False}, 2: {}})
        # Shared envelope, both configs, step 1's narrowed metadata, pipeline 1's manifest.
        assert er.execution_input_metadata_key(EXEC_ID) in puts
        assert er.pipeline_input_metadata_key(EXEC_ID, 1) in puts
        assert er.pipeline_input_manifest_key(EXEC_ID, 1) in puts
        assert len(puts) == 5
        for key, kwargs in puts.items():
            assert kwargs["ContentType"] == "application/json", key
            try:
                json.loads(kwargs["Body"].decode("utf-8"))
            except ValueError as exc:
                pytest.fail(f"{key} is declared application/json but does not parse: {exc}")
