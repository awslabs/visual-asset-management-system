"""Tests for the Isaac Sim connector's VAMS CLI wrapper.

The wrapper builds `vamscli` argument lists and parses `--json-output`, so nothing catches a drift
against the CLI at import time. These tests pin the argument lists, the per-command JSON keys, and
the workflow input gates the connector uses to decide which run controls to offer.
"""

import json

import pytest

from vams.connector.isaacsim.vams_cli_service import Workflow


def _workflow_list(*items):
    return json.dumps({"Items": list(items)})


def _wf_item(workflow_id, **overrides):
    item = {
        "workflowId": workflow_id,
        "workflowName": workflow_id,
        "databaseId": "GLOBAL",
        "enabled": True,
        "archived": False,
        "systemConfig": {},
    }
    item.update(overrides)
    return item


class TestWorkflowRunnability:
    """A disabled or archived workflow is rejected by the execute API, so it is never offered."""

    def test_enabled_unarchived_is_runnable(self):
        assert Workflow(enabled=True, archived=False).is_runnable is True

    def test_disabled_is_not_runnable(self):
        assert Workflow(enabled=False, archived=False).is_runnable is False

    def test_archived_is_not_runnable(self):
        assert Workflow(enabled=True, archived=True).is_runnable is False

    def test_list_drops_disabled_and_archived(self, cli_service):
        cli_service.responses = [_workflow_list(
            _wf_item("ok"),
            _wf_item("off", enabled=False),
            _wf_item("gone", archived=True),
        )]
        assert [wf.workflow_id for wf in cli_service.list_workflows()] == ["ok"]

    def test_list_can_include_unrunnable(self, cli_service):
        cli_service.responses = [_workflow_list(_wf_item("ok"), _wf_item("off", enabled=False))]
        listed = cli_service.list_workflows(include_unrunnable=True)
        assert [wf.workflow_id for wf in listed] == ["ok", "off"]

    def test_list_never_requests_archived(self, cli_service):
        cli_service.responses = [_workflow_list()]
        cli_service.list_workflows("my-db")
        assert cli_service.commands == [[
            "workflow", "list", "--auto-paginate", "--json-output", "-d", "my-db",
        ]]


class TestWholeAssetGate:
    """assetScope decides whether a '/' selection is accepted. Both the canonical
    `wholeAssetAllowed` key and the registration shorthand `wholeAsset` are honored, and an
    undeclared scope denies — matching the workflow-level gate's `scope.get(key, False)`."""

    @pytest.mark.parametrize("scope, expected", [
        ({"wholeAssetAllowed": True}, True),
        ({"wholeAssetAllowed": False}, False),
        ({"wholeAsset": True}, True),
        ({"wholeAsset": False}, False),
        ({}, False),
    ])
    def test_scope_key_variants(self, scope, expected):
        wf = Workflow(system_config={"inputFileArity": "one", "assetScope": scope})
        assert wf.allows_whole_asset is expected

    @pytest.mark.parametrize("system_config", [
        {"inputFileArity": "one"},
        {"inputFileArity": "one", "assetScope": {}},
        {"inputFileArity": "one", "assetScope": None},
        {"inputFileArity": "one", "assetScope": {"folderAllowed": True}},
    ])
    def test_an_undeclared_scope_denies_the_whole_asset(self, system_config):
        """`_scope_errors` is called for the workflow WITHOUT `declared_only`, so its
        `scope.get("wholeAssetAllowed", False)` denies a '/' selection on any scope that does not
        declare the key. Defaulting to True here would offer a run the API rejects outright.

        `vamscli workflow create --system-config '{"inputFileArity":"one"}'` stores exactly these
        shapes: the request systemConfig is persisted verbatim, and the Pydantic model defaults
        assetScope to an empty map rather than to the deny-by-default block.
        """
        assert Workflow(system_config=system_config).allows_whole_asset is False

    def test_canonical_key_wins_over_shorthand(self):
        wf = Workflow(system_config={
            "inputFileArity": "one",
            "assetScope": {"wholeAssetAllowed": False, "wholeAsset": True},
        })
        assert wf.allows_whole_asset is False

    def test_arity_none_takes_no_selection(self):
        wf = Workflow(system_config={
            "inputFileArity": "none",
            "assetScope": {"wholeAssetAllowed": True},
        })
        assert wf.input_file_arity == "none"
        assert wf.allows_whole_asset is False

    def test_arity_defaults_to_one(self):
        assert Workflow(system_config={}).input_file_arity == "one"

    def test_system_config_is_parsed_from_the_listing(self, cli_service):
        scope = {"crossAssetAllowed": False, "singleAssetOnly": True,
                 "wholeAssetAllowed": False, "folderAllowed": False}
        cli_service.responses = [_workflow_list(_wf_item(
            "3d-basic", systemConfig={"inputFileArity": "one", "assetScope": scope}))]
        wf = cli_service.list_workflows()[0]
        assert wf.system_config["assetScope"] == scope
        assert wf.allows_whole_asset is False

    def test_a_declared_allowance_is_still_honored(self):
        """Control for the deny-by-default cases above: an explicit True must still allow, otherwise
        `allows_whole_asset` could satisfy every test by returning False unconditionally."""
        wf = Workflow(system_config={"inputFileArity": "one",
                                     "assetScope": {"wholeAssetAllowed": True}})
        assert wf.allows_whole_asset is True


