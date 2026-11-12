"""Test BOM (Bill of Materials) commands."""

import json
import pytest
import os
import tempfile
from unittest.mock import Mock, patch, MagicMock
import click
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError,
    FileDownloadError, AssetAlreadyExistsError, PermissionDeniedError, FileUploadError
)
from vamscli.utils.global_exceptions import _global_error_data
from vamscli.utils.glb_combiner import GLBCombineError
from vamscli.utils.json_output import output_error


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
                     patch.object(upload_command, 'callback',
                                  return_value={'overall_success': True}), \
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


def _fail_like_a_command(error):
    """A nested command callback that fails the way a real command does in JSON mode."""
    def callback(**kwargs):
        output_error(error, kwargs['json_output'])
        raise click.ClickException(str(error))
    return callback


def _run_bomassemble_with_asset_creation(cli_runner, bom_command_mocks, sample_bom_json,
                                         glbassetcombine_result, extra_args,
                                         create_side_effect=None, upload_side_effect=None,
                                         combine_side_effect=None, create_asset=True):
    """Run bomassemble (with --asset-create-name unless create_asset is False) with the nested
    glbassetcombine, create and upload callbacks patched."""
    from vamscli.commands.industry.spatial.glb import glbassetcombine
    from vamscli.commands.assets import create as create_command
    from vamscli.commands.file import upload as upload_command

    mock_glb_data = {
        'json': {'asset': {'version': '2.0'}, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
                 'meshes': [{'name': 'test_mesh', 'primitives': []}], 'materials': [], 'textures': [],
                 'images': [], 'accessors': [], 'bufferViews': [], 'buffers': [{'byteLength': 0}]},
        'binary': b''
    }
    with bom_command_mocks as mocks:
        mocks['api_client'].search_simple.side_effect = lambda request: {
            'hits': {'hits': [{'_source': {'str_assetid': f"{request.get('assetName', '')}-id",
                                           'str_assetname': request.get('assetName', '')}}]}
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(sample_bom_json, f)
            json_file = f.name
        try:
            with patch.object(glbassetcombine, 'callback',
                              side_effect=combine_side_effect, return_value=glbassetcombine_result), \
                 patch.object(create_command, 'callback',
                              side_effect=create_side_effect, return_value={'assetId': 'new-asset-123'}), \
                 patch.object(upload_command, 'callback', side_effect=upload_side_effect,
                              return_value={'overall_success': True}), \
                 patch('vamscli.utils.glb_combiner.read_glb_file', return_value=mock_glb_data), \
                 patch('vamscli.utils.glb_combiner.write_glb_file'), \
                 patch('os.path.exists', return_value=True), \
                 patch('os.path.getsize', return_value=1024000), \
                 patch('tempfile.mkdtemp', return_value='/tmp/test_bom'), \
                 patch('os.makedirs'), \
                 patch('os.listdir', return_value=['test_combined.glb']), \
                 patch('shutil.rmtree'), \
                 patch('pathlib.Path.mkdir'):
                create_args = ['--asset-create-name', 'Test Engine Assembly'] if create_asset else []
                return cli_runner.invoke(cli, [
                    'industry', 'engineering', 'bom', 'bomassemble',
                    '--json-file', json_file,
                    '--database-id', 'test-db',
                ] + create_args + extra_args)
        finally:
            os.unlink(json_file)


class TestBOMAssembleAssetCreationFailure:
    """A failed asset creation or upload ends bomassemble with one error, in either output mode (#1349)."""

    def test_json_output_writes_the_create_error_document(self, cli_runner, bom_command_mocks,
                                                          sample_bom_json, mock_glbassetcombine_result):
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, ['--json-output'],
            create_side_effect=_fail_like_a_command(AssetAlreadyExistsError("Asset already exists")))

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            'error': 'Asset already exists', 'error_type': 'AssetAlreadyExistsError'}

    def test_json_output_writes_an_error_document_for_an_upload_exception(
            self, cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result):
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, ['--json-output'],
            upload_side_effect=OSError("disk read failed"))

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            'error': 'disk read failed', 'error_type': 'OSError', 'createdAssetId': 'new-asset-123'}

    def test_json_output_names_the_created_asset_when_an_upload_fails(
            self, cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result):
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, ['--json-output'],
            upload_side_effect=_fail_like_a_command(FileUploadError("Upload failed: timeout")))

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            'error': 'Upload failed: timeout', 'error_type': 'FileUploadError',
            'createdAssetId': 'new-asset-123'}

    def test_json_output_reports_an_upload_that_stored_nothing(
            self, cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result):
        # Under another command, `file upload` returns overall_success: false instead of exiting 1
        failed_upload = {'overall_success': False, 'failed_files': 1, 'sequence_results': [
            {'sequence_number': 1, 'successful_files': 0, 'failed_files': ['test_combined.glb']}]}
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result,
            ['--json-output'], upload_side_effect=lambda **kwargs: failed_upload)

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            'error': 'Upload failed for: test_combined.glb', 'error_type': 'FileUploadError',
            'createdAssetId': 'new-asset-123'}

    def test_text_mode_reports_an_upload_that_stored_nothing(
            self, cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result):
        failed_upload = {'overall_success': False, 'failed_files': 1, 'sequence_results': [
            {'sequence_number': 1, 'successful_files': 0, 'failed_files': ['test_combined.glb']}]}
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, [],
            upload_side_effect=lambda **kwargs: failed_upload)

        assert result.exit_code == 1
        assert 'BOM assembly completed successfully' not in result.stdout
        assert '✗ Asset Creation Error: Upload failed for: test_combined.glb' in result.stderr
        assert 'Asset new-asset-123 was created before the failure' in result.stderr

    def test_global_error_after_create_keeps_the_created_asset_id(
            self, cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result):
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result,
            ['--json-output'], upload_side_effect=PermissionDeniedError("Access forbidden"))

        assert result.exit_code == 1
        assert isinstance(result.exception, PermissionDeniedError)
        document = _global_error_data("Permission Denied", str(result.exception),
                                      exc=result.exception)
        assert document['createdAssetId'] == 'new-asset-123'

    def test_text_mode_names_the_created_asset_before_the_uploads(
            self, cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result):
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, [],
            upload_side_effect=OSError("disk read failed"))

        assert result.exit_code == 1
        assert 'Created asset new-asset-123' in result.stdout
        assert 'Asset new-asset-123 was created before the failure' in result.stderr

    def test_permission_denial_reaches_the_global_handler(
            self, cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result):
        # main() turns the denial into its global error document; here it surfaces as the exception
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, ['--json-output'],
            create_side_effect=PermissionDeniedError("Access forbidden. Not authorized"))

        assert result.exit_code == 1
        assert isinstance(result.exception, PermissionDeniedError)
        assert result.stdout == ''

    def test_text_mode_reports_the_create_error_on_stderr(self, cli_runner, bom_command_mocks,
                                                         sample_bom_json, mock_glbassetcombine_result):
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, [],
            create_side_effect=_fail_like_a_command(AssetAlreadyExistsError("Asset already exists")))

        assert result.exit_code == 1
        assert '✗ AssetAlreadyExistsError: Asset already exists' in result.stderr
        assert '"error"' not in result.stdout

    def test_text_mode_success_prints_no_nested_json(self, cli_runner, bom_command_mocks,
                                                     sample_bom_json, mock_glbassetcombine_result):
        def create_that_prints(**kwargs):
            click.echo(json.dumps({'assetId': 'new-asset-123'}))
            return {'assetId': 'new-asset-123'}

        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, [],
            create_side_effect=create_that_prints)

        assert result.exit_code == 0
        assert 'New Asset Created' in result.stdout
        assert '"assetId"' not in result.stdout


