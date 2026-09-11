# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

from vamscli.main import cli
from vamscli.utils.api_client import APIClient


def _client():
    # Bypass __init__/network setup; stub the internal request method.
    c = APIClient.__new__(APIClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": {"Items": []}}
    c.get = MagicMock(return_value=mock_response)
    return c


def _params(mock_get):
    _, kwargs = mock_get.call_args
    return kwargs.get("params", {}) or {}


def test_get_tag_types_passes_database_id():
    c = _client()
    c.get_tag_types(database_id="factory-db")
    assert _params(c.get).get("databaseId") == "factory-db"


def test_get_tag_types_passes_scope():
    c = _client()
    c.get_tag_types(scope="global")
    assert _params(c.get).get("scope") == "global"


def test_get_tag_types_no_scope_omits_params():
    c = _client()
    c.get_tag_types()
    params = _params(c.get)
    assert "databaseId" not in params and "scope" not in params


def test_create_command_puts_database_id_in_body(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag_type') as mocks:
        mocks['api_client'].create_tag_types.return_value = {"message": "Tag type created"}
        result = cli_runner.invoke(cli, [
            'tag-type', 'create',
            '--tag-type-name', 'Equipment',
            '--description', 'd',
            '--database', 'factory-db',
        ])
        assert result.exit_code == 0, result.output
        mocks['api_client'].create_tag_types.assert_called_once()
        (body,), _ = mocks['api_client'].create_tag_types.call_args
        assert body.get("databaseId") == "factory-db"


def test_create_command_without_database_is_global(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag_type') as mocks:
        mocks['api_client'].create_tag_types.return_value = {"message": "Tag type created"}
        result = cli_runner.invoke(cli, [
            'tag-type', 'create',
            '--tag-type-name', 'System',
            '--description', 'd',
        ])
        assert result.exit_code == 0, result.output
        (body,), _ = mocks['api_client'].create_tag_types.call_args
        assert "databaseId" not in body or body.get("databaseId") in (None, "")


def test_list_command_passes_scope(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag_type') as mocks:
        mocks['api_client'].get_tag_types.return_value = {"message": {"Items": []}}
        result = cli_runner.invoke(cli, ['tag-type', 'list', '--scope', 'global'])
        assert result.exit_code == 0, result.output
        _, kwargs = mocks['api_client'].get_tag_types.call_args
        assert kwargs.get("scope") == "global"


def _delete_client():
    c = APIClient.__new__(APIClient)
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": "deleted"}
    c.delete = MagicMock(return_value=mock_response)
    return c


def _delete_params(mock_delete):
    _, kwargs = mock_delete.call_args
    return kwargs.get("params", {}) or {}


def test_delete_tag_type_passes_database_id():
    c = _delete_client()
    c.delete_tag_type("Equipment", database_id="factory-db")
    assert _delete_params(c.delete).get("databaseId") == "factory-db"


def test_delete_tag_type_no_database_omits_param():
    c = _delete_client()
    c.delete_tag_type("System")
    assert "databaseId" not in _delete_params(c.delete)


def test_update_command_puts_database_id_in_body(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag_type') as mocks:
        mocks['api_client'].get_tag_types.return_value = {
            "message": {"Items": [
                {"tagTypeName": "Equipment", "description": "d", "required": "False"}
            ]}
        }
        mocks['api_client'].update_tag_types.return_value = {"message": "Tag type updated"}
        result = cli_runner.invoke(cli, [
            'tag-type', 'update',
            '--tag-type-name', 'Equipment',
            '--description', 'updated',
            '--database', 'factory-db',
        ])
        assert result.exit_code == 0, result.output
        mocks['api_client'].update_tag_types.assert_called_once()
        (body,), _ = mocks['api_client'].update_tag_types.call_args
        assert body.get("databaseId") == "factory-db"


def test_update_command_without_database_is_global(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag_type') as mocks:
        mocks['api_client'].get_tag_types.return_value = {
            "message": {"Items": [
                {"tagTypeName": "System", "description": "d", "required": "False"}
            ]}
        }
        mocks['api_client'].update_tag_types.return_value = {"message": "Tag type updated"}
        result = cli_runner.invoke(cli, [
            'tag-type', 'update',
            '--tag-type-name', 'System',
            '--description', 'updated',
        ])
        assert result.exit_code == 0, result.output
        (body,), _ = mocks['api_client'].update_tag_types.call_args
        assert "databaseId" not in body or body.get("databaseId") in (None, "")


def test_delete_command_passes_database_id(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag_type') as mocks:
        mocks['api_client'].delete_tag_type.return_value = {"message": "Tag type deleted"}
        result = cli_runner.invoke(cli, [
            'tag-type', 'delete', 'Equipment', '--confirm', '--database', 'db-a',
        ])
        assert result.exit_code == 0, result.output
        mocks['api_client'].delete_tag_type.assert_called_once()
        args, kwargs = mocks['api_client'].delete_tag_type.call_args
        assert args[0] == 'Equipment'
        assert kwargs.get("database_id") == "db-a"


def test_delete_command_without_database_is_global(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag_type') as mocks:
        mocks['api_client'].delete_tag_type.return_value = {"message": "Tag type deleted"}
        result = cli_runner.invoke(cli, ['tag-type', 'delete', 'System', '--confirm'])
        assert result.exit_code == 0, result.output
        _, kwargs = mocks['api_client'].delete_tag_type.call_args
        assert kwargs.get("database_id") in (None, "")
