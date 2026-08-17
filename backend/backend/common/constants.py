ALLOWED_ASSET_LINKS = {
    "PARENT-CHILD": "parent-child",
    "RELATED": "related"
}

# ---------------------------------------------------------------------------
# Permission / constraint constants
#
# Allowed-value lists are the validation source of truth; the *_LABELS / *_FIELDS
# structures add the human-facing display values served by
# GET /auth/constraints/objectTypes and consumed by the web editor and CLI.
# The labels/fields stay in sync with the allowed-value lists (enforced by tests).
# ---------------------------------------------------------------------------

# Constraint field validation constants
ALLOWED_CONSTRAINT_PERMISSIONS = ['GET', 'PUT', 'POST', 'DELETE']

ALLOWED_CONSTRAINT_PERMISSION_TYPES = ['allow', 'deny']

ALLOWED_CONSTRAINT_OBJECT_TYPES = [
    'database',
    'asset',
    'api',
    'web',
    'tag',
    'tagType',
    'role',
    'userRole',
    'pipeline',
    'workflow',
    'metadataSchema'
]

ALLOWED_CONSTRAINT_OPERATORS = [
    'equals',
    'contains',
    'does_not_contain',
    'starts_with',
    'ends_with',
    'is_one_of',
    'is_not_one_of'
]

PERMISSION_CONSTRAINT_FIELDS = {
            "databaseId": "",

            "assetName": "",
            "assetType": "",
            "tags": [],

            "tagName": "",

            "tagTypeName": "",

            "roleName": "",
            "userId": "",

            "pipelineId": "",
            "pipelineExecutionType": "",

            "workflowId": "",

            "category": "",
            "name": "",

            "metadataSchemaName": "",
            "metadataSchemaEntityType": "",
            #"field": "", //deprecated, old metadata schema

            "object__type": "",
            "route__path": "",
        }


PERMISSION_CONSTRAINT_POLICY = """
        [request_definition]
        r = sub, obj, act

        [policy_definition]
        p = sub, obj_rule, act, eft

        [role_definition]
        g = _, _

        [policy_effect]
        e = some(where (p.eft == allow)) && !some(where (p.eft == deny))

        [matchers]
        m = g(r.sub, p.sub) && eval(p.obj_rule) && r.act == p.act
        """

# Display labels for each constraint object type and the fields valid on it.
# Human-facing / constraint-editor view and the authoritative per-type field matrix.
# Keys stay in sync with ALLOWED_CONSTRAINT_OBJECT_TYPES; every field value exists in
# PERMISSION_CONSTRAINT_FIELDS (enforced by tests).
CONSTRAINT_OBJECT_TYPE_FIELDS = {
    "database": {"label": "Database", "fields": [
        {"label": "Database ID", "value": "databaseId"}]},
    "asset": {"label": "Asset", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Asset Name", "value": "assetName"},
        {"label": "Asset Type", "value": "assetType"},
        {"label": "Tags", "value": "tags"}]},
    "api": {"label": "API", "fields": [
        {"label": "Route Path", "value": "route__path"}]},
    "web": {"label": "Web", "fields": [
        {"label": "Route Path", "value": "route__path"}]},
    "tag": {"label": "Tag", "fields": [
        {"label": "Tag Name", "value": "tagName"},
        {"label": "Database ID", "value": "databaseId"}]},
    "tagType": {"label": "Tag Type", "fields": [
        {"label": "Tag Type Name", "value": "tagTypeName"},
        {"label": "Database ID", "value": "databaseId"}]},
    "role": {"label": "Role", "fields": [
        {"label": "Role Name", "value": "roleName"}]},
    "userRole": {"label": "User Role", "fields": [
        {"label": "Role Name", "value": "roleName"},
        {"label": "User ID", "value": "userId"}]},
    "pipeline": {"label": "Pipeline", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Pipeline ID", "value": "pipelineId"},
        {"label": "Pipeline Execution Type", "value": "pipelineExecutionType"},
        {"label": "Category", "value": "category"},
        {"label": "Name", "value": "name"}]},
    "workflow": {"label": "Workflow", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Workflow ID", "value": "workflowId"},
        {"label": "Category", "value": "category"},
        {"label": "Name", "value": "name"}]},
    "metadataSchema": {"label": "Metadata Schema", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Metadata Schema Name", "value": "metadataSchemaName"},
        {"label": "Metadata Schema Entity Type", "value": "metadataSchemaEntityType"}]},
}

