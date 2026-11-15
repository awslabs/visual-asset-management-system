#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""multi/modelOps ``pipelineEnd`` deletes the run's staged input copy on success and on failure.

``constructPipeline`` copies a non-current input version into the auxiliary bucket for the ModelOps
handler and names the copy in ``stagedInputS3Uri``; the state machine keeps that output as the state
on both routes into ``PipelineEndTask``. The copy is the customer's file, so ``pipelineEnd`` deletes
every version of it (the bucket is versioned, so a plain delete would leave the data as a noncurrent
version), only under the staged-inputs prefix, and never lets a cleanup failure change how the run
is reported. Reuses the loader and events of ``test_pipeline_end_token_routes`` (same directory)."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import test_pipeline_end_token_routes as routes  # noqa: E402

STAGED_KEY = "pipelines/modelops.staged-inputs/modelOps/E1/xidM/sub/model.glb"
STAGED_URI = f"s3://aux-bkt/{STAGED_KEY}"


def _s3(versions=None, markers=None, errors=None):
    s3 = MagicMock()
    page = {"Versions": versions if versions is not None else [{"Key": STAGED_KEY, "VersionId": "c1"}],
            "DeleteMarkers": markers or []}
    s3.get_paginator.return_value.paginate.return_value = [page]
    s3.delete_objects.return_value = {"Errors": errors} if errors else {}
    return s3


def _run(event, s3, sfn=None):
    mod = routes._load()
    sfn = sfn or MagicMock()
    with patch.object(mod, "s3", s3), patch.object(mod, "sfn", sfn):
        mod.lambda_handler(event, MagicMock())
    return sfn


def _success_event():
    """The success route: constructPipeline's output with the ECS result at its ResultPath."""
    event = routes._construct_pipeline_output()
    event["stagedInputS3Uri"] = STAGED_URI
    event["ModelOpsTaskResult"] = {"LastStatus": "STOPPED"}
    return event


def _failure_event():
    event = routes._caught_error_event()
    event["stagedInputS3Uri"] = STAGED_URI
    return event


@pytest.mark.unit
class TestTheStagedCopyIsDeleted:
    @pytest.mark.parametrize("event", [_success_event(), _failure_event()], ids=["success", "failure"])
    def test_every_version_is_deleted_on_both_routes(self, event):
        s3 = _s3(versions=[{"Key": STAGED_KEY, "VersionId": "c1"}, {"Key": STAGED_KEY, "VersionId": "c0"}],
                 markers=[{"Key": STAGED_KEY, "VersionId": "m1"}])
        _run(event, s3)
        s3.get_paginator.assert_called_once_with("list_object_versions")
        s3.get_paginator.return_value.paginate.assert_called_once_with(Bucket="aux-bkt", Prefix=STAGED_KEY)
        s3.delete_objects.assert_called_once_with(Bucket="aux-bkt", Delete={
            "Objects": [{"Key": STAGED_KEY, "VersionId": v} for v in ("c1", "c0", "m1")], "Quiet": True})

    def test_the_run_is_reported_before_the_cleanup(self):
        order = []
        s3 = _s3()
        s3.get_paginator.side_effect = lambda *_: order.append("cleanup") or MagicMock(
            paginate=MagicMock(return_value=[]))
        sfn = MagicMock()
        sfn.send_task_success.side_effect = lambda **_: order.append("report")
        _run(_success_event(), s3, sfn)
        assert order == ["report", "cleanup"]

    def test_a_key_that_only_shares_the_prefix_is_not_deleted(self):
        s3 = _s3(versions=[{"Key": STAGED_KEY + ".bak", "VersionId": "x"}])
        _run(_success_event(), s3)
        s3.delete_objects.assert_not_called()


@pytest.mark.unit
class TestNothingElseIsTouched:
    @pytest.mark.parametrize("uri", [
        "",
        "s3://abkt/xidM/sub/model.glb",
        "s3://aux-bkt/pipelines/modelOps/E1/output/model.glb",
        "s3://aux-bkt/pipelines/modelops.staged-inputs/",
        "pipelines/modelops.staged-inputs/a.glb",
    ])
    def test_no_delete_outside_the_staged_prefix(self, uri):
        event = _success_event()
        event["stagedInputS3Uri"] = uri
        s3 = _s3()
        _run(event, s3)
        s3.get_paginator.assert_not_called()
        s3.delete_objects.assert_not_called()

    def test_an_event_from_an_older_state_machine_deletes_nothing(self):
        # A state machine without the ECS ResultPath replaces the state with the task description.
        s3 = _s3()
        sfn = _run(routes._completed_ecs_task_event(), s3)
        s3.get_paginator.assert_not_called()
        assert sfn.send_task_success.call_args.kwargs["taskToken"] == routes.TASK_TOKEN


@pytest.mark.unit
class TestACleanupFailureDoesNotChangeTheReport:
    def test_a_list_failure_is_logged_not_raised(self):
        s3 = _s3()
        s3.get_paginator.return_value.paginate.side_effect = Exception("AccessDenied")
        sfn = _run(_success_event(), s3)
        assert sfn.send_task_success.call_args.kwargs["taskToken"] == routes.TASK_TOKEN

    def test_refused_version_deletes_are_logged_not_raised(self):
        s3 = _s3(errors=[{"Key": STAGED_KEY, "Code": "AccessDenied"}])
        sfn = _run(_failure_event(), s3)
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == routes.TASK_TOKEN

    def test_the_copy_is_deleted_even_when_the_report_raises(self):
        s3 = _s3()
        sfn = MagicMock()
        sfn.send_task_success.side_effect = Exception("TaskTimedOut")
        with pytest.raises(Exception, match="TaskTimedOut"):
            _run(_success_event(), s3, sfn)
        s3.delete_objects.assert_called_once()


@pytest.mark.unit
def test_the_prefix_matches_construct_pipeline():
    source = open(os.path.join(os.path.dirname(_TESTS_DIR), "constructPipeline.py"), encoding="utf-8").read()
    assert f'STAGED_INPUT_PREFIX = "{routes._load().STAGED_INPUT_PREFIX}"' in source
