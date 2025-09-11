"""Test database management commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.commands.database import database
from vamscli.utils.exceptions import (
    DatabaseNotFoundError, DatabaseAlreadyExistsError, DatabaseDeletionError,
    BucketNotFoundError, InvalidDatabaseDataError, AuthenticationError, APIError
)


# File-level fixtures for database-specific testing patterns
@pytest.fixture
def database_command_mocks(generic_command_mocks):
    """Provide database-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for database command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('database')


@pytest.fixture
def database_no_setup_mocks(no_setup_command_mocks):
    """Provide database command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('database')


class TestDatabaseListCommand:
    """Test database list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(database, ['list', '--help'])
        assert result.exit_code == 0
        assert 'List all databases' in result.output
        assert '--show-deleted' in result.output
        assert '--max-items' in result.output
        assert '--page-size' in result.output
        assert '--starting-token' in result.output
        assert '--json-output' in result.output
    
    def test_list_success(self, cli_runner, generic_command_mocks):
        """Test successful database listing."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_databases.return_value = {
                'Items': [
                    {
                        'databaseId': 'test-db-1',
                        'description': 'Test Database 1',
                        'dateCreated': '2024-01-01T00:00:00Z',
                        'assetCount': 5,
                        'defaultBucketId': 'bucket-uuid-1',
                        'bucketName': 'test-bucket-1'
                    },
                    {
                        'databaseId': 'test-db-2',
                        'description': 'Test Database 2',
                        'dateCreated': '2024-01-02T00:00:00Z',
                        'assetCount': 10,
                        'defaultBucketId': 'bucket-uuid-2',
                        'bucketName': 'test-bucket-2'
                    }
                ],
                'NextToken': 'next-page-token'
            }
            
            result = cli_runner.invoke(database, ['list'])
            
            assert result.exit_code == 0
            assert 'Found 2 database(s):' in result.output
            assert 'test-db-1' in result.output
            assert 'test-db-2' in result.output
            assert 'Test Database 1' in result.output
            assert 'Test Database 2' in result.output
            assert 'More results available' in result.output
            
            # Verify API call
            mocks['api_client'].list_databases.assert_called_once_with(show_deleted=False, params={})
    
    def test_list_with_pagination(self, cli_runner, generic_command_mocks):
        """Test database listing with pagination parameters."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_databases.return_value = {
                'Items': [],
                'NextToken': None
            }
            
            result = cli_runner.invoke(database, [
                'list', 
                '--max-items', '50',
                '--page-size', '25',
                '--starting-token', 'test-token'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call with pagination parameters
            expected_params = {
                'maxItems': 50,
                'pageSize': 25,
                'startingToken': 'test-token'
            }
            mocks['api_client'].list_databases.assert_called_once_with(show_deleted=False, params=expected_params)
    
    def test_list_json_output(self, cli_runner, generic_command_mocks):
        """Test database listing with JSON output."""
        with generic_command_mocks('database') as mocks:
            api_response = {
                'Items': [
                    {
                        'databaseId': 'test-db',
                        'description': 'Test Database',
                        'assetCount': 5
                    }
                ]
            }
            mocks['api_client'].list_databases.return_value = api_response
            
            result = cli_runner.invoke(database, ['list', '--json-output'])
            
            assert result.exit_code == 0
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert output_json == api_response
    


class TestDatabaseCreateCommand:
    """Test database create command."""
    
    def test_create_help(self, cli_runner):
        """Test create command help."""
        result = cli_runner.invoke(database, ['create', '--help'])
        assert result.exit_code == 0
        assert 'Create a new database' in result.output
        assert '--database-id' in result.output
        assert '--description' in result.output
        assert '--default-bucket-id' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_create_success(self, cli_runner, generic_command_mocks):
        """Test successful database creation."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].create_database.return_value = {
                'databaseId': 'test-database',
                'message': 'Database created successfully'
            }
            
            result = cli_runner.invoke(database, [
                'create', 
                '-d', 'test-database',
                '--description', 'Test Database',
                '--default-bucket-id', 'bucket-uuid'
            ])
            
            assert result.exit_code == 0
            assert '✓ Database created successfully!' in result.output
            assert 'test-database' in result.output
            
            # Verify API call
            expected_data = {
                'databaseId': 'test-database',
                'description': 'Test Database',
                'defaultBucketId': 'bucket-uuid'
            }
            mocks['api_client'].create_database.assert_called_once_with(expected_data)
    
    @patch('vamscli.commands.database.prompt_bucket_selection')
    def test_create_with_bucket_prompt(self, mock_prompt, cli_runner, generic_command_mocks):
        """Test database creation with bucket selection prompt."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].create_database.return_value = {
                'databaseId': 'test-database',
                'message': 'Database created successfully'
            }
            
            mock_prompt.return_value = 'selected-bucket-uuid'
            
            result = cli_runner.invoke(database, [
                'create', 
                '-d', 'test-database',
                '--description', 'Test Database'
            ])
            
            assert result.exit_code == 0
            assert '✓ Database created successfully!' in result.output
            assert 'No bucket ID provided' in result.output
            
            # Verify bucket selection was called
            mock_prompt.assert_called_once_with(mocks['api_client'])
            
            # Verify API call with selected bucket
            expected_data = {
                'databaseId': 'test-database',
                'description': 'Test Database',
                'defaultBucketId': 'selected-bucket-uuid'
            }
            mocks['api_client'].create_database.assert_called_once_with(expected_data)
    
    def test_create_json_input(self, cli_runner, generic_command_mocks):
        """Test database creation with JSON input."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].create_database.return_value = {
                'databaseId': 'json-database',
                'message': 'Database created successfully'
            }
            
            json_data = {
                'databaseId': 'json-database',
                'description': 'JSON Database',
                'defaultBucketId': 'json-bucket-uuid'
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(database, [
                    'create',
                    '-d', 'test-database',  # This should be overridden by JSON
                    '--json-input', 'test.json'
                ])
            
            assert result.exit_code == 0
            assert '✓ Database created successfully!' in result.output
            
            # Verify API call uses JSON data (with command line database ID override)
            expected_data = {
                'databaseId': 'test-database',  # Overridden from command line
                'description': 'JSON Database',
                'defaultBucketId': 'json-bucket-uuid'
            }
            mocks['api_client'].create_database.assert_called_once_with(expected_data)
    
    def test_create_database_already_exists(self, cli_runner, generic_command_mocks):
        """Test create command with database already exists error."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].create_database.side_effect = DatabaseAlreadyExistsError("Database 'test-database' already exists")
            
            result = cli_runner.invoke(database, [
                'create',
                '-d', 'test-database',
                '--description', 'Test Database',
                '--default-bucket-id', 'bucket-uuid'
            ])

            assert result.exit_code == 1
            assert '✗ Database Already Exists' in result.output
            assert 'vamscli database get' in result.output
    
    def test_create_missing_description(self, cli_runner, generic_command_mocks):
        """Test create command without required description."""
        with generic_command_mocks('database') as mocks:
            result = cli_runner.invoke(database, [
                'create',
                '-d', 'test-database'
            ])

            assert result.exit_code == 1  # Our custom error handling
            assert '--description is required' in result.output


class TestDatabaseUpdateCommand:
    """Test database update command."""
    
    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(database, ['update', '--help'])
        assert result.exit_code == 0
        assert 'Update an existing database' in result.output
        assert '--database-id' in result.output
        assert '--description' in result.output
        assert '--default-bucket-id' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_update_success(self, cli_runner, generic_command_mocks):
        """Test successful database update."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].update_database.return_value = {
                'databaseId': 'test-database',
                'message': 'Database updated successfully'
            }
            
            result = cli_runner.invoke(database, [
                'update', 
                '-d', 'test-database',
                '--description', 'Updated Description'
            ])
            
            assert result.exit_code == 0
            assert '✓ Database updated successfully!' in result.output
            assert 'test-database' in result.output
            
            # Verify API call
            expected_data = {
                'databaseId': 'test-database',
                'description': 'Updated Description'
            }
            mocks['api_client'].update_database.assert_called_once_with(expected_data)
    
    def test_update_database_not_found(self, cli_runner, generic_command_mocks):
        """Test update command with database not found."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].update_database.side_effect = DatabaseNotFoundError("Database 'test-database' not found")
            
            result = cli_runner.invoke(database, [
                'update',
                '-d', 'test-database',
                '--description', 'Updated Description'
            ])

            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert 'vamscli database list' in result.output
    
    def test_update_no_fields(self, cli_runner, generic_command_mocks):
        """Test update command without any fields to update."""
        with generic_command_mocks('database') as mocks:
            result = cli_runner.invoke(database, [
                'update',
                '-d', 'test-database'
            ])

            assert result.exit_code == 1  # Our custom error handling
            assert 'At least one field must be provided' in result.output


class TestDatabaseGetCommand:
    """Test database get command."""
    
    def test_get_help(self, cli_runner):
        """Test get command help."""
        result = cli_runner.invoke(database, ['get', '--help'])
        assert result.exit_code == 0
        assert 'Get details for a specific database' in result.output
        assert '--database-id' in result.output
        assert '--show-deleted' in result.output
        assert '--json-output' in result.output
    
    def test_get_success(self, cli_runner, generic_command_mocks):
        """Test successful database retrieval."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].get_database.return_value = {
                'databaseId': 'test-database',
                'description': 'Test Database',
                'dateCreated': '2024-01-01T00:00:00Z',
                'assetCount': 5,
                'defaultBucketId': 'bucket-uuid',
                'bucketName': 'test-bucket',
                'baseAssetsPrefix': 'assets/'
            }
            
            result = cli_runner.invoke(database, [
                'get', 
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 0
            assert 'Database Details:' in result.output
            assert 'test-database' in result.output
            assert 'Test Database' in result.output
            assert 'test-bucket' in result.output
            
            # Verify API call
            mocks['api_client'].get_database.assert_called_once_with('test-database', False)
    
    def test_get_database_not_found(self, cli_runner, generic_command_mocks):
        """Test get command with database not found."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].get_database.side_effect = DatabaseNotFoundError("Database 'test-database' not found")
            
            result = cli_runner.invoke(database, [
                'get',
                '-d', 'test-database'
            ])

            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert '--show-deleted' in result.output


class TestDatabaseDeleteCommand:
    """Test database delete command."""
    
    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(database, ['delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete a database' in result.output
        assert 'WARNING: This action will delete the database!' in result.output
        assert '--database-id' in result.output
        assert '--confirm' in result.output
        assert '--json-output' in result.output
    
    @patch('click.confirm')
    def test_delete_success(self, mock_confirm, cli_runner, generic_command_mocks):
        """Test successful database deletion."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].delete_database.return_value = {
                'message': 'Database deleted successfully'
            }
            
            mock_confirm.return_value = True  # User confirms deletion
            
            result = cli_runner.invoke(database, [
                'delete', 
                '-d', 'test-database',
                '--confirm'
            ])

            assert result.exit_code == 0
            assert '✓ Database deleted successfully!' in result.output
            assert 'test-database' in result.output
            
            # Verify API call
            mocks['api_client'].delete_database.assert_called_once_with('test-database')
    
    def test_delete_no_confirm_flag(self, cli_runner, generic_command_mocks):
        """Test delete command without confirm flag."""
        with generic_command_mocks('database') as mocks:
            result = cli_runner.invoke(database, [
                'delete', 
                '-d', 'test-database'
            ])

            assert result.exit_code == 1
            assert 'Database deletion requires explicit confirmation!' in result.output
            assert 'Use --confirm flag' in result.output
            assert 'cannot be undone' in result.output
            
            # Verify API was not called
            mocks['api_client'].delete_database.assert_not_called()
    
    @patch('click.confirm')
    def test_delete_user_cancels(self, mock_confirm, cli_runner, generic_command_mocks):
        """Test delete command when user cancels confirmation."""
        with generic_command_mocks('database') as mocks:
            mock_confirm.return_value = False  # User cancels deletion
            
            result = cli_runner.invoke(database, [
                'delete', 
                '-d', 'test-database',
                '--confirm'
            ])

            assert result.exit_code == 0
            assert 'Deletion cancelled' in result.output
            
            # Verify API was not called
            mocks['api_client'].delete_database.assert_not_called()
    
    @patch('click.confirm')
    def test_delete_deletion_error(self, mock_confirm, cli_runner, generic_command_mocks):
        """Test delete command with deletion error."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].delete_database.side_effect = DatabaseDeletionError("Database contains active assets")
            mock_confirm.return_value = True  # User confirms deletion
            
            result = cli_runner.invoke(database, [
                'delete',
                '-d', 'test-database',
                '--confirm'
            ])

            assert result.exit_code == 1
            assert '✗ Database Deletion Error' in result.output
            assert 'active assets, workflows, or pipelines' in result.output


class TestDatabaseListBucketsCommand:
    """Test database list-buckets command."""
    
    def test_list_buckets_help(self, cli_runner):
        """Test list-buckets command help."""
        result = cli_runner.invoke(database, ['list-buckets', '--help'])
        assert result.exit_code == 0
        assert 'List available S3 bucket configurations' in result.output
        assert '--max-items' in result.output
        assert '--page-size' in result.output
        assert '--starting-token' in result.output
        assert '--json-output' in result.output
    
    def test_list_buckets_success(self, cli_runner, generic_command_mocks):
        """Test successful bucket listing."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_buckets.return_value = {
                'Items': [
                    {
                        'bucketId': 'bucket-uuid-1',
                        'bucketName': 'test-bucket-1',
                        'baseAssetsPrefix': 'assets/'
                    },
                    {
                        'bucketId': 'bucket-uuid-2',
                        'bucketName': 'test-bucket-2',
                        'baseAssetsPrefix': 'data/'
                    }
                ]
            }
            
            result = cli_runner.invoke(database, ['list-buckets'])
            
            assert result.exit_code == 0
            assert 'Found 2 bucket configuration(s):' in result.output
            assert 'test-bucket-1' in result.output
            assert 'test-bucket-2' in result.output
            assert 'bucket-uuid-1' in result.output
            assert 'bucket-uuid-2' in result.output
            
            # Verify API call
            mocks['api_client'].list_buckets.assert_called_once_with({})
    
    def test_list_buckets_empty(self, cli_runner, generic_command_mocks):
        """Test bucket listing with no buckets."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_buckets.return_value = {
                'Items': []
            }
            
            result = cli_runner.invoke(database, ['list-buckets'])
            
            assert result.exit_code == 0
            assert 'No bucket configurations found.' in result.output


class TestDatabaseCommandsIntegration:
    """Test integration scenarios for database commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_database_id(self, mock_main_profile_manager):
        """Test that database commands require database ID where appropriate."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test create without database ID
        result = runner.invoke(database, ['create'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test update without database ID
        result = runner.invoke(database, ['update'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test get without database ID
        result = runner.invoke(database, ['get'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test delete without database ID
        result = runner.invoke(database, ['delete'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    


class TestDatabaseCommandsJSONHandling:
    """Test JSON input/output handling for database commands."""
    
    # Note: JSON input tests combine fixtures with patches for builtins.open
    def test_invalid_json_input_file(self, cli_runner, generic_command_mocks):
        """Test handling of invalid JSON input file."""
        with generic_command_mocks('database') as mocks:
            with patch('builtins.open', mock_open(read_data='invalid json')):
                result = cli_runner.invoke(database, [
                    'create',
                    '-d', 'test-database',
                    '--json-input', 'invalid.json'
                ])
            
            assert result.exit_code == 1  # Our custom error handling
            assert 'Invalid JSON input' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, generic_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with generic_command_mocks('database') as mocks:
            with patch('builtins.open', side_effect=FileNotFoundError()):
                result = cli_runner.invoke(database, [
                    'create',
                    '-d', 'test-database',
                    '--json-input', 'nonexistent.json'
                ])
            
            assert result.exit_code == 1  # Our custom error handling
            assert 'Invalid JSON input' in result.output


class TestDatabaseCommandsEdgeCases:
    """Test edge cases for database commands."""
    
    
    def test_bucket_not_found_error(self, cli_runner, generic_command_mocks):
        """Test create command with bucket not found error."""
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].create_database.side_effect = BucketNotFoundError("Bucket 'invalid-bucket' not found")
            
            result = cli_runner.invoke(database, [
                'create',
                '-d', 'test-database',
                '--description', 'Test Database',
                '--default-bucket-id', 'invalid-bucket'
            ])
            
            assert result.exit_code == 1
            assert '✗ Bucket Not Found' in result.output
            assert 'vamscli database list-buckets' in result.output


class TestBucketSelectionFunction:
    """Test bucket selection functionality."""
    
    @patch('click.prompt')
    def test_prompt_bucket_selection_success(self, mock_prompt):
        """Test successful bucket selection."""
        from vamscli.commands.database import prompt_bucket_selection
        
        mock_api_client = Mock()
        mock_api_client.list_buckets.return_value = {
            'Items': [
                {
                    'bucketId': 'bucket-1',
                    'bucketName': 'Test Bucket 1',
                    'baseAssetsPrefix': 'assets/'
                },
                {
                    'bucketId': 'bucket-2',
                    'bucketName': 'Test Bucket 2',
                    'baseAssetsPrefix': 'data/'
                }
            ]
        }
        
        mock_prompt.return_value = 2  # User selects second bucket
        
        result = prompt_bucket_selection(mock_api_client)
        
        assert result == 'bucket-2'
        mock_prompt.assert_called_once_with("Select bucket number", type=int)
    
    def test_prompt_bucket_selection_no_buckets(self):
        """Test bucket selection with no available buckets."""
        from vamscli.commands.database import prompt_bucket_selection
        
        mock_api_client = Mock()
        mock_api_client.list_buckets.return_value = {
            'Items': []
        }
        
        with pytest.raises(click.ClickException) as exc_info:
            prompt_bucket_selection(mock_api_client)
        
        assert "No buckets available" in str(exc_info.value)
    
    @patch('click.prompt')
    def test_prompt_bucket_selection_invalid_choice(self, mock_prompt):
        """Test bucket selection with invalid choice."""
        from vamscli.commands.database import prompt_bucket_selection
        
        mock_api_client = Mock()
        mock_api_client.list_buckets.return_value = {
            'Items': [
                {
                    'bucketId': 'bucket-1',
                    'bucketName': 'Test Bucket 1',
                    'baseAssetsPrefix': 'assets/'
                }
            ]
        }
        
        # First call returns invalid choice, second call returns valid choice
        mock_prompt.side_effect = [5, 1]  # Invalid then valid
        
        result = prompt_bucket_selection(mock_api_client)
        
        assert result == 'bucket-1'
        assert mock_prompt.call_count == 2


if __name__ == '__main__':
    pytest.main([__file__])
