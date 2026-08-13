/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { fetchAllDatabases } from "../../../services/APIService";

export interface DatabaseSummary {
    databaseId: string;
    description?: string;
}

/**
 * All databases the caller can see, used to pick a target database when creating a pipeline or
 * workflow from the global (database-less) list pages.
 *
 * Databases are a core entity, so this delegates to the standard `fetchAllDatabases` in
 * `services/APIService.ts` (the registered owner of the `/database` call + its NextToken paging)
 * rather than calling `apiClient` again here. It adapts that function's return — an array on
 * success, or an error/message string (or `false`) on failure — into the orchestration module's
 * `[ok, data]` tuple.
 */
export async function listAllDatabases(): Promise<[boolean, DatabaseSummary[] | string]> {
    const result = await fetchAllDatabases();
    if (Array.isArray(result)) {
        return [true, result as DatabaseSummary[]];
    }
    return [false, typeof result === "string" ? result : "Failed to load databases."];
}
