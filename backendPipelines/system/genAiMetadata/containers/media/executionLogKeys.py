#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The run's execution ids on every log line a pipeline Lambda writes.

Each Lambda of this pipeline writes to its own log group, which every run shares. openPipeline registers
those groups as log sources of the pipeline execution, and VAMS reads a registered shared group for the
lines that contain both the workflow execution id and the pipeline execution id. A line that names
neither id (a branch warning, a stack trace) reaches CloudWatch but not the run's log in VAMS unless the
ids are among the logger's keys.

The logger keeps its keys in a reused execution environment, so each invocation removes the previous
run's ids before it binds its own. A direct invocation without the ids logs without them.

The media and Blender images carry byte-identical copies of this module.
"""

WORKFLOW_EXECUTION_ID_KEY = "workflowExecutionId"
PIPELINE_EXECUTION_ID_KEY = "pipelineExecutionId"
EXECUTION_ID_KEYS = (WORKFLOW_EXECUTION_ID_KEY, PIPELINE_EXECUTION_ID_KEY)
# The orchestration event prefix is <source>.execution.<executionId>.pipeline.<pipelineExecutionId>.
_PIPELINE_MARKER = ".pipeline."


def _non_empty_string(value):
    return value if isinstance(value, str) and value else ""


def pipeline_execution_id_from_prefix(prefix):
    """The pipeline execution id the orchestration event prefix ends in, or "" when it names none."""
    prefix = _non_empty_string(prefix)
    index = prefix.rfind(_PIPELINE_MARKER)
    if index < 0:
        return ""
    return prefix[index + len(_PIPELINE_MARKER):]


def execution_ids(state):
    """The non-empty workflow and pipeline execution ids `state` carries. The states before
    constructPipeline carry the pipeline execution id only inside orchestrationEventPrefix."""
    if not isinstance(state, dict):
        return {}
    ids = {}
    workflow_execution_id = _non_empty_string(state.get(WORKFLOW_EXECUTION_ID_KEY))
    if workflow_execution_id:
        ids[WORKFLOW_EXECUTION_ID_KEY] = workflow_execution_id
    pipeline_execution_id = (_non_empty_string(state.get(PIPELINE_EXECUTION_ID_KEY))
                             or pipeline_execution_id_from_prefix(state.get("orchestrationEventPrefix")))
    if pipeline_execution_id:
        ids[PIPELINE_EXECUTION_ID_KEY] = pipeline_execution_id
    return ids


def bind_execution_ids(logger, state):
    """Replace the logger's execution-id keys with the ones `state` carries, and return them."""
    logger.remove_keys(list(EXECUTION_ID_KEYS))
    ids = execution_ids(state)
    if ids:
        logger.append_keys(**ids)
    return ids