# Display labels for the constraint criteria operators (editor view).
CONSTRAINT_OPERATOR_LABELS = [
    {"label": "Equals", "value": "equals"},
    {"label": "Contains", "value": "contains"},
    {"label": "Does Not Contain", "value": "does_not_contain"},
    {"label": "Starts With", "value": "starts_with"},
    {"label": "Ends With", "value": "ends_with"},
    {"label": "Is One Of", "value": "is_one_of"},
    {"label": "Is Not One Of", "value": "is_not_one_of"},
]

# Display labels for the constraint permissions (HTTP actions) and permission types.
CONSTRAINT_PERMISSION_LABELS = [
    {"label": "View/GET", "value": "GET"},
    {"label": "Add/PUT", "value": "PUT"},
    {"label": "Update/POST", "value": "POST"},
    {"label": "DELETE", "value": "DELETE"},
]

CONSTRAINT_PERMISSION_TYPE_LABELS = [
    {"label": "Allow", "value": "allow"},
    {"label": "Deny", "value": "deny"},
]

# Object keys always kept when scrubbing a request object to its object type's
# fields. These are specially mapped outside the traditional field matrix:
# object__type is the type gate, and method carries the HTTP action for route
# checks. route__path is a real mapped field (api/web) and is kept via the matrix.
ALWAYS_ALLOWED_OBJECT_KEYS = {"object__type", "method"}


def get_constraint_fields_for_object_type(object_type):
    """Return the list of valid field-value strings for a constraint object type ([] if unknown)."""
    entry = CONSTRAINT_OBJECT_TYPE_FIELDS.get(object_type)
    return [f["value"] for f in entry["fields"]] if entry else []


# Role field validation constants
ALLOWED_ROLE_SOURCES = ['INTERNAL_SYSTEM']

#Unallowed file extension list
UNALLOWED_FILE_EXTENSION_LIST = [
    ".jar",
    ".java",
    ".com",
    ".php",
    ".reg",
    ".pif",
    ".bak",
    ".java",
    ".dll",
    ".exe",
    ".nat",
    ".cmd",
    ".exe",
    ".lnk",
    ".docm",
    ".vbs",
    ".bat"
] 

#Unallowed MIME type list for many of the equivilent file extensions in UNALLOWED_FILE_EXTENSION_LIST:
UNALLOWED_MIME_LIST = [
    "application/java-archive",
    "application/x-python-code",
    "text/x-python-source",
    "text/x-java-source",
    "application/x-sh",
    "application/java-vm",
    "application/x-msdownload",
    "application/x-sh",
    "application/x-php",
    "application/x-ms-dos-executable",
    "application/x-ini",
    "application/x-inf",
    "application/x-sql",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-ms-shortcut",
    "application/x-bat-script",
    "application/vnd.ms-word.document.macroEnabled.12",
    "application/javascript",
    "application/x-vbs",
    "application/x-powershell",
    "application/x-msdos-program",
    "application/vbscript",
    "application/powershell"
]


PERMISSION_CONSTRAINT_FIELDS = {
            "databaseId": "",

            "assetName": "",
            "assetType": "",
            "tags": [],

            "tagName": "",

            "tagTypeName": "",

            "roleName": "",
            "userId": "",

            "pipelineId": "",
            "pipelineExecutionType": "",

            "workflowId": "",

            "category": "",
            "name": "",

            "metadataSchemaName": "",
            "metadataSchemaEntityType": "",
            #"field": "", //deprecated, old metadata schema

            "object__type": "",
            "route__path": "",
        }


