"""Tests for execution operations commands (list, details, logs, abort, rerun, permanent-delete)."""

import json
import pytest

from vamscli.main import cli
from vamscli.utils.exceptions import (
    ExecutionNotFoundError, ExecutionInProgressError, InvalidExecutionDataError,
)


class TestExecutionList:
    def test_list_shows_output_target(self, cli_runner, generic_command_mocks):
        """The list projects the run's output target (from the execution's configuration row), so the
        CLI reports where the outputs landed without a second call."""
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1', 'workflowId': 'wf1',
                                       'workflowDatabaseId': 'db1', 'executionStatus': 'SUCCEEDED',
                                       'outputLocationType': 'asset',
                                       'outputAssetId': 'aOut', 'outputDatabaseId': 'dbOut'}]}}
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code == 0
            assert 'Output Type: asset' in result.output
            assert 'dbOut:aOut' in result.output

    def test_list_omits_output_target_for_results_only(self, cli_runner, generic_command_mocks):
        """A results-only run writes no files and has no destination asset, so the output lines are
        omitted rather than printed as N/A."""
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1', 'workflowId': 'wf1',
                                       'workflowDatabaseId': 'db1',
                                       'executionStatus': 'SUCCEEDED'}]}}
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code == 0
            assert 'Output Type:' not in result.output
            assert 'Output Asset:' not in result.output

    def test_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1', 'workflowId': 'wf1',
                                       'workflowDatabaseId': 'db1', 'executionStatus': 'SUCCEEDED'}]}}
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code == 0
            assert 'e1' in result.output

    def test_list_filters_passed(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, [
                'execution', 'list', '-w', 'wf1', '--status', 'RUNNING', '--group-id', 'grp1'])
            assert result.exit_code == 0
            params = mocks['api_client'].list_executions.call_args.kwargs['params']
            assert params['workflowId'] == 'wf1'
            assert params['status'] == 'RUNNING'
            assert params['groupId'] == 'grp1'

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1'}]}}
            result = cli_runner.invoke(cli, ['execution', 'list', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['Items'][0]['workflowExecutionId'] == 'e1'

    def test_list_empty_page_surfaces_next_token(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {
                'message': {'Items': [], 'NextToken': 'tok-abc'}}
            result = cli_runner.invoke(cli, ['execution', 'list', '--status', 'FAILED'])
            assert result.exit_code == 0
            assert 'tok-abc' in result.output
            assert 'more pages available' in result.output

    def test_list_auto_paginate(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.side_effect = [
                {'message': {'Items': [{'workflowExecutionId': 'e1'}], 'NextToken': 'tok'}},
                {'message': {'Items': [{'workflowExecutionId': 'e2'}]}},
            ]
            result = cli_runner.invoke(cli, ['execution', 'list', '--auto-paginate'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_executions.call_count == 2

    def test_list_auto_paginate_max_items_keeps_the_token(self, cli_runner, generic_command_mocks):
        """A script chunking a large deployment resumes from the outstanding token; without it the
        next chunk has to re-walk every page already fetched."""
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1'}], 'NextToken': 'tok-resume'}}
            result = cli_runner.invoke(cli, [
                'execution', 'list', '--auto-paginate', '--max-items', '1', '--json-output'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_executions.call_count == 1
            data = json.loads(result.output)
            assert data['NextToken'] == 'tok-resume'
            assert '--starting-token tok-resume' in data['note']

    def test_list_auto_paginate_omits_the_token_when_the_walk_completed(self, cli_runner,
                                                                      generic_command_mocks):
        # A token on a completed walk would send the caller after a page that does not exist.
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1'}]}}
            result = cli_runner.invoke(cli, [
                'execution', 'list', '--auto-paginate', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert 'NextToken' not in data and 'note' not in data

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('execution'):
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code != 0


class TestExecutionDetails:
    def test_details_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details.return_value = {
                'message': {'workflowExecutionId': 'e1', 'workflowId': 'wf1', 'workflowDatabaseId': 'db1',
                            'executionStatus': 'SUCCEEDED',
                            'pipelines': [{'name': 'p1', 'executionStatus': 'SUCCEEDED'}],
                            'inputFiles': [{'databaseId': 'db1', 'assetId': 'a1', 'inputAssetFileKey': '/f.glb'}],
                            'outputs': {'files': [{'relativeFilePath': '/out.glb'}]}}}
            result = cli_runner.invoke(cli, ['execution', 'details', 'e1'])
            assert result.exit_code == 0
            assert 'e1' in result.output
            assert 'p1' in result.output

    def test_details_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details.side_effect = ExecutionNotFoundError("nope")
            result = cli_runner.invoke(cli, ['execution', 'details', 'bad'])
            assert result.exit_code != 0

    def _details(self, mocks, cli_runner, message):
        mocks['api_client'].get_execution_details.return_value = {'message': message}
        return cli_runner.invoke(cli, ['execution', 'details', 'e1'])

    def test_details_marks_a_truncated_input_section(self, cli_runner, generic_command_mocks):
        """A shortened section must be marked where it is rendered — a user must never read a partial
        list as the complete set."""
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'inputFiles': [{'databaseId': 'db1', 'assetId': 'a1', 'inputAssetFileKey': '/f.glb'}],
                'truncatedCollections': ['inputFiles']})
            assert result.exit_code == 0
            assert 'Input files (1) [PARTIAL - more rows exist]' in result.output

    def test_details_does_not_mark_a_complete_section(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'inputFiles': [{'databaseId': 'db1', 'assetId': 'a1', 'inputAssetFileKey': '/f.glb'}],
                'truncatedCollections': []})
            assert result.exit_code == 0
            assert 'PARTIAL' not in result.output
            assert 'Truncated collections' not in result.output

    def test_details_reports_metadata_counts_and_their_truncation(self, cli_runner,
                                                                  generic_command_mocks):
        """The rows themselves are only in --json-output, so the counts (and their partial state) are
        what makes a truncated metadata section visible in CLI output."""
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'inputMetadata': [{'assetId': 'a1', 'metadata': {'k': 'v'}}],
                'inputDatabaseMetadata': [{'databaseId': 'src', 'metadata': {'k': 'v'}}],
                'truncatedCollections': ['inputMetadata']})
            assert result.exit_code == 0
            assert 'Input metadata: 1 row(s) [PARTIAL - more rows exist]' in result.output
            # The database collection was NOT truncated, so it carries no marker.
            assert 'Input database metadata: 1 row(s)\n' in result.output

    def test_details_shows_the_config_body_s3_location_when_truncated(self, cli_runner,
                                                                     generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'pipelines': [{'name': 'p1', 'executionStatus': 'SUCCEEDED',
                               'renderedConfigTruncated': True,
                               'renderedConfigLocation': {'bucket': 'run-bkt',
                                                          'key': 'executions/e1/input/1/config.json'}}]})
            assert result.exit_code == 0
            assert 'Config body: truncated in this response' in result.output
            assert 's3://run-bkt/executions/e1/input/1/config.json' in result.output

    def test_details_omits_the_config_location_when_not_truncated(self, cli_runner,
                                                                  generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'pipelines': [{'name': 'p1', 'executionStatus': 'SUCCEEDED',
                               'renderedConfigTruncated': False}]})
            assert result.exit_code == 0
            assert 'Config body' not in result.output
            assert 's3://' not in result.output

    def test_details_lists_every_captured_source_database_not_just_the_named_one(
            self, cli_runner, generic_command_mocks):
        """A run WITH input files derives its databases from those files, so the named id is empty
        while the captured list is what the run read. Rendering only the named id would report no
        database metadata source at all."""
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'metadataSourceDatabaseId': '',
                'metadataSourceDatabases': ['db-a', 'db-b'],
                'metadataSourceAssets': []})
            assert result.exit_code == 0
            assert 'Metadata sources:' in result.output
            assert 'Databases captured: db-a, db-b' in result.output
            assert 'Named database' not in result.output

    def test_details_shows_the_named_database_and_source_assets(self, cli_runner,
                                                               generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'metadataSourceDatabaseId': 'src-db',
                'metadataSourceDatabases': ['src-db'],
                'metadataSourceAssets': [{'databaseId': 'src-db', 'assetId': 'a1'}]})
            assert result.exit_code == 0
            assert 'Named database: src-db' in result.output
            assert 'Asset: src-db:a1' in result.output

    def test_details_omits_the_source_section_when_the_run_read_none(self, cli_runner,
                                                                    generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'metadataSourceDatabaseId': '',
                'metadataSourceDatabases': [],
                'metadataSourceAssets': []})
            assert result.exit_code == 0
            assert 'Metadata sources:' not in result.output

    def test_details_marks_truncated_database_metadata_independently(self, cli_runner,
                                                                    generic_command_mocks):
        """inputDatabaseMetadata is trimmed on its own, so it must carry its own marker rather than
        inheriting the asset/file collection's state."""
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'inputMetadata': [{'assetId': 'a1'}],
                'inputDatabaseMetadata': [{'databaseId': 'src'}],
                'truncatedCollections': ['inputDatabaseMetadata']})
            assert result.exit_code == 0
            assert 'Input metadata: 1 row(s)\n' in result.output
            assert 'Input database metadata: 1 row(s) [PARTIAL - more rows exist]' in result.output
            assert 'Truncated collections: inputDatabaseMetadata' in result.output

    def test_details_flags_an_empty_but_truncated_database_metadata_collection(
            self, cli_runner, generic_command_mocks):
        """Zero rows with the collection named as truncated is not "no database metadata" — it is a
        section the response could not carry, and must not read as complete."""
        with generic_command_mocks('execution') as mocks:
            result = self._details(mocks, cli_runner, {
                'workflowExecutionId': 'e1',
                'inputDatabaseMetadata': [],
                'truncatedCollections': ['inputDatabaseMetadata']})
            assert result.exit_code == 0
            assert 'Input database metadata: 0 row(s) [PARTIAL - more rows exist]' in result.output


