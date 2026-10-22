# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A workflow-record fixture whose recorded deployment matches its pipelines.

The execute handler starts a run only on a published state machine version whose stored per-pipeline
fingerprints equal the pipelines' current ones, and redeploys (or refuses) otherwise. A test that is
not about that check needs its workflow fixture to look like a workflow deployed from the pipelines
it serves, which is what `with_current_deployment` produces. The fingerprints come from the real
`workflowAsl` functions, so a fixture cannot agree with the handler by accident.
"""

from backend.backend.common.workflows import workflowAsl

TEST_VERSION_SUFFIX = ":1"


def with_current_deployment(workflow, pipelines, version_arn=None):
    """A copy of `workflow` carrying a published version ARN and the fingerprints of `pipelines`."""
    stamped = dict(workflow)
    base_arn = stamped.get("workflow_arn") or "arn:aws:states:us-east-1:1:stateMachine:vams-test"
    stamped["workflowVersionArn"] = version_arn or base_arn + TEST_VERSION_SUFFIX
    stamped["pipelineFingerprints"] = workflowAsl.workflow_pipeline_fingerprints(pipelines)
    return stamped
