#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every pipeline's `customLogging/logger.py` is one file, and it redacts the task tokens.

Each pipeline's Lambda code asset vendors its own copy of the logger, because the Lambda layer's copy
is only a fallback and nothing shared is importable from a per-pipeline asset. A task token is a
bearer credential: whoever holds it can complete or fail the parent workflow's task. It travels
through these handlers under several spellings -- the workflow body's `TaskToken`, the nested-invoke
payload's `sfnExternalTaskToken`, the state machine input's `externalSfnTaskToken`, and the Batch
container environment's `TASK_TOKEN` -- and every handler logs the event it received, so the masker
has to know every spelling and has to walk lists, where a `containerOverrides.environment` entry
carries the token as `{"name": ..., "value": ...}` pairs inside a list.

Byte identity across the copies is asserted first, so a fix applied to one pipeline's logger and not
propagated fails here rather than surfacing as a token in one pipeline's CloudWatch stream.
"""

import hashlib
import importlib.util
import json
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Named explicitly rather than globbed: a glob that stops matching reads as "all copies agree".
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

TOKEN_SPELLINGS = (
    "externalSfnTaskToken",
    "sfnExternalTaskToken",
    "taskToken",
    "TaskToken",
    "TASK_TOKEN",
)

_TOKEN = "AQCEAAAAKgAAAAMAAAAAAAAAAexampleTaskTokenValue"
REDACTED = "<redacted>"


def _load(rel_path):
    """Load one logger copy under its own module name, so the copies never shadow each other."""
    path = os.path.join(_REPO_ROOT, rel_path)
    name = "pipeline_logger_" + hashlib.md5(rel_path.encode()).hexdigest()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_every_copy_is_the_same_file():
    """Fails when a fix lands in one pipeline's logger and is not propagated to the rest."""
    digests = {}
    for rel_path in LOGGER_COPIES:
        with open(os.path.join(_REPO_ROOT, rel_path), "rb") as handle:
            digests[rel_path] = hashlib.md5(handle.read()).hexdigest()
    distinct = sorted(set(digests.values()))
    assert len(distinct) == 1, (
        "logger.py differs across pipelines:\n"
        + "\n".join(f"  {digest}  {path}" for path, digest in sorted(digests.items()))
    )


def test_every_lambda_directory_carries_a_copy():
    """A pipeline lambda directory with no customLogging/logger.py fails at import in Lambda."""
    listed = set(LOGGER_COPIES)
    found = set()
    for root, dirs, files in os.walk(os.path.join(_REPO_ROOT, "backendPipelines")):
        if os.path.basename(root) == "customLogging" and "logger.py" in files:
            rel = os.path.relpath(os.path.join(root, "logger.py"), _REPO_ROOT).replace(os.sep, "/")
            if "/lambda/" in rel:
                found.add(rel)
    assert found == listed, (
        f"unlisted copies: {sorted(found - listed)}; listed but missing: {sorted(listed - found)}"
    )


@pytest.mark.parametrize("rel_path", LOGGER_COPIES, ids=[p.split("/")[-4] for p in LOGGER_COPIES])
class TestRedaction:
    def test_every_token_spelling_is_redacted_in_a_dict(self, rel_path):
        logger = _load(rel_path)
        event = {spelling: _TOKEN for spelling in TOKEN_SPELLINGS}
        event["assetId"] = "asset-1"
        out = logger.mask_sensitive_data(event)
        for spelling in TOKEN_SPELLINGS:
            assert out[spelling] == REDACTED, f"{spelling} reached the log"
        assert out["assetId"] == "asset-1"
        assert _TOKEN not in json.dumps(out)

    def test_every_token_spelling_is_redacted_inside_a_list(self, rel_path):
        # The shape a Batch containerOverrides.environment list takes, plus a list of nested dicts.
        logger = _load(rel_path)
        event = {
            "environment": [{spelling: _TOKEN} for spelling in TOKEN_SPELLINGS],
            "steps": [[{"TaskToken": _TOKEN}]],
        }
        out = logger.mask_sensitive_data(event)
        for entry, spelling in zip(out["environment"], TOKEN_SPELLINGS):
            assert entry[spelling] == REDACTED, f"{spelling} in a list reached the log"
        assert out["steps"][0][0]["TaskToken"] == REDACTED
        assert _TOKEN not in json.dumps(out)

    def test_nested_dict_token_is_redacted(self, rel_path):
        logger = _load(rel_path)
        out = logger.mask_sensitive_data({"body": {"TaskToken": _TOKEN, "databaseId": "db"}})
        assert out["body"]["TaskToken"] == REDACTED
        assert out["body"]["databaseId"] == "db"

    def test_authorization_is_still_redacted(self, rel_path):
        logger = _load(rel_path)
        out = logger.mask_sensitive_data({"headers": {"authorization": "Bearer abc.def.ghi"}})
        assert out["headers"]["authorization"] == REDACTED

    def test_top_level_list_and_scalars_pass_through(self, rel_path):
        logger = _load(rel_path)
        assert logger.mask_sensitive_data([{"TASK_TOKEN": _TOKEN}]) == [{"TASK_TOKEN": REDACTED}]
        assert logger.mask_sensitive_data("plain message") == "plain message"
        assert logger.mask_sensitive_data(None) is None
