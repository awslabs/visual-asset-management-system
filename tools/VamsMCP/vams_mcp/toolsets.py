"""Named tool sets: which tools a server registers (``VAMS_MCP_TOOLSETS``).

A remote MCP client limits how many tools one connection may list (Amazon Quick lists at most 100 and
silently drops the rest), and every connected agent reads every description on every session. A tool
set is a named selection of tools for one kind of user, so a connection carries only what that user
needs. Sets combine (``consumer,edh``), and the write and destructive gates still apply inside a set:
a write tool in ``operator`` is registered only with ``VAMS_ENABLE_WRITES=true``.

Each named set holds at most ``MAX_TOOLS_PER_SET`` tools and ``MAX_DESCRIPTION_CHARS_PER_SET``
characters of descriptions with every gate and add-on on (``tests/test_toolsets.py``). ``all`` (the
default over stdio) registers every tool the gates allow, as before tool sets existed. ``custom``
selects the tools a fork adds in ``custom_tools.py``: a tool listed in no named set.

This module imports nothing from the server, so ``config.py`` can validate names at start.
"""

from __future__ import annotations

from typing import FrozenSet, Iterable, Optional, Tuple

MAX_TOOLS_PER_SET = 100
MAX_DESCRIPTION_CHARS_PER_SET = 30000

ALL = "all"
CUSTOM = "custom"

# The default selection of each transport. Over stdio the server keeps its full surface; a remote
# server starts with the read-only consumer set, and the operator opts into more.
DEFAULT_STDIO: Tuple[str, ...] = (ALL,)
DEFAULT_REMOTE: Tuple[str, ...] = ("consumer",)

# Reads a person working with assets needs. No credential inventory, no other users' identifiers, no
# administration reads. The download-link tools are reads (they change nothing) and are here; each
# link they return is a bearer credential, which the docs say.
CONSUMER: FrozenSet[str] = frozenset(
    {
        "vams_status",
        "list_features",
        "list_allowed_api_routes",
        "list_databases",
        "get_database",
        "get_database_metadata",
        "list_assets",
        "get_asset",
        "get_asset_metadata",
        "get_asset_history",
        "export_asset",
        "search_assets",
        "search_files",
        "get_search_fields",
        "find_and_summarize",
        "list_asset_files",
        "get_file_info",
        "get_file_metadata",
        "generate_download_url",
        "generate_download_urls_bulk",
        "list_asset_versions",
        "get_asset_version",
        "get_asset_links",
        "get_asset_link_metadata",
        "list_tags",
        "list_tag_types",
        "list_metadata_schemas",
        "list_workflows",
        "get_workflow",
        "list_workflow_executions",
        "list_executions",
        "get_execution_details",
        "page_execution_detail_metadata",
        "page_execution_detail_files",
        "get_execution_results",
        "get_execution_logs",
        "list_asset_comments",
        "list_asset_version_comments",
        "get_comment",
        "check_subscription",
        "list_zip_downloads",
        "get_zip_download",
    }
)

# The consumer set plus the writes and deletes of asset content: assets, files, metadata, versions,
# links, comments, subscriptions, Download as Zip jobs, and running workflows.
OPERATOR: FrozenSet[str] = CONSUMER | frozenset(
    {
        "create_asset",
        "update_asset",
        "set_asset_metadata",
        "set_file_metadata",
        "set_primary_file",
        "create_folder",
        "move_file",
        "copy_file",
        "create_asset_version",
        "revert_file_version",
        "create_asset_link",
        "add_comment",
        "update_comment",
        "create_subscription",
        "update_subscription",
        "execute_workflow",
        "rerun_execution",
        "abort_execution",
        "archive_asset",
        "unarchive_asset",
        "delete_asset",
        "delete_asset_metadata",
        "archive_file",
        "unarchive_file",
        "delete_file",
        "delete_asset_link",
        "delete_comment",
        "delete_subscription",
        "unsubscribe",
        "create_zip_download",
        "get_zip_download_url",
        "cancel_zip_download",
        "delete_zip_download",
    }
)

