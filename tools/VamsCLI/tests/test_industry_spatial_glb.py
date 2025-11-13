"""Test industry spatial GLB commands."""

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


# File-level fixtures for spatial command testing
@pytest.fixture
def spatial_command_mocks(generic_command_mocks):
    """Provide spatial command-specific mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for spatial command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('industry.spatial.glb')


@pytest.fixture
def spatial_no_setup_mocks(no_setup_command_mocks):
    """Provide spatial command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('industry.spatial.glb')


@pytest.fixture
def mock_export_result():
    """Provide mock export result data."""
    return {
        'assets': [
            {
                'is_root_lookup_asset': True,
                'assetid': 'root-asset-id',
                'assetname': 'Root Asset',
                'databaseid': 'test-db',
                'files': [
                    {
                        'fileName': 'root.glb',
                        'key': 'root-asset-id/root.glb'
                    }
                ]
            },
            {
                'is_root_lookup_asset': False,
                'assetid': 'child-asset-id',
                'assetname': 'Child Asset',
                'databaseid': 'test-db',
                'files': [
                    {
                        'fileName': 'child.glb',
                        'key': 'child-asset-id/child.glb'
                    }
                ]
            }
        ],
        'relationships': [
            {
                'parentAssetId': 'root-asset-id',
                'childAssetId': 'child-asset-id',
                'metadata': {
                    'Matrix': {
                        'value': '1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1'
                    }
                }
            }
        ],
        'totalAssetsInTree': 2,
        'assetsRetrieved': 2
    }


