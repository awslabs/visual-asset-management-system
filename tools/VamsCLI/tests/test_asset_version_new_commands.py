"""Tests for asset version update, archive, and unarchive commands."""

import json
import pytest
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.commands.asset_version import asset_version
from vamscli.utils.exceptions import (
    AssetVersionNotFoundError, AssetVersionOperationError,
    InvalidAssetVersionDataError, AssetVersionArchiveError,
    AssetNotFoundError, DatabaseNotFoundError
)


@pytest.fixture
def asset_version_command_mocks(generic_command_mocks):
    """Provide asset version-specific command mocks."""
    return generic_command_mocks('asset_version')


class TestAssetVersionUpdate:
    """Tests for asset-version update command."""

    def test_update_comment_success(self, cli_runner, asset_version_command_mocks):
        """Test updating only the comment."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].update_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '1',
                'comment': 'Updated comment',
                'message': 'Asset version updated successfully'
            }

            result = cli_runner.invoke(asset_version, [
                'update',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1',
                '--comment', 'Updated comment'
            ])

            assert result.exit_code == 0
            assert 'Asset version updated successfully' in result.output
            assert 'test-asset' in result.output
            assert 'Version ID: 1' in result.output
            assert 'Comment: Updated comment' in result.output

            mocks['api_client'].update_asset_version.assert_called_once_with(
                'test-db', 'test-asset', '1', {'comment': 'Updated comment'}
            )

    def test_update_alias_success(self, cli_runner, asset_version_command_mocks):
        """Test updating only the alias."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].update_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '1',
                'versionAlias': 'RC1',
                'message': 'Asset version updated successfully'
            }

            result = cli_runner.invoke(asset_version, [
                'update',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1',
                '--alias', 'RC1'
            ])

            assert result.exit_code == 0
            assert 'Asset version updated successfully' in result.output
            assert 'Alias: RC1' in result.output

            mocks['api_client'].update_asset_version.assert_called_once_with(
                'test-db', 'test-asset', '1', {'versionAlias': 'RC1'}
            )

    def test_update_both_success(self, cli_runner, asset_version_command_mocks):
        """Test updating both comment and alias."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].update_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '1',
                'comment': 'Final release',
                'versionAlias': 'v1.0',
                'message': 'Asset version updated successfully'
            }

            result = cli_runner.invoke(asset_version, [
                'update',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1',
                '--comment', 'Final release',
                '--alias', 'v1.0'
            ])

            assert result.exit_code == 0
            assert 'Asset version updated successfully' in result.output
            assert 'Comment: Final release' in result.output
            assert 'Alias: v1.0' in result.output

            mocks['api_client'].update_asset_version.assert_called_once_with(
                'test-db', 'test-asset', '1',
                {'comment': 'Final release', 'versionAlias': 'v1.0'}
            )

    def test_update_json_output(self, cli_runner, asset_version_command_mocks):
        """Test update command with JSON output."""
        with asset_version_command_mocks as mocks:
            api_response = {
                'assetId': 'test-asset',
                'assetVersionId': '1',
                'comment': 'Updated',
                'versionAlias': 'RC1',
                'message': 'Asset version updated successfully'
            }
            mocks['api_client'].update_asset_version.return_value = api_response

            result = cli_runner.invoke(asset_version, [
                'update',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1',
                '--comment', 'Updated',
                '--json-output'
            ])

            assert result.exit_code == 0
            output_json = json.loads(result.output.strip())
            assert output_json == api_response

    def test_update_neither_provided(self, cli_runner, asset_version_command_mocks):
        """Test update command when neither comment nor alias is provided."""
        with asset_version_command_mocks as mocks:
            result = cli_runner.invoke(asset_version, [
                'update',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1'
            ])

            assert result.exit_code == 1
            assert 'At least one of --comment or --alias must be provided' in result.output

    def test_update_not_found(self, cli_runner, asset_version_command_mocks):
        """Test update command with version not found error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].update_asset_version.side_effect = AssetVersionNotFoundError(
                "Asset version '999' not found"
            )

            result = cli_runner.invoke(asset_version, [
                'update',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '999',
                '--comment', 'Test'
            ])

            assert result.exit_code == 1
            assert 'Version Not Found' in result.output
            assert 'vamscli asset-version list' in result.output

    def test_update_invalid_data(self, cli_runner, asset_version_command_mocks):
        """Test update command with invalid data error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].update_asset_version.side_effect = InvalidAssetVersionDataError(
                "Invalid update data: alias too long"
            )

            result = cli_runner.invoke(asset_version, [
                'update',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1',
                '--alias', 'x' * 200
            ])

            assert result.exit_code == 1
            assert 'Invalid Version Data' in result.output

    def test_update_database_not_found(self, cli_runner, asset_version_command_mocks):
        """Test update command with database not found error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].update_asset_version.side_effect = DatabaseNotFoundError(
                "Database 'nonexistent-db' not found"
            )

            result = cli_runner.invoke(asset_version, [
                'update',
                '-d', 'nonexistent-db',
                '-a', 'test-asset',
                '-v', '1',
                '--comment', 'Test'
            ])

            assert result.exit_code == 1
            assert 'Database Not Found' in result.output
            assert 'vamscli database list' in result.output

    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(asset_version, ['update', '--help'])
        assert result.exit_code == 0
        assert 'Update an asset version' in result.output
        assert '--comment' in result.output
        assert '--alias' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--version' in result.output
        assert '--json-output' in result.output


