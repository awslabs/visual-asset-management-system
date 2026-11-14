#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Render3D Lambda puts the run's execution ids on every line it logs.

VAMS reads the function's log group, which every run shares, for the lines that carry both the workflow
execution id and the pipeline execution id. The format handlers log through the root logger and the
libraries they call log through their own; a filter on the root logger's handlers reaches both.
"""

import importlib.util
import io
import logging
import os

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HANDLER_PATH = os.path.join(os.path.dirname(TESTS_DIR), "lambda_handler.py")


@pytest.fixture(scope="module")
def handler():
    spec = importlib.util.spec_from_file_location("render3d_execution_ids_under_test", HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def root_streams(handler):
    """Two root handlers writing to separate buffers, removed (with the filter) after the test."""
    root = logging.getLogger()
    streams = [io.StringIO(), io.StringIO()]
    added = [logging.StreamHandler(stream) for stream in streams]
    previous_level = root.level
    root.setLevel(logging.INFO)
    for item in added:
        root.addHandler(item)
    yield streams
    for item in added:
        root.removeHandler(item)
    for item in root.handlers:
        item.removeFilter(handler.EXECUTION_ID_FILTER)
    handler.EXECUTION_ID_FILTER.suffix = ""
    root.setLevel(previous_level)


@pytest.mark.unit
class TestExecutionIds:
    def test_both_ids_from_the_state(self, handler):
        assert handler.execution_ids({"workflowExecutionId": "E1", "pipelineExecutionId": "P1"}) == {
            "workflowExecutionId": "E1", "pipelineExecutionId": "P1"}

    def test_the_pipeline_execution_id_falls_back_to_the_event_prefix(self, handler):
        state = {"workflowExecutionId": "E1", "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"}
        assert handler.execution_ids(state) == {"workflowExecutionId": "E1", "pipelineExecutionId": "P1"}

    @pytest.mark.parametrize("state", [None, "E1", {}, {"workflowExecutionId": ""}, {"pipelineExecutionId": 3}])
    def test_no_ids_when_the_state_carries_none(self, handler, state):
        assert handler.execution_ids(state) == {}


@pytest.mark.unit
class TestTheFilter:
    def test_root_and_library_lines_carry_both_ids_once(self, handler, root_streams):
        handler.bind_execution_ids({"workflowExecutionId": "E1", "pipelineExecutionId": "P1"})
        logging.getLogger().warning("render3d: %d frames", 4)
        logging.getLogger("trimesh").warning("a face could not be triangulated")
        for stream in root_streams:
            lines = stream.getvalue().splitlines()
            assert lines == ["render3d: 4 frames [workflowExecutionId=E1 pipelineExecutionId=P1]",
                             "a face could not be triangulated [workflowExecutionId=E1 pipelineExecutionId=P1]"]

    def test_a_reused_environment_does_not_carry_the_previous_run(self, handler, root_streams):
        handler.bind_execution_ids({"workflowExecutionId": "E1", "pipelineExecutionId": "P1"})
        logging.getLogger().warning("first run")
        handler.bind_execution_ids({"workflowExecutionId": "E2"})
        logging.getLogger().warning("second run")
        handler.bind_execution_ids(None)
        logging.getLogger().warning("direct invocation")
        assert root_streams[0].getvalue().splitlines() == [
            "first run [workflowExecutionId=E1 pipelineExecutionId=P1]",
            "second run [workflowExecutionId=E2]",
            "direct invocation",
        ]

    def test_the_filter_is_installed_once_per_handler(self, handler, root_streams):
        for _ in range(3):
            handler.bind_execution_ids({"workflowExecutionId": "E1"})
        for item in logging.getLogger().handlers:
            assert item.filters.count(handler.EXECUTION_ID_FILTER) == 1

    def test_a_malformed_record_is_left_to_the_formatter(self, handler):
        handler.EXECUTION_ID_FILTER.suffix = "[workflowExecutionId=E1]"
        try:
            record = logging.LogRecord("x", logging.INFO, __file__, 1, "%d frames", ("four",), None)
            assert handler.EXECUTION_ID_FILTER.filter(record) is True
            assert record.msg == "%d frames"
        finally:
            handler.EXECUTION_ID_FILTER.suffix = ""
