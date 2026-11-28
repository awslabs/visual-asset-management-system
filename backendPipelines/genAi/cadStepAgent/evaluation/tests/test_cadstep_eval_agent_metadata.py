# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Both runners turn the agent metadata on: the honesty scorer reads the cadAgent* file metadata, and the
templates' WRITE_AGENT_METADATA tag defaults to off. No network."""

import os
import sys
import types

EVALUATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EVALUATION_DIR not in sys.path:
    sys.path.insert(0, EVALUATION_DIR)
import run_live  # noqa: E402
import run_offline as ro  # noqa: E402

NAMING = types.SimpleNamespace(resolve_output_filename=lambda *args: "plate-20261027-120000.step")


def test_the_offline_definition_writes_the_agent_metadata():
    case = {"id": "G1", "mode": "generate", "prompt": "a plate"}
    assert ro.build_definition(case, {}, "", NAMING, "model-x")["agent"]["writeAgentMetadata"] is True


def test_the_live_runner_sets_the_agent_metadata_tag_on_both_workflows():
    for case in ({"id": "G1", "mode": "generate", "prompt": "a plate"},
                 {"id": "M1", "mode": "modify", "prompt": "add a hole"}):
        tags = {tag["key"]: tag["value"] for tag in run_live.template_tags(case, {})}
        assert tags["WRITE_AGENT_METADATA"] is True, case["id"]