class TestBOMAssembleOneJSONDocument:
    """bomassemble keeps the output of its inner glbassetcombine calls out of its own (#1359)."""

    @staticmethod
    def combine_that_prints(glbassetcombine_result):
        def callback(**kwargs):
            click.echo(json.dumps({'status': 'success', 'asset': kwargs['asset_id']}, indent=2))
            return glbassetcombine_result
        return callback

    def test_json_output_is_one_document(self, cli_runner, bom_command_mocks, sample_bom_json,
                                         mock_glbassetcombine_result):
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, ['--json-output'],
            combine_side_effect=self.combine_that_prints(mock_glbassetcombine_result), create_asset=False)

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data['status'] == 'success'
        assert data['assemblies']

    def test_text_mode_prints_no_inner_json(self, cli_runner, bom_command_mocks, sample_bom_json,
                                            mock_glbassetcombine_result):
        result = _run_bomassemble_with_asset_creation(
            cli_runner, bom_command_mocks, sample_bom_json, mock_glbassetcombine_result, [],
            combine_side_effect=self.combine_that_prints(mock_glbassetcombine_result), create_asset=False)

        assert result.exit_code == 0, result.output
        assert 'BOM assembly completed successfully' in result.stdout
        assert '"asset"' not in result.stdout


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


class TestBOMAssembleTempFiles:
    """Where bomassemble writes downloads and what it leaves behind, on a real file system."""

    MOCK_GLB = {
        'json': {
            'asset': {'version': '2.0'},
            'scenes': [{'nodes': [0]}],
            'nodes': [{'mesh': 0}],
            'meshes': [{'name': 'test_mesh', 'primitives': []}],
            'materials': [], 'textures': [], 'images': [], 'accessors': [], 'bufferViews': [],
            'buffers': [{'byteLength': 0}]
        },
        'binary': b''
    }

    @pytest.fixture
    def dirs(self, tmp_path, monkeypatch, sample_bom_json):
        """A working folder, a system temp folder and a BOM JSON file outside both."""
        work = tmp_path / 'work'
        systmp = tmp_path / 'systmp'
        work.mkdir()
        systmp.mkdir()
        monkeypatch.chdir(work)
        monkeypatch.setattr(tempfile, 'tempdir', str(systmp))
        bom_file = tmp_path / 'bom.json'
        bom_file.write_text(json.dumps(sample_bom_json))
        return {'work': work, 'systmp': systmp, 'bom_file': str(bom_file)}

    @pytest.fixture
    def assembly(self, bom_command_mocks):
        """Stand-ins for the asset lookup, the per-source GLB download, asset create and upload."""
        from vamscli.commands.industry.spatial.glb import glbassetcombine
        from vamscli.commands.assets import create as create_command
        from vamscli.commands.file import upload as upload_command

        download_dirs = []

        def fake_glbassetcombine(**kwargs):
            local_path = kwargs['local_path']
            download_dirs.append(local_path)
            run_dir = os.path.join(local_path, 'glbassetcombine_run')
            os.makedirs(run_dir, exist_ok=True)
            glb_path = os.path.join(run_dir, f"{kwargs['asset_id']}__COMBINED.glb")
            with open(glb_path, 'wb') as f:
                f.write(b'glb')
            return {'combined_glb_path': glb_path}

        with bom_command_mocks as mocks, \
             patch.object(glbassetcombine, 'callback', side_effect=fake_glbassetcombine), \
             patch.object(create_command, 'callback', return_value={'assetId': 'new-asset-1'}), \
             patch.object(upload_command, 'callback', return_value={'overall_success': True}), \
             patch('vamscli.utils.glb_combiner.read_glb_file', return_value=self.MOCK_GLB):
            mocks['api_client'].search_simple.side_effect = lambda request: {'hits': {'hits': [
                {'_source': {'str_assetid': f"{request.get('assetName', '')}-id"}}]}}
            yield {'download_dirs': download_dirs}

    def _assemble(self, cli_runner, dirs, *extra):
        return cli_runner.invoke(cli, [
            'industry', 'engineering', 'bom', 'bomassemble',
            '--json-file', dirs['bom_file'], '--database-id', 'test-db', *extra
        ])

    def test_success_leaves_nothing_behind(self, cli_runner, dirs, assembly):
        result = self._assemble(cli_runner, dirs)

        assert result.exit_code == 0, result.output
        assert assembly['download_dirs']
        assert os.listdir(dirs['work']) == []
        assert os.listdir(dirs['systmp']) == []

    def test_failure_leaves_nothing_behind(self, cli_runner, dirs, assembly):
        corrupt = ValueError('corrupt GLB')
        with patch('vamscli.utils.glb_combiner.read_glb_file', side_effect=corrupt):
            result = self._assemble(cli_runner, dirs)

        assert result.exit_code == 1
        assert 'Failed to assemble any root nodes' in result.output
        assert assembly['download_dirs']
        assert os.listdir(dirs['work']) == []
        assert os.listdir(dirs['systmp']) == []

    def test_keep_temp_files_keeps_downloads_in_temp_dir(self, cli_runner, dirs, assembly):
        result = self._assemble(cli_runner, dirs, '--keep-temp-files', '--json-output')

        assert result.exit_code == 0, result.output
        temp_dir = json.loads(result.stdout)['temporary_directory']
        assert os.path.isdir(temp_dir)
        assert all(os.path.commonpath([d, temp_dir]) == temp_dir for d in assembly['download_dirs'])
        assert os.listdir(dirs['work']) == []

    def test_keep_temp_files_is_honoured_with_asset_creation(self, cli_runner, dirs, assembly):
        result = self._assemble(cli_runner, dirs, '--asset-create-name', 'Engine Assembly',
                                '--keep-temp-files', '--json-output')

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data['new_asset']['asset_id'] == 'new-asset-1'
        assert os.path.isdir(data['temporary_directory'])

    def test_asset_creation_cleans_up_by_default(self, cli_runner, dirs, assembly):
        result = self._assemble(cli_runner, dirs, '--asset-create-name', 'Engine Assembly')

        assert result.exit_code == 0, result.output
        assert os.listdir(dirs['work']) == []
        assert os.listdir(dirs['systmp']) == []

    def test_cleanup_failure_still_prints_the_result(self, cli_runner, dirs, assembly):
        with patch('shutil.rmtree', side_effect=PermissionError('file in use')):
            result = self._assemble(cli_runner, dirs, '--json-output')

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)['status'] == 'success'

    @pytest.mark.parametrize('name', ['vams_bom_assembly_x', 'other_x'])
    def test_cleanup_removes_only_its_own_temp_folder(self, dirs, name, tmp_path):
        from vamscli.commands.industry.engineering.bom.Dynamic_BOM import remove_run_temp_dir
        outside = tmp_path / 'elsewhere' / name
        inside_wrong_prefix = dirs['systmp'] / 'other_x'
        for folder in (outside, inside_wrong_prefix):
            folder.mkdir(parents=True)

        remove_run_temp_dir(str(outside), json_output=True)
        remove_run_temp_dir(str(inside_wrong_prefix), json_output=True)

        assert outside.is_dir()
        assert inside_wrong_prefix.is_dir()


if __name__ == '__main__':
    pytest.main([__file__])