"""Coverage manifest: every public method of the CLI's ``APIClient`` and what this server does with it.

``APIClient`` (``tools/VamsCLI/vamscli/utils/api_client.py``) includes the add-on mixin
(``vamscli/utils/addon/``) and the fork mixin (``vamscli/utils/custom_api_client.py``). Each public
method of it appears in exactly ONE of the three tables below:

* ``TOOLS``: the tools that call it (core tools in ``vams_mcp/server.py``, add-on tools in
  ``vams_mcp/addon_tools/``).
* ``EXCLUDED``: a decision that agents do not get this operation, with the one-line reason.
* ``NOT_YET_EXPOSED``: an operation agents could use that has no tool yet, with what it is. A gap,
  recorded so it is visible rather than forgotten; adding the tool moves the entry to ``TOOLS``.

``tests/test_api_coverage.py`` fails when a public method is in none of them (a new CLI method nobody
decided on), when a table names a method that no longer exists, when ``TOOLS`` names a tool that is
not registered, and when ``TOOLS`` and the tools' own ``client.api.<method>`` calls disagree.

A fork's own methods (``CustomAPIClientMixin``) count as covered when ``vams_mcp/custom_tools.py``
calls them; a fork that keeps one CLI-only lists it in ``EXCLUDED``.
"""

