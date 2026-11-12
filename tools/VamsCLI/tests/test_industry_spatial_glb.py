"""Test industry spatial GLB commands."""

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
    FileDownloadError, AssetAlreadyExistsError, FileUploadError, PermissionDeniedError
)
from vamscli.utils.global_exceptions import _global_error_data
from vamscli.utils.glb_combiner import GLBCombineError
from vamscli.utils.json_output import output_error


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
                # Use raw_decode to handle potential trailing RuntimeWarning from GC'd coroutines
                try:
                    output_data, _ = json.JSONDecoder().raw_decode(result.output.strip())
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
    
    def test_glbassetcombine_asset_creation_succeeds_after_cleanup(
            self, cli_runner, spatial_command_mocks, mock_export_result, tmp_path, monkeypatch):
        """The default cleanup after the upload does not turn a successful run into exit 1 (#1356)."""
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))

        def write_combined_glb(export_result, temp_dir, root_name, json_output, failed_operations):
            path = os.path.join(temp_dir, f"{root_name}__COMBINED.glb")
            with open(path, 'wb') as f:
                f.write(b'0123456789')
            return path, {'total_assets_processed': 2, 'total_glbs_combined': 1}

        with spatial_command_mocks, \
             patch('vamscli.commands.industry.spatial.glb.export_command', return_value=mock_export_result), \
             patch('vamscli.commands.industry.spatial.glb.process_and_combine_glbs',
                   side_effect=write_combined_glb), \
             patch('vamscli.commands.industry.spatial.glb.create_command',
                   return_value={'assetId': 'new-asset-id'}), \
             patch('vamscli.commands.industry.spatial.glb.upload_command',
                   return_value={'overall_success': True}) as mock_upload:
            result = cli_runner.invoke(cli, [
                'industry', 'spatial', 'glbassetcombine', '-d', 'test-db', '-a', 'root-asset',
                '--asset-create-name', 'Combined Model', '--json-output'
            ])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data['new_asset']['asset_id'] == 'new-asset-id'
        assert data['combined_glb_size'] == 10
        assert data['temporary_directory'] == 'deleted'
        assert not os.path.exists(data['combined_glb_path'])
        assert mock_upload.call_count == 2
    
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
            mock_upload.return_value = {'overall_success': True}
            
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


def _fail_like_a_command(error):
    """A nested command that fails the way a real command does in JSON mode."""
    def command(**kwargs):
        output_error(error, kwargs['json_output'])
        raise click.ClickException(str(error))
    return command


