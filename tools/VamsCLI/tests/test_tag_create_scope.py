# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from vamscli.commands.tag import flatten_tag_input
from vamscli.main import cli


def test_flatten_preserves_database_id():
    out = flatten_tag_input({
        "tagName": "EquipID", "description": "d", "tagTypeName": "Custom",
        "databaseId": "factory-db",
    })
    assert out.get("databaseId") == "factory-db"


def test_flatten_without_database_id_is_global():
    out = flatten_tag_input({"tagName": "Status", "description": "d", "tagTypeName": "System"})
    assert "databaseId" not in out or out.get("databaseId") in (None, "")


def test_create_command_puts_database_id_in_body(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag') as mocks:
        mocks['api_client'].create_tags.return_value = {"message": "Tag created"}
        result = cli_runner.invoke(cli, [
            'tag', 'create',
            '--tag-name', 'EquipID',
            '--description', 'd',
            '--tag-type-name', 'Custom',
            '--database', 'factory-db',
        ])
        assert result.exit_code == 0, result.output
        mocks['api_client'].create_tags.assert_called_once()
        (body,), _ = mocks['api_client'].create_tags.call_args
        assert body.get("databaseId") == "factory-db"


def test_create_command_without_database_is_global(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag') as mocks:
        mocks['api_client'].create_tags.return_value = {"message": "Tag created"}
        result = cli_runner.invoke(cli, [
            'tag', 'create',
            '--tag-name', 'Status',
            '--description', 'd',
            '--tag-type-name', 'System',
        ])
        assert result.exit_code == 0, result.output
        (body,), _ = mocks['api_client'].create_tags.call_args
        assert "databaseId" not in body or body.get("databaseId") in (None, "")


def test_update_command_puts_database_id_in_body(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag') as mocks:
        mocks['api_client'].get_tags.return_value = {
            "message": {"Items": [
                {"tagName": "EquipID", "description": "d", "tagTypeName": "Custom"}
            ]}
        }
        mocks['api_client'].update_tags.return_value = {"message": "Tag updated"}
        result = cli_runner.invoke(cli, [
            'tag', 'update',
            '--tag-name', 'EquipID',
            '--description', 'updated',
            '--tag-type-name', 'Custom',
            '--database', 'factory-db',
        ])
        assert result.exit_code == 0, result.output
        mocks['api_client'].update_tags.assert_called_once()
        (body,), _ = mocks['api_client'].update_tags.call_args
        assert body.get("databaseId") == "factory-db"


def test_update_command_without_database_is_global(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag') as mocks:
        mocks['api_client'].get_tags.return_value = {
            "message": {"Items": [
                {"tagName": "Status", "description": "d", "tagTypeName": "System"}
            ]}
        }
        mocks['api_client'].update_tags.return_value = {"message": "Tag updated"}
        result = cli_runner.invoke(cli, [
            'tag', 'update',
            '--tag-name', 'Status',
            '--description', 'updated',
            '--tag-type-name', 'System',
        ])
        assert result.exit_code == 0, result.output
        (body,), _ = mocks['api_client'].update_tags.call_args
        assert "databaseId" not in body or body.get("databaseId") in (None, "")


def test_delete_command_passes_database_id(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag') as mocks:
        mocks['api_client'].delete_tag.return_value = {"message": "Tag deleted"}
        result = cli_runner.invoke(cli, [
            'tag', 'delete', 'EquipID', '--confirm', '--database', 'db-a',
        ])
        assert result.exit_code == 0, result.output
        mocks['api_client'].delete_tag.assert_called_once()
        args, kwargs = mocks['api_client'].delete_tag.call_args
        assert args[0] == 'EquipID'
        assert kwargs.get("database_id") == "db-a"


def test_delete_command_without_database_is_global(cli_runner, generic_command_mocks):
    with generic_command_mocks('tag') as mocks:
        mocks['api_client'].delete_tag.return_value = {"message": "Tag deleted"}
        result = cli_runner.invoke(cli, ['tag', 'delete', 'Status', '--confirm'])
        assert result.exit_code == 0, result.output
        _, kwargs = mocks['api_client'].delete_tag.call_args
        assert kwargs.get("database_id") in (None, "")