class TestAssetVersionArchive:
    """Tests for asset-version archive command."""

    def test_archive_success(self, cli_runner, asset_version_command_mocks):
        """Test successful version archiving."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].archive_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'message': 'Asset version 2 archived successfully'
            }

            result = cli_runner.invoke(asset_version, [
                'archive',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '2'
            ])

            assert result.exit_code == 0
            assert 'Asset version archived successfully' in result.output
            assert 'test-asset' in result.output
            assert 'Version ID: 2' in result.output

            mocks['api_client'].archive_asset_version.assert_called_once_with(
                'test-db', 'test-asset', '2'
            )

    def test_archive_json_output(self, cli_runner, asset_version_command_mocks):
        """Test archive command with JSON output."""
        with asset_version_command_mocks as mocks:
            api_response = {
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'message': 'Asset version 2 archived successfully'
            }
            mocks['api_client'].archive_asset_version.return_value = api_response

            result = cli_runner.invoke(asset_version, [
                'archive',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '2',
                '--json-output'
            ])

            assert result.exit_code == 0
            output_json = json.loads(result.output.strip())
            assert output_json == api_response

    def test_archive_not_found(self, cli_runner, asset_version_command_mocks):
        """Test archive command with version not found error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].archive_asset_version.side_effect = AssetVersionNotFoundError(
                "Asset version '999' not found"
            )

            result = cli_runner.invoke(asset_version, [
                'archive',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '999'
            ])

            assert result.exit_code == 1
            assert 'Version Not Found' in result.output
            assert 'vamscli asset-version list' in result.output

    def test_archive_current_version_error(self, cli_runner, asset_version_command_mocks):
        """Test archive command when trying to archive the current version."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].archive_asset_version.side_effect = AssetVersionArchiveError(
                "Archive failed: Cannot archive the current version"
            )

            result = cli_runner.invoke(asset_version, [
                'archive',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1'
            ])

            assert result.exit_code == 1
            assert 'Archive Failed' in result.output

    def test_archive_database_not_found(self, cli_runner, asset_version_command_mocks):
        """Test archive command with database not found error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].archive_asset_version.side_effect = DatabaseNotFoundError(
                "Database 'nonexistent-db' not found"
            )

            result = cli_runner.invoke(asset_version, [
                'archive',
                '-d', 'nonexistent-db',
                '-a', 'test-asset',
                '-v', '2'
            ])

            assert result.exit_code == 1
            assert 'Database Not Found' in result.output

    def test_archive_help(self, cli_runner):
        """Test archive command help."""
        result = cli_runner.invoke(asset_version, ['archive', '--help'])
        assert result.exit_code == 0
        assert 'Archive an asset version' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--version' in result.output
        assert '--json-output' in result.output