class TestGLBAssetCombineNestedFailure:
    """A failed nested export, asset creation or upload ends glbassetcombine with one error (#1349)."""

    def _run(self, cli_runner, spatial_command_mocks, mock_export_result, extra_args,
             export_side_effect=None, create_side_effect=None, upload_side_effect=None):
        with spatial_command_mocks, \
             patch('vamscli.commands.industry.spatial.glb.export_command',
                   side_effect=export_side_effect, return_value=mock_export_result), \
             patch('vamscli.commands.industry.spatial.glb.process_and_combine_glbs',
                   return_value=('/tmp/test/root__COMBINED.glb', {'total_assets_processed': 2})), \
             patch('vamscli.commands.industry.spatial.glb.create_command',
                   side_effect=create_side_effect, return_value={'assetId': 'new-asset-id'}), \
             patch('vamscli.commands.industry.spatial.glb.upload_command',
                   side_effect=upload_side_effect, return_value={'overall_success': True}), \
             patch('builtins.open', create=True), \
             patch('os.path.getsize', return_value=1024000), \
             patch('tempfile.mkdtemp', return_value='/tmp/test'), \
             patch('os.makedirs'), \
             patch('shutil.rmtree'):
            return cli_runner.invoke(cli, [
                'industry', 'spatial', 'glbassetcombine', '-d', 'test-db', '-a', 'root-asset',
            ] + extra_args)

    def test_json_output_writes_the_export_error_document(self, cli_runner, spatial_command_mocks,
                                                          mock_export_result):
        result = self._run(cli_runner, spatial_command_mocks, mock_export_result, ['--json-output'],
                           export_side_effect=_fail_like_a_command(AssetNotFoundError("Asset not found")))

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {'error': 'Asset not found', 'error_type': 'AssetNotFoundError'}

    def test_json_output_writes_the_create_error_document(self, cli_runner, spatial_command_mocks,
                                                          mock_export_result):
        result = self._run(cli_runner, spatial_command_mocks, mock_export_result,
                           ['--asset-create-name', 'Combined Model', '--json-output'],
                           create_side_effect=_fail_like_a_command(
                               AssetAlreadyExistsError("Asset already exists")))

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            'error': 'Asset already exists', 'error_type': 'AssetAlreadyExistsError'}

    def test_json_output_writes_an_error_document_for_a_create_exception(
            self, cli_runner, spatial_command_mocks, mock_export_result):
        result = self._run(cli_runner, spatial_command_mocks, mock_export_result,
                           ['--asset-create-name', 'Combined Model', '--json-output'],
                           create_side_effect=KeyError('assetId'))

        assert result.exit_code == 1
        assert json.loads(result.stdout)['error_type'] == 'KeyError'

    def test_json_output_names_the_created_asset_when_an_upload_fails(
            self, cli_runner, spatial_command_mocks, mock_export_result):
        result = self._run(cli_runner, spatial_command_mocks, mock_export_result,
                           ['--asset-create-name', 'Combined Model', '--json-output'],
                           upload_side_effect=_fail_like_a_command(FileUploadError("Upload failed: timeout")))

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            'error': 'Upload failed: timeout', 'error_type': 'FileUploadError',
            'createdAssetId': 'new-asset-id'}

    def test_json_output_reports_an_upload_that_stored_nothing(
            self, cli_runner, spatial_command_mocks, mock_export_result):
        # Under another command, `file upload` returns overall_success: false instead of exiting 1
        failed_upload = {'overall_success': False, 'failed_files': 1, 'sequence_results': [
            {'sequence_number': 1, 'successful_files': 0, 'failed_files': ['root__COMBINED.glb']}]}
        result = self._run(cli_runner, spatial_command_mocks, mock_export_result,
                           ['--asset-create-name', 'Combined Model', '--json-output'],
                           upload_side_effect=lambda **kwargs: failed_upload)

        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            'error': 'Upload failed for: root__COMBINED.glb', 'error_type': 'FileUploadError',
            'createdAssetId': 'new-asset-id'}

    def test_global_error_after_create_keeps_the_created_asset_id(
            self, cli_runner, spatial_command_mocks, mock_export_result):
        result = self._run(cli_runner, spatial_command_mocks, mock_export_result,
                           ['--asset-create-name', 'Combined Model', '--json-output'],
                           upload_side_effect=PermissionDeniedError("Access forbidden"))

        assert result.exit_code == 1
        assert isinstance(result.exception, PermissionDeniedError)
        document = _global_error_data("Permission Denied", str(result.exception),
                                      exc=result.exception)
        assert document['createdAssetId'] == 'new-asset-id'
        assert document['error_type'] == 'PermissionDeniedError'

    def test_text_mode_reports_the_create_error_on_stderr(self, cli_runner, spatial_command_mocks,
                                                         mock_export_result):
        result = self._run(cli_runner, spatial_command_mocks, mock_export_result,
                           ['--asset-create-name', 'Combined Model'],
                           create_side_effect=_fail_like_a_command(
                               AssetAlreadyExistsError("Asset already exists")))

        assert result.exit_code == 1
        assert '✗ AssetAlreadyExistsError: Asset already exists' in result.stderr
        assert '"error"' not in result.stdout


