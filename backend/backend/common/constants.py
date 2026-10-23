import keyword
import re

# The add-on and fork lanes for the permission object types and upload deny lists below. Each is
# merged into its core list right after that list (see "Extension lanes").
from common.addonConstants import (
    ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS,
    ADDON_PERMISSION_CONSTRAINT_FIELDS,
    ADDON_UNALLOWED_FILE_EXTENSIONS,
    ADDON_UNALLOWED_MIME_TYPES,
)
from custom.customConstants import (
    CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS,
    CUSTOM_PERMISSION_CONSTRAINT_FIELDS,
    CUSTOM_UNALLOWED_FILE_EXTENSIONS,
    CUSTOM_UNALLOWED_MIME_TYPES,
)

# ---------------------------------------------------------------------------
# Extension lanes
#
# The add-ons (common/addonConstants.py) and a fork (custom/customConstants.py) extend the
# permission object types, their constraint fields and the upload deny lists. Each public list is
# core + add-on + custom, built once at import right after its CORE_* list; every rule is checked
# here and a broken one raises ConstantsExtensionError naming the lane file and the entry, so a bad
# extension fails every Lambda's import and the backend tests rather than loosening authorization
# at runtime. With every lane empty each public list equals its CORE_* list exactly.
# ---------------------------------------------------------------------------

class ConstantsExtensionError(ValueError):
    """An add-on or custom constant breaks a merge rule; raised at import (fail closed)."""


ADDON_CONSTANTS_SOURCE = "common/addonConstants.py"
CUSTOM_CONSTANTS_SOURCE = "custom/customConstants.py"

# A type or field name an extension declares. Letters and digits only: no underscore, so it can
# never take the reserved `<x>__<y>` shape, and nothing that could leave the `r.obj.<field>`
# attribute position in the Casbin matcher expression (handlers/authz _generate_criteria_object_rules).
EXTENSION_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9]{1,63}$"
_EXTENSION_NAME = re.compile(EXTENSION_NAME_PATTERN)

# Keys with a fixed meaning in every authorization object (object__type, method) or in the api/web
# types (route__path). No extension may declare one or list one in a type's fields.
RESERVED_CONSTRAINT_KEYS = frozenset({"object__type", "route__path", "method"})

# Attribute names a constraint field cannot take, because `r.obj.<field>` would not read the field's
# value. The matcher's evaluator (simpleeval, as Casbin uses it) refuses the DISALLOW_METHODS names
# outright, and it resolves an attribute with getattr() before the dict lookup, so a field named
# like a dict method (`keys`, `get`, ...) reads the bound method instead of the value. Either way
# every rule on the field fails to evaluate. Python keywords are refused for the same reason
# (`r.obj.if` does not parse). Kept in step with simpleeval and dict by
# tests/common/test_constants_extension_lanes.py.
EVALUATOR_RESERVED_ATTRIBUTES = frozenset({
    # simpleeval DISALLOW_METHODS (the ones with an underscore are already outside the name pattern)
    "format", "format_map", "mro", "tb_frame", "gi_frame", "ag_frame", "cr_frame", "exec",
    # public attributes of dict, the type of the object the enforcer evaluates
    "clear", "copy", "fromkeys", "get", "items", "keys", "pop", "popitem", "setdefault", "update",
    "values",
})

# Upload deny-list entries an extension adds. The upload check (common/s3.py
# validateUnallowedFileExtensionAndContentType) lower-cases the media type with its parameters
# stripped, and lower-cases each extension it reads: everything from the last dot of each
# ':'-separated piece of the key's final non-empty segment, after trailing dots and whitespace are
# removed. So an entry is lower-case, an extension has exactly one leading dot and no other dot,
# ':' or whitespace, and a MIME type has no parameters.
EXTENSION_DENY_EXTENSION_PATTERN = r"^\.[a-z0-9][a-z0-9_+-]{0,31}$"
EXTENSION_DENY_MIME_PATTERN = r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$"


def _require(condition, source, message):
    if not condition:
        raise ConstantsExtensionError(f"{source}: {message}")


