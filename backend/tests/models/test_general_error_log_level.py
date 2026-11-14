# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``general_error()`` logs by status class: a 4xx at WARNING, a 5xx at ERROR (issue #935).

Handlers use ``general_error()`` for expected client outcomes -- a 404 for a missing record, a 409 for a
resource that is not in an allowed state, a 400 for a refused request -- and the helper used to log every
one of them at ERROR. Log queries, metric filters and alarms that count ERROR lines then fired on normal
client behaviour. ERROR stays for a 5xx, which is a failure of the service rather than of the request.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.models import common as response_models


def _call_with_spy(status_code, body=None):
    spy = MagicMock()
    with patch.object(response_models, "logger", spy):
        if body is None:
            response = response_models.general_error(status_code=status_code)
        else:
            response = response_models.general_error(status_code=status_code, body=body)
    return spy, response


@pytest.mark.unit
class TestGeneralErrorLogLevel:
    @pytest.mark.parametrize("status_code", [400, 404, 409, 422, 429, 499])
    def test_a_4xx_response_logs_at_warning_and_not_at_error(self, status_code):
        spy, response = _call_with_spy(status_code, {"message": "Resource is not in an allowed state."})

        assert response["statusCode"] == status_code
        assert spy.warning.call_count == 1
        assert spy.error.call_count == 0

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    def test_a_5xx_response_logs_at_error_and_not_at_warning(self, status_code):
        spy, response = _call_with_spy(status_code, {"message": "Upstream failure."})

        assert response["statusCode"] == status_code
        assert spy.error.call_count == 1
        assert spy.warning.call_count == 0

    def test_the_default_status_is_a_4xx_and_logs_at_warning(self):
        spy, response = _call_with_spy(400)

        assert response["statusCode"] == 400
        assert spy.warning.call_count == 1
        assert spy.error.call_count == 0

    @pytest.mark.parametrize("status_code", [409, 500])
    def test_the_record_keeps_its_message_and_structured_fields(self, status_code):
        body = {"message": "Resource is not in an allowed state."}
        spy, _response = _call_with_spy(status_code, body)

        logged = spy.error if status_code >= 500 else spy.warning
        assert logged.call_args.args[0] == "General error"
        assert logged.call_args.kwargs["extra"] == {"statusCode": status_code, "errorBody": body}

    @pytest.mark.parametrize("status_code", [409, 500])
    def test_the_response_on_the_wire_does_not_change(self, status_code):
        body = {"message": "Resource is not in an allowed state."}
        _spy, response = _call_with_spy(status_code, body)

        assert response["isBase64Encoded"] is False
        assert response["statusCode"] == status_code
        assert response["headers"] == response_models.commonHeaders()
        assert json.loads(response["body"]) == body
