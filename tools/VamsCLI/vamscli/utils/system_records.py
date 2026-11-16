"""Recognise the API's refusal of a change to a system (deployment-owned) pipeline or workflow.

The API answers such a change with a 400 whose message names the refused field or action and what
stays editable (backend ``common/workflows/systemRecords.py``). A command shows that message on its
own: the generic hint it prints after other invalid-data errors would point at the wrong fix.

Matching is exact, the way ``api_client`` recognises other handler messages: the fixed messages
verbatim, and each field-naming message as its fixed prefix and suffix around one bare field name.
A message that only mentions system records does not match. ``tests/test_system_records.py`` checks
these copies against the backend's messages.
"""

import re
from typing import FrozenSet, Tuple

SYSTEM_RECORD_REFUSALS: FrozenSet[str] = frozenset({
    "System pipelines cannot be archived or restored through the API; the deployment owns them.",
    "System workflows cannot be archived or restored through the API; the deployment owns them.",
    "Templates of system pipelines cannot be added or deleted.",
    "Triggers of system workflows cannot be added or deleted.",
})

# (prefix, suffix) of each message that names the refused field between them.
SYSTEM_RECORD_FIELD_REFUSALS: Tuple[Tuple[str, str], ...] = (
    ('System pipelines are read-only except "enabled"; "', '" cannot be changed.'),
    ('System workflows are read-only except "enabled"; "', '" cannot be changed.'),
    ('Templates of system pipelines are read-only except "configBody", "tagSchema" and '
     '"webFormJson"; "', '" differs from the stored value.'),
    ('Triggers of system workflows are read-only except "enabled"; "',
     '" differs from the stored trigger.'),
)

_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def is_system_record_refusal(error: object) -> bool:
    """True when `error` (an exception or its message) is one of the API's system-record refusals."""
    message = str(error)
    if message in SYSTEM_RECORD_REFUSALS:
        return True
    for prefix, suffix in SYSTEM_RECORD_FIELD_REFUSALS:
        if message.startswith(prefix) and message.endswith(suffix):
            field = message[len(prefix):len(message) - len(suffix)]
            if _FIELD_NAME.fullmatch(field):
                return True
    return False


def system_runs_hidden_hint(workflow_id=None, workflow_database_id=None) -> str:
    """The text-mode line an execution list prints when the service hid system workflow runs.

    The service shows a system workflow's runs by default only when the list is filtered to ONE
    workflow, which takes both -w and --workflow-database-id. With -w alone it is not, so the hint
    names the missing option as well as --include-system."""
    if workflow_id and not workflow_database_id:
        return ("System workflow runs hidden; add --include-system to list them, or add "
                "--workflow-database-id to filter to that one workflow.")
    return "System workflow runs hidden; add --include-system to list them."