def _check_extension_name(source, kind, name):
    _require(isinstance(name, str) and _EXTENSION_NAME.fullmatch(name) is not None, source,
             f"{kind} name {name!r} must match {EXTENSION_NAME_PATTERN}")


def merge_constraint_extensions(core_object_types, core_fields, core_type_fields, lanes):
    """Return (object_types, fields, type_fields): core + each lane, in lane order.

    ``lanes`` is a sequence of ``(source, fields, type_fields)``: a lane's
    ``*_PERMISSION_CONSTRAINT_FIELDS`` and ``*_CONSTRAINT_OBJECT_TYPE_FIELDS``. Raises
    ConstantsExtensionError when an entry breaks a rule: a malformed name; a reserved key; a Python
    keyword or evaluator-reserved field name; a sample value other than "" or []; a type or field
    that repeats a core one or one another lane declares, compared case-insensitively; a malformed
    type entry; or a type that lists a field the merged field set does not hold. The core arguments
    are not modified.
    """
    object_types = list(core_object_types)
    fields = dict(core_fields)
    type_fields = dict(core_type_fields)
    field_owner = {name: "core" for name in fields}
    type_owner = {name: "core" for name in object_types}
    # A name repeats another when the two are equal ignoring letter case (`Asset` repeats `asset`),
    # so the constraint editor never lists two names that differ only in case. Rules still match a
    # name exactly. Each maps the casefolded name to the name as declared.
    field_folded = {name.casefold(): name for name in fields}
    type_folded = {name.casefold(): name for name in object_types}

    # Fields first, across every lane, so a type may list a field another lane declares.
    for source, lane_fields, _lane_types in lanes:
        _require(isinstance(lane_fields, dict), source,
                 "the PERMISSION_CONSTRAINT_FIELDS lane must be a dict of field name -> sample value")
        for name, sample in lane_fields.items():
            _check_extension_name(source, "constraint field", name)
            _require(name not in RESERVED_CONSTRAINT_KEYS, source,
                     f"constraint field {name!r} is a reserved key")
            _require(not keyword.iskeyword(name) and name not in EVALUATOR_RESERVED_ATTRIBUTES,
                     source, f"constraint field {name!r} cannot be evaluated as an attribute name")
            _require(name not in field_owner, source,
                     f"constraint field {name!r} is already declared by {field_owner.get(name)}")
            folded = field_folded.get(name.casefold())
            _require(folded is None, source,
                     f"constraint field {name!r} is already declared by {field_owner.get(folded)} "
                     f"as {folded!r}")
            _require((isinstance(sample, str) and sample == "")
                     or (isinstance(sample, list) and sample == []), source,
                     f"constraint field {name!r} must map to \"\" (one value) or [] (a list of values)")
            fields[name] = [] if isinstance(sample, list) else ""
            field_owner[name] = source
            field_folded[name.casefold()] = name

    for source, _lane_fields, lane_types in lanes:
        _require(isinstance(lane_types, dict), source,
                 "the CONSTRAINT_OBJECT_TYPE_FIELDS lane must be a dict of type name -> entry")
        for type_name, entry in lane_types.items():
            _check_extension_name(source, "object type", type_name)
            _require(type_name not in RESERVED_CONSTRAINT_KEYS, source,
                     f"object type {type_name!r} is a reserved key")
            _require(type_name not in type_owner, source,
                     f"object type {type_name!r} is already declared by {type_owner.get(type_name)}")
            folded = type_folded.get(type_name.casefold())
            _require(folded is None, source,
                     f"object type {type_name!r} is already declared by {type_owner.get(folded)} "
                     f"as {folded!r}")
            _require(isinstance(entry, dict) and set(entry) == {"label", "fields"}, source,
                     f"object type {type_name!r} must map to {{\"label\": ..., \"fields\": [...]}}")
            _require(isinstance(entry["label"], str) and entry["label"].strip() != "", source,
                     f"object type {type_name!r} needs a non-empty label")
            _require(isinstance(entry["fields"], list) and len(entry["fields"]) > 0, source,
                     f"object type {type_name!r} needs a non-empty fields list")
            listed = []
            for field in entry["fields"]:
                _require(isinstance(field, dict) and set(field) == {"label", "value"}
                         and isinstance(field["label"], str) and field["label"].strip() != ""
                         and isinstance(field["value"], str), source,
                         f"object type {type_name!r}: each field is {{\"label\": ..., \"value\": ...}} "
                         "with a non-empty label")
                value = field["value"]
                _require(value not in RESERVED_CONSTRAINT_KEYS, source,
                         f"object type {type_name!r} lists the reserved key {value!r}")
                _require(value in fields, source,
                         f"object type {type_name!r} lists {value!r}, which is not a constraint "
                         "field (declare it in the lane's PERMISSION_CONSTRAINT_FIELDS or use a core field)")
                _require(value not in listed, source,
                         f"object type {type_name!r} lists {value!r} twice")
                listed.append(value)
            object_types.append(type_name)
            type_fields[type_name] = {
                "label": entry["label"],
                "fields": [{"label": f["label"], "value": f["value"]} for f in entry["fields"]],
            }
            type_owner[type_name] = source
            type_folded[type_name.casefold()] = type_name

    return object_types, fields, type_fields


