"""Test search functionality - Dual-Index OpenSearch Support."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    SearchDisabledError, SearchUnavailableError, InvalidSearchParametersError,
    SearchQueryError, SearchMappingError, AuthenticationError, APIError, SetupRequiredError
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
        assert '--metadata-query' in result.output
        assert '--metadata-mode' in result.output
        assert '--explain-results' in result.output
        assert '--output-format' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_success(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test successful asset search."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Mock search results with dual-index format
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_index_type": "asset",
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
            assert 'Found 1 assets' in result.output
            assert 'Search completed. Found 1 assets' in result.output
            
            # Verify API call with new format
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert call_args['entityTypes'] == ['asset']
            assert call_args['query'] == 'test'
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_with_metadata_query(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test asset search with metadata query."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_index_type": "asset",
                            "_source": {
                                "str_assetname": "metadata-asset",
                                "str_databaseid": "test-db",
                                "MD_str_product": "Training"
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
                '--metadata-query', 'MD_str_product:Training',
                '--metadata-mode', 'both'
            ])
            
            assert result.exit_code == 0
            assert 'Search completed. Found 1 assets' in result.output
            
            # Verify API call includes metadata parameters
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert call_args['metadataQuery'] == 'MD_str_product:Training'
            assert call_args['metadataSearchMode'] == 'both'
    
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
                            "_index_type": "asset",
                            "_source": {
                                "str_assetname": "filtered-asset",
                                "str_databaseid": "test-db",
                                "str_assettype": "3d-model",
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
            assert call_args['entityTypes'] == ['asset']
            assert call_args['sort'] == [{'field': 'str_assetname', 'order': 'desc'}]
            assert len(call_args['filters']) == 3  # database, asset_type, and tags filters
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_assets_with_explain_results(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test asset search with explain results option."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_index_type": "asset",
                            "_source": {
                                "str_assetname": "explained-asset",
                                "str_databaseid": "test-db"
                            },
                            "_score": 0.95,
                            "explanation": {
                                "query_type": "general",
                                "index_type": "asset",
                                "matched_fields": ["str_assetname"]
                            }
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_query.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, [
                'search', 'assets', 
                '-q', 'test',
                '--explain-results'
            ])
            
            assert result.exit_code == 0
            
            # Verify explainResults parameter
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert call_args['explainResults'] is True
    
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
                            "_index_type": "asset",
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
            
            # Check that JSON output contains expected data
            # With --output-format json, the command outputs the raw API response
            assert '{' in result.output
            assert '"hits"' in result.output
            assert '_index_type' in result.output
            assert 'asset' in result.output
            assert 'test-asset' in result.output
    
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
            assert 'Found 0 assets' in result.output
            assert 'Search completed. Found 0 assets' in result.output
    
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
            # Global exception handler catches SetupRequiredError
            assert isinstance(result.exception, SetupRequiredError)


class TestSearchFilesCommand:
    """Test search files command."""
    
    def test_files_help(self, cli_runner):
        """Test files command help."""
        result = cli_runner.invoke(cli, ['search', 'files', '--help'])
        assert result.exit_code == 0
        assert 'Search files using OpenSearch' in result.output
        assert '--file-ext' in result.output
        assert '--query' in result.output
        assert '--database' in result.output
        assert '--metadata-query' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_files_success(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test successful file search."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Mock file search results with dual-index format
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_index_type": "file",
                            "_source": {
                                "str_key": "test-asset-001/model.gltf",
                                "str_assetname": "test-asset-001",
                                "str_databaseid": "test-db",
                                "str_fileext": "gltf",
                                "num_filesize": 2048000
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
            assert 'Found 1 files' in result.output
            assert 'Search completed. Found 1 files' in result.output
            
            # Verify API call with new format
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert call_args['entityTypes'] == ['file']
    
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
                            "_index_type": "file",
                            "_source": {
                                "str_key": "test-asset/texture.png",
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
                '--file-ext', 'png'
            ])
            
            assert result.exit_code == 0
            assert 'Search completed. Found 1 files' in result.output
            
            # Verify API was called with correct parameters
            mocks['api_client'].search_query.assert_called_once()
            call_args = mocks['api_client'].search_query.call_args[0][0]
            assert call_args['query'] == 'texture'
            assert call_args['entityTypes'] == ['file']
            assert len(call_args['filters']) == 2  # database and file_ext filters
    
    def test_files_no_setup(self, cli_runner, search_no_setup_mocks):
        """Test files command without setup."""
        with search_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['search', 'files', '--file-ext', 'gltf'])
            
            assert result.exit_code == 1
            # Global exception handler catches SetupRequiredError
            assert isinstance(result.exception, SetupRequiredError)


class TestSearchSimpleCommand:
    """Test search simple command."""
    
    def test_simple_help(self, cli_runner):
        """Test simple command help."""
        result = cli_runner.invoke(cli, ['search', 'simple', '--help'])
        assert result.exit_code == 0
        assert 'Simple search with user-friendly parameters' in result.output
        assert '--asset-name' in result.output
        assert '--asset-id' in result.output
        assert '--file-key' in result.output
        assert '--file-ext' in result.output
        assert '--metadata-key' in result.output
        assert '--metadata-value' in result.output
        assert '--entity-types' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_simple_success(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test successful simple search."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Mock simple search results
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_index_type": "asset",
                            "_source": {
                                "str_assetname": "training-model",
                                "str_databaseid": "test-db"
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_simple.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, ['search', 'simple', '-q', 'training'])
            
            assert result.exit_code == 0
            assert 'Found 1 results' in result.output
            assert 'Search completed. Found 1 results' in result.output
            
            # Verify API call
            mocks['api_client'].search_simple.assert_called_once()
            call_args = mocks['api_client'].search_simple.call_args[0][0]
            assert call_args['query'] == 'training'
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_simple_with_asset_filters(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test simple search with asset-specific filters."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_index_type": "asset",
                            "_source": {
                                "str_assetname": "specific-asset",
                                "str_assetid": "asset-123",
                                "str_assettype": "3d-model"
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_simple.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, [
                'search', 'simple', 
                '--asset-name', 'specific',
                '--asset-id', 'asset-123',
                '--asset-type', '3d-model',
                '--entity-types', 'asset'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call with SimpleSearchRequestModel parameters
            mocks['api_client'].search_simple.assert_called_once()
            call_args = mocks['api_client'].search_simple.call_args[0][0]
            assert call_args['assetName'] == 'specific'
            assert call_args['assetId'] == 'asset-123'
            assert call_args['assetType'] == '3d-model'
            assert call_args['entityTypes'] == ['asset']
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_simple_with_file_filters(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test simple search with file-specific filters."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_index_type": "file",
                            "_source": {
                                "str_key": "asset/model.gltf",
                                "str_fileext": "gltf"
                            },
                            "_score": 0.92
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_simple.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, [
                'search', 'simple', 
                '--file-key', 'model',
                '--file-ext', 'gltf',
                '--entity-types', 'file'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call
            mocks['api_client'].search_simple.assert_called_once()
            call_args = mocks['api_client'].search_simple.call_args[0][0]
            assert call_args['fileKey'] == 'model'
            assert call_args['fileExtension'] == 'gltf'
            assert call_args['entityTypes'] == ['file']
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_simple_with_metadata_filters(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test simple search with metadata filters."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_result = {
                "hits": {
                    "hits": [
                        {
                            "_index_type": "asset",
                            "_source": {
                                "str_assetname": "metadata-asset",
                                "MD_str_product": "Training"
                            },
                            "_score": 0.95
                        }
                    ],
                    "total": {"value": 1}
                }
            }
            mocks['api_client'].search_simple.return_value = mock_search_result
            
            result = cli_runner.invoke(cli, [
                'search', 'simple', 
                '--metadata-key', 'product',
                '--metadata-value', 'Training'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call
            mocks['api_client'].search_simple.assert_called_once()
            call_args = mocks['api_client'].search_simple.call_args[0][0]
            assert call_args['metadataKey'] == 'product'
            assert call_args['metadataValue'] == 'Training'
    
    def test_simple_no_setup(self, cli_runner, search_no_setup_mocks):
        """Test simple command without setup."""
        with search_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['search', 'simple', '-q', 'test'])
            
            assert result.exit_code == 1
            # Global exception handler catches SetupRequiredError
            assert isinstance(result.exception, SetupRequiredError)


class TestSearchMappingCommand:
    """Test search mapping command."""
    
    def test_mapping_help(self, cli_runner):
        """Test mapping command help."""
        result = cli_runner.invoke(cli, ['search', 'mapping', '--help'])
        assert result.exit_code == 0
        assert 'Get search index mapping' in result.output
        assert 'dual-index system' in result.output
        assert '--output-format' in result.output
        assert '--jsonOutput' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_mapping_success(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test successful search mapping retrieval."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            # Mock dual-index search mapping
            mock_search_mapping = {
                "mappings": {
                    "asset_index": {
                        "mappings": {
                            "properties": {
                                "str_assetname": {"type": "text"},
                                "str_description": {"type": "text"},
                                "str_assettype": {"type": "keyword"}
                            }
                        }
                    },
                    "file_index": {
                        "mappings": {
                            "properties": {
                                "str_key": {"type": "text"},
                                "str_fileext": {"type": "keyword"},
                                "num_filesize": {"type": "long"}
                            }
                        }
                    }
                }
            }
            mocks['api_client'].get_search_mapping.return_value = mock_search_mapping
            
            result = cli_runner.invoke(cli, ['search', 'mapping'])
            
            assert result.exit_code == 0
            assert 'Search mapping retrieved successfully' in result.output
            assert 'Retrieved search index mappings' in result.output
    
    @patch('vamscli.commands.search.is_feature_enabled')
    def test_mapping_json_output(self, mock_is_feature_enabled, cli_runner, search_command_mocks):
        """Test search mapping with JSON output."""
        with search_command_mocks as mocks:
            # Mock feature check (search enabled)
            mock_is_feature_enabled.return_value = False
            
            mock_search_mapping = {
                "mappings": {
                    "asset_index": {
                        "mappings": {
                            "properties": {
                                "str_assetname": {"type": "text"}
                            }
                        }
                    }
                }
            }
            mocks['api_client'].get_search_mapping.return_value = mock_search_mapping
            
            result = cli_runner.invoke(cli, ['search', 'mapping', '--jsonOutput'])
            
            assert result.exit_code == 0
            
            # Parse JSON output
            json_output = json.loads(result.output)
            assert 'mappings' in json_output
            assert 'asset_index' in json_output['mappings']
    
    def test_mapping_no_setup(self, cli_runner, search_no_setup_mocks):
        """Test mapping command without setup."""
        with search_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['search', 'mapping'])
            
            assert result.exit_code == 1
            # Global exception handler catches SetupRequiredError
            assert isinstance(result.exception, SetupRequiredError)


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
    
    def test_parse_entity_types_valid(self):
        """Test valid entity type parsing."""
        from vamscli.commands.search import _parse_entity_types
        
        # Test single type
        result = _parse_entity_types("asset")
        assert result == ["asset"]
        
        # Test multiple types
        result = _parse_entity_types("asset,file")
        assert result == ["asset", "file"]
        
        # Test with spaces
        result = _parse_entity_types("asset , file")
        assert result == ["asset", "file"]
        
        # Test empty string
        result = _parse_entity_types("")
        assert result == []
    
    def test_parse_entity_types_invalid(self):
        """Test invalid entity type parsing."""
        from vamscli.commands.search import _parse_entity_types
        from vamscli.utils.exceptions import InvalidSearchParametersError
        
        # Test invalid type
        with pytest.raises(InvalidSearchParametersError, match="Invalid entity types"):
            _parse_entity_types("invalid")
        
        # Test mixed valid and invalid
        with pytest.raises(InvalidSearchParametersError, match="Invalid entity types"):
            _parse_entity_types("asset,invalid,file")
    
    def test_build_sort_config(self):
        """Test sort configuration building."""
        from vamscli.commands.search import _build_sort_config
        
        # Test with field and descending
        result = _build_sort_config("str_assetname", True)
        assert result == [{"field": "str_assetname", "order": "desc"}]
        
        # Test with field and ascending
        result = _build_sort_config("str_assetname", False)
        assert result == [{"field": "str_assetname", "order": "asc"}]
        
        # Test without field (default to score)
        result = _build_sort_config(None, False)
        assert result == ["_score"]


class TestSearchCommandsIntegration:
    """Test integration scenarios for search commands."""
    
    def test_search_help(self, cli_runner):
        """Test search group help."""
        result = cli_runner.invoke(cli, ['search', '--help'])
        assert result.exit_code == 0
        assert 'Search assets and files using OpenSearch dual-index system' in result.output
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
            # Global exception handler catches AuthenticationError
            assert isinstance(result.exception, AuthenticationError)
    
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


if __name__ == '__main__':
    pytest.main([__file__])