class TestInputFileFilterGate:
    """systemConfig.inputFileFilters is a HARD execute-time gate ("One or more input files fail the
    workflow input-file filters."), so a rejected file is offered no run control. These mirror the
    service's allow-then-exclude evaluation for one concrete file."""

    @pytest.mark.parametrize("key, expected", [
        ("/a.glb", True),
        ("a.glb", True),          # normalized to the leading-slash form the execute request carries
        ("/A.GLB", True),         # matching is case-insensitive
        ("/notes.txt", False),
        ("/models/b.glb", True),
    ])
    def test_an_extension_allow_list(self, key, expected):
        wf = Workflow(system_config={"inputFileFilters": {"allow": ["*.glb"], "exclude": []}})
        assert wf.allows_file(key) is expected

    @pytest.mark.parametrize("allow", [None, [], ["*"], ["*.*"], ["/**"], ["  "]])
    def test_an_open_allow_list_admits_everything(self, allow):
        """Absent, empty, and match-everything allow lists all mean "no restriction at this level"."""
        wf = Workflow(system_config={"inputFileFilters": {"allow": allow}})
        assert wf.allows_file("/notes.txt") is True

    def test_no_filters_at_all_admits_everything(self):
        assert Workflow(system_config={}).allows_file("/notes.txt") is True

    def test_exclude_is_applied_after_allow(self):
        wf = Workflow(system_config={"inputFileFilters": {
            "allow": ["*.glb"], "exclude": ["*skip*"]}})
        assert wf.allows_file("/a.glb") is True
        assert wf.allows_file("/skip-me.glb") is False

    def test_a_path_glob_matches_the_leading_slash_form(self):
        wf = Workflow(system_config={"inputFileFilters": {"allow": ["/models/*"]}})
        assert wf.allows_file("models/a.glb") is True
        assert wf.allows_file("textures/a.png") is False

    def test_an_exact_key_allow_list(self):
        wf = Workflow(system_config={"inputFileFilters": {"allow": ["/models/a.glb"]}})
        assert wf.allows_file("/models/a.glb") is True
        assert wf.allows_file("/models/b.glb") is False

    def test_filters_are_parsed_from_the_listing(self, cli_service):
        filters = {"allow": ["*.glb"], "exclude": ["*.previewFile.*"]}
        cli_service.responses = [_workflow_list(_wf_item(
            "3d-basic", systemConfig={"inputFileArity": "one", "inputFileFilters": filters}))]
        wf = cli_service.list_workflows()[0]
        assert wf.input_file_filters == filters
        assert wf.allows_file("/a.glb") is True
        assert wf.allows_file("/a.previewFile.png") is False


class TestExecuteWorkflowArguments:
    """`workflow execute` addresses inputs as 'databaseId:assetId:relativeFileKey'."""

    def test_file_key_is_normalized_to_a_leading_slash(self, cli_service):
        cli_service.responses = ['{"message": {"executionId": "e1"}}']
        cli_service.execute_workflow("db", "asset", "wf", "GLOBAL", file_key="models/a.glb")
        assert cli_service.commands == [[
            "workflow", "execute",
            "--workflow-database-id", "GLOBAL",
            "-w", "wf",
            "--input-file", "db:asset:/models/a.glb",
            "--json-output",
        ]]

    def test_omitted_file_key_selects_the_whole_asset(self, cli_service):
        cli_service.responses = ['{"executionId": "e1"}']
        cli_service.execute_workflow("db", "asset", "wf", "GLOBAL")
        assert cli_service.commands[0][cli_service.commands[0].index("--input-file") + 1] == \
            "db:asset:/"


