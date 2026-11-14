/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect } from "react";
import { fetchAllowedApiRoutes } from "../../../services/APIService";

interface AllowedRoute {
    path: string;
    methods: string[];
    category: string;
}

interface AllowedRoutesData {
    routes: AllowedRoute[];
    userId: string;
}

type FetchResult = [boolean, AllowedRoutesData | string];

// Module-level cache for SPA session-wide persistence
let cachedPromise: Promise<FetchResult> | null = null;

/**
 * Test helper to reset the module-level cache between tests.
 * @internal
 */
export function __resetCache() {
    cachedPromise = null;
}

/**
 * Segment matcher for route templates.
 * Returns true if two paths match segment-by-segment. A {param} in the GRANTED route matches any
 * segment; a {param} in the queried path is a placeholder for a value and only matches a granted
 * {param}, so a concrete segment in a grant is never satisfied by an unrelated query param.
 */
function pathsMatch(templatePath: string, queryPath: string): boolean {
    const templateSegments = templatePath.split("/");
    const querySegments = queryPath.split("/");

    if (templateSegments.length !== querySegments.length) {
        return false;
    }

    for (let i = 0; i < templateSegments.length; i++) {
        const templateSeg = templateSegments[i];
        const querySeg = querySegments[i];

        if (templateSeg.startsWith("{") && templateSeg.endsWith("}")) {
            continue;
        }

        if (templateSeg !== querySeg) {
            return false;
        }
    }

    return true;
}

/**
 * Hook to check if the current user is allowed to call a given API route.
 * Fetches allowed routes once per SPA session (module-level cached promise).
 * Returns { loading, failed, retry, can(method, pathTemplate) }.
 * Fail-closed: while loading or on error, can() returns false. `failed` is true when the last fetch
 * failed, and `retry()` fetches again (a failed fetch is never cached).
 */
export function useAllowedRoutes() {
    const [loading, setLoading] = useState(true);
    const [routes, setRoutes] = useState<AllowedRoute[]>([]);
    const [failed, setFailed] = useState(false);
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
        if (!cachedPromise) {
            cachedPromise = fetchAllowedApiRoutes() as Promise<FetchResult>;
        }

        const promise = cachedPromise;
        promise
            .then((result) => {
                const [success, data] = result;
                if (success && typeof data === "object" && "routes" in data) {
                    setRoutes((data as AllowedRoutesData).routes);
                    setFailed(false);
                } else {
                    // Only a successful fetch is cached; a failure is dropped so the next mount
                    // retries instead of pinning every action closed for the SPA session.
                    if (cachedPromise === promise) cachedPromise = null;
                    setRoutes([]);
                    setFailed(true);
                }
            })
            .catch(() => {
                if (cachedPromise === promise) cachedPromise = null;
                setRoutes([]);
                setFailed(true);
            })
            .finally(() => {
                setLoading(false);
            });
    }, [attempt]);

    const retry = () => {
        setLoading(true);
        setAttempt((n) => n + 1);
    };

    const can = (method: string, pathTemplate: string): boolean => {
        if (loading) {
            return false;
        }

        const upperMethod = method.toUpperCase();

        return routes.some((route) => {
            const routeMethodsUpper = route.methods.map((m) => m.toUpperCase());
            return routeMethodsUpper.includes(upperMethod) && pathsMatch(route.path, pathTemplate);
        });
    };

    return { loading, failed, retry, can };
}
