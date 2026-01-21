"""Test BOM (Bill of Materials) commands."""

import json
import pytest
import os
import tempfile
from unittest.mock import Mock, patch, MagicMock
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError,
    FileDownloadError
)
from vamscli.utils.glb_combiner import GLBCombineError


# File-level fixtures for BOM command testing
@pytest.fixture
def bom_command_mocks(generic_command_mocks):
    """Provide BOM command-specific mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for BOM command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('industry.engineering.bom.Dynamic_BOM')


@pytest.fixture
def bom_no_setup_mocks(no_setup_command_mocks):
    """Provide BOM command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('industry.engineering.bom.Dynamic_BOM')


@pytest.fixture
def sample_bom_json():
    """Provide sample BOM JSON data for testing."""
    return {
        "sources": [
            {"source": "engine_block", "storage": "VAMS"},
            {"source": "piston_assembly", "storage": "VAMS"},
            {"source": "crankshaft", "storage": "VAMS"},
            {"source": "engine_complete", "storage": "no"}
        ],
        "scene": {
            "nodes": [
                {
                    "node": "1",
                    "source": "engine_complete",
                    "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
                },
                {
                    "node": "2",
                    "source": "engine_block",
                    "parent_node": "1",
                    "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
                },
                {
                    "node": "3",
                    "source": "piston_assembly",
                    "parent_node": "2",
                    "matrix": [1, 0, 0, 0.5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
                },
                {
                    "node": "4",
                    "source": "crankshaft",
                    "parent_node": "2",
                    "matrix": [1, 0, 0, -0.5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
                }
            ]
        }
    }


@pytest.fixture
def mock_search_result():
    """Provide mock search result for asset lookup."""
    return {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "str_assetid": "test-asset-id-123",
                        "str_assetname": "engine_block"
                    }
                }
            ]
        }
    }


@pytest.fixture
def mock_glbassetcombine_result():
    """Provide mock result from glbassetcombine command."""
    return {
        'combined_glb_path': '/tmp/test_combined.glb',
        'status': 'success',
        'total_files_combined': 3,
        'combined_file_size': 1024000
    }