# Methods that reach VAMS through a tool. A method several tools call lists every one of them.
TOOLS = {
    # --- Access and routes ---
    "list_allowed_api_routes": ("list_allowed_api_routes",),
    "list_api_routes": ("list_api_routes",),
    "list_constraint_permission_objects": ("list_constraint_permission_objects",),
    # --- Databases and buckets ---
    "list_databases": ("list_databases",),
    "get_database": ("get_database",),
    "list_buckets": ("list_buckets",),
    "create_database": ("create_database",),
    "delete_database": ("delete_database",),
    "get_database_metadata_v2": ("get_database_metadata",),
    # --- Assets ---
    "get_asset": ("get_asset",),
    "create_asset": ("create_asset",),
    "update_asset": ("update_asset",),
    "archive_asset": ("archive_asset",),
    "unarchive_asset": ("unarchive_asset",),
    "delete_asset_permanent": ("delete_asset",),
    "get_asset_history": ("get_asset_history",),
    "export_asset": ("export_asset",),
    "get_asset_metadata_v2": ("get_asset_metadata",),
    "update_asset_metadata_v2": ("set_asset_metadata",),
    "delete_asset_metadata_v2": ("delete_asset_metadata",),
    # --- Files ---
    "list_asset_files": ("list_asset_files",),
    "get_file_info": ("get_file_info",),
    "get_file_metadata_v2": ("get_file_metadata",),
    "update_file_metadata_v2": ("set_file_metadata",),
    "set_primary_file": ("set_primary_file",),
    "revert_file_version": ("revert_file_version",),
    "create_folder": ("create_folder",),
    "move_file": ("move_file",),
    "copy_file": ("copy_file",),
    "archive_file": ("archive_file",),
    "unarchive_file": ("unarchive_file",),
    "delete_file": ("delete_file",),
    "download_asset_file": ("generate_download_url",),
    "download_asset_files_bulk": ("generate_download_urls_bulk",),
    # --- Asset versions ---
    "get_asset_versions": ("find_and_summarize", "list_asset_versions"),
    "get_asset_version": ("get_asset_version",),
    "create_asset_version": ("create_asset_version",),
    # --- Asset links ---
    "get_asset_links_for_asset": ("get_asset_links",),
    "create_asset_link": ("create_asset_link",),
    "delete_asset_link": ("delete_asset_link",),
    "get_asset_link_metadata_v2": ("get_asset_link_metadata",),
    # --- Search ---
    "search_query": ("find_and_summarize", "search_assets", "search_files"),
    "search_nlp": ("search_nlp",),
    "get_search_mapping": ("get_search_fields",),
    # --- Tags and tag types ---
    "get_tags": ("list_tags",),
    "create_tags": ("create_tag",),
    "update_tags": ("update_tag",),
    "delete_tag": ("delete_tag",),
    "get_tag_types": ("list_tag_types",),
    "create_tag_types": ("create_tag_type",),
    "update_tag_types": ("update_tag_type",),
    "delete_tag_type": ("delete_tag_type",),
    # --- Metadata schemas ---
    "list_metadata_schemas": ("list_metadata_schemas",),
    "create_metadata_schema": ("create_metadata_schema",),
    "update_metadata_schema": ("update_metadata_schema",),
    "delete_metadata_schema": ("delete_metadata_schema",),
    # --- Pipelines and templates ---
    "list_pipelines": ("list_pipelines",),
    "get_pipeline": ("get_pipeline",),
    "create_pipeline": ("create_pipeline",),
    "update_pipeline": ("unarchive_pipeline", "update_pipeline"),
    "delete_pipeline": ("archive_pipeline",),
    "list_pipeline_templates": ("list_pipeline_templates",),
    "get_pipeline_template": ("get_pipeline_template",),
    "create_pipeline_template": ("create_pipeline_template",),
    "update_pipeline_template": ("update_pipeline_template",),
    "delete_pipeline_template": ("delete_pipeline_template",),
    "get_pipeline_template_tag_schema": ("get_pipeline_template_tag_schema",),
    "set_pipeline_template_tag_schema": ("set_pipeline_template_tag_schema",),
    # --- Workflows and triggers ---
    "list_workflows": ("list_workflows",),
    "get_workflow": ("get_workflow",),
    "create_workflow": ("create_workflow",),
    "update_workflow": ("unarchive_workflow", "update_workflow"),
    "delete_workflow": ("archive_workflow",),
    "list_workflow_triggers": ("list_workflow_triggers",),
    "get_workflow_trigger": ("get_workflow_trigger",),
    "set_workflow_trigger": ("set_workflow_trigger",),
    "delete_workflow_trigger": ("delete_workflow_trigger",),
    # --- Executions ---
    "execute_workflow": ("execute_workflow",),
    "list_executions": ("list_executions",),
    "list_workflow_executions": ("list_workflow_executions",),
    "get_execution_details": ("get_execution_details",),
    "get_execution_details_metadata": ("page_execution_detail_metadata",),
    "get_execution_details_files": ("page_execution_detail_files",),
    "get_execution_results": ("get_execution_results",),
    "get_execution_logs": ("get_execution_logs",),
    "rerun_execution": ("rerun_execution",),
    "abort_execution": ("abort_execution",),
    "permanent_delete_execution": ("permanent_delete_execution",),
    # --- Comments ---
    "list_asset_comments": ("list_asset_comments",),
    "list_asset_version_comments": ("list_asset_version_comments",),
    "get_comment": ("get_comment",),
    "add_comment": ("add_comment",),
    "update_comment": ("update_comment",),
    "delete_comment": ("delete_comment",),
    # --- Subscriptions ---
    "list_subscriptions": ("list_subscriptions",),
    "check_subscription": ("check_subscription",),
    "create_subscription": ("create_subscription",),
    "update_subscription": ("update_subscription",),
    "delete_subscription": ("delete_subscription",),
    "unsubscribe": ("unsubscribe",),
    # --- API keys ---
    "list_api_keys": ("list_api_keys",),
    "get_api_key": ("get_api_key",),
    "create_api_key": ("create_api_key",),
    "update_api_key": ("update_api_key",),
    "delete_api_key": ("delete_api_key",),
    "list_user_api_keys": ("list_user_api_keys",),
    "get_user_api_key": ("get_user_api_key",),
    "create_user_api_key": ("create_user_api_key",),
    "update_user_api_key": ("update_user_api_key",),
    "delete_user_api_key": ("delete_user_api_key",),
    # --- EDH Workstations add-on (vams_mcp/addon_tools/edh.py) ---
    "get_edh_health": ("get_edh_health",),
    "get_edh_capacity": ("get_edh_capacity",),
    "get_edh_catalog_lease": ("get_edh_catalog_lease",),
    "list_edh_catalog_images": ("list_edh_catalog_images",),
    "list_edh_catalog_instance_types": ("list_edh_catalog_instance_types",),
    "list_edh_catalog_capacity_reservations": ("list_edh_catalog_capacity_reservations",),
    "list_edh_workstations": ("list_edh_workstations",),
    "get_edh_workstation": ("get_edh_workstation",),
    "create_edh_workstation": ("create_edh_workstation",),
    "start_edh_workstation": ("start_edh_workstation",),
    "stop_edh_workstation": ("stop_edh_workstation",),
    "renew_edh_workstation": ("renew_edh_workstation",),
    "delete_edh_workstation": ("delete_edh_workstation",),
    "get_edh_workstation_connection": ("get_edh_workstation_connect",),
    "list_edh_admin_instances": ("list_edh_admin_instances",),
    "list_edh_stale_instances": ("list_edh_stale_instances",),
    "stop_edh_admin_instance": ("stop_edh_admin_instance",),
    "deactivate_edh_admin_instance": ("deactivate_edh_admin_instance",),
    "get_edh_admin_settings": ("get_edh_admin_settings",),
    "update_edh_admin_settings": ("update_edh_admin_settings",),
    "list_edh_admin_images": ("list_edh_admin_images",),
    "register_edh_vdi_image": ("register_edh_vdi_image",),
    "delete_edh_vdi_image": ("delete_edh_vdi_image",),
    "register_edh_simulation_image": ("register_edh_simulation_image",),
    "delete_edh_simulation_image": ("delete_edh_simulation_image",),
    "list_edh_admin_capacity_reservations": ("list_edh_admin_capacity_reservations",),
    "register_edh_capacity_reservation": ("register_edh_capacity_reservation",),
    "update_edh_capacity_reservation": ("update_edh_capacity_reservation",),
    "unregister_edh_capacity_reservation": ("unregister_edh_capacity_reservation",),
    "list_edh_container_catalog": ("list_edh_container_catalog",),
    "list_edh_container_jobs": ("list_edh_container_jobs",),
    "submit_edh_container_job": ("submit_edh_container_job",),
    "kill_edh_container_job": ("kill_edh_container_job",),
    "list_edh_ami_recipes": ("list_edh_ami_recipes",),
    "get_edh_ami_recipe": ("get_edh_ami_recipe",),
    "create_edh_ami_recipe": ("create_edh_ami_recipe",),
    "update_edh_ami_recipe": ("update_edh_ami_recipe",),
    "duplicate_edh_ami_recipe": ("duplicate_edh_ami_recipe",),
    "delete_edh_ami_recipe": ("delete_edh_ami_recipe",),
    "list_edh_ami_recipe_builds": ("list_edh_ami_recipe_builds",),
    "start_edh_ami_recipe_build": ("start_edh_ami_recipe_build",),
    "register_edh_ami_recipe_build": ("register_edh_ami_recipe_build",),
    "retire_edh_ami_recipe_build": ("retire_edh_ami_recipe_build",),
}

