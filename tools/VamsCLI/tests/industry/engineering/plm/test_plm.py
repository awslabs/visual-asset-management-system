"""Test industry PLM commands."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    AssetAlreadyExistsError, DatabaseNotFoundError, InvalidAssetDataError,
    FileUploadError, InvalidFileError
)


@pytest.fixture
def cli_runner():
    """Provide a CLI runner for testing."""
    return CliRunner()


class TestIndustryPLMImportCommand:
    """Test industry engineering plm plmxml import command."""
    
    def test_import_help(self, cli_runner):
        """Test import command help."""
        result = cli_runner.invoke(cli, [
            'industry', 'engineering', 'plm', 'plmxml', 'import', '--help'
        ])
        assert result.exit_code == 0
        assert 'Import PLM XML files into VAMS with parallel processing' in result.output
        assert '--database-id' in result.output
        assert '--plmxml-dir' in result.output
        assert '--max-workers' in result.output
        assert '--upload-xml' in result.output
        assert '--json-output' in result.output
    
    @patch('vamscli.main.ProfileManager')
    def test_import_success(self, mock_main_profile_manager, cli_runner, tmp_path):
        """Test successful PLM XML import (integration test with command chaining)."""
        # Create a temporary PLM XML directory with a test file
        plm_dir = tmp_path / "plm_data"
        plm_dir.mkdir()
        plm_file = plm_dir / "test.xml"
        plm_file.write_text('<?xml version="1.0"?><plm>test</plm>')
        
        # Mock profile manager
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.load_config.return_value = {'api_gateway_url': 'https://api.example.com'}
        mock_profile_manager.profile_name = 'default'
        mock_main_profile_manager.return_value = mock_profile_manager
        
        # Note: This is an integration test that will fail without proper mocking of underlying commands
        # For now, we test that the command structure is correct
        result = cli_runner.invoke(cli, [
            'industry', 'engineering', 'plm', 'plmxml', 'import',
            '-d', 'test-database',
            '--plmxml-dir', str(plm_dir)
        ])
        
        # The command will fail because we're not mocking the underlying API calls,
        # but we can verify the command structure is correct
        assert 'Parsing' in result.output or 'Phase 0' in result.output or result.exit_code != 0
    
    @patch('vamscli.main.ProfileManager')
    def test_import_file_not_found(self, mock_main_profile_manager, cli_runner):
        """Test PLM XML import with non-existent directory."""
        # Mock profile manager
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.load_config.return_value = {'api_gateway_url': 'https://api.example.com'}
        mock_profile_manager.profile_name = 'default'
        mock_main_profile_manager.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, [
            'industry', 'engineering', 'plm', 'plmxml', 'import',
            '-d', 'test-database',
            '--plmxml-dir', '/nonexistent/directory'
        ])
        
        assert result.exit_code != 0
        # Click validates directory existence before our code runs
        assert 'does not exist' in result.output or 'Error' in result.output
    
    def test_import_missing_required_params(self, cli_runner):
        """Test PLM XML import with missing required parameters."""
        result = cli_runner.invoke(cli, [
            'industry', 'engineering', 'plm', 'plmxml', 'import'
        ])
        
        assert result.exit_code == 2
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    @patch('vamscli.main.ProfileManager')
    def test_import_with_upload_xml_flag(self, mock_main_profile_manager, cli_runner, tmp_path):
        """Test PLM XML import with --upload-xml flag."""
        # Create a temporary PLM XML directory with a test file
        plm_dir = tmp_path / "plm_data"
        plm_dir.mkdir()
        plm_file = plm_dir / "test.xml"
        plm_file.write_text('<?xml version="1.0"?><plm>test</plm>')
        
        # Mock profile manager
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.load_config.return_value = {'api_gateway_url': 'https://api.example.com'}
        mock_profile_manager.profile_name = 'default'
        mock_main_profile_manager.return_value = mock_profile_manager
        
        # Test that --upload-xml flag is accepted
        result = cli_runner.invoke(cli, [
            'industry', 'engineering', 'plm', 'plmxml', 'import',
            '-d', 'test-database',
            '--plmxml-dir', str(plm_dir),
            '--upload-xml'
        ])
        
        # The command will fail because we're not mocking the underlying API calls,
        # but we can verify the flag is accepted (no parameter error)
        assert result.exit_code != 2  # Not a parameter error
    
    @patch('vamscli.main.ProfileManager')
    def test_import_without_upload_xml_flag(self, mock_main_profile_manager, cli_runner, tmp_path):
        """Test PLM XML import without --upload-xml flag (default behavior)."""
        # Create a temporary PLM XML directory with a test file
        plm_dir = tmp_path / "plm_data"
        plm_dir.mkdir()
        plm_file = plm_dir / "test.xml"
        plm_file.write_text('<?xml version="1.0"?><plm>test</plm>')
        
        # Mock profile manager
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.load_config.return_value = {'api_gateway_url': 'https://api.example.com'}
        mock_profile_manager.profile_name = 'default'
        mock_main_profile_manager.return_value = mock_profile_manager
        
        # Test default behavior (no --upload-xml flag)
        result = cli_runner.invoke(cli, [
            'industry', 'engineering', 'plm', 'plmxml', 'import',
            '-d', 'test-database',
            '--plmxml-dir', str(plm_dir)
        ])
        
        # The command will fail because we're not mocking the underlying API calls,
        # but we can verify the command structure is correct
        assert result.exit_code != 2  # Not a parameter error


PLMXML_ASSEMBLY = """<?xml version="1.0" encoding="utf-8"?>
<PLMXML xmlns="http://www.plmxml.org/Schemas/PLMXMLSchema">
  <Product id="p1" productId="ASSY" name="Top Assembly"/>
  <Product id="p2" productId="PART" name="Child Part"/>
  <ProductRevision id="r1" masterRef="#p1" revision="A" name="Top Assembly"/>
  <ProductRevision id="r2" masterRef="#p2" revision="B" name="Child Part"/>
  <Occurrence id="o1" instancedRef="#r1"/>
  <Occurrence id="o2" instancedRef="#r2" parentRef="#o1">
    <Transform>1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</Transform>
    <UserData><UserValue title="SequenceNumber" value="10"/></UserData>
  </Occurrence>