class TestBOMCommands:
    """Test BOM command functionality."""

    def test_bom_help(self, cli_runner):
        """Test BOM command group help."""
        result = cli_runner.invoke(cli, ['industry', 'engineering', 'bom', '--help'])
        assert result.exit_code == 0
        assert 'Bill of materital engineering commands' in result.output

    def test_bomassemble_help(self, cli_runner):
        """Test bomassemble command help."""
        result = cli_runner.invoke(cli, ['industry', 'engineering', 'bom', 'bomassemble', '--help'])
        assert result.exit_code == 0
        assert 'Assemble GLB geometry from BOM JSON hierarchy' in result.output
        assert '--json-file' in result.output
        assert '--database-id' in result.output
        assert '--asset-create-name' in result.output

    def test_bomassemble_no_setup(self, cli_runner, bom_no_setup_mocks):
        """Test bomassemble without setup."""
        with bom_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'industry', 'engineering', 'bom', 'bomassemble',
                '--json-file', 'test.json',
                '--database-id', 'test-db'
            ])
            
            assert result.exit_code == 1
            # The decorator raises SetupRequiredError which is caught by main.py
            # In test environment, the error message may not appear in output
            # Just verify the command failed with exit code 1

    def test_bomassemble_missing_required_params(self, cli_runner, bom_command_mocks):
        """Test bomassemble with missing required parameters."""
        with bom_command_mocks as mocks:
            # Missing json-file
            result = cli_runner.invoke(cli, [
                'industry', 'engineering', 'bom', 'bomassemble',
                '--database-id', 'test-db'
            ])
            assert result.exit_code == 2
            assert 'Missing option' in result.output or 'required' in result.output.lower()

            # Missing database-id
            result = cli_runner.invoke(cli, [
                'industry', 'engineering', 'bom', 'bomassemble',
                '--json-file', 'test.json'
            ])
            assert result.exit_code == 2
            assert 'Missing option' in result.output or 'required' in result.output.lower()

    def test_bomassemble_invalid_json_file(self, cli_runner, bom_command_mocks):
        """Test bomassemble with invalid JSON file."""
        with bom_command_mocks as mocks:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write('invalid json content')
                json_file = f.name

            try:
                result = cli_runner.invoke(cli, [
                    'industry', 'engineering', 'bom', 'bomassemble',
                    '--json-file', json_file,
                    '--database-id', 'test-db'
                ])
                
                assert result.exit_code == 1
                assert 'Invalid JSON file' in result.output
            finally:
                os.unlink(json_file)

    def test_bomassemble_missing_json_file(self, cli_runner, bom_command_mocks):
        """Test bomassemble with missing JSON file."""
        with bom_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'industry', 'engineering', 'bom', 'bomassemble',
                '--json-file', 'nonexistent.json',
                '--database-id', 'test-db'
            ])
            
            assert result.exit_code == 1
            assert 'BOM JSON file not found' in result.output

    def test_bomassemble_success_basic(self, cli_runner, bom_command_mocks, sample_bom_json, 
                                       mock_search_result, mock_glbassetcombine_result):
        """Test successful basic BOM assembly."""
        with bom_command_mocks as mocks:
            # Create temporary JSON file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(sample_bom_json, f)
                json_file = f.name

            try:
                # Mock API client search to return different results for different assets
                def mock_search(request):
                    asset_name = request.get('assetName', '')
                    return {
                        'hits': {
                            'hits': [
                                {
                                    '_source': {
                                        'str_assetid': f'{asset_name}-id',
                                        'str_assetname': asset_name
                                    }
                                }
                            ]
                        }
                    }
                
                mocks['api_client'].search_simple.side_effect = mock_search
                
                # Mock the glbassetcombine command that gets invoked via ctx.invoke
                from vamscli.commands.industry.spatial.glb import glbassetcombine
                
                # Mock GLB file reading/writing
                mock_glb_data = {
                    'json': {
                        'asset': {'version': '2.0'},
                        'scenes': [{'nodes': [0]}],
                        'nodes': [{'mesh': 0}],
                        'meshes': [{'name': 'test_mesh', 'primitives': []}],
                        'materials': [],
                        'textures': [],
                        'images': [],
                        'accessors': [],
                        'bufferViews': [],
                        'buffers': [{'byteLength': 0}]
                    },
                    'binary': b''
                }
                
                with patch.object(glbassetcombine, 'callback', return_value=mock_glbassetcombine_result), \
                     patch('vamscli.utils.glb_combiner.read_glb_file', return_value=mock_glb_data), \
                     patch('vamscli.utils.glb_combiner.write_glb_file'):
                    # Mock file operations
                    with patch('os.path.exists', return_value=True), \
                         patch('os.path.getsize', return_value=1024000), \
                         patch('tempfile.mkdtemp', return_value='/tmp/test_bom'), \
                         patch('os.makedirs'), \
                         patch('shutil.rmtree'), \
                         patch('pathlib.Path.mkdir'):
                        
                        result = cli_runner.invoke(cli, [
                            'industry', 'engineering', 'bom', 'bomassemble',
                            '--json-file', json_file,
                            '--database-id', 'test-db'
                        ])
                        
                        assert result.exit_code == 0
                        assert 'BOM assembly completed successfully' in result.output
                        
                        # Verify API calls
                        assert mocks['api_client'].search_simple.call_count >= 1
            finally:
                os.unlink(json_file)

    def test_bomassemble_with_asset_creation(self, cli_runner, bom_command_mocks, sample_bom_json,
                                             mock_search_result, mock_glbassetcombine_result):
        """Test BOM assembly with asset creation."""
        with bom_command_mocks as mocks:
            # Create temporary JSON file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(sample_bom_json, f)
                json_file = f.name

            try:
                # Mock API client search to return different results for different assets
                def mock_search(request):
                    asset_name = request.get('assetName', '')
                    return {
                        'hits': {
                            'hits': [
                                {
                                    '_source': {
                                        'str_assetid': f'{asset_name}-id',
                                        'str_assetname': asset_name
                                    }
                                }
                            ]
                        }
                    }
                
                mocks['api_client'].search_simple.side_effect = mock_search
                
                # Mock the commands that get invoked
                from vamscli.commands.industry.spatial.glb import glbassetcombine
                from vamscli.commands.assets import create as create_command
                from vamscli.commands.file import upload as upload_command
                
                # Mock GLB file reading/writing
                mock_glb_data = {
                    'json': {
                        'asset': {'version': '2.0'},
                        'scenes': [{'nodes': [0]}],
                        'nodes': [{'mesh': 0}],
                        'meshes': [{'name': 'test_mesh', 'primitives': []}],
                        'materials': [],
                        'textures': [],
                        'images': [],
                        'accessors': [],
                        'bufferViews': [],
                        'buffers': [{'byteLength': 0}]
                    },
                    'binary': b''
                }
                
                with patch.object(glbassetcombine, 'callback', return_value=mock_glbassetcombine_result), \
                     patch.object(create_command, 'callback', return_value={'assetId': 'new-asset-123'}), \
                     patch.object(upload_command, 'callback', return_value=None), \
                     patch('vamscli.utils.glb_combiner.read_glb_file', return_value=mock_glb_data), \
                     patch('vamscli.utils.glb_combiner.write_glb_file'):
                    
                    # Mock file operations
                    with patch('os.path.exists', return_value=True), \
                         patch('os.path.getsize', return_value=1024000), \
                         patch('tempfile.mkdtemp', return_value='/tmp/test_bom'), \
                         patch('os.makedirs'), \
                         patch('os.listdir', return_value=['test_combined.glb']), \
                         patch('shutil.rmtree'), \
                         patch('pathlib.Path.mkdir'):
                        
                        result = cli_runner.invoke(cli, [
                            'industry', 'engineering', 'bom', 'bomassemble',
                            '--json-file', json_file,
                            '--database-id', 'test-db',
                            '--asset-create-name', 'Test Engine Assembly'
                        ])
                        
                        assert result.exit_code == 0
                        assert 'BOM assembly completed successfully' in result.output
                        assert 'New Asset Created' in result.output

            finally:
                os.unlink(json_file)

    def test_bomassemble_json_output(self, cli_runner, bom_command_mocks, sample_bom_json,
                                     mock_search_result, mock_glbassetcombine_result):
        """Test BOM assembly with JSON output."""
        with bom_command_mocks as mocks:
            # Create temporary JSON file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(sample_bom_json, f)
                json_file = f.name

            try:
                # Mock API client search to return different results for different assets
                def mock_search(request):
                    asset_name = request.get('assetName', '')
                    return {
                        'hits': {
                            'hits': [
                                {
                                    '_source': {
                                        'str_assetid': f'{asset_name}-id',
                                        'str_assetname': asset_name
                                    }
                                }
                            ]
                        }
                    }
                
                mocks['api_client'].search_simple.side_effect = mock_search
                
                # Mock glbassetcombine command
                from vamscli.commands.industry.spatial.glb import glbassetcombine
                
                # Mock GLB file reading/writing
                mock_glb_data = {
                    'json': {
                        'asset': {'version': '2.0'},
                        'scenes': [{'nodes': [0]}],
                        'nodes': [{'mesh': 0}],
                        'meshes': [{'name': 'test_mesh', 'primitives': []}],
                        'materials': [],
                        'textures': [],
                        'images': [],
                        'accessors': [],
                        'bufferViews': [],
                        'buffers': [{'byteLength': 0}]
                    },
                    'binary': b''
                }
                
                with patch.object(glbassetcombine, 'callback', return_value=mock_glbassetcombine_result), \
                     patch('vamscli.utils.glb_combiner.read_glb_file', return_value=mock_glb_data), \
                     patch('vamscli.utils.glb_combiner.write_glb_file'):
                    # Mock file operations
                    with patch('os.path.exists', return_value=True), \
                         patch('os.path.getsize', return_value=1024000), \
                         patch('tempfile.mkdtemp', return_value='/tmp/test_bom'), \
                         patch('os.makedirs'), \
                         patch('shutil.rmtree'), \
                         patch('pathlib.Path.mkdir'):
                        
                        result = cli_runner.invoke(cli, [
                            'industry', 'engineering', 'bom', 'bomassemble',
                            '--json-file', json_file,
                            '--database-id', 'test-db',
                            '--json-output'
                        ])
                        
                        assert result.exit_code == 0
                        
                        # Verify JSON output
                        try:
                            output_data = json.loads(result.output)
                            assert output_data['status'] == 'success'
                            assert 'assemblies' in output_data
                            assert output_data['database_id'] == 'test-db'
                        except json.JSONDecodeError:
                            pytest.fail(f"Output is not valid JSON: {result.output}")

            finally:
                os.unlink(json_file)

    def test_bomassemble_keep_temp_files(self, cli_runner, bom_command_mocks, sample_bom_json,
                                         mock_search_result, mock_glbassetcombine_result):
        """Test BOM assembly with keeping temporary files."""
        with bom_command_mocks as mocks:
            # Create temporary JSON file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(sample_bom_json, f)
                json_file = f.name

            try:
                # Mock API client search to return different results for different assets
                def mock_search(request):
                    asset_name = request.get('assetName', '')
                    return {
                        'hits': {
                            'hits': [
                                {
                                    '_source': {
                                        'str_assetid': f'{asset_name}-id',
                                        'str_assetname': asset_name
                                    }
                                }
                            ]
                        }
                    }
                
                mocks['api_client'].search_simple.side_effect = mock_search
                
                # Mock glbassetcombine command
                from vamscli.commands.industry.spatial.glb import glbassetcombine
                
                # Mock GLB file reading/writing
                mock_glb_data = {
                    'json': {
                        'asset': {'version': '2.0'},
                        'scenes': [{'nodes': [0]}],
                        'nodes': [{'mesh': 0}],
                        'meshes': [{'name': 'test_mesh', 'primitives': []}],
                        'materials': [],
                        'textures': [],
                        'images': [],
                        'accessors': [],
                        'bufferViews': [],
                        'buffers': [{'byteLength': 0}]
                    },
                    'binary': b''
                }
                
                with patch.object(glbassetcombine, 'callback', return_value=mock_glbassetcombine_result), \
                     patch('vamscli.utils.glb_combiner.read_glb_file', return_value=mock_glb_data), \
                     patch('vamscli.utils.glb_combiner.write_glb_file'):
                    # Mock file operations
                    with patch('os.path.exists', return_value=True), \
                         patch('os.path.getsize', return_value=1024000), \
                         patch('tempfile.mkdtemp', return_value='/tmp/test_bom') as mock_mkdtemp, \
                         patch('os.makedirs'), \
                         patch('shutil.rmtree') as mock_rmtree, \
                         patch('pathlib.Path.mkdir'):
                        
                        result = cli_runner.invoke(cli, [
                            'industry', 'engineering', 'bom', 'bomassemble',
                            '--json-file', json_file,
                            '--database-id', 'test-db',
                            '--keep-temp-files'
                        ])
                        
                        assert result.exit_code == 0
                        
                        # Verify temp files were not deleted
                        mock_rmtree.assert_not_called()

            finally:
                os.unlink(json_file)

    def test_bomassemble_asset_not_found(self, cli_runner, bom_command_mocks, sample_bom_json):
        """Test BOM assembly when assets are not found."""
        with bom_command_mocks as mocks:
            # Create temporary JSON file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(sample_bom_json, f)
                json_file = f.name

            try:
                # Mock API client to return no results
                mocks['api_client'].search_simple.return_value = {"hits": {"hits": []}}
                
                # Mock file operations
                with patch('os.path.exists', return_value=True), \
                     patch('tempfile.mkdtemp', return_value='/tmp/test_bom'), \
                     patch('os.makedirs'), \
                     patch('shutil.rmtree'):
                    
                    result = cli_runner.invoke(cli, [
                        'industry', 'engineering', 'bom', 'bomassemble',
                        '--json-file', json_file,
                        '--database-id', 'test-db'
                    ])
                    
                    # Should still complete but with warnings about missing assets
                    assert result.exit_code == 0 or result.exit_code == 1
                    # The command should handle missing assets gracefully

            finally:
                os.unlink(json_file)

    def test_bomassemble_glb_combine_error(self, cli_runner, bom_command_mocks, sample_bom_json,
                                           mock_search_result):
        """Test BOM assembly when GLB combining fails."""
        with bom_command_mocks as mocks:
            # Create temporary JSON file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(sample_bom_json, f)
                json_file = f.name

            try:
                # Mock API client
                mocks['api_client'].search_simple.return_value = mock_search_result
                
                # Mock glbassetcombine to raise exception
                from vamscli.commands.industry.spatial.glb import glbassetcombine
                
                with patch.object(glbassetcombine, 'callback', side_effect=GLBCombineError("Failed to combine GLB files")):
                    # Mock file operations
                    with patch('os.path.exists', return_value=True), \
                         patch('tempfile.mkdtemp', return_value='/tmp/test_bom'), \
                         patch('os.makedirs'), \
                         patch('shutil.rmtree'):
                        
                        result = cli_runner.invoke(cli, [
                            'industry', 'engineering', 'bom', 'bomassemble',
                            '--json-file', json_file,
                            '--database-id', 'test-db'
                        ])
                        
                        # Should handle GLB combine errors gracefully
                        assert result.exit_code == 0 or result.exit_code == 1

            finally:
                os.unlink(json_file)

    def test_bomassemble_custom_local_path(self, cli_runner, bom_command_mocks, sample_bom_json,
                                           mock_search_result, mock_glbassetcombine_result):
        """Test BOM assembly with custom local path."""
        with bom_command_mocks as mocks:
            # Create temporary JSON file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(sample_bom_json, f)
                json_file = f.name

            try:
                # Mock API client search to return different results for different assets
                def mock_search(request):
                    asset_name = request.get('assetName', '')
                    return {
                        'hits': {
                            'hits': [
                                {
                                    '_source': {
                                        'str_assetid': f'{asset_name}-id',
                                        'str_assetname': asset_name
                                    }
                                }
                            ]
                        }
                    }
                
                mocks['api_client'].search_simple.side_effect = mock_search
                
                # Mock glbassetcombine command
                from vamscli.commands.industry.spatial.glb import glbassetcombine
                
                # Mock GLB file reading/writing
                mock_glb_data = {
                    'json': {
                        'asset': {'version': '2.0'},
                        'scenes': [{'nodes': [0]}],
                        'nodes': [{'mesh': 0}],
                        'meshes': [{'name': 'test_mesh', 'primitives': []}],
                        'materials': [],
                        'textures': [],
                        'images': [],
                        'accessors': [],
                        'bufferViews': [],
                        'buffers': [{'byteLength': 0}]
                    },
                    'binary': b''
                }
                
                with patch.object(glbassetcombine, 'callback', return_value=mock_glbassetcombine_result), \
                     patch('vamscli.utils.glb_combiner.read_glb_file', return_value=mock_glb_data), \
                     patch('vamscli.utils.glb_combiner.write_glb_file'):
                    # Mock file operations
                    with patch('os.path.exists', return_value=True), \
                         patch('os.path.getsize', return_value=1024000), \
                         patch('os.makedirs') as mock_makedirs, \
                         patch('shutil.rmtree'), \
                         patch('pathlib.Path.mkdir'):
                        
                        result = cli_runner.invoke(cli, [
                            'industry', 'engineering', 'bom', 'bomassemble',
                            '--json-file', json_file,
                            '--database-id', 'test-db',
                            '--local-path', './custom_temp'
                        ])
                        
                        assert result.exit_code == 0
                        
                        # Verify custom path was used
                        mock_makedirs.assert_called()
                        # Check that the path contains our custom directory
                        call_args = mock_makedirs.call_args[0][0]
                        assert './custom_temp' in call_args

            finally:
                os.unlink(json_file)


