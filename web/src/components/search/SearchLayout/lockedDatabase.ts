/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Synonyms from "../../../synonyms";

/**
 * What the Database multiselect's trigger reads. Unlocked it is the "All databases" placeholder (the
 * selection shows as tokens below it). On a database's page the control is locked and its tokens are
 * hidden, so the trigger names the database it is locked to instead of suggesting every database is
 * searched.
 */
export function lockedDatabasePlaceholder(
    databaseLocked: boolean,
    selected: Array<{ label?: string; value?: string }>
): string {
    const names = selected.map((option) => option.label || option.value || "").filter(Boolean);
    if (databaseLocked && names.length > 0) {
        return names.join(", ");
    }
    return `All ${Synonyms.databases}`;
}