class TestSpatialGLBAssetCombineCommand:
    """Test industry spatial glbassetcombine command."""
    
    def test_glbassetcombine_help(self, cli_runner):
        """Test glbassetcombine command help."""
        result = cli_runner.invoke(cli, ['industry', 'spatial', 'glbassetcombine', '--help'])
        assert result.exit_code == 0
        assert 'Combine multiple GLB files' in result.output
        assert '--database-id' in result.output
        assert '--asset-id' in result.output
        assert '--asset-create-name' in result.output
    
    def test_glbassetcombine_no_setup(self, cli_runner, spatial_no_setup_mocks):
        """Test glbassetcombine without setup."""
        with spatial_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'industry', 'spatial', 'glbassetcombine',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            # Check for setup-related error in output or exception
            error_text = result.output + str(result.exception) if result.exception else result.output
            assert ('Configuration not found' in error_text or 
                    'Setup required' in error_text or
                    'vamscli setup' in error_text)
    
    @patch('vamscli.commands.industry.spatial.glb.export_command')
    @patch('vamscli.commands.industry.spatial.glb.process_and_combine_glbs')
    @patch('tempfile.mkdtemp')
    @patch('os.makedirs')
    def test_glbassetcombine_basic_success(self, mock_makedirs, mock_mkdtemp, 
                                          mock_process_combine, mock_export,
                                          cli_runner, spatial_command_mocks, mock_export_result):
        """Test successful basic GLB combination."""
        with spatial_command_mocks as mocks:
            # Setup mocks
            mock_mkdtemp.return_value = '/tmp/test_temp'
            mock_export.return_value = mock_export_result
            
            combined_path = '/tmp/test_temp/glbassetcombine_20251111_120000/root-asset-id__COMBINED.glb'
            mock_process_combine.return_value = (
                combined_path,
                {'total_assets_processed': 2, 'total_glbs_combined': 1}
            )
            
            # Mock file operations
            with patch('builtins.open', create=True) as mock_open:
                with patch('os.path.getsize', return_value=1024000):
                    result = cli_runner.invoke(cli, [
                        'industry', 'spatial', 'glbassetcombine',
                        '-d', 'test-db',
                        '-a', 'root-asset'
                    ])
            
            assert result.exit_code == 0
            assert '✓ GLB combination completed successfully!' in result.output
            assert 'Assets Processed: 2' in result.output
    
    @patch('vamscli.commands.industry.spatial.glb.export_command')
    def test_glbassetcombine_json_output(self, mock_export, cli_runner, 
                                        spatial_command_mocks, mock_export_result):
        """Test glbassetcombine with JSON output."""
        with spatial_command_mocks as mocks:
            mock_export.return_value = mock_export_result
            
            with patch('vamscli.commands.industry.spatial.glb.process_and_combine_glbs') as mock_process:
                combined_path = '/tmp/test/root-asset-id__COMBINED.glb'
                mock_process.return_value = (
                    combined_path,
                    {'total_assets_processed': 2, 'total_glbs_combined': 1}
                )
                
                with patch('builtins.open', create=True):
                    with patch('os.path.getsize', return_value=1024000):
                        with patch('tempfile.mkdtemp', return_value='/tmp/test'):
                            with patch('os.makedirs'):
                                result = cli_runner.invoke(cli, [
                                    'industry', 'spatial', 'glbassetcombine',
                                    '-d', 'test-db',
                                    '-a', 'root-asset',
                                    '--json-output'
                                ])
                
                assert result.exit_code == 0
                
                # Verify output is valid JSON
                try:
                    output_data = json.loads(result.output)
                    assert output_data['status'] == 'success'
                    assert 'combined_glb_path' in output_data
                    assert output_data['total_assets_processed'] == 2
                except json.JSONDecodeError:
                    pytest.fail(f"Output is not valid JSON: {result.output}")
    
    def test_glbassetcombine_missing_required_params(self, cli_runner):
        """Test glbassetcombine with missing required parameters."""
        # Missing asset-id
        result = cli_runner.invoke(cli, [
            'industry', 'spatial', 'glbassetcombine',
            '-d', 'test-db'
        ])
        assert result.exit_code == 2
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Missing database-id
        result = cli_runner.invoke(cli, [
            'industry', 'spatial', 'glbassetcombine',
            '-a', 'test-asset'
        ])
        assert result.exit_code == 2
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    @patch('vamscli.commands.industry.spatial.glb.export_command')
    def test_glbassetcombine_asset_not_found(self, mock_export, cli_runner, spatial_command_mocks):
        """Test glbassetcombine with non-existent asset."""
        with spatial_command_mocks as mocks:
            mock_export.side_effect = AssetNotFoundError("Asset not found")
            
            with patch('tempfile.mkdtemp', return_value='/tmp/test'):
                with patch('os.makedirs'):
                    result = cli_runner.invoke(cli, [
                        'industry', 'spatial', 'glbassetcombine',
                        '-d', 'test-db',
                        '-a', 'nonexistent-asset'
                    ])
            
            assert result.exit_code == 1
            assert 'Asset not found' in result.output
    
    @patch('vamscli.commands.industry.spatial.glb.export_command')
    @patch('vamscli.commands.industry.spatial.glb.process_and_combine_glbs')
    @patch('vamscli.commands.industry.spatial.glb.create_command')
    @patch('vamscli.commands.industry.spatial.glb.upload_command')
    def test_glbassetcombine_with_asset_creation(self, mock_upload, mock_create,
                                                 mock_process, mock_export,
                                                 cli_runner, spatial_command_mocks,
                                                 mock_export_result):
        """Test glbassetcombine with new asset creation."""
        with spatial_command_mocks as mocks:
            mock_export.return_value = mock_export_result
            
            combined_path = '/tmp/test/root-asset-id__COMBINED.glb'
            mock_process.return_value = (
                combined_path,
                {'total_assets_processed': 2, 'total_glbs_combined': 1}
            )
            
            mock_create.return_value = {'assetId': 'new-asset-id'}
            mock_upload.return_value = {'success': True}
            
            with patch('builtins.open', create=True):
                with patch('os.path.getsize', return_value=1024000):
                    with patch('os.path.basename', side_effect=lambda x: x.split('/')[-1]):
                        with patch('tempfile.mkdtemp', return_value='/tmp/test'):
                            with patch('os.makedirs'):
                                with patch('shutil.rmtree'):
                                    result = cli_runner.invoke(cli, [
                                        'industry', 'spatial', 'glbassetcombine',
                                        '-d', 'test-db',
                                        '-a', 'root-asset',
                                        '--asset-create-name', 'Combined Model'
                                    ])
            
            assert result.exit_code == 0
            assert '✓ New Asset Created:' in result.output
            assert 'new-asset-id' in result.output
            
            # Verify create was called
            mock_create.assert_called_once()
            
            # Verify upload was called twice (GLB + JSON)
            assert mock_upload.call_count == 2