# Deployment administration: databases, tags and tag types, metadata schemas, pipelines, templates,
# workflows and triggers, executions, subscriptions and API keys. User, role and constraint
# administration has no tools (it stays in the CLI).
ADMIN: FrozenSet[str] = frozenset(
    {
        "vams_status",
        "list_features",
        "list_allowed_api_routes",
        "list_api_routes",
        "list_constraint_permission_objects",
        "list_databases",
        "get_database",
        "list_buckets",
        "create_database",
        "delete_database",
        "list_tags",
        "list_tag_types",
        "create_tag",
        "update_tag",
        "create_tag_type",
        "update_tag_type",
        "delete_tag",
        "delete_tag_type",
        "list_metadata_schemas",
        "create_metadata_schema",
        "update_metadata_schema",
        "delete_metadata_schema",
        "list_pipelines",
        "get_pipeline",
        "create_pipeline",
        "update_pipeline",
        "archive_pipeline",
        "unarchive_pipeline",
        "list_pipeline_templates",
        "get_pipeline_template",
        "get_pipeline_template_tag_schema",
        "create_pipeline_template",
        "update_pipeline_template",
        "set_pipeline_template_tag_schema",
        "delete_pipeline_template",
        "list_workflows",
        "get_workflow",
        "create_workflow",
        "update_workflow",
        "archive_workflow",
        "unarchive_workflow",
        "list_workflow_triggers",
        "get_workflow_trigger",
        "set_workflow_trigger",
        "delete_workflow_trigger",
        "list_executions",
        "get_execution_details",
        "get_execution_logs",
        "abort_execution",
        "permanent_delete_execution",
        "list_subscriptions",
        "get_api_key",
        "list_api_keys",
        "create_api_key",
        "update_api_key",
        "delete_api_key",
        "get_user_api_key",
        "list_user_api_keys",
        "create_user_api_key",
        "update_user_api_key",
        "delete_user_api_key",
    }
)

# Compliance: schemas, bindings, evaluations and sweeps, quarantine and exceptions, cascades and the
# compliance audit trail, for the people who run compliance, with the reads that orient a session.
# The compliance tools are in this set only, and their descriptions keep a total of their own
# (tests/test_agent_surface.py MAX_COMPLIANCE_TOTAL_CHARS), so they take no room from another set.
# The gates apply as everywhere: the reads are always registered, the writes (create, update, bind,
# unbind, evaluate, sweep, release, exceptions, cascades) only with VAMS_ENABLE_WRITES, and
# delete_compliance_schema only with VAMS_ENABLE_DESTRUCTIVE.
COMPLIANCE_TOOLS: FrozenSet[str] = frozenset(
    {
        "list_compliance_schemas",
        "get_compliance_schema",
        "get_compliance_bindings",
        "get_asset_compliance_state",
        "get_database_compliance_overview",
        "list_compliance_evaluations",
        "list_quarantined_assets",
        "list_compliance_cascades",
        "get_compliance_cascade",
        "query_compliance_audit",
        "get_asset_compliance_audit",
        "create_compliance_schema",
        "update_compliance_schema",
        "bind_compliance_schema",
        "unbind_compliance_schema",
        "evaluate_asset_compliance",
        "sweep_compliance_schema",
        "evaluate_database_compliance",
        "release_quarantine",
        "grant_quarantine_exception",
        "revoke_quarantine_exception",
        "create_compliance_cascade",
        "approve_compliance_cascade",
        "reject_compliance_cascade",
        "delete_compliance_schema",
    }
)
COMPLIANCE: FrozenSet[str] = COMPLIANCE_TOOLS | frozenset(
    {
        "vams_status",
        "list_features",
        "list_allowed_api_routes",
        "list_databases",
        "get_database",
    }
)