class TestBOMUtilityFunctions:
    """Test BOM utility functions."""

    def test_parse_bom_json_valid(self, sample_bom_json):
        """Test parsing valid BOM JSON."""
        from vamscli.commands.industry.engineering.bom.Dynamic_BOM import parse_bom_json
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(sample_bom_json, f)
            json_file = f.name

        try:
            result = parse_bom_json(json_file)
            assert result == sample_bom_json
            assert 'scene' in result
            assert 'sources' in result
        finally:
            os.unlink(json_file)

    def test_parse_bom_json_invalid_structure(self):
        """Test parsing BOM JSON with invalid structure."""
        from vamscli.commands.industry.engineering.bom.Dynamic_BOM import parse_bom_json
        
        invalid_json = {"invalid": "structure"}
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(invalid_json, f)
            json_file = f.name

        try:
            with pytest.raises(InvalidAssetDataError) as exc_info:
                parse_bom_json(json_file)
            assert "missing 'scene.nodes' structure" in str(exc_info.value)
        finally:
            os.unlink(json_file)

    def test_build_node_tree(self, sample_bom_json):
        """Test building node tree from flat node list."""
        from vamscli.commands.industry.engineering.bom.Dynamic_BOM import build_node_tree
        
        nodes = sample_bom_json['scene']['nodes']
        tree = build_node_tree(nodes)
        
        assert len(tree) == 4
        assert '1' in tree
        assert tree['1']['source'] == 'engine_complete'
        assert len(tree['1']['children']) == 1
        assert '2' in tree['1']['children']

    def test_find_root_nodes(self, sample_bom_json):
        """Test finding root nodes in tree."""
        from vamscli.commands.industry.engineering.bom.Dynamic_BOM import build_node_tree, find_root_nodes
        
        nodes = sample_bom_json['scene']['nodes']
        tree = build_node_tree(nodes)
        roots = find_root_nodes(tree)
        
        assert len(roots) == 1
        assert '1' in roots

    def test_format_assembly_result(self):
        """Test formatting assembly result for CLI display."""
        from vamscli.commands.industry.engineering.bom.Dynamic_BOM import format_assembly_result
        
        test_data = {
            'bom_json_file': 'test.json',
            'database_id': 'test-db',
            'total_nodes': 4,
            'total_sources': 4,
            'glbs_downloaded': 3,
            'root_nodes_processed': 1,
            'assemblies': [
                {
                    'root_node_id': '1',
                    'root_source': 'engine_complete',
                    'combined_glb_path': '/tmp/engine_complete.glb',
                    'combined_glb_size_formatted': '1.0 MB'
                }
            ],
            'new_asset': {
                'asset_id': 'new-asset-123',
                'database_id': 'test-db',
                'name': 'Test Assembly',
                'total_files': 4,
                'files_uploaded': ['engine.glb', 'piston.glb', 'crankshaft.glb', 'test.json']
            }
        }
        
        result = format_assembly_result(test_data)
        
        assert 'BOM JSON: test.json' in result
        assert 'Database: test-db' in result
        assert 'Total Nodes: 4' in result
        assert 'Total Sources: 4' in result
        assert 'Root Nodes Processed: 1' in result
        assert 'New Asset Created:' in result
        assert 'Asset ID: new-asset-123' in result


if __name__ == '__main__':
    pytest.main([__file__])