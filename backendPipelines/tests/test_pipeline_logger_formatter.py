#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The line each pipeline logger EMITS carries no task token, whatever shape the handler logged.

`mask_sensitive_data` is exercised directly in `test_pipeline_logger_identity.py`; this module drives
the real Powertools `Logger` with the pipeline's own `CustomFormatter` and reads the serialized line
off a handler, because that is what reaches CloudWatch. Three shapes a handler uses are covered --
the event rendered into the message by an f-string, the event passed as a structured keyword, and a
Batch `containerOverrides.environment` list -- plus the token on its own inside a sentence. A
40-character S3 key, a UUID and a hex SHA-512 are the positive controls: none of them may be touched,
or the shape scrub is eating ordinary identifiers.
"""

import base64
import hashlib
import importlib.util
import io
import json
import logging
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Kept in step with test_pipeline_logger_identity.LOGGER_COPIES, which asserts the list is complete.
LOGGER_COPIES = (
    "backendPipelines/3dRecon/splatToolbox/lambda/customLogging/logger.py",
    "backendPipelines/conversion/coordinateTransform/lambda/customLogging/logger.py",
    "backendPipelines/genAi/metadata3dLabeling/lambda/customLogging/logger.py",
    "backendPipelines/genAi/nvidia/cosmos/3/lambda/customLogging/logger.py",
    "backendPipelines/genAi/nvidia/cosmos/predict/lambda/customLogging/logger.py",
    "backendPipelines/genAi/nvidia/cosmos/reason/lambda/customLogging/logger.py",
    "backendPipelines/genAi/nvidia/cosmos/transfer/lambda/customLogging/logger.py",
    "backendPipelines/genAi/nvidia/gr00t/lambda/customLogging/logger.py",
    "backendPipelines/multi/modelOps/lambda/customLogging/logger.py",
    "backendPipelines/multi/rapidPipeline/lambda/customLogging/logger.py",
    "backendPipelines/multi/rapidPipelineEKS/lambda/customLogging/logger.py",
    "backendPipelines/preview/3dThumbnail/lambda/customLogging/logger.py",
    "backendPipelines/preview/pcPotreeViewer/lambda/customLogging/logger.py",
    "backendPipelines/simulation/isaacLabTraining/lambda/customLogging/logger.py",
)

REDACTED = "<redacted>"

# The length and charset of a real Step Functions task token: one run of URL-safe base64, several
# hundred characters long. 640 characters here.
TOKEN = base64.b64encode(hashlib.sha512(b"pipeline-logger-formatter").digest() * 8).decode()
assert len(TOKEN) >= 600 and TOKEN.endswith("=")

# Positive controls: ordinary values a handler logs, none of which may be altered.
S3_KEY = "assets/x1b2c3d4e5f6/models/pump-station.glb"
UUID = "123e4567-e89b-12d3-a456-426614174000"
SHA512_HEX = hashlib.sha512(b"asset bytes").hexdigest()
assert len(S3_KEY) == 43 and len(SHA512_HEX) == 128


def _event():
    return {
        "body": {"TaskToken": TOKEN, "assetId": "asset-1", "fileKey": S3_KEY},
        "executionId": UUID,
        "containerOverrides": {
            "environment": [
                {"name": "TASK_TOKEN", "value": TOKEN},
                {"name": "ASSET_ID", "value": "asset-1"},
            ]
        },
        "checksum": SHA512_HEX,
    }


def _emit(rel_path, service, log):
    """Load one copy, build its Logger on a StringIO handler, run `log(logger)`, return the output."""
    path = os.path.join(_REPO_ROOT, rel_path)
    name = "pipeline_logger_formatter_" + hashlib.md5(rel_path.encode()).hexdigest()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    stream = io.StringIO()
    # A distinct service per case: Powertools binds handlers to the named logger and reuses it.
    logger = module.safeLogger(service=service, logger_handler=logging.StreamHandler(stream))
    log(logger)
    return stream.getvalue()


def _assert_scrubbed(line):
    assert TOKEN not in line
    assert REDACTED in line
    # The controls survive intact, so the scrub did not reach past the token.
    assert S3_KEY in line
    assert UUID in line
    assert SHA512_HEX in line
    assert "asset-1" in line
    # The line is still one JSON record.
    json.loads(line)


@pytest.mark.parametrize("rel_path", LOGGER_COPIES, ids=[p.split("/")[-4] for p in LOGGER_COPIES])
class TestEmittedLine:
    def test_event_rendered_into_the_message_by_an_f_string(self, rel_path):
        event = _event()
        line = _emit(rel_path, "fstring-" + rel_path, lambda logger: logger.info(f"Event: {event}"))
        _assert_scrubbed(line)
        # The keys remain as evidence the fields were present; only the values are gone.
        assert "'TaskToken': '<redacted>'" in line
        assert "'name': 'TASK_TOKEN', 'value': '<redacted>'" in line

    def test_event_passed_as_a_structured_keyword(self, rel_path):
        event = _event()
        line = _emit(rel_path, "kwarg-" + rel_path, lambda logger: logger.info("Event", event=event))
        _assert_scrubbed(line)
        record = json.loads(line)
        assert record["event"]["body"]["TaskToken"] == REDACTED
        assert record["event"]["containerOverrides"]["environment"][0] == {
            "name": "TASK_TOKEN",
            "value": REDACTED,
        }
        assert record["event"]["containerOverrides"]["environment"][1] == {
            "name": "ASSET_ID",
            "value": "asset-1",
        }

    def test_event_logged_as_the_message_object(self, rel_path):
        event = _event()
        line = _emit(rel_path, "object-" + rel_path, lambda logger: logger.info(event))
        _assert_scrubbed(line)
        record = json.loads(line)
        assert record["message"]["body"]["TaskToken"] == REDACTED
        assert record["message"]["containerOverrides"]["environment"][0]["value"] == REDACTED

    def test_token_on_its_own_inside_a_sentence(self, rel_path):
        line = _emit(
            rel_path,
            "sentence-" + rel_path,
            lambda logger: logger.info(f"Task token {TOKEN} for {UUID} and {S3_KEY} {SHA512_HEX} asset-1"),
        )
        _assert_scrubbed(line)
        assert json.loads(line)["message"] == (
            f"Task token {REDACTED} for {UUID} and {S3_KEY} {SHA512_HEX} asset-1"
        )

    def test_json_rendered_event_in_the_message(self, rel_path):
        event = _event()
        line = _emit(
            rel_path, "json-" + rel_path, lambda logger: logger.info("Event: " + json.dumps(event))
        )
        _assert_scrubbed(line)
        assert '\\"TaskToken\\": \\"<redacted>\\"' in line

    def test_controls_alone_are_untouched(self, rel_path):
        # No token anywhere: the line must come out exactly as logged.
        message = f"key={S3_KEY} id={UUID} sha={SHA512_HEX}"
        line = _emit(rel_path, "controls-" + rel_path, lambda logger: logger.info(message))
        assert json.loads(line)["message"] == message
        assert REDACTED not in line
