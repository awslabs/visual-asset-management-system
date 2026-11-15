/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { appCache } from "../services/appCache";
import { featuresEnabled } from "../common/constants/featuresEnabled";
import { useAllowedRoutes } from "../features/orchestration/permissions/useAllowedRoutes";

// The routes the database's assets page lists from: search, or the database's asset listing when
// the deployment runs without OpenSearch.
const SEARCH_API_ROUTE = "/search";
const DATABASE_ASSETS_API_ROUTE = "/database/{databaseId}/assets";
// The route the asset page loads the asset from.
const DATABASE_ASSET_API_ROUTE = "/database/{databaseId}/assets/{assetId}";

/**
 * Whether the caller may open a database's assets page and an asset's page, so a link to either
 * is offered only to a caller the page will not answer with Not Authorized. Both are false while
 * the allowed routes load.
 */
export function useAssetPageAccess(): { canOpenDatabaseAssets: boolean; canOpenAsset: boolean } {
    const { can: canCallRoute } = useAllowedRoutes();
    const config = appCache.getItem("config");
    const noOpenSearch = config?.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH);
    const canOpenDatabaseAssets = noOpenSearch
        ? canCallRoute("GET", DATABASE_ASSETS_API_ROUTE)
        : canCallRoute("POST", SEARCH_API_ROUTE);
    const canOpenAsset = canCallRoute("GET", DATABASE_ASSET_API_ROUTE);
    return { canOpenDatabaseAssets, canOpenAsset };
}