class TestGLBCombinerUtilities:
    """Test GLB combiner utility functions."""
    
    def test_build_transform_matrix_from_space_separated_string(self):
        """Test building transform matrix from space-separated string."""
        from vamscli.utils.glb_combiner import build_transform_matrix_from_metadata
        
        metadata = {
            'Matrix': {
                'value': '1 0 0 0 0 1 0 0 0 0 1 0 5 10 15 1'
            }
        }
        
        result = build_transform_matrix_from_metadata(metadata)
        
        assert len(result) == 16
        assert result == [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 
                         0.0, 0.0, 1.0, 0.0, 5.0, 10.0, 15.0, 1.0]
    
    def test_build_transform_matrix_from_2d_row_major_array(self):
        """Test building transform matrix from 2D row-major array."""
        from vamscli.utils.glb_combiner import build_transform_matrix_from_metadata
        
        # Row-major format (last row is translation)
        metadata = {
            'Matrix': {
                'value': '[[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, -1.0, 0.0, 0.0], [0.075, 0.001, 5.095, 1.0]]'
            }
        }
        
        result = build_transform_matrix_from_metadata(metadata)
        
        assert len(result) == 16
        # Should be transposed to column-major
        # Translation should be in positions 12, 13, 14
        assert abs(result[12] - 0.075) < 0.001
        assert abs(result[13] - 0.001) < 0.001
        assert abs(result[14] - 5.095) < 0.001
        assert result[15] == 1.0
    
    def test_build_transform_matrix_from_translation(self):
        """Test building transform matrix from translation component."""
        from vamscli.utils.glb_combiner import build_transform_matrix_from_metadata
        
        metadata = {
            'Translation': {
                'value': '{"x": 5, "y": 10, "z": 15}'
            }
        }
        
        result = build_transform_matrix_from_metadata(metadata)
        
        assert len(result) == 16
        # Check translation values (positions 12, 13, 14)
        assert result[12] == 5.0
        assert result[13] == 10.0
        assert result[14] == 15.0
        assert result[15] == 1.0
        # Check scale (diagonal should be 1.0)
        assert result[0] == 1.0
        assert result[5] == 1.0
        assert result[10] == 1.0
    
    def test_build_transform_matrix_from_transform_alias(self):
        """Test building transform matrix from Transform (alias for Translation)."""
        from vamscli.utils.glb_combiner import build_transform_matrix_from_metadata
        
        metadata = {
            'Transform': {
                'value': '{"x": 2, "y": 3, "z": 4}'
            }
        }
        
        result = build_transform_matrix_from_metadata(metadata)
        
        assert len(result) == 16
        assert result[12] == 2.0
        assert result[13] == 3.0
        assert result[14] == 4.0
    
    def test_build_transform_matrix_with_scale(self):
        """Test building transform matrix with translation and scale."""
        from vamscli.utils.glb_combiner import build_transform_matrix_from_metadata
        
        metadata = {
            'Translation': {
                'value': '{"x": 1, "y": 2, "z": 3}'
            },
            'Scale': {
                'value': '{"x": 2, "y": 2, "z": 2}'
            }
        }
        
        result = build_transform_matrix_from_metadata(metadata)
        
        assert len(result) == 16
        # Check scale (diagonal)
        assert result[0] == 2.0
        assert result[5] == 2.0
        assert result[10] == 2.0
        # Check translation
        assert result[12] == 1.0
        assert result[13] == 2.0
        assert result[14] == 3.0
    
    def test_build_transform_matrix_default_identity(self):
        """Test building transform matrix with no metadata."""
        from vamscli.utils.glb_combiner import build_transform_matrix_from_metadata
        
        metadata = {}
        
        result = build_transform_matrix_from_metadata(metadata)
        
        # Should return identity matrix
        identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 
                   0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        assert result == identity
    
    def test_sanitize_node_name(self):
        """Test node name sanitization."""
        from vamscli.utils.glb_combiner import sanitize_node_name
        
        assert sanitize_node_name("Normal Name") == "Normal Name"
        assert sanitize_node_name("Name/With\\Slashes") == "Name_With_Slashes"
        assert sanitize_node_name("Name@#$%Special") == "Name____Special"
        assert sanitize_node_name("") == "node"
        assert sanitize_node_name("___") == "node"
        assert sanitize_node_name("  spaces  ") == "spaces"
    
    def test_validate_export_has_glbs(self):
        """Test GLB validation in export result."""
        from vamscli.utils.glb_combiner import validate_export_has_glbs
        
        # Export with GLBs
        export_with_glbs = {
            'assets': [
                {'assetid': 'a1', 'files': [{'fileName': 'model.glb'}]},
                {'assetid': 'a2', 'files': [{'fileName': 'texture.png'}]}
            ]
        }
        has_glbs, count = validate_export_has_glbs(export_with_glbs)
        assert has_glbs is True
        assert count == 1
        
        # Export without GLBs
        export_no_glbs = {
            'assets': [
                {'assetid': 'a1', 'files': [{'fileName': 'texture.png'}]},
                {'assetid': 'a2', 'files': []}
            ]
        }
        has_glbs, count = validate_export_has_glbs(export_no_glbs)
        assert has_glbs is False
        assert count == 0
    
    def test_build_transform_tree_with_instancing(self):
        """Test building transform tree with asset instancing (same asset, different aliases)."""
        from vamscli.utils.glb_combiner import build_transform_tree_from_export
        
        export_result = {
            'assets': [
                {
                    'assetid': 'root',
                    'assetname': 'Root',
                    'is_root_lookup_asset': True,
                    'files': []
                },
                {
                    'assetid': 'bolt',
                    'assetname': 'Bolt',
                    'files': [{'fileName': 'bolt.glb', 'key': 'bolt/bolt.glb'}]
                }
            ],
            'relationships': [
                {
                    'parentAssetId': 'root',
                    'childAssetId': 'bolt',
                    'assetLinkAliasId': '1',
                    'metadata': {'Translation': {'value': '{"x": 0, "y": 0, "z": 0}'}}
                },
                {
                    'parentAssetId': 'root',
                    'childAssetId': 'bolt',
                    'assetLinkAliasId': '2',
                    'metadata': {'Translation': {'value': '{"x": 1, "y": 0, "z": 0}'}}
                },
                {
                    'parentAssetId': 'root',
                    'childAssetId': 'bolt',
                    'assetLinkAliasId': '3',
                    'metadata': {'Translation': {'value': '{"x": 2, "y": 0, "z": 0}'}}
                }
            ]
        }
        
        tree_data = build_transform_tree_from_export(export_result)
        
        # Should have 4 nodes: 1 root + 3 bolt instances
        assert len(tree_data['gltf']['nodes']) == 4
        
        # Check node names
        assert tree_data['gltf']['nodes'][0]['name'] == 'Root'
        assert tree_data['gltf']['nodes'][1]['name'] == 'Bolt__1'
        assert tree_data['gltf']['nodes'][2]['name'] == 'Bolt__2'
        assert tree_data['gltf']['nodes'][3]['name'] == 'Bolt__3'
        
        # Check root has 3 children
        assert 'children' in tree_data['gltf']['nodes'][0]
        assert len(tree_data['gltf']['nodes'][0]['children']) == 3
        
        # Check each bolt instance has GLB files mapped
        assert 1 in tree_data['glb_map']
        assert 2 in tree_data['glb_map']
        assert 3 in tree_data['glb_map']
    
    def test_build_transform_tree_without_aliases(self):
        """Test building transform tree without aliases (normal hierarchy)."""
        from vamscli.utils.glb_combiner import build_transform_tree_from_export
        
        export_result = {
            'assets': [
                {
                    'assetid': 'root',
                    'assetname': 'Root',
                    'is_root_lookup_asset': True,
                    'files': []
                },
                {
                    'assetid': 'child',
                    'assetname': 'Child',
                    'files': [{'fileName': 'child.glb', 'key': 'child/child.glb'}]
                }
            ],
            'relationships': [
                {
                    'parentAssetId': 'root',
                    'childAssetId': 'child',
                    'assetLinkAliasId': None,
                    'metadata': {}
                }
            ]
        }
        
        tree_data = build_transform_tree_from_export(export_result)
        
        # Should have 2 nodes: 1 root + 1 child
        assert len(tree_data['gltf']['nodes']) == 2
        
        # Check node names (no alias suffix)
        assert tree_data['gltf']['nodes'][0]['name'] == 'Root'
        assert tree_data['gltf']['nodes'][1]['name'] == 'Child'
    
    def test_format_file_size(self):
        """Test file size formatting."""
        from vamscli.utils.glb_combiner import format_file_size
        
        assert format_file_size(500) == "500.0 B"
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(1024 * 1024) == "1.0 MB"
        assert format_file_size(1024 * 1024 * 1024) == "1.0 GB"
    
    @patch('vamscli.utils.glb_combiner.read_glb_file')
    @patch('vamscli.utils.glb_combiner.write_glb_file')
    @patch('os.path.basename')
    @patch('os.makedirs')
    @patch('os.path.getsize')
    def test_combine_glb_files(self, mock_getsize, mock_makedirs, mock_basename,
                               mock_write, mock_read):
        """Test combining two GLB files."""
        from vamscli.utils.glb_combiner import combine_glb_files
        
        # Mock GLB data
        mock_read.side_effect = [
            {
                'json': {
                    'asset': {'version': '2.0'},
                    'scenes': [{'nodes': [0]}],
                    'nodes': [{'name': 'parent'}],
                    'buffers': [{'byteLength': 100}]
                },
                'binary': b'parent_data'
            },
            {
                'json': {
                    'asset': {'version': '2.0'},
                    'scenes': [{'nodes': [0]}],
                    'nodes': [{'name': 'child'}],
                    'buffers': [{'byteLength': 50}]
                },
                'binary': b'child_data'
            }
        ]
        
        mock_basename.return_value = 'child.glb'
        mock_getsize.return_value = 1000
        
        result = combine_glb_files(
            'parent.glb',
            'child.glb',
            'output.glb',
            verbose=False
        )
        
        assert result is True
        mock_write.assert_called_once()


class TestSpatialCommandIntegration:
    """Test spatial command integration."""
    
    def test_spatial_group_registered(self, cli_runner):
        """Test that spatial group is registered under industry."""
        result = cli_runner.invoke(cli, ['industry', '--help'])
        assert result.exit_code == 0
        assert 'spatial' in result.output
    
    def test_glbassetcombine_registered(self, cli_runner):
        """Test that glbassetcombine is registered under spatial."""
        result = cli_runner.invoke(cli, ['industry', 'spatial', '--help'])
        assert result.exit_code == 0
        assert 'glbassetcombine' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
