"""Test search functionality."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    SearchDisabledError, SearchUnavailableError, InvalidSearchParametersError,
    SearchQueryError, SearchMappingError, AuthenticationError, APIError
)


# File-level fixtures for search-specific testing patterns
@pytest.fixture
def search_command_mocks(generic_command_mocks):
    """Provide search-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for search command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('search')


@pytest.fixture
def search_no_setup_mocks(no_setup_command_mocks):
    """Provide search command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('search')


class TestSearchAssetsCommand:
    """Test search assets command."""
    
    def test_assets_help(self, cli_runner):
        """Test assets command help."""
        result = cli_runner.invoke(cli, ['search', 'assets', '--help'])
        assert result.exit_code == 0
        assert 'Search assets using OpenSearch' in result.output
        assert '--query' in result.output
        assert '--database' in result.output
        assert '--asset-type' in result.output
        assert '--tags' in result.output
        assert '--property-filters' in result.output
        assert '--output-format' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_success(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test successful asset search."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Mock search results
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_assetname": "test-asset-001",
                                "str_databaseid": "test-db",
                                "str_assettype": "3d-model",
                                "str_description": "Test asset description",
                                "list_tags": ["test", "model"]
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'test'])
            
            assert result.exit_code == 0
            assert 'Search Results (1 found)' in result.output
            assert 'test-asset-001' in result.output
            assert 'Search completed. Found 1 assets' in result.output
            
            # Verify API call
            mocks['api_client'].search_query.assert_called_once()
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_with_filters(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test asset search with various filters."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_assetname": "filtered-asset",
                                "str_databaseid": "test-db",
                                "str_assettype": "3d-model",
                                "str_description": "Filtered asset",
                                "list_tags": ["test", "model"]
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, [
                'search', 'assets', 
                '-q', 'test',
                '-d', 'test-db',
                '--asset-type', '3d-model',
                '--tags', 'test,model',
                '--sort-field', 'str_assetname',
                '--sort-desc'
            ])
            
            assert result.exit_code == 0
            assert 'Search completed. Found 1 assets' in result.output
            
            # Verify API was called with correct parameters
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert call_args['query'] == 'test'
            assert call_args['operation'] == 'AND'
            assert call_args['sort'][0]['str_assetname.raw']['order'] == 'desc'
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_json_output(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test asset search with JSON output format."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_assetname": "test-asset",
                                "str_databaseid": "test-db",
                                "str_assettype": "3d-model",
                                "str_description": "Test description",
                                "list_tags": ["test"]
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'test', '--output-format', 'json'])
            
            assert result.exit_code == 0
            
            # Check that JSON output is present and contains expected data
            assert '[' in result.output, f"No JSON array found in output: {result.output}"
            assert 'test-asset' in result.output
            assert 'test-db' in result.output
            assert 'assetName' in result.output
            assert 'database' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_csv_output(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test asset search with CSV output format."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_assetname": "test-asset",
                                "str_databaseid": "test-db",
                                "str_assettype": "3d-model",
                                "str_description": "Test description",
                                "list_tags": ["test", "model"]
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'test', '--output-format', 'csv'])
            
            assert result.exit_code == 0
            assert 'Asset Name,Database,Type,Description,Tags,Score' in result.output
            assert 'test-asset,test-db,3d-model' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_legacy_json_output(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test asset search with legacy JSON output."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_assetname": "test-asset",
                                "str_databaseid": "test-db"
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'test', '--jsonOutput'])
            
            assert result.exit_code == 0
            
            # Parse JSON output - should be raw API response
            json_output = json.loads(result.output)
            assert 'hits' in json_output
            assert json_output['hits']['total']['value'] == 1
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_json_input(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test asset search with JSON input file."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_assetname": "json-asset",
                                "str_databaseid": "json-db"
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            json_data = {
                'query': 'test model',
                'database': 'test-db',
                'operation': 'AND'
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, ['search', 'assets', '--jsonInput', 'search_params.json'])
            
            assert result.exit_code == 0
            assert 'Search completed. Found 1 assets' in result.output
            
            # Verify API was called with correct parameters
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert call_args['query'] == 'test model'
            assert call_args['operation'] == 'AND'
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_property_filters(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test asset search with property filters."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_assetname": "filtered-asset",
                                "str_description": "training"
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            property_filters = '[{"propertyKey":"str_description","operator":"=","value":"training"}]'
            result = cli_runner.invoke(cli, [
                'search', 'assets', 
                '--property-filters', property_filters
            ])
            
            assert result.exit_code == 0
            assert 'Search completed. Found 1 assets' in result.output
            
            # Verify API was called with property filters
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert len(call_args['tokens']) == 1
            assert call_args['tokens'][0]['propertyKey'] == 'str_description'
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_no_results(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search with no results."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Mock API client with empty results
            mock_search_result = {
                "hits": {"hits": [], "total": {"value": 0}}
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'nonexistent'])
            
            assert result.exit_code == 0
            assert 'No assets found' in result.output
            assert 'Search completed. Found 0 assets' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_pagination(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search with pagination parameters."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_assetname": "paginated-asset",
                                "str_databaseid": "test-db"
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, [
                'search', 'assets', 
                '-q', 'test',
                '--from', '10',
                '--size', '50'
            ])
            
            assert result.exit_code == 0
            assert 'Search completed. Found 1 assets' in result.output
            
            # Verify pagination parameters
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert call_args['from'] == 10
            assert call_args['size'] == 50
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_search_disabled_error(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search disabled error when NOOPENSEARCH feature is enabled."""
        with search_command_mocks as mocks:
            # Mock feature check (search disabled)
            mock_is_feature_enabled.return_value = True
            
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'test'])
            
            assert result.exit_code == 1
            assert 'Search Disabled' in result.output
            assert 'Use \'vamscli assets list\'' in result.output
    
    def test_assets_no_setup(self, cli_runner, search_no_setup_mocks):
        """Test assets command without setup."""
        with search_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'test'])
            
            assert result.exit_code == 1
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_sort_validation(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search sort option validation."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Test conflicting sort options
            result = cli_runner.invoke(cli, [
                'search', 'assets', 
                '-q', 'test',
                '--sort-desc',
                '--sort-asc'
            ])
            
            assert result.exit_code == 1
            assert 'Cannot specify both --sort-desc and --sort-asc' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_invalid_property_filters(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search with invalid property filters."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Test invalid JSON
            result = cli_runner.invoke(cli, [
                'search', 'assets', 
                '--property-filters', 'invalid json'
            ])
            
            assert result.exit_code == 1
            assert 'Property Filter Error' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_api_error(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search API error handling."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Mock API client with error
            mocks['api_client'].search_query.side_effect = APIError("Search service unavailable")
            
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'test'])
            
            assert result.exit_code == 1
            assert 'API Error' in result.output
            assert 'Search service unavailable' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_json_input_file_not_found(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search with missing JSON input file."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            with patch('builtins.open', side_effect=FileNotFoundError("File not found")):
                result = cli_runner.invoke(cli, ['search', 'assets', '--jsonInput', 'missing.json'])
            
            assert result.exit_code == 1
            assert 'JSON Input Error' in result.output
            assert 'not found' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_json_input_invalid_json(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search with invalid JSON input file."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            with patch('builtins.open', mock_open(read_data='invalid json')):
                result = cli_runner.invoke(cli, ['search', 'assets', '--jsonInput', 'invalid.json'])
            
            assert result.exit_code == 1
            assert 'JSON Input Error' in result.output
            assert 'Invalid JSON' in result.output


class TestSearchFilesCommand:
    """Test search files command."""
    
    def test_files_help(self, cli_runner):
        """Test files command help."""
        result = cli_runner.invoke(cli, ['search', 'files', '--help'])
        assert result.exit_code == 0
        assert 'Search files using OpenSearch' in result.output
        assert '--file-ext' in result.output
        assert '--asset-type' in result.output
        assert '--query' in result.output
        assert '--database' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_files_success(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test successful file search."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Mock file search results
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_filename": "model.gltf",
                                "str_assetname": "test-asset-001",
                                "str_databaseid": "test-db",
                                "str_key": "test-asset-001/model.gltf",
                                "str_fileext": "gltf",
                                "num_size": 2048000
                            },
                            "_score": 0.92
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, ['search', 'files', '--file-ext', 'gltf'])
            
            assert result.exit_code == 0
            assert 'Search Results (1 found)' in result.output
            assert 'model.gltf' in result.output
            assert 'Search completed. Found 1 files' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_files_with_filters(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test file search with various filters."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_filename": "texture.png",
                                "str_assetname": "test-asset",
                                "str_databaseid": "test-db",
                                "str_fileext": "png"
                            },
                            "_score": 0.88
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, [
                'search', 'files', 
                '-q', 'texture',
                '-d', 'test-db',
                '--file-ext', 'png',
                '--asset-type', '3d-model'
            ])
            
            assert result.exit_code == 0
            assert 'Search completed. Found 1 files' in result.output
            
            # Verify API was called with correct parameters
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert call_args['query'] == 'texture'
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_files_csv_output(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test file search with CSV output format."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "str_filename": "model.gltf",
                                "str_assetname": "test-asset",
                                "str_databaseid": "test-db",
                                "str_key": "test-asset/model.gltf",
                                "str_fileext": "gltf",
                                "num_size": 2048000
                            },
                            "_score": 0.92
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, ['search', 'files', '--file-ext', 'gltf', '--output-format', 'csv'])
            
            assert result.exit_code == 0
            assert 'File Name,Asset,Database,Path,Size (MB),Type,Score' in result.output
            assert 'model.gltf,test-asset,test-db' in result.output
    
    def test_files_no_setup(self, cli_runner, search_no_setup_mocks):
        """Test files command without setup."""
        with search_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['search', 'files', '--file-ext', 'gltf'])
            
            assert result.exit_code == 1
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output


