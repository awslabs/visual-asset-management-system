/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** The reserved sentinel for the scope shared by every database. */
export const GLOBAL_SCOPE = "GLOBAL";

/** True when a scope value means "shared by every database". An absent value counts as global. */
export function isGlobalScope(databaseId?: string | null): boolean {
    return !databaseId || databaseId === GLOBAL_SCOPE;
}

/** How a scope is written in plain text: the sentinel's own spelling, or the database's id. */
export function scopeLabel(databaseId?: string | null): string {
    return isGlobalScope(databaseId) ? GLOBAL_SCOPE : (databaseId as string);
}

/**
 * How a scope is shown to the user: the globe marks the shared scope so it reads distinctly from a
 * real database, matching both the database selector and ScopeBadge.
 */
export function scopeDisplayLabel(databaseId?: string | null): string {
    return isGlobalScope(databaseId) ? "🌐 GLOBAL" : (databaseId as string);
}
