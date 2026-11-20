/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { appCache } from "../services/appCache";
import {
    ALLOWED_API_ROUTES_CACHE_KEY,
    ALLOWED_API_ROUTES_CACHE_TTL_MILLIS,
} from "../common/constants/authRoutes";

/** Dispatched on `window` each time a new allowed-routes listing is cached. */
export const ALLOWED_API_ROUTES_CACHED_EVENT = "vams:allowed-api-routes-cached";

/**
 * Cache the `GET /auth/routes/api/allowed` listing for `isApiRouteAllowed` and tell the views that
 * read it (the side navigation and the profile menu) to render again, so a link gated on the
 * listing (`requiresApiRoute`) appears as soon as the listing lands.
 */
export function cacheAllowedApiRoutes(listing: unknown): void {
    appCache.setItemWithExpiry(
        ALLOWED_API_ROUTES_CACHE_KEY,
        listing,
        ALLOWED_API_ROUTES_CACHE_TTL_MILLIS
    );
    window.dispatchEvent(new Event(ALLOWED_API_ROUTES_CACHED_EVENT));
}