class TestGLBAssetCombineUploadRefused:
    """A role that can create assets but not upload files: the real `file upload` runs (#1349)."""

    def test_can_create_but_not_upload_exits_1_and_names_the_asset(
            self, cli_runner, spatial_command_mocks, mock_export_result, tmp_path, monkeypatch):
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))

        def write_combined_glb(export_result, temp_dir, root_name, json_output, failed_operations):
            path = os.path.join(temp_dir, f"{root_name}__COMBINED.glb")
            with open(path, 'wb') as f:
                f.write(b'glb')
            return path, {'total_assets_processed': 2, 'total_glbs_combined': 1}

        with spatial_command_mocks as mocks, \
             patch('vamscli.commands.file.APIClient', return_value=mocks['api_client']), \
             patch('vamscli.commands.file.get_profile_manager_from_context',
                   return_value=mocks['profile_manager']), \
             patch('vamscli.commands.industry.spatial.glb.export_command',
                   return_value=mock_export_result), \
             patch('vamscli.commands.industry.spatial.glb.process_and_combine_glbs',
                   side_effect=write_combined_glb), \
             patch('vamscli.commands.industry.spatial.glb.create_command',
                   return_value={'assetId': 'new-asset-id'}):
            mocks['api_client'].get_database.return_value = {'databaseId': 'test-db'}
            mocks['api_client'].initialize_upload.side_effect = PermissionDeniedError(
                "Access forbidden. Not authorized to upload")
            result = cli_runner.invoke(cli, [
                'industry', 'spatial', 'glbassetcombine', '-d', 'test-db', '-a', 'root-asset',
                '--asset-create-name', 'Combined Model', '--json-output'
            ])

        assert result.exit_code == 1, result.output
        document = json.loads(result.stdout)
        assert document['error_type'] == 'FileUploadError'
        assert document['error'] == 'Upload failed for: /Root Asset__COMBINED.glb'
        assert document['createdAssetId'] == 'new-asset-id'
        mocks['api_client'].initialize_upload.assert_called()


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


class TestGLBAssetCombineDeleteTemporaryFiles:
    """--delete-temporary-files is an on/off switch with --asset-create-name (#1350)."""

    def _run(self, cli_runner, spatial_command_mocks, mock_export_result, tmp_path, *extra):
        with spatial_command_mocks, \
             patch('vamscli.commands.industry.spatial.glb.export_command', return_value=mock_export_result), \
             patch('vamscli.commands.industry.spatial.glb.process_and_combine_glbs',
                   return_value=(str(tmp_path / 'root__COMBINED.glb'), {'total_assets_processed': 2})), \
             patch('vamscli.commands.industry.spatial.glb.create_command', return_value={'assetId': 'new-asset-id'}), \
             patch('vamscli.commands.industry.spatial.glb.upload_command',
                   return_value={'overall_success': True}), \
             patch('builtins.open', create=True), \
             patch('os.path.getsize', return_value=1024000), \
             patch('tempfile.mkdtemp', return_value=str(tmp_path)), \
             patch('shutil.rmtree') as rmtree:
            result = cli_runner.invoke(cli, [
                'industry', 'spatial', 'glbassetcombine', '-d', 'test-db', '-a', 'root-asset',
                '--asset-create-name', 'Combined Model', '--json-output', *extra
            ])
        return result, rmtree

    def test_no_delete_temporary_files_keeps_the_folder(self, cli_runner, spatial_command_mocks,
                                                        mock_export_result, tmp_path):
        result, rmtree = self._run(cli_runner, spatial_command_mocks, mock_export_result, tmp_path,
                                   '--no-delete-temporary-files')

        assert result.exit_code == 0, result.output
        temp_dir = json.loads(result.stdout)['temporary_directory']
        assert os.path.dirname(temp_dir) == str(tmp_path)
        assert os.path.isdir(temp_dir)
        rmtree.assert_not_called()

    def test_delete_temporary_files_is_still_accepted(self, cli_runner, spatial_command_mocks,
                                                      mock_export_result, tmp_path):
        result, rmtree = self._run(cli_runner, spatial_command_mocks, mock_export_result, tmp_path,
                                   '--delete-temporary-files')

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)['temporary_directory'] == 'deleted'
        rmtree.assert_called_once()


