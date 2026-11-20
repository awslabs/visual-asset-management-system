# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Records for the audit search tests, produced by the real writer.

Every record a parser test reads is the output of ``customLogging.auditLogging`` (``log_action``,
``log_authorization`` and the other writing functions) as the writer handed it to the CloudWatch Logs
client, so a change to the formatter breaks the parser tests instead of leaving fixtures behind. The
frozen legacy records (``LEGACY_*``) are written out by hand in the format before the record contract, which the
writer does not produce.
"""

import importlib
import json
from typing import Any, Dict, List, Tuple

from test_audit_action_records import api_event, auditLogging

GROUPS = importlib.import_module("common.auditLogGroups")
REGISTRY = importlib.import_module("common.auditActions")

# test_audit_action_records sets AUDIT_LOG_<X> to "test-<group key>"
_KEY_BY_LOG_GROUP_NAME = {f"test-{group.key}": group.key for group in GROUPS.AUDIT_LOG_GROUPS}


def written(writer) -> List[Tuple[str, str]]:
    """Every (group key, message) the writer produced, in order, from both clients."""
    out: List[Tuple[str, str]] = []
    for client in (writer.module, writer.flush):
        for call in client.calls:
            key = _KEY_BY_LOG_GROUP_NAME.get(call["logGroupName"], call["logGroupName"])
            for event in call["logEvents"]:
                out.append((key, event["message"]))
    return out


def descriptor(group_key: str):
    return GROUPS.audit_log_group(group_key)


def with_request(event: Dict[str, Any], **context: Any) -> Dict[str, Any]:
    event["requestContext"].update(context)
    return event


# ---------------------------------------------------------------------------------------------------
# Frozen legacy records: the format before the record contract, written by hand
# ---------------------------------------------------------------------------------------------------

_ECHO = ('--- [event: {"version": "2.0", "routeKey": "PUT /database/{databaseId}/assets/{assetId}", '
         '"rawPath": "/database/db-1/assets/a-1", "headers": {"x-custom": "h"}, "body": "{\\"assetName\\": \\"n\\"}", '
         '"requestContext": {"requestId": "req-legacy-1", "http": {"method": "PUT", '
         '"path": "/database/db-1/assets/a-1", "sourceIp": "198.51.100.9"}}}]')

LEGACY_AUTHENTICATION = ('[AUTHENTICATION][authenticated: True] [user: alice] [roles: ["admin"]] '
                         '[mfaEnabled: True] {"method": "cognito", "ip": "192.168.1.1"} ' + _ECHO)
LEGACY_AUTHENTICATION_FAILED = ('[AUTHENTICATION][authenticated: False] [user: unknown] [roles: []] '
                                '[mfaEnabled: False] {"method": "cognito"}')
LEGACY_AUTHORIZATION_ALLOWED = ('[AUTHORIZATION][authorized: True] [user: alice] [roles: ["editor"]] '
                                '[mfaEnabled: True] {"action": "PUT", "obj": {"object__type": "api", '
                                '"route__path": "/database/db-1/assets/a-1"}} ' + _ECHO)
LEGACY_AUTHORIZATION_DENIED = ('[AUTHORIZATION][authorized: False] [user: bob] [roles: ["viewer"]] '
                               '[mfaEnabled: True] {"action": "DELETE", "obj": {"object__type": "database", '
                               '"databaseId": "db-1"}} ' + _ECHO)
LEGACY_GATEWAY_DENIAL = ('[AUTHORIZATION][authorized: False] [user: unknown] [roles: []] [mfaEnabled: False] '
                         '{"sourceIp": "203.0.113.5", "failureReason": "invalidToken"} --- [event: {"requestContext": '
                         '{"requestId": "req-gw-1", "http": {"method": "GET", "path": "/database", '
                         '"sourceIp": "203.0.113.5"}}, "headers": {"user-agent": "curl"}}]')
LEGACY_FILE_UPLOAD = ('[FILEUPLOAD] [user: carol] [roles: ["editor"]] [mfaEnabled: True] {"databaseId": "db-1", '
                      '"assetId": "a-1", "filePath": "/data/model.obj", "uploadDenied": false, '
                      '"customData": {"fileSize": 1024000}} ' + _ECHO)
LEGACY_FILE_UPLOAD_DENIED = ('[FILEUPLOAD] [user: carol] [roles: ["viewer"]] [mfaEnabled: True] '
                             '{"databaseId": "db-1", "assetId": "a-1", "filePath": "/data/model.obj", '
                             '"uploadDenied": true, "uploadDeniedReason": "extension"} ' + _ECHO)
LEGACY_FILE_DOWNLOAD = ('[FILEDOWNLOAD] [user: dave] [roles: ["viewer"]] [mfaEnabled: False] '
                        '{"databaseId": "db-1", "assetId": "a-1", "filePath": "/data/model.obj"} ' + _ECHO)
LEGACY_FILE_DOWNLOAD_STREAMED = ('[FILEDOWNLOAD-STREAMED] [user: dave] [roles: []] [mfaEnabled: False] '
                                 '{"databaseId": "db-1", "assetId": "a-1", "filePath": "/data/model.obj"} ' + _ECHO)
LEGACY_AUTH_CHANGES = ('[AUTHCHANGES][type: roleCreate] [user: admin] [roles: ["admin"]] [mfaEnabled: True] '
                       '{"roleName": "r1", "operation": "create", "description": "d", "mfaRequired": false} ' + _ECHO)
LEGACY_AUTH_OTHER = ('[AUTHOTHER][type: userProfileUpdate] [user: alice] [roles: ["editor"]] [mfaEnabled: True] '
                     '{"userId": "alice", "operation": "update"} ' + _ECHO)
LEGACY_ERRORS = ('[ERRORS][type: validation_error] [user: john.doe] [roles: ["editor"]] [mfaEnabled: True] '
                 '{"error": "Invalid asset ID format", "assetId": "invalid-id"} ' + _ECHO)
LEGACY_ACTIONS = ('[ACTIONS][type: edhAmiRecipe] [user: admin] [roles: ["admin"]] [mfaEnabled: True] '
                  '{"operation": "create", "recipeId": "r-1", "outcome": "in-progress"} ' + _ECHO)
LEGACY_SEARCH = ('[ACTIONS][type: search] [user: alice] [roles: ["editor"]] [mfaEnabled: True] '
                 '{"requestParameters": {"query": "SECRET-TERM-777", "from": 0, "size": 10, '
                 '"tokens": [{"propertyKey": "all", "operator": "=", "value": "SECRET-TERM-777"}]}} ' + _ECHO)
LEGACY_ROLES_WITH_BRACKETS = ('[ACTIONS][type: assetUpdate] [user: alice] [roles: ["x] [user: admin"]] '
                              '[mfaEnabled: True] {"assetId": "a-1"}')


def contract_payload(message: str) -> Dict[str, Any]:
    """The payload of a record by the documented rule."""
    start = message.index("{", message.index("[mfaEnabled: ") + 1)
    return json.JSONDecoder().raw_decode(message, start)[0]


def one_action_message(writer, action: str = "assetUpdate", **kwargs: Any) -> str:
    """Write one action record with the real formatter and return its message."""
    event = kwargs.pop("event", None) or api_event()
    auditLogging.log_action(event, action, **kwargs)
    messages = [message for _, message in written(writer)]
    assert messages, "the writer produced no record"
    return messages[-1]