class TestSearchMappingCommand:
    """Test search mapping command."""
    
    def test_mapping_help(self, cli_runner):
        """Test mapping command help."""
        result = cli_runner.invoke(cli, ['search', 'mapping', '--help'])
        assert result.exit_code == 0
        assert 'Get search index mapping' in result.output
        assert '--output-format' in result.output
        assert '--jsonOutput' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_mapping_success(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test successful search mapping retrieval."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Mock search mapping
            mock_search_mapping = {
                "mappings": {
                    "properties": {
                        "str_assetname": {"type": "text"},
                        "str_description": {"type": "text"},
                        "str_databaseid": {"type": "keyword"},
                        "str_assettype": {"type": "keyword"},
                        "list_tags": {"type": "keyword"},
                        "num_size": {"type": "long"},
                        "date_created": {"type": "date"}
                    }
                }
            }
            mocks['api_client'].get_search_mapping.return_value = mock_search_mapping
            
            result = cli_runner.invoke(cli, ['search', 'mapping'])
            
            assert result.exit_code == 0
            assert 'Available Search Fields' in result.output
            assert 'String Fields' in result.output
            assert 'str_assetname' in result.output
            assert 'Retrieved mapping for 7 search fields' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_mapping_csv_output(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search mapping with CSV output."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_mapping = {
                "mappings": {
                    "properties": {
                        "str_assetname": {"type": "text"},
                        "num_size": {"type": "long"}
                    }
                }
            }
            mocks['api_client'].get_search_mapping.return_value = mock_search_mapping
            
            result = cli_runner.invoke(cli, ['search', 'mapping', '--output-format', 'csv'])
            
            assert result.exit_code == 0
            assert 'Field Name,Field Type,Display Name' in result.output
            assert 'str_assetname,text,Str Assetname' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_mapping_json_output(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search mapping with JSON output."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_mapping = {
                "mappings": {
                    "properties": {
                        "str_assetname": {"type": "text"}
                    }
                }
            }
            mocks['api_client'].get_search_mapping.return_value = mock_search_mapping
            
            result = cli_runner.invoke(cli, ['search', 'mapping', '--jsonOutput'])
            
            assert result.exit_code == 0
            
            # Parse JSON output
            json_output = json.loads(result.output)
            assert 'mappings' in json_output
            assert 'properties' in json_output['mappings']
    
    def test_mapping_no_setup(self, cli_runner, search_no_setup_mocks):
        """Test mapping command without setup."""
        with search_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['search', 'mapping'])
            
            assert result.exit_code == 1
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output