_UPLOAD = "file upload is CLI-only (vamscli file upload / vamscli sync): it streams local files"
_ADMIN = "user, role and constraint administration is CLI-only"

# Decided: no tool, and the reason.
EXCLUDED = {
    # --- HTTP verbs the other methods are built on (no operation of their own) ---
    "get": "HTTP verb helper; tools call a named method (or VamsClient.get_json for list_assets)",
    "post": "HTTP verb helper; tools call a named method",
    "put": "HTTP verb helper; tools call a named method",
    "delete": "HTTP verb helper; tools call a named method",
    # --- Setup and sign-in, which the USER does (Mandatory Rule 14) ---
    "check_version": "vamscli setup's API version check, not an agent operation",
    "check_api_availability": "vamscli setup reachability check, not an agent operation",
    "get_amplify_config": "vamscli setup reads it to find the API URL; the server takes the URL from the profile",
    "call_login_profile": "part of vamscli auth login; the user signs in, never the agent",
    "get_secure_config": "read by vamscli auth login; list_features and vams_status report the snapshot it stores",
    # --- Upload ---
    "initialize_upload": _UPLOAD,
    "complete_upload": _UPLOAD,
    # --- Administration ---
    "list_cognito_users": _ADMIN,
    "create_cognito_user": _ADMIN,
    "update_cognito_user": _ADMIN,
    "delete_cognito_user": _ADMIN,
    "reset_cognito_user_password": _ADMIN,
    "list_roles": _ADMIN,
    "create_role": _ADMIN,
    "update_role": _ADMIN,
    "delete_role": _ADMIN,
    "list_user_roles": _ADMIN,
    "create_user_roles": _ADMIN,
    "update_user_roles": _ADMIN,
    "delete_user_roles": _ADMIN,
    "list_constraints": _ADMIN,
    "get_constraint": _ADMIN,
    "create_constraint": _ADMIN,
    "update_constraint": _ADMIN,
    "delete_constraint": _ADMIN,
    "import_constraints_template": _ADMIN,
    # --- Covered by another tool ---
    "get_metadata_schema": "the same rows as list_metadata_schemas(database_id=...)",
    "search_simple": "search_assets / search_files send the full search request (search_query)",
    "get_single_asset_link": "get_asset_links returns every link of an asset with the same fields",
    "get_metadata_schema_by_id": "list_metadata_schemas returns each schema in full",
    # --- Preview images, which the web app shows and the pipelines regenerate ---
    "download_asset_preview": "a presigned URL of the preview image the web app shows; agents read the asset's files",
    "delete_auxiliary_preview_files": "removes generated preview files a pipeline rebuilds; maintenance, not an agent task",
    # --- Never returned to an agent ---
    "get_edh_workstation_os_sign_in": "the desktop sign-in (a Windows password) is shown to its owner only",
}

# Agent-usable operations without a tool yet.
NOT_YET_EXPOSED = {
    "update_database": "change a database's description, default bucket or restrictions",
    "delete_database_metadata_v2": "delete database metadata keys",
    "update_database_metadata_v2": "set database metadata keys",
    "delete_file_metadata_v2": "delete a file's metadata or attribute keys",
    "update_asset_version": "change an asset version's comment or alias",
    "archive_asset_version": "archive an asset version",
    "unarchive_asset_version": "restore an archived asset version",
    "revert_asset_version": "revert an asset to an earlier version",
    "delete_asset_preview": "delete the asset's preview image",
    "update_asset_link": "change an asset link's tags or alias",
    "update_asset_link_metadata_v2": "set an asset link's metadata",
    "delete_asset_link_metadata_v2": "delete an asset link's metadata keys",
}
