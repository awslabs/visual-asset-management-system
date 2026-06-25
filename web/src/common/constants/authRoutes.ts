/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { appCache } from "../../services/appCache";

// How long the cached list of API routes the user is allowed to call stays
// fresh (appCache key "allowedApiRoutes"). The auth flow fetches the list at
// login and renews it on this interval while the session is active.
export const ALLOWED_API_ROUTES_CACHE_TTL_MILLIS = 15 * 60 * 1000; // 15 minutes

// appCache key holding the allowed API routes envelope ({routes, userId}).
export const ALLOWED_API_ROUTES_CACHE_KEY = "allowedApiRoutes";

/**
 * Check the cached allowed-API-routes list (fetched at login and periodically
 * renewed by the auth flow) for whether the current user may call a given
 * route/method. Returns null when the cache is empty or expired (unknown) so
 * callers can fall back to their own behavior, true/false otherwise.
 */
export function isApiRouteAllowed(routePath: string, method: string): boolean | null {
    const cached = appCache.getItemWithExpiry(ALLOWED_API_ROUTES_CACHE_KEY);
    if (!cached || !Array.isArray(cached.routes)) {
        return null;
    }
    const route = cached.routes.find((r: { path: string }) => r.path === routePath);
    if (!route) {
        return false;
    }
    return Array.isArray(route.methods) && route.methods.includes(method);
}