class TestSearchCommandsIntegration:
    """Test integration scenarios for search commands."""
    
    def test_search_help(self, cli_runner):
        """Test search group help."""
        result = cli_runner.invoke(cli, ['search', '--help'])
        assert result.exit_code == 0
        assert 'Search assets and files using OpenSearch' in result.output
        assert 'NOOPENSEARCH feature is enabled' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_authentication_error_handling(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test authentication error handling."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mocks['api_client'].search_query.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'test'])
            
            assert result.exit_code == 1
            assert 'Authentication Error' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_search_query_error_handling(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search query error handling."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mocks['api_client'].search_query.side_effect = SearchQueryError("Invalid search query")
            
            result = cli_runner.invoke(cli, ['search', 'assets', '-q', 'test'])
            
            assert result.exit_code == 1
            assert 'Search Query Error' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_search_mapping_error_handling(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search mapping error handling."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mocks['api_client'].get_search_mapping.side_effect = SearchMappingError("Mapping unavailable")
            
            result = cli_runner.invoke(cli, ['search', 'mapping'])
            
            assert result.exit_code == 1
            assert 'Search Mapping Error' in result.output


class TestSearchUtilities:
    """Test search utility functions."""
    
    def test_parse_tags_list(self):
        """Test tag list parsing."""
        from vamscli.commands.search import _parse_tags_list
        
        # Test normal case
        result = _parse_tags_list("tag1,tag2,tag3")
        assert result == ["tag1", "tag2", "tag3"]
        
        # Test with spaces
        result = _parse_tags_list("tag1, tag2 , tag3")
        assert result == ["tag1", "tag2", "tag3"]
        
        # Test empty string
        result = _parse_tags_list("")
        assert result == []
        
        # Test None
        result = _parse_tags_list(None)
        assert result == []
    
    def test_parse_property_filters_valid(self):
        """Test valid property filter parsing."""
        from vamscli.commands.search import _parse_property_filters
        
        filters_json = '[{"propertyKey":"str_description","operator":"=","value":"test"}]'
        result = _parse_property_filters(filters_json)
        
        assert len(result) == 1
        assert result[0]['propertyKey'] == 'str_description'
        assert result[0]['operator'] == '='
        assert result[0]['value'] == 'test'
    
    def test_parse_property_filters_invalid(self):
        """Test invalid property filter parsing."""
        from vamscli.commands.search import _parse_property_filters
        from vamscli.utils.exceptions import InvalidSearchParametersError
        
        # Test invalid JSON
        with pytest.raises(InvalidSearchParametersError, match="Invalid JSON"):
            _parse_property_filters('invalid json')
        
        # Test non-array JSON
        with pytest.raises(InvalidSearchParametersError, match="must be a JSON array"):
            _parse_property_filters('{"not": "array"}')
        
        # Test missing required fields
        with pytest.raises(InvalidSearchParametersError, match="missing required field"):
            _parse_property_filters('[{"propertyKey":"test"}]')
    
    def test_build_search_request_asset(self):
        """Test building search request for assets."""
        from vamscli.commands.search import _build_search_request
        
        result = _build_search_request(
            search_type="asset",
            query="test query",
            database="test-db",
            operation="AND",
            sort_field="str_assetname",
            sort_desc=True,
            from_offset=10,
            size=50,
            asset_type="3d-model",
            tags=["test", "model"]
        )
        
        assert result['query'] == 'test query'
        assert result['operation'] == 'AND'
        assert result['from'] == 10
        assert result['size'] == 50
        assert result['sort'][0]['str_assetname.raw']['order'] == 'desc'
        
        # Check filters
        filter_queries = [f['query_string']['query'] for f in result['filters']]
        assert any('_rectype:("asset")' in q for q in filter_queries)
        assert any('str_databaseid:("test-db")' in q for q in filter_queries)
        assert any('str_assettype:("3d-model")' in q for q in filter_queries)
        assert any('list_tags:("test" OR "model")' in q for q in filter_queries)
    
    def test_build_search_request_file(self):
        """Test building search request for files."""
        from vamscli.commands.search import _build_search_request
        
        result = _build_search_request(
            search_type="file",
            query="texture",
            file_ext="png",
            sort_field="str_filename"
        )
        
        assert result['query'] == 'texture'
        assert result['sort'][0]['str_filename.raw']['order'] == 'asc'
        
        # Check filters
        filter_queries = [f['query_string']['query'] for f in result['filters']]
        assert any('_rectype:("s3object")' in q for q in filter_queries)
        assert any('str_fileext:("png")' in q for q in filter_queries)
    
    def test_format_search_results_table_assets(self):
        """Test formatting asset search results as table."""
        from vamscli.commands.search import _format_search_results_table
        
        mock_results = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "str_assetname": "test-asset",
                            "str_databaseid": "test-db",
                            "str_assettype": "3d-model",
                            "str_description": "Test description",
                            "list_tags": ["test", "model"]
                        },
                        "_score": 0.95
                    }
                ],
                "total": {"value": 1}
            }
        }
        
        result = _format_search_results_table(mock_results, "asset")
        assert "Search Results (1 found)" in result
        assert "Asset: test-asset" in result
        assert "Database: test-db" in result
        assert "Type: 3d-model" in result
        assert "Tags: test, model" in result
        assert "Score: 0.95" in result
    
    def test_format_search_results_table_files(self):
        """Test formatting file search results as table."""
        from vamscli.commands.search import _format_search_results_table
        
        mock_results = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "str_filename": "model.gltf",
                            "str_assetname": "test-asset",
                            "str_databaseid": "test-db",
                            "str_key": "test-asset/model.gltf",
                            "str_fileext": "gltf",
                            "num_size": 2048000
                        },
                        "_score": 0.92
                    }
                ],
                "total": {"value": 1}
            }
        }
        
        result = _format_search_results_table(mock_results, "file")
        assert "Search Results (1 found)" in result
        assert "File: model.gltf" in result
        assert "Asset: test-asset" in result
        assert "Path: test-asset/model.gltf" in result
        assert "Size: 1.95 MB" in result
        assert "Type: gltf" in result
    
    def test_format_search_results_csv_assets(self):
        """Test formatting asset search results as CSV."""
        from vamscli.commands.search import _format_search_results_csv
        
        mock_results = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "str_assetname": "test-asset",
                            "str_databaseid": "test-db",
                            "str_assettype": "3d-model",
                            "str_description": "Test description",
                            "list_tags": ["test", "model"]
                        },
                        "_score": 0.95
                    }
                ],
                "total": {"value": 1}
            }
        }
        
        result = _format_search_results_csv(mock_results, "asset")
        lines = [line.strip() for line in result.strip().split('\n')]
        assert lines[0] == 'Asset Name,Database,Type,Description,Tags,Score'
        assert 'test-asset,test-db,3d-model,Test description,"test, model",0.95' in lines[1]
    
    def test_format_search_results_json_assets(self):
        """Test formatting asset search results as JSON."""
        from vamscli.commands.search import _format_search_results_json
        
        mock_results = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "str_assetname": "test-asset",
                            "str_databaseid": "test-db",
                            "str_assettype": "3d-model",
                            "str_description": "Test description",
                            "list_tags": ["test", "model"]
                        },
                        "_score": 0.95
                    }
                ],
                "total": {"value": 1}
            }
        }
        
        result = _format_search_results_json(mock_results, "asset")
        json_data = json.loads(result)
        
        assert len(json_data) == 1
        assert json_data[0]['assetName'] == 'test-asset'
        assert json_data[0]['database'] == 'test-db'
        assert json_data[0]['type'] == '3d-model'
        assert json_data[0]['tags'] == ['test', 'model']
        assert json_data[0]['score'] == 0.95
    
    def test_format_mapping_table(self):
        """Test formatting search mapping as table."""
        from vamscli.commands.search import _format_mapping_table
        
        mock_mapping = {
            "mappings": {
                "properties": {
                    "str_assetname": {"type": "text"},
                    "num_size": {"type": "long"},
                    "date_created": {"type": "date"},
                    "bool_active": {"type": "boolean"}
                }
            }
        }
        
        result = _format_mapping_table(mock_mapping)
        assert "Available Search Fields" in result
        assert "String Fields:" in result
        assert "Numeric Fields:" in result
        assert "Date Fields:" in result
        assert "Boolean Fields:" in result
        assert "str_assetname" in result
        assert "num_size" in result
    
    def test_format_mapping_csv(self):
        """Test formatting search mapping as CSV."""
        from vamscli.commands.search import _format_mapping_csv
        
        mock_mapping = {
            "mappings": {
                "properties": {
                    "str_assetname": {"type": "text"},
                    "num_size": {"type": "long"}
                }
            }
        }
        
        result = _format_mapping_csv(mock_mapping)
        lines = [line.strip() for line in result.strip().split('\n')]
        assert lines[0] == 'Field Name,Field Type,Display Name'
        assert 'num_size,long,Num Size' in result
        assert 'str_assetname,text,Str Assetname' in result


if __name__ == '__main__':
    pytest.main([__file__])