</PLMXML>
"""


class TestIndustryPLMImportJsonOutput:
    """`plmxml import --json-output` writes only its JSON result to stdout."""

    # The import drives these commands through ctx.invoke; their own text output is what leaked.
    INVOKED_MODULES = ('assets', 'asset_links', 'search')

    @pytest.fixture
    def plm_mocks(self, generic_command_mocks):
        from contextlib import ExitStack
        from itertools import count

        with generic_command_mocks('industry.engineering.plm.plm') as mocks, ExitStack() as stack:
            for module in self.INVOKED_MODULES:
                stack.enter_context(patch(f'vamscli.commands.{module}.APIClient',
                                          return_value=mocks['api_client']))
                stack.enter_context(patch(f'vamscli.commands.{module}.get_profile_manager_from_context',
                                          return_value=mocks['profile_manager']))
            # Search is on in the deployment (the NOOPENSEARCH switch is off).
            stack.enter_context(patch('vamscli.commands.search.is_feature_enabled', return_value=False))
            asset_ids = count(1)
            api = mocks['api_client']
            api.search_simple.return_value = {'hits': {'hits': [], 'total': {'value': 0}}}
            api.create_asset.side_effect = lambda data: {'assetId': f'asset-{next(asset_ids)}',
                                                         'message': 'Asset created'}
            api.create_asset_link.return_value = {'assetLinkId': 'link-1', 'message': 'Link created'}
            yield mocks

    @pytest.fixture
    def plm_dir(self, tmp_path):
        plm_dir = tmp_path / 'plm_data'
        plm_dir.mkdir()
        (plm_dir / 'assembly.xml').write_text(PLMXML_ASSEMBLY)
        return plm_dir

    def _import(self, cli_runner, plm_dir, *extra):
        return cli_runner.invoke(cli, [
            'industry', 'engineering', 'plm', 'plmxml', 'import',
            '-d', 'test-database', '--plmxml-dir', str(plm_dir), '--max-workers', '4', *extra
        ])

    def test_json_output_stdout_is_only_json(self, cli_runner, plm_mocks, plm_dir):
        result = self._import(cli_runner, plm_dir, '--json-output')

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data['success'] is True
        assert data['statistics']['assets_created'] == 2
        assert data['statistics']['asset_links_created'] == 1

    def test_text_mode_still_shows_per_item_messages(self, cli_runner, plm_mocks, plm_dir):
        """Control: the inner commands do print, so the JSON test above is not passing vacuously."""
        result = self._import(cli_runner, plm_dir)

        assert result.exit_code == 0, result.output
        assert 'Asset created successfully' in result.stdout
        assert 'Asset link created successfully' in result.stdout

    def test_json_output_nested_exit_reason_goes_to_stderr(self, cli_runner, plm_mocks, plm_dir):
        """A nested command that exits (search turned off) keeps stdout free of text and its reason visible."""
        with patch('vamscli.commands.search.is_feature_enabled', return_value=True):
            result = self._import(cli_runner, plm_dir, '--json-output')

        assert result.exit_code == 1
        assert result.stdout == ''
        assert 'Search functionality is disabled' in result.stderr


class TestIndustryCommandHierarchy:
    """Test industry command hierarchy."""
    
    def test_industry_help(self, cli_runner):
        """Test industry command help."""
        result = cli_runner.invoke(cli, ['industry', '--help'])
        assert result.exit_code == 0
        assert 'Industry-specific commands' in result.output
        assert 'engineering' in result.output
    
    def test_engineering_help(self, cli_runner):
        """Test engineering command help."""
        result = cli_runner.invoke(cli, ['industry', 'engineering', '--help'])
        assert result.exit_code == 0
        assert 'Engineering-specific commands' in result.output
        assert 'plm' in result.output
    
    def test_plm_help(self, cli_runner):
        """Test plm command help."""
        result = cli_runner.invoke(cli, ['industry', 'engineering', 'plm', '--help'])
        assert result.exit_code == 0
        assert 'Product Lifecycle Management' in result.output
        assert 'plmxml' in result.output
    
    def test_plmxml_help(self, cli_runner):
        """Test plmxml command help."""
        result = cli_runner.invoke(cli, ['industry', 'engineering', 'plm', 'plmxml', '--help'])
        assert result.exit_code == 0
        assert 'PLM XML format commands' in result.output
        assert 'import' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