class TestListingArgumentsAndKeys:
    """Each command's argument list and the JSON keys that specific command returns. `file list`
    items and the `file info` response are different shapes, so keys are asserted per command."""

    def test_database_list(self, cli_service):
        cli_service.responses = [json.dumps({"Items": [
            {"databaseId": "db", "description": "d", "dateCreated": "2026-01-01", "assetCount": 3}]})]
        databases = cli_service.list_databases()
        assert cli_service.commands == [["database", "list", "--auto-paginate", "--json-output"]]
        assert (databases[0].database_id, databases[0].asset_count) == ("db", 3)

    def test_asset_list(self, cli_service):
        cli_service.responses = [json.dumps({"Items": [
            {"assetId": "a1", "assetName": "Asset One", "databaseId": "db",
             "isDistributable": True, "status": "active", "tags": ["t"]}]})]
        assets = cli_service.list_assets("db")
        assert cli_service.commands == [[
            "assets", "list", "--database-id", "db", "--auto-paginate", "--json-output"]]
        assert (assets[0].asset_id, assets[0].asset_name) == ("a1", "Asset One")

    def test_file_list_uses_the_lowercase_items_key(self, cli_service):
        """The real `--basic` item shape. Basic mode skips the per-object head_object calls, so the
        service hard-codes `versionId: null`, `primaryType: null` and `previewFile: ""`; only the
        fields the S3 list response itself supplies are populated. Pinning the nulls here records
        that the listing CANNOT supply them, so a caller needing them must enrich from `file info`
        (which supplies primaryType and previewFile — but has no top-level versionId at all).
        """
        cli_service.responses = [json.dumps({"items": [
            {"fileName": "a.glb", "relativePath": "/a.glb", "key": "p/a.glb", "size": 12,
             "isFolder": False, "isArchived": False, "primaryType": None,
             "dateCreatedCurrentVersion": "2026-01-01T00:00:00Z", "versionId": None,
             "etag": "e", "previewFile": "", "storageClass": "STANDARD"}]})]
        files = cli_service.list_files("db", "a1")
        assert cli_service.commands == [[
            "file", "list", "-d", "db", "-a", "a1",
            "--basic", "--auto-paginate", "--json-output"]]
        entry = files[0]
        assert (entry.file_name, entry.relative_path, entry.key) == ("a.glb", "/a.glb", "p/a.glb")
        assert (entry.size, entry.etag) == (12, "e")
        assert entry.date_created_current_version == "2026-01-01T00:00:00Z"
        # contentType is a file-info-only field; a listing entry never carries one.
        assert entry.content_type == ""
        # These three are null/empty in every --basic listing, so the mappings can never populate
        # them. A JSON null must land as "" rather than None, or a caller string-formatting the
        # field prints "None".
        assert entry.primary_type == ""
        assert entry.version_id == ""
        assert entry.preview_file == ""

    def test_a_full_mode_style_listing_would_populate_primary_type(self, cli_service):
        """Control for the assertions above: the mappings themselves work, so the empty values in
        `--basic` come from the response and not from a broken key name."""
        cli_service.responses = [json.dumps({"items": [
            {"fileName": "a.glb", "relativePath": "/a.glb", "primaryType": "model",
             "versionId": "v1", "previewFile": "/a.previewFile.png"}]})]
        entry = cli_service.list_files("db", "a1")[0]
        assert (entry.primary_type, entry.version_id, entry.preview_file) == (
            "model", "v1", "/a.previewFile.png")

    def test_file_info_carries_content_type(self, cli_service):
        cli_service.responses = [json.dumps({
            "fileName": "a.glb", "relativePath": "/a.glb", "contentType": "model/gltf-binary",
            "lastModified": "2026-01-02T00:00:00Z"})]
        info = cli_service.get_file_info("db", "a1", "/a.glb")
        assert cli_service.commands == [[
            "file", "info", "-d", "db", "-a", "a1", "-p", "/a.glb", "--json-output"]]
        assert info["contentType"] == "model/gltf-binary"

    def test_list_workflow_executions(self, cli_service):
        cli_service.responses = [json.dumps({"Items": [
            {"workflowExecutionId": "x1", "workflowId": "wf", "workflowDatabaseId": "GLOBAL",
             "executionStatus": "Succeeded", "triggerType": "manual", "executionGroupId": "",
             "executionStartDate": "2026-01-01T00:00:00Z",
             "executionStopDate": "2026-01-01T00:05:00Z",
             "inputAssetFileKey": "prefix/a1/a.glb"}]})]
        executions = cli_service.list_workflow_executions("db", "a1", workflow_id="wf")
        assert cli_service.commands == [[
            "workflow", "list-executions", "-d", "db", "-a", "a1",
            "--auto-paginate", "--json-output", "-w", "wf"]]
        execution = executions[0]
        assert execution.execution_id == "x1"
        assert execution.start_date == "2026-01-01T00:00:00Z"
        assert execution.input_file_key == "prefix/a1/a.glb"

    def test_download_asset_recursive(self, cli_service, tmp_path):
        cli_service.responses = [json.dumps({
            "overall_success": True, "total_files": 2, "successful_files": 2, "failed_files": 0,
            "total_size": 99, "total_size_formatted": "99 B",
            "successful_downloads": [], "failed_downloads": []})]
        target = str(tmp_path / "out")
        result = cli_service.download_asset(target, "db", "a1")
        assert cli_service.commands == [[
            "assets", "download", target, "-d", "db", "-a", "a1",
            "--file-key", "/", "--recursive", "--json-output"]]
        assert (result.overall_success, result.total_files) == (True, 2)

    def test_upload_file(self, cli_service, tmp_path):
        source = tmp_path / "scene.usd"
        source.write_text("usd")
        cli_service.responses = ['{"overall_success": true}']
        cli_service.upload_file(str(source), "db", "a1")
        assert cli_service.commands == [[
            "file", "upload", str(source), "-d", "db", "-a", "a1",
            "--json-output", "--hide-progress"]]

    def test_upload_directory_recursive(self, cli_service, tmp_path):
        cli_service.responses = ['{"overall_success": true}']
        cli_service.upload_directory(str(tmp_path), "db", "a1")
        assert cli_service.commands == [[
            "file", "upload", "--directory", str(tmp_path), "-d", "db", "-a", "a1",
            "--json-output", "--hide-progress", "--recursive"]]