# EDH Workstations add-on, for the people who use workstations and container jobs.
EDH: FrozenSet[str] = frozenset(
    {
        "get_edh_health",
        "get_edh_capacity",
        "list_edh_workstations",
        "get_edh_workstation",
        "list_edh_catalog_images",
        "list_edh_catalog_instance_types",
        "get_edh_catalog_lease",
        "list_edh_catalog_capacity_reservations",
        "list_edh_container_catalog",
        "list_edh_container_jobs",
        "get_edh_workstation_connect",
        "create_edh_workstation",
        "start_edh_workstation",
        "renew_edh_workstation",
        "stop_edh_workstation",
        "submit_edh_container_job",
        "delete_edh_workstation",
        "kill_edh_container_job",
    }
)

# EDH Workstations add-on, for its administrators: images, AMI recipes and builds, capacity
# reservations, instances and settings.
EDH_ADMIN: FrozenSet[str] = frozenset(
    {
        "get_edh_health",
        "get_edh_capacity",
        "list_edh_admin_instances",
        "list_edh_stale_instances",
        "stop_edh_admin_instance",
        "deactivate_edh_admin_instance",
        "get_edh_admin_settings",
        "update_edh_admin_settings",
        "list_edh_admin_images",
        "register_edh_vdi_image",
        "delete_edh_vdi_image",
        "register_edh_simulation_image",
        "delete_edh_simulation_image",
        "list_edh_admin_capacity_reservations",
        "register_edh_capacity_reservation",
        "update_edh_capacity_reservation",
        "unregister_edh_capacity_reservation",
        "list_edh_ami_recipes",
        "get_edh_ami_recipe",
        "create_edh_ami_recipe",
        "update_edh_ami_recipe",
        "duplicate_edh_ami_recipe",
        "delete_edh_ami_recipe",
        "list_edh_ami_recipe_builds",
        "start_edh_ami_recipe_build",
        "register_edh_ami_recipe_build",
        "retire_edh_ami_recipe_build",
    }
)

TOOLSETS = {
    "consumer": CONSUMER,
    "operator": OPERATOR,
    "admin": ADMIN,
    "compliance": COMPLIANCE,
    "edh": EDH,
    "edh-admin": EDH_ADMIN,
}

# The sets that hold an add-on's tools, by the add-on module's short name (addon_tools/<name>.py).
ADDON_TOOLSETS = {"edh": ("edh", "edh-admin")}

KNOWN_NAMES = frozenset(TOOLSETS) | {ALL, CUSTOM}
_NAMED_TOOLS = frozenset().union(*TOOLSETS.values())


def parse(raw: Optional[str], default: Tuple[str, ...]) -> Tuple[str, ...]:
    """The selected set names from a comma-separated value, ``default`` when it is empty.

    Raises ``ValueError`` naming an unknown set, so a misspelled name stops the server at start
    instead of silently serving fewer tools.
    """
    names = tuple(dict.fromkeys(part.strip().lower() for part in (raw or "").split(",") if part.strip()))
    if not names:
        return default
    unknown = [name for name in names if name not in KNOWN_NAMES]
    if unknown:
        raise ValueError(
            f"unknown tool set(s) {', '.join(unknown)}; known sets: {', '.join(sorted(KNOWN_NAMES))}"
        )
    return names


class Selection:
    """Whether a tool name is selected by a list of set names."""

    def __init__(self, names: Iterable[str]) -> None:
        self.names: Tuple[str, ...] = tuple(names)
        self.everything = ALL in self.names
        self.custom = CUSTOM in self.names
        self.tools: FrozenSet[str] = frozenset().union(
            *(TOOLSETS[name] for name in self.names if name in TOOLSETS)
        )

    def includes(self, tool_name: str) -> bool:
        if self.everything or tool_name in self.tools:
            return True
        # A tool in no named set is a fork's own tool (custom_tools.py).
        return self.custom and tool_name not in _NAMED_TOOLS

    def includes_addon(self, addon_name: str) -> bool:
        """Whether any set holding the add-on's tools is selected."""
        if self.everything:
            return True
        return any(name in self.names for name in ADDON_TOOLSETS.get(addon_name, ()))