class TestExecutionDetailsMetadata:
    def test_input_collection_is_the_default(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [{'databaseId': 'db1', 'assetId': 'a1', 'filePath': '/f.glb',
                           'scope': 'asset', 'metadata': {'k': 'v'}, 'pipelineId': 'p1'}],
                'collection': 'input'}}
            result = cli_runner.invoke(cli, ['execution', 'details-metadata', 'e1'])
            assert result.exit_code == 0
            params = mocks['api_client'].get_execution_details_metadata.call_args.kwargs['params']
            assert params['collection'] == 'input'
            assert 'db1:a1/f.glb' in result.output
            assert 'scope=asset' in result.output
            assert '[p1]' in result.output

    def test_output_collection_renders_the_output_row_shape(self, cli_runner, generic_command_mocks):
        """The output collection's rows have different fields (targetFilePath/metadataKey/
        metadataValue), so rendering them with the input columns would print only '?'."""
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [{'targetFilePath': '/out.glb', 'metadataKey': 'triangles',
                           'metadataValue': '1200', 'pipelineId': 'p1'}],
                'collection': 'output'}}
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--collection', 'output'])
            assert result.exit_code == 0
            assert mocks['api_client'].get_execution_details_metadata.call_args.kwargs[
                'params']['collection'] == 'output'
            assert '/out.glb' in result.output
            assert 'triangles=1200' in result.output

    def test_input_database_collection_passed_through(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [], 'collection': 'inputDatabase'}}
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--collection', 'inputDatabase'])
            assert result.exit_code == 0
            assert mocks['api_client'].get_execution_details_metadata.call_args.kwargs[
                'params']['collection'] == 'inputDatabase'

    def test_unknown_collection_rejected_before_the_call(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--collection', 'bogus'])
            assert result.exit_code != 0
            mocks['api_client'].get_execution_details_metadata.assert_not_called()

    def test_pipeline_id_filter_passed(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [], 'collection': 'input'}}
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--pipeline-id', 'p1'])
            assert result.exit_code == 0
            assert mocks['api_client'].get_execution_details_metadata.call_args.kwargs[
                'params']['pipelineId'] == 'p1'

    def test_page_size_above_the_cap_is_clamped(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [], 'collection': 'input'}}
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--page-size', '5000'])
            assert result.exit_code == 0
            assert mocks['api_client'].get_execution_details_metadata.call_args.kwargs[
                'params']['pageSize'] == 500

    def test_starting_token_resumes(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [], 'collection': 'input'}}
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--starting-token', 'tok-abc'])
            assert result.exit_code == 0
            assert mocks['api_client'].get_execution_details_metadata.call_args.kwargs[
                'params']['startingToken'] == 'tok-abc'

    def test_next_token_is_surfaced_for_manual_paging(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [{'databaseId': 'db1', 'assetId': 'a1', 'filePath': '/f.glb',
                           'scope': 'asset', 'metadata': {}}],
                'collection': 'input', 'NextToken': 'tok-next'}}
            result = cli_runner.invoke(cli, ['execution', 'details-metadata', 'e1'])
            assert result.exit_code == 0
            assert 'tok-next' in result.output

    def test_auto_paginate_stops_when_next_token_is_absent(self, cli_runner, generic_command_mocks):
        """NextToken absent is the ONLY end-of-walk signal, and every page's rows must be kept —
        a walk that stopped early or dropped a page would under-report the collection."""
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.side_effect = [
                {'message': {'Items': [{'databaseId': 'db1', 'assetId': 'a1', 'filePath': '/1',
                                        'scope': 'asset', 'metadata': {}}],
                             'collection': 'input', 'NextToken': 'tok1'}},
                {'message': {'Items': [{'databaseId': 'db1', 'assetId': 'a2', 'filePath': '/2',
                                        'scope': 'asset', 'metadata': {}}],
                             'collection': 'input'}},
            ]
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--auto-paginate', '--json-output'])
            assert result.exit_code == 0
            assert mocks['api_client'].get_execution_details_metadata.call_count == 2
            data = json.loads(result.output)
            assert [row['assetId'] for row in data['Items']] == ['a1', 'a2']
            # The second request resumes with the first page's token.
            second = mocks['api_client'].get_execution_details_metadata.call_args_list[1]
            assert second.kwargs['params']['startingToken'] == 'tok1'

    def test_auto_paginate_stops_at_max_items(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [{'databaseId': 'db1', 'assetId': 'a1', 'filePath': '/1',
                           'scope': 'asset', 'metadata': {}}],
                'collection': 'input', 'NextToken': 'tok'}}
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--auto-paginate', '--max-items', '1',
                '--json-output'])
            assert result.exit_code == 0
            assert mocks['api_client'].get_execution_details_metadata.call_count == 1
            data = json.loads(result.output)
            assert 'More may be available' in data['note']
            # The outstanding token is the only way to continue the walk.
            assert data['NextToken'] == 'tok'
            assert '--starting-token tok' in data['note']

    def test_auto_paginate_rejects_a_starting_token(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--auto-paginate',
                '--starting-token', 'tok'])
            assert result.exit_code != 0
            mocks['api_client'].get_execution_details_metadata.assert_not_called()

    def test_json_output_is_the_page_payload(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [{'databaseId': 'db1', 'assetId': 'a1', 'filePath': '/f.glb',
                           'scope': 'asset', 'metadata': {'k': 'v'}, 'pipelineId': 'p1'}],
                'collection': 'input', 'NextToken': 'tok'}}
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['collection'] == 'input'
            assert data['NextToken'] == 'tok'
            assert data['Items'][0]['metadata'] == {'k': 'v'}

    def test_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.side_effect = \
                ExecutionNotFoundError("nope")
            result = cli_runner.invoke(cli, ['execution', 'details-metadata', 'bad'])
            assert result.exit_code != 0

    def test_invalid_token_reports_the_collection_pinning(self, cli_runner, generic_command_mocks):
        """A token issued for one collection/pipelineId is refused by the service, so the error must
        say which inputs a resume has to match."""
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.side_effect = \
                InvalidExecutionDataError("startingToken is invalid.")
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--starting-token', 'stale'])
            assert result.exit_code != 0
            assert '--collection' in result.output

    def test_empty_collection_message(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details_metadata.return_value = {'message': {
                'Items': [], 'collection': 'output'}}
            result = cli_runner.invoke(cli, [
                'execution', 'details-metadata', 'e1', '--collection', 'output'])
            assert result.exit_code == 0
            assert 'No output metadata rows found.' in result.output

    def test_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('execution'):
            result = cli_runner.invoke(cli, ['execution', 'details-metadata', 'e1'])
            assert result.exit_code != 0


class TestExecutionLogs:
    def test_logs_truncated_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_logs.return_value = {
                'message': {'mode': 'truncated', 'executionLog': 'log text', 'executionError': ''}}
            result = cli_runner.invoke(cli, ['execution', 'logs', 'e1'])
            assert result.exit_code == 0
            assert 'log text' in result.output
            assert mocks['api_client'].get_execution_logs.call_args.kwargs['params']['mode'] == 'truncated'

    def test_logs_full_mode(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_logs.return_value = {
                'message': {'mode': 'full', 'events': [{'timestamp': 1, 'message': 'evt'}]}}
            result = cli_runner.invoke(cli, ['execution', 'logs', 'e1', '--mode', 'full', '--limit', '10'])
            assert result.exit_code == 0
            params = mocks['api_client'].get_execution_logs.call_args.kwargs['params']
            assert params['mode'] == 'full'
            assert params['limit'] == 10

    def test_logs_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_logs.side_effect = ExecutionNotFoundError("nope")
            result = cli_runner.invoke(cli, ['execution', 'logs', 'bad'])
            assert result.exit_code != 0

    def test_logs_full_mode_renders_the_per_step_and_history_logs(self, cli_runner,
                                                                  generic_command_mocks):
        """subProcessEvents and sfnHistoryEvents must reach the human-readable output.

        These carry the step invocation log (a Lambda step's own CloudWatch log) and the state
        transition timeline — the logs that explain a launch that failed before the pipeline started.
        Rendering only `events` left them reachable solely via --json-output.
        """
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_logs.return_value = {'message': {
                'mode': 'full',
                'pipelineExecutionId': 'p1',
                'events': [{'timestamp': 1, 'message': 'main-evt'}],
                'sfnHistoryEvents': [{'timestamp': 2, 'message': 'TaskStateEntered: Convert'}],
                'subProcessEvents': [{
                    'timestamp': 3, 'message': 'lambda-evt',
                    'logGroupArn': 'arn:aws:logs:us-west-2:1:log-group:/aws/lambda/vams-fn:*'}],
            }}
            result = cli_runner.invoke(cli, ['execution', 'logs', 'e1', '--mode', 'full'])
            assert result.exit_code == 0
            assert 'main-evt' in result.output
            assert 'TaskStateEntered: Convert' in result.output
            assert 'lambda-evt' in result.output
            # The originating log group is named: subProcessEvents mix several groups, so an
            # unlabelled line cannot be attributed to the step's own log vs a registered one.
            assert '/aws/lambda/vams-fn' in result.output

    def test_logs_warnings_are_shown_not_swallowed(self, cli_runner, generic_command_mocks):
        # A log VAMS could not read. Hiding it makes partial output look complete.
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_logs.return_value = {'message': {
                'mode': 'full',
                'events': [{'timestamp': 1, 'message': 'evt'}],
                'warnings': ['Step invocation log retrieval failed for arn:...: AccessDenied'],
            }}
            result = cli_runner.invoke(cli, ['execution', 'logs', 'e1', '--mode', 'full'])
            assert result.exit_code == 0
            assert 'Warnings' in result.output
            assert 'AccessDenied' in result.output

    def test_logs_omits_empty_sections(self, cli_runner, generic_command_mocks):
        # An execution type with no invocation log (SQS/EventBridge/DeadlineCloud) returns no
        # subProcessEvents; an empty section header would read as "there is a log and it is blank".
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_logs.return_value = {'message': {
                'mode': 'full', 'events': [{'timestamp': 1, 'message': 'evt'}]}}
            result = cli_runner.invoke(cli, ['execution', 'logs', 'e1', '--mode', 'full'])
            assert result.exit_code == 0
            assert 'Sub-Process Logs' not in result.output
            assert 'Warnings' not in result.output


class TestExecutionAbort:
    def test_abort_single_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].abort_execution.return_value = {'message': 'Execution aborted'}
            result = cli_runner.invoke(cli, ['execution', 'abort', 'e1'])
            assert result.exit_code == 0
            assert mocks['api_client'].abort_execution.call_args.kwargs['group_id'] is None

    def test_abort_group_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].abort_execution.return_value = {
                'message': {'groupId': 'grp1', 'results': [{'executionId': 'e1', 'status': 'aborted'}]}}
            result = cli_runner.invoke(cli, ['execution', 'abort', 'e1', '--group-id', 'grp1', '--yes'])
            assert result.exit_code == 0
            assert mocks['api_client'].abort_execution.call_args.kwargs['group_id'] == 'grp1'

    def test_abort_group_requires_confirmation(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            result = cli_runner.invoke(cli, ['execution', 'abort', 'e1', '--group-id', 'grp1'],
                                       input='n\n')
            assert result.exit_code != 0
            mocks['api_client'].abort_execution.assert_not_called()

    def test_abort_carries_warnings(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].abort_execution.return_value = {
                'message': 'Execution aborted', 'warnings': ['Sub-process abort failed']}
            result = cli_runner.invoke(cli, ['execution', 'abort', 'e1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['warnings'] == ['Sub-process abort failed']

    def test_abort_requires_target(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution'):
            result = cli_runner.invoke(cli, ['execution', 'abort'])
            assert result.exit_code != 0

    def test_abort_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].abort_execution.side_effect = ExecutionNotFoundError("nope")
            result = cli_runner.invoke(cli, ['execution', 'abort', 'bad'])
            assert result.exit_code != 0


class TestExecutionRerun:
    def test_rerun_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].rerun_execution.return_value = {'message': {'executionId': 'e2'}}
            result = cli_runner.invoke(cli, ['execution', 'rerun', 'e1'])
            assert result.exit_code == 0
            assert 'e2' in result.output

    def test_rerun_with_group(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].rerun_execution.return_value = {'message': {'executionId': 'e2', 'executionGroupId': 'grp1'}}
            result = cli_runner.invoke(cli, ['execution', 'rerun', 'e1', '--execution-group-id', 'grp1'])
            assert result.exit_code == 0
            assert mocks['api_client'].rerun_execution.call_args.kwargs['execution_group_id'] == 'grp1'

    def test_rerun_unavailable(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].rerun_execution.side_effect = InvalidExecutionDataError("unavailable")
            result = cli_runner.invoke(cli, ['execution', 'rerun', 'e1'])
            assert result.exit_code != 0

    def test_rerun_surfaces_response_warnings(self, cli_runner, generic_command_mocks):
        """A re-run goes through the execute path, so it returns that handler's warnings — e.g. a
        metadata capture the per-entity cap bounded, meaning the re-run's inputs differ."""
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].rerun_execution.return_value = {'message': {
                'executionId': 'e2',
                'warnings': ["Metadata for asset 'a1' was truncated at the per-entity cap."]}}
            result = cli_runner.invoke(cli, ['execution', 'rerun', 'e1'])
            assert result.exit_code == 0
            assert 'Warnings:' in result.output
            assert 'truncated at the per-entity cap' in result.output

    def test_rerun_warnings_reach_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].rerun_execution.return_value = {'message': {
                'executionId': 'e2', 'warnings': ['Metadata truncated.']}}
            result = cli_runner.invoke(cli, ['execution', 'rerun', 'e1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['warnings'] == ['Metadata truncated.']


class TestExecutionPermanentDelete:
    def test_permanent_delete_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].permanent_delete_execution.return_value = {
                'message': 'Execution records permanently deleted'}
            result = cli_runner.invoke(cli, ['execution', 'permanent-delete', 'e1', '--yes'])
            assert result.exit_code == 0

    def test_permanent_delete_in_progress(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].permanent_delete_execution.side_effect = ExecutionInProgressError("in progress")
            result = cli_runner.invoke(cli, ['execution', 'permanent-delete', 'e1', '--yes'])
            assert result.exit_code != 0

    def test_permanent_delete_json_with_yes(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].permanent_delete_execution.return_value = {'message': 'deleted'}
            result = cli_runner.invoke(cli, [
                'execution', 'permanent-delete', 'e1', '--yes', '--json-output'])
            assert result.exit_code == 0
            mocks['api_client'].permanent_delete_execution.assert_called_once()

    def test_permanent_delete_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].permanent_delete_execution.side_effect = ExecutionNotFoundError("nope")
            result = cli_runner.invoke(cli, ['execution', 'permanent-delete', 'bad', '--yes'])
            assert result.exit_code != 0
