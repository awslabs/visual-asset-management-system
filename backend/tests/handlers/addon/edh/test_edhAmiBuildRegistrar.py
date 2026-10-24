# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The registrar routes the two events it receives and ignores everything else."""
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.addon.edh import edhAmiBuildRegistrar as registrar


@pytest.mark.unit
class TestRouting:
    def test_an_image_state_change_is_handed_to_the_lifecycle(self, monkeypatch):
        handle = MagicMock(return_value={"outcome": "available"})
        monkeypatch.setattr(registrar.recipes, "handle_image_state", handle)
        # The documented event: the image ARN in resources[], detail holding only the state transition.
        event = {"source": "aws.imagebuilder", "detail-type": "EC2 Image Builder Image State Change",
                 "resources": ["arn:img/1"],
                 "detail": {"previous-state": {"status": "INTEGRATING"}, "state": {"status": "AVAILABLE"}}}
        assert registrar.lambda_handler(event, None) == {"outcome": "available"}
        handle.assert_called_once_with("arn:img/1", "AVAILABLE", None)

    def test_a_state_event_without_resources_falls_back_to_a_detail_arn(self, monkeypatch):
        handle = MagicMock(return_value={"outcome": "failed"})
        monkeypatch.setattr(registrar.recipes, "handle_image_state", handle)
        event = {"source": "aws.imagebuilder", "detail-type": "EC2 Image Builder Image State Change",
                 "detail": {"arn": "arn:img/2", "state": {"status": "FAILED", "reason": "boom"}}}
        registrar.lambda_handler(event, None)
        handle.assert_called_once_with("arn:img/2", "FAILED", "boom")

    def test_a_sweep_event_runs_the_sweep(self, monkeypatch):
        sweep = MagicMock(return_value={"checked": 0})
        monkeypatch.setattr(registrar.recipes, "sweep", sweep)
        assert registrar.lambda_handler({"sweep": True}, None) == {"checked": 0}

    def test_anything_else_is_ignored(self, monkeypatch):
        monkeypatch.setattr(registrar.recipes, "handle_image_state", MagicMock())
        monkeypatch.setattr(registrar.recipes, "sweep", MagicMock())
        assert registrar.lambda_handler({"source": "aws.ec2"}, None) == {"ignored": True}
        registrar.recipes.handle_image_state.assert_not_called()
        registrar.recipes.sweep.assert_not_called()