class TestGLBAssetCombineSystemTempFolder:
    """What a run without --local-path leaves in the system temp directory (#1388, #1389)."""

    @staticmethod
    def _write_combined_glb(export_result, temp_dir, root_name, json_output, failed_operations):
        path = os.path.join(temp_dir, f"{root_name}__COMBINED.glb")
        with open(path, 'wb') as f:
            f.write(b'0123456789')
        return path, {'total_assets_processed': 2, 'total_glbs_combined': 1}

    def _run(self, cli_runner, spatial_command_mocks, mock_export_result, *extra, upload=None,
             export=None, json_output=True):
        export_patch = ({'side_effect': export} if export is not None
                        else {'return_value': mock_export_result})
        upload_patch = ({'side_effect': upload} if upload is not None
                        else {'return_value': {'overall_success': True}})
        with spatial_command_mocks, \
             patch('vamscli.commands.industry.spatial.glb.export_command', **export_patch), \
             patch('vamscli.commands.industry.spatial.glb.process_and_combine_glbs',
                   side_effect=self._write_combined_glb), \
             patch('vamscli.commands.industry.spatial.glb.create_command',
                   return_value={'assetId': 'new-asset-id'}), \
             patch('vamscli.commands.industry.spatial.glb.upload_command', **upload_patch):
            return cli_runner.invoke(cli, [
                'industry', 'spatial', 'glbassetcombine', '-d', 'test-db', '-a', 'root-asset',
                *(['--json-output'] if json_output else []), *extra
            ])

    def test_reported_paths_exist_without_asset_create_name(
            self, cli_runner, spatial_command_mocks, mock_export_result, tmp_path, monkeypatch):
        """Without an upload the temp folder holds the only copy of the output, so it stays (#1389)."""
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))

        result = self._run(cli_runner, spatial_command_mocks, mock_export_result)

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert os.path.isdir(data['temporary_directory'])
        assert os.path.isfile(data['combined_glb_path'])
        assert os.path.isfile(data['export_json_path'])

    def test_upload_cleanup_removes_the_whole_mkdtemp_folder(
            self, cli_runner, spatial_command_mocks, mock_export_result, tmp_path, monkeypatch):
        """The cleanup after an upload leaves no empty vams_glb_combine_* folder behind (#1388)."""
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))

        result = self._run(cli_runner, spatial_command_mocks, mock_export_result,
                           '--asset-create-name', 'Combined Model')

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)['temporary_directory'] == 'deleted'
        assert os.listdir(tmp_path) == []

    def test_failed_run_removes_the_whole_mkdtemp_folder(
            self, cli_runner, spatial_command_mocks, mock_export_result, tmp_path, monkeypatch):
        """A run that fails before it has a result leaves nothing in the system temp directory (#1388)."""
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))

        result = self._run(cli_runner, spatial_command_mocks, mock_export_result,
                           export=AssetNotFoundError("Asset not found"))

        assert result.exit_code == 1
        assert os.listdir(tmp_path) == []

    def test_failed_upload_removes_the_whole_mkdtemp_folder(
            self, cli_runner, spatial_command_mocks, mock_export_result, tmp_path, monkeypatch):
        """A failed upload ends the run with an error (#1349) and leaves nothing behind (#1388)."""
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))

        result = self._run(cli_runner, spatial_command_mocks, mock_export_result,
                           '--asset-create-name', 'Combined Model',
                           upload=RuntimeError("upload refused"), json_output=False)

        assert result.exit_code == 1
        assert '✗ Asset Creation Error: upload refused' in result.stderr
        assert os.listdir(tmp_path) == []


if __name__ == '__main__':
    pytest.main([__file__])
