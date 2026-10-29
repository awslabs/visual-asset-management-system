# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Whether a database id names a database, for the compliance reads that take one.

A compliance read of a database id that names no database would answer like a read of an empty
database: an overview with no assets, an empty page. A mistyped id would then read as "no assets".
So the database-level compliance reads (the database overview, and the quarantine, cascade and
audit listings filtered by `databaseId`) answer `404` "Database not found" for such an id, once the
caller has passed the read's own authorization check on it. A caller refused on the id gets the
answer it got before, whether the database exists or not, so the answer tells it nothing of the
database's existence.

A deleted database still names one: deleting it moves its row to `<databaseId>#deleted`, and the
compliance history kept under its id (audit entries, cascades) stays readable. `GLOBAL`, the scope
of global schemas, names no database row and is always accepted.
"""

from models.common import general_error

# The answer of a database-level compliance read for an id that names no database.
DATABASE_NOT_FOUND_STATUS = 404
DATABASE_NOT_FOUND_MESSAGE = "Database not found"
# The scope keyword the compliance validators accept beside database ids (`allowGlobalKeyword`).
GLOBAL_SCOPE = "GLOBAL"
# The suffix of a deleted database's row id (`databaseService.delete_database`).
DELETED_DATABASE_ID_SUFFIX = "#deleted"


def database_known(database_table, database_id: str) -> bool:
    """Whether `database_id` names a database: `GLOBAL`, a database row, or the row a deleted
    database leaves under `<databaseId>#deleted`. One strongly-consistent GetItem (keys only), and
    a second only for an id with no live row."""
    if database_id == GLOBAL_SCOPE:
        return True
    for key in (database_id, f"{database_id}{DELETED_DATABASE_ID_SUFFIX}"):
        response = database_table.get_item(
            Key={"databaseId": key}, ConsistentRead=True, ProjectionExpression="databaseId")
        if response.get("Item"):
            return True
    return False


def database_not_found(event):
    """The `404` "Database not found" response of a database-level compliance read."""
    return general_error(status_code=DATABASE_NOT_FOUND_STATUS,
                         body={"message": DATABASE_NOT_FOUND_MESSAGE}, event=event)