def merge_deny_list(kind, core, lanes, entry_pattern):
    """Return core + each lane's entries (append-only), raising on a malformed or repeated entry.

    ``lanes`` is a sequence of ``(source, entries)``. An entry must fully match ``entry_pattern`` and
    must not repeat a core entry (compared lower-cased) or one another lane already added. The core
    list is returned unchanged at the front, so no lane can remove or reorder a core entry.
    """
    merged = list(core)
    owner = {str(entry).lower(): "core" for entry in core}
    compiled = re.compile(entry_pattern)
    for source, entries in lanes:
        _require(isinstance(entries, (list, tuple)), source, f"the {kind} lane must be a list")
        for entry in entries:
            _require(isinstance(entry, str) and compiled.fullmatch(entry) is not None, source,
                     f"{kind} {entry!r} must match {entry_pattern}")
            _require(entry not in owner, source,
                     f"{kind} {entry!r} is already blocked by {owner.get(entry)}")
            merged.append(entry)
            owner[entry] = source
    return merged


ALLOWED_ASSET_LINKS = {
    "PARENT-CHILD": "parent-child",
    "RELATED": "related"
}

# ---------------------------------------------------------------------------
# Permission / constraint constants
#
# Allowed-value lists are the validation source of truth; the *_LABELS / *_FIELDS
# structures add the human-facing display values served by
# GET /auth/constraints/permissionObjects and consumed by the web editor and CLI.
# The labels/fields stay in sync with the allowed-value lists (enforced by tests).
#
# The object types, their fields and the constraint fields are declared here as the CORE_* lists;
# the names consumers import (ALLOWED_CONSTRAINT_OBJECT_TYPES, CONSTRAINT_OBJECT_TYPE_FIELDS,
# PERMISSION_CONSTRAINT_FIELDS) are core + add-on + custom, built right after the CORE_* lists.
# Import those, never a CORE_* name, so an extension type is validated and enforced like a core one.
# ---------------------------------------------------------------------------

# Constraint field validation constants
ALLOWED_CONSTRAINT_PERMISSIONS = ['GET', 'PUT', 'POST', 'DELETE']

ALLOWED_CONSTRAINT_PERMISSION_TYPES = ['allow', 'deny']

CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES = [
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
    'metadataSchema',
    'complianceSchema',
    'complianceEvaluation',
    'complianceCascade',
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

CORE_PERMISSION_CONSTRAINT_FIELDS = {
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

            "complianceSchemaName": "",
            "complianceState": "",
            "cascadeId": "",

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
CORE_CONSTRAINT_OBJECT_TYPE_FIELDS = {
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
    "complianceSchema": {"label": "Compliance Schema", "fields": [
        {"label": "Schema Name", "value": "complianceSchemaName"}]},
    "complianceEvaluation": {"label": "Compliance Evaluation", "fields": [
        {"label": "Database ID", "value": "databaseId"},
        {"label": "Compliance State", "value": "complianceState"}]},
    "complianceCascade": {"label": "Compliance Cascade", "fields": [
        {"label": "Cascade ID", "value": "cascadeId"}]},
}

_MERGED_CONSTRAINT_CONSTANTS = merge_constraint_extensions(
    CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES,
    CORE_PERMISSION_CONSTRAINT_FIELDS,
    CORE_CONSTRAINT_OBJECT_TYPE_FIELDS,
    [
        (ADDON_CONSTANTS_SOURCE, ADDON_PERMISSION_CONSTRAINT_FIELDS,
         ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS),
        (CUSTOM_CONSTANTS_SOURCE, CUSTOM_PERMISSION_CONSTRAINT_FIELDS,
         CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS),
    ],
)

# The names every consumer imports: core + add-on + custom.
ALLOWED_CONSTRAINT_OBJECT_TYPES = _MERGED_CONSTRAINT_CONSTANTS[0]
PERMISSION_CONSTRAINT_FIELDS = _MERGED_CONSTRAINT_CONSTANTS[1]
CONSTRAINT_OBJECT_TYPE_FIELDS = _MERGED_CONSTRAINT_CONSTANTS[2]

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
    {"label": "PUT", "value": "PUT"},
    {"label": "POST", "value": "POST"},
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

#Unallowed file extension list. Entries are lower-case and compared against the lower-cased
#extension (common/s3.py validateUnallowedFileExtensionAndContentType); tests/common/
#test_s3_blocklist_case_insensitive.py rejects an entry that is not lower-case or is listed twice.
CORE_UNALLOWED_FILE_EXTENSION_LIST = [
    ".jar",
    ".java",
    ".com",
    ".php",
    ".reg",
    ".pif",
    ".bak",
    ".dll",
    ".exe",
    ".nat",
    ".cmd",
    ".lnk",
    ".docm",
    ".vbs",
    ".bat"
] 

#Unallowed MIME type list for many of the equivilent file extensions in UNALLOWED_FILE_EXTENSION_LIST.
#Same contract: lower-case entries, matched against the lower-cased media type (parameters stripped).
CORE_UNALLOWED_MIME_LIST = [
    "application/java-archive",
    "application/x-python-code",
    "text/x-python-source",
    "text/x-java-source",
    "application/x-sh",
    "application/java-vm",
    "application/x-msdownload",
    "application/x-php",
    "application/x-ms-dos-executable",
    "application/x-ini",
    "application/x-inf",
    "application/x-sql",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-ms-shortcut",
    "application/x-bat-script",
    "application/vnd.ms-word.document.macroenabled.12",
    "application/javascript",
    "application/x-vbs",
    "application/x-powershell",
    "application/x-msdos-program",
    "application/vbscript",
    "application/powershell"
]

# The lists common/s3.py imports: core + add-on + custom, append-only (see "Extension lanes"). The
# hygiene tests in tests/common/test_s3_blocklist_case_insensitive.py read these merged lists.
UNALLOWED_FILE_EXTENSION_LIST = merge_deny_list(
    "file extension",
    CORE_UNALLOWED_FILE_EXTENSION_LIST,
    [(ADDON_CONSTANTS_SOURCE, ADDON_UNALLOWED_FILE_EXTENSIONS),
     (CUSTOM_CONSTANTS_SOURCE, CUSTOM_UNALLOWED_FILE_EXTENSIONS)],
    EXTENSION_DENY_EXTENSION_PATTERN,
)

UNALLOWED_MIME_LIST = merge_deny_list(
    "MIME type",
    CORE_UNALLOWED_MIME_LIST,
    [(ADDON_CONSTANTS_SOURCE, ADDON_UNALLOWED_MIME_TYPES),
     (CUSTOM_CONSTANTS_SOURCE, CUSTOM_UNALLOWED_MIME_TYPES)],
    EXTENSION_DENY_MIME_PATTERN,
)


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
