ALLOWED_ASSET_LINKS = {
    "PARENT-CHILD": "parent-child",
    "RELATED": "related"
}

#Unallowed file extension list
UNALLOWED_FILE_EXTENSION_LIST = [
    ".jar",
    ".py",
    ".java",
    ".com",
    ".sh",
    ".php",
    ".reg",
    ".ini",
    ".inf",
    ".reg",
    ".sql",
    ".pif",
    ".bak",
    ".py",
    ".java",
    ".dll",
    ".exe",
    ".docx",
    ".nat",
    ".cmd",
    ".exe",
    ".lnk",
    ".docm",
    ".js",
    ".vbs",
    ".ps1",
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
            "pipelineType": "",
            "pipelineExecutionType": "",

            "workflowId": "",

            "field": "",

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
# Normal JSON REST response for use in most lambda handlers
#
STANDARD_JSON_RESPONSE = {
    'statusCode': 200,
    'body': '',
    'headers': {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache, no-store',
    }
}
