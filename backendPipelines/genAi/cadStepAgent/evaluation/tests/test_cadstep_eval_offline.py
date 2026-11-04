# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The offline runner's settings that need no agent run: the Region its model and container package use.
No network."""

import os
import sys

EVALUATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EVALUATION_DIR not in sys.path:
    sys.path.insert(0, EVALUATION_DIR)
import run_offline as ro  # noqa: E402


def test_an_exported_region_outranks_the_corpus_region():
    environ = {"AWS_REGION": "eu-west-1"}
    assert ro.bedrock_region({"region": "us-east-1"}, environ) == "eu-west-1"
    assert environ == {"AWS_REGION": "eu-west-1"}


def test_the_corpus_region_and_then_us_east_1_apply_when_none_is_exported():
    environ = {}
    assert ro.bedrock_region(ro.load_corpus(os.path.join(EVALUATION_DIR, "corpus.yaml"))["globals"], environ) == \
        "us-east-1"
    assert ro.bedrock_region({"region": "us-west-2"}, {}) == "us-west-2"
    assert ro.bedrock_region({}, {}) == "us-east-1"
    # The resolved Region is exported, so the container package's clients use the same one.
    assert environ == {"AWS_REGION": "us-east-1"}