PERMISSION_CONSTRAINT_POLICY = """
        [request_definition]
        r = sub, obj, act

        [policy_definition]
        p = sub, obj_rule, act, eft

        [role_definition]
        g = _, _

        [policy_effect]
        e = some(where (p.eft == allow)) && !some(where (p.eft == deny))

        [matchers]
        m = g(r.sub, p.sub) && eval(p.obj_rule) && r.act == p.act
        """

# Display labels for each constraint object type and the fields valid on it.
# Human-facing / constraint-editor view and the authoritative per-type field matrix.
# Keys stay in sync with ALLOWED_CONSTRAINT_OBJECT_TYPES; every field value exists in
# PERMISSION_CONSTRAINT_FIELDS (enforced by tests).
CONSTRAINT_OBJECT_TYPE_FIELDS = {
    "database": {"label": "Database", "fields": [
        {"label": "Database ID", "value": "databaseId"}]},
    "asset": {"label": "Asset", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Asset Name", "value": "assetName"},
        {"label": "Asset Type", "value": "assetType"},
        {"label": "Tags", "value": "tags"}]},
    "api": {"label": "API", "fields": [
        {"label": "Route Path", "value": "route__path"}]},
    "web": {"label": "Web", "fields": [
        {"label": "Route Path", "value": "route__path"}]},
    "tag": {"label": "Tag", "fields": [
        {"label": "Tag Name", "value": "tagName"},
        {"label": "Database ID", "value": "databaseId"}]},
    "tagType": {"label": "Tag Type", "fields": [
        {"label": "Tag Type Name", "value": "tagTypeName"},
        {"label": "Database ID", "value": "databaseId"}]},
    "role": {"label": "Role", "fields": [
        {"label": "Role Name", "value": "roleName"}]},
    "userRole": {"label": "User Role", "fields": [
        {"label": "Role Name", "value": "roleName"},
        {"label": "User ID", "value": "userId"}]},
    "pipeline": {"label": "Pipeline", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Pipeline ID", "value": "pipelineId"},
        {"label": "Pipeline Execution Type", "value": "pipelineExecutionType"},
        {"label": "Category", "value": "category"},
        {"label": "Name", "value": "name"}]},
    "workflow": {"label": "Workflow", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Workflow ID", "value": "workflowId"},
        {"label": "Category", "value": "category"},
        {"label": "Name", "value": "name"}]},
    "metadataSchema": {"label": "Metadata Schema", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Metadata Schema Name", "value": "metadataSchemaName"},
        {"label": "Metadata Schema Entity Type", "value": "metadataSchemaEntityType"}]},
}

# Display labels for the constraint criteria operators (editor view).
CONSTRAINT_OPERATOR_LABELS = [
    {"label": "Equals", "value": "equals"},
    {"label": "Contains", "value": "contains"},
    {"label": "Does Not Contain", "value": "does_not_contain"},
    {"label": "Starts With", "value": "starts_with"},
    {"label": "Ends With", "value": "ends_with"},
    {"label": "Is One Of", "value": "is_one_of"},
    {"label": "Is Not One Of", "value": "is_not_one_of"},
]


def get_constraint_fields_for_object_type(object_type):
    """Return the list of valid field-value strings for a constraint object type ([] if unknown)."""
    entry = CONSTRAINT_OBJECT_TYPE_FIELDS.get(object_type)
    return [f["value"] for f in entry["fields"]] if entry else []

# Normal JSON REST response for use in most lambda handlers
#
STANDARD_JSON_RESPONSE = {
    'statusCode': 200,
    'body': '',
    'headers': {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache, no-store',
        # The REST API integration returns Lambda responses verbatim, so the CORS origin
        # header must be set on the response itself (OPTIONS preflight is handled by the
        # API's MOCK method). Mirrors the preflight allow-origin for cross-origin callers.
        'Access-Control-Allow-Origin': '*',
    }
}
