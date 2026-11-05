"""include_system on the two execution list tools (#1121).

System workflow runs are hidden by both list routes unless asked for. Unset must send nothing, so the
server's default applies (it shows a single system workflow's runs); True and False must reach the
APIClient as 'true' and 'false' on every page; and the applied `includeSystem` echo, a boolean, must
survive pagination when it is False.
"""

from unittest.mock import MagicMock

import pytest

from vams_mcp import server


@pytest.fixture
def real_paginate_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(server, "CLIENT", client)
    client.config = server.CONFIG
    client.unwrap_message = server.VamsClient.unwrap_message
    client.paginate = lambda *args, **kwargs: server.VamsClient.paginate(client, *args, **kwargs)
    return client


def _params(api_method):
    return [call.kwargs["params"] for call in api_method.call_args_list]


def test_the_parameter_name_matches_the_cli_constant():
    from vamscli.constants import EXECUTION_LIST_INCLUDE_SYSTEM_PARAM

    assert server.EXECUTION_LIST_INCLUDE_SYSTEM_PARAM == EXECUTION_LIST_INCLUDE_SYSTEM_PARAM


@pytest.mark.parametrize("value, sent", [(None, None), (True, "true"), (False, "false")])
def test_list_executions_forwards_include_system_on_every_page(real_paginate_client, value, sent):
    real_paginate_client.api.list_executions.side_effect = [
        {"message": {"Items": [{"workflowExecutionId": "e1"}], "NextToken": "t1"}},
        {"message": {"Items": [{"workflowExecutionId": "e2"}]}},
    ]
    server.list_executions(include_system=value)
    for params in _params(real_paginate_client.api.list_executions):
        assert params.get("includeSystem") == sent


@pytest.mark.parametrize("value, sent", [(None, None), (True, "true"), (False, "false")])
def test_list_workflow_executions_forwards_include_system(real_paginate_client, value, sent):
    real_paginate_client.api.list_workflow_executions.side_effect = [
        {"message": {"Items": [{"workflowExecutionId": "e1"}]}},
    ]
    server.list_workflow_executions("db", "a1", include_system=value)
    params = _params(real_paginate_client.api.list_workflow_executions)[0]
    assert params.get("includeSystem") == sent


def test_a_false_echo_and_the_row_flag_survive_pagination(real_paginate_client):
    real_paginate_client.api.list_executions.side_effect = [
        {"message": {"Items": [{"workflowExecutionId": "e1", "isSystem": False}],
                     "includeSystem": False, "filterStartDate": "2026-01-01T00:00:00Z"}},
    ]
    result = server.list_executions()
    assert result["includeSystem"] is False
    assert result["Items"][0]["isSystem"] is False
    assert result["filterStartDate"] == "2026-01-01T00:00:00Z"


def test_a_true_echo_is_carried_for_the_asset_list(real_paginate_client):
    real_paginate_client.api.list_workflow_executions.side_effect = [
        {"message": {"Items": [{"workflowExecutionId": "e1", "isSystem": True}],
                     "includeSystem": True}},
    ]
    result = server.list_workflow_executions("db", "a1", include_system=True)
    assert result["includeSystem"] is True
    assert result["Items"][0]["isSystem"] is True
