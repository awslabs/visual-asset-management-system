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
 * Returns true if two paths match segment-by-segment, where {param} placeholders match any segment.
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

        // If either is a {param}, it matches
        const templateIsParam = templateSeg.startsWith("{") && templateSeg.endsWith("}");
        const queryIsParam = querySeg.startsWith("{") && querySeg.endsWith("}");

        if (templateIsParam || queryIsParam) {
            continue;
        }

        // Both are concrete segments, must match exactly
        if (templateSeg !== querySeg) {
            return false;
        }
    }

    return true;
}

/**
 * Hook to check if the current user is allowed to call a given API route.
 * Fetches allowed routes once per SPA session (module-level cached promise).
 * Returns { loading, can(method, pathTemplate) }.
 * Fail-closed: while loading or on error, can() returns false.
 */
export function useAllowedRoutes() {
    const [loading, setLoading] = useState(true);
    const [routes, setRoutes] = useState<AllowedRoute[]>([]);

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
                } else {
                    setRoutes([]);
                }
            })
            .catch(() => {
                setRoutes([]);
            })
            .finally(() => {
                setLoading(false);
            });
    }, []);

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

    return { loading, can };
}