class TestAssetVersionUnarchive:
    """Tests for asset-version unarchive command."""

    def test_unarchive_success(self, cli_runner, asset_version_command_mocks):
        """Test successful version unarchiving."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].unarchive_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'message': 'Asset version 2 unarchived successfully'
            }

            result = cli_runner.invoke(asset_version, [
                'unarchive',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '2'
            ])

            assert result.exit_code == 0
            assert 'Asset version unarchived successfully' in result.output
            assert 'test-asset' in result.output
            assert 'Version ID: 2' in result.output

            mocks['api_client'].unarchive_asset_version.assert_called_once_with(
                'test-db', 'test-asset', '2'
            )

    def test_unarchive_json_output(self, cli_runner, asset_version_command_mocks):
        """Test unarchive command with JSON output."""
        with asset_version_command_mocks as mocks:
            api_response = {
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'message': 'Asset version 2 unarchived successfully'
            }
            mocks['api_client'].unarchive_asset_version.return_value = api_response

            result = cli_runner.invoke(asset_version, [
                'unarchive',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '2',
                '--json-output'
            ])

            assert result.exit_code == 0
            output_json = json.loads(result.output.strip())
            assert output_json == api_response

    def test_unarchive_not_found(self, cli_runner, asset_version_command_mocks):
        """Test unarchive command with version not found error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].unarchive_asset_version.side_effect = AssetVersionNotFoundError(
                "Asset version '999' not found"
            )

            result = cli_runner.invoke(asset_version, [
                'unarchive',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '999'
            ])

            assert result.exit_code == 1
            assert 'Version Not Found' in result.output

    def test_unarchive_error(self, cli_runner, asset_version_command_mocks):
        """Test unarchive command with unarchive error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].unarchive_asset_version.side_effect = AssetVersionArchiveError(
                "Unarchive failed: Version is not archived"
            )

            result = cli_runner.invoke(asset_version, [
                'unarchive',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '2'
            ])

            assert result.exit_code == 1
            assert 'Unarchive Failed' in result.output

    def test_unarchive_help(self, cli_runner):
        """Test unarchive command help."""
        result = cli_runner.invoke(asset_version, ['unarchive', '--help'])
        assert result.exit_code == 0
        assert 'Unarchive a previously archived asset version' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--version' in result.output
        assert '--json-output' in result.output


class TestVersionDisplayWithAlias:
    """Tests for version display with alias and archived status."""

    def test_list_shows_alias(self, cli_runner, asset_version_command_mocks):
        """Test that version list shows alias in parentheses."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_versions.return_value = {
                'versions': [
                    {
                        'Version': '3',
                        'DateModified': '2024-01-03T00:00:00Z',
                        'Comment': 'Release candidate',
                        'createdBy': 'user@example.com',
                        'isCurrent': True,
                        'fileCount': 3,
                        'versionAlias': 'RC1'
                    },
                    {
                        'Version': '2',
                        'DateModified': '2024-01-02T00:00:00Z',
                        'Comment': 'Beta version',
                        'createdBy': 'user@example.com',
                        'isCurrent': False,
                        'fileCount': 2,
                        'versionAlias': ''
                    }
                ],
                'NextToken': None
            }

            result = cli_runner.invoke(asset_version, [
                'list',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])

            assert result.exit_code == 0
            assert 'Version: 3 (RC1) (CURRENT)' in result.output
            # Version 2 has empty alias, should not show parentheses for alias
            assert 'Version: 2' in result.output
            # Make sure empty alias does not produce "()"
            assert '()' not in result.output

    def test_list_shows_archived_marker(self, cli_runner, asset_version_command_mocks):
        """Test that version list shows ARCHIVED marker for archived versions."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_versions.return_value = {
                'versions': [
                    {
                        'Version': '3',
                        'DateModified': '2024-01-03T00:00:00Z',
                        'Comment': 'Current version',
                        'createdBy': 'user@example.com',
                        'isCurrent': True,
                        'fileCount': 3,
                        'isArchived': False
                    },
                    {
                        'Version': '2',
                        'DateModified': '2024-01-02T00:00:00Z',
                        'Comment': 'Old version',
                        'createdBy': 'user@example.com',
                        'isCurrent': False,
                        'fileCount': 2,
                        'isArchived': True,
                        'versionAlias': 'Beta'
                    },
                    {
                        'Version': '1',
                        'DateModified': '2024-01-01T00:00:00Z',
                        'Comment': 'Initial',
                        'createdBy': 'user@example.com',
                        'isCurrent': False,
                        'fileCount': 1,
                        'isArchived': True
                    }
                ],
                'NextToken': None
            }

            result = cli_runner.invoke(asset_version, [
                'list',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])

            assert result.exit_code == 0
            assert 'Version: 3 (CURRENT)' in result.output
            assert 'Version: 2 (Beta) (ARCHIVED)' in result.output
            assert 'Version: 1 (ARCHIVED)' in result.output
            # Version 3 should NOT show ARCHIVED
            assert 'Version: 3 (CURRENT)' in result.output
            assert '3 (ARCHIVED)' not in result.output

    def test_get_shows_alias_and_archived(self, cli_runner, asset_version_command_mocks):
        """Test that version get shows alias and archived status."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'dateCreated': '2024-01-02T00:00:00Z',
                'comment': 'Beta release',
                'createdBy': 'user@example.com',
                'versionAlias': 'Beta',
                'isArchived': True,
                'files': []
            }

            result = cli_runner.invoke(asset_version, [
                'get',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '2'
            ])

            assert result.exit_code == 0
            assert 'Asset Version Details:' in result.output
            assert 'Alias: Beta' in result.output
            assert 'Status: ARCHIVED' in result.output

    def test_get_no_alias_no_archived(self, cli_runner, asset_version_command_mocks):
        """Test that version get does not show alias/archived when not set."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '1',
                'dateCreated': '2024-01-01T00:00:00Z',
                'comment': 'Initial',
                'createdBy': 'user@example.com',
                'files': []
            }

            result = cli_runner.invoke(asset_version, [
                'get',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1'
            ])

            assert result.exit_code == 0
            assert 'Alias:' not in result.output
            assert 'ARCHIVED' not in result.output


class TestAssetVersionHelpCommandsNew:
    """Test that new commands appear in help output."""

    def test_asset_version_help_shows_new_commands(self, cli_runner):
        """Test asset-version help includes update, archive, and unarchive."""
        result = cli_runner.invoke(asset_version, ['--help'])
        assert result.exit_code == 0
        assert 'update' in result.output
        assert 'archive' in result.output
        assert 'unarchive' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
