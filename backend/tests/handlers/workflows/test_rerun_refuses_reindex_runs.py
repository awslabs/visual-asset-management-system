# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A vector-search reindex run is a system run, so it is never re-run (#316 with #1135).

Its row stores the system trigger value System-Reindex, which the re-run guard refuses like any system
trigger value (common.workflows.systemPipelines.is_system_trigger), the rows earlier releases wrote under
that value included. A reindex run is launched again by running the reindex again. The re-run harness is
the system pipeline hooks suite's."""

import json

import pytest

from backend.backend.common.workflows import systemPipelines as sp
from tests.handlers.workflows import test_system_pipeline_hooks as hooks


@pytest.mark.unit
class TestAReindexRunIsNotReRun:
    def test_a_stored_reindex_run_is_refused_400_and_launches_nothing(self):
        response, invoke = hooks.TestRerunGuard()._rerun(hooks._row(trigger="System-Reindex"))
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == sp.SYSTEM_RERUN_REFUSED_MESSAGE
        invoke.assert_not_called()

    def test_the_refusal_still_follows_the_visibility_check(self):
        response, invoke = hooks.TestRerunGuard()._rerun(hooks._row(trigger="System-Reindex"), visible=False)
        assert response["statusCode"] == 403
        invoke.assert_not_called()
