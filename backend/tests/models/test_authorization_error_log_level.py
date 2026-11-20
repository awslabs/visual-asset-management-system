# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``authorization_error()`` logs by status class: a 4xx at WARNING, a 5xx at ERROR.

Every Tier-1 and Tier-2 refusal answers through this helper, including the routine refusals the web
app's permission probes produce, so logging each at ERROR made log queries and alarms that count
ERROR lines fire on normal client behaviour, as ``general_error()`` and ``validation_error()`` did
before. The denial is audited by the Casbin enforcer, not by this log line.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.models import common as response_models


def _call_with_spy(**kwargs):
    spy = MagicMock()
    with patch.object(response_models, "logger", spy):
        response = response_models.authorization_error(**kwargs)
    return spy, response


@pytest.mark.unit
class TestAuthorizationErrorLogLevel:
    @pytest.mark.parametrize("status_code", [401, 403])
    def test_a_4xx_response_logs_at_warning_and_not_at_error(self, status_code):
        spy, response = _call_with_spy(status_code=status_code)
        assert response["statusCode"] == status_code
        assert spy.warning.call_count == 1
        assert spy.error.call_count == 0

    def test_the_default_403_logs_at_warning(self):
        spy, response = _call_with_spy()
        assert response["statusCode"] == 403
        assert json.loads(response["body"]) == {"message": "Not Authorized"}
        assert spy.warning.call_args.args[0] == "Not Authorized Error"
        assert spy.error.call_count == 0

    def test_a_5xx_response_logs_at_error(self):
        spy, response = _call_with_spy(status_code=500)
        assert response["statusCode"] == 500
        assert spy.error.call_count == 1
        assert spy.warning.call_count == 0
