/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { webRoutes } from "./APIService";

/**
 * Batched, cached web route access checks.
 *
 * Multiple components check web route access on mount (the route table in
 * routes.tsx and the side navigation in layout/Navigation.tsx). Each used to
 * POST auth/routes independently, producing duplicate API calls on page load.
 * This helper coalesces all checks requested within the same tick into a
 * single POST and caches per-route results for the lifetime of the SPA
 * session (module state resets on full page load, e.g. sign-out redirect).
 */

export interface WebRouteCheck {
    method: string;
    route__path: string;
}

interface PendingEntry {
    route: WebRouteCheck;
    resolves: ((allowed: boolean) => void)[];
    rejects: ((error: any) => void)[];
}

const allowedCache = new Map<string, boolean>();
let pendingRoutes: Map<string, PendingEntry> | null = null;
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function keyOf(route: WebRouteCheck): string {
    return `${route.method} ${route.route__path}`;
}

function scheduleFlush() {
    if (flushTimer !== null) {
        return;
    }
    flushTimer = setTimeout(async () => {
        const batch = pendingRoutes;
        pendingRoutes = null;
        flushTimer = null;
        if (!batch || batch.size === 0) {
            return;
        }
        const routes = Array.from(batch.values()).map((entry) => entry.route);
        try {
            const value: any = await webRoutes({ routes });
            if (!value || value[0] === false || !Array.isArray(value.allowedRoutes)) {
                throw new Error(
                    "webRoutes - " + (Array.isArray(value) ? value[1] : "Unexpected response")
                );
            }
            const allowedSet = new Set(
                value.allowedRoutes.map((r: WebRouteCheck) => `${r.method} ${r.route__path}`)
            );
            batch.forEach((entry, key) => {
                const allowed = allowedSet.has(key);
                allowedCache.set(key, allowed);
                entry.resolves.forEach((resolve) => resolve(allowed));
            });
        } catch (error) {
            // Do not cache failures; let every caller handle the error
            // (existing callers fail closed by blocking their routes).
            batch.forEach((entry) => {
                entry.rejects.forEach((reject) => reject(error));
            });
        }
    }, 0);
}

/**
 * Check which of the given web routes the current user may access.
 * Returns the allowed subset (same shape the callers previously consumed
 * from the raw auth/routes response). Checks already resolved this session
 * are served from cache; concurrent uncached checks share one POST.
 */
export async function checkWebRoutesAllowed(routes: WebRouteCheck[]): Promise<WebRouteCheck[]> {
    const checks = routes.map((route) => {
        const key = keyOf(route);
        const cached = allowedCache.get(key);
        if (cached !== undefined) {
            return Promise.resolve({ route, allowed: cached });
        }
        if (!pendingRoutes) {
            pendingRoutes = new Map();
        }
        let entry = pendingRoutes.get(key);
        if (!entry) {
            entry = { route, resolves: [], rejects: [] };
            pendingRoutes.set(key, entry);
        }
        const pendingEntry = entry;
        return new Promise<{ route: WebRouteCheck; allowed: boolean }>((resolve, reject) => {
            pendingEntry.resolves.push((allowed) => resolve({ route, allowed }));
            pendingEntry.rejects.push(reject);
        });
    });
    scheduleFlush();
    const results = await Promise.all(checks);
    return results.filter((result) => result.allowed).map((result) => result.route);
}
