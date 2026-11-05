/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Shown on an action while the allowed-routes check is still loading (the check fails closed). */
export const PERMISSIONS_LOADING_REASON = "Checking your permissions.";

/**
 * Shown on an action when the allowed-routes check itself failed, so the role's permissions are
 * unknown rather than refused. `useAllowedRoutes` retries the check when a disabled action is
 * pressed or the page regains focus.
 */
export const PERMISSIONS_FAILED_REASON =
    "Your permissions could not be checked. Select this button or return to this page to try again.";

/**
 * The `disabledReason` for an action gated on `useAllowedRoutes().can()`: the loading text while the
 * check runs, the failed text when the check failed, `deniedReason` once it says no, and undefined
 * (the action is enabled) once it says yes. The API still authorizes every request; this only
 * keeps a role from being offered an action it is refused.
 */
export function routeDisabledReason(
    loading: boolean,
    failed: boolean,
    allowed: boolean,
    deniedReason: string
): string | undefined {
    if (loading) {
        return PERMISSIONS_LOADING_REASON;
    }
    if (failed) {
        return PERMISSIONS_FAILED_REASON;
    }
    return allowed ? undefined : deniedReason;
}
