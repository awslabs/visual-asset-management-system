/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../apiClient";

/**
 * Response envelope returned by `GET /addon/physna/viewer`.
 *
 * The backend returns JSON describing what the frontend should show. The
 * frontend switches on `status` and, for `"ready"`, uses the viewer-token
 * bundle to build a direct Physna iframe src.
 */
export interface PhysnaViewerMetadataResponse {
    status:
        | "ready"
        | "indexing"
        | "failed"
        | "not_synced"
        | "not_found"
        | "unsupported"
        | "forbidden"
        | "upstream_unavailable"
        | "invalid_request"
        | "method_not_allowed"
        | "request_failed"
        | "internal_error";
    message: string;
    /** Populated only when `status === "ready"`. */
    tenantId?: string;
    physnaAssetId?: string;
    viewerToken?: string;
    physnaApiBase?: string;
    /** Populated on `indexing` and `failed` — raw upstream state for display. */
    physnaState?: string;
}

/**
 * Fetch Physna viewer metadata (authz + lookup + viewer-token mint) from the
 * VAMS backend. The frontend uses the returned envelope to decide whether to
 * render a direct-to-Physna iframe, show a "still indexing" placeholder, or
 * surface an error.
 */
export const fetchPhysnaViewerMetadata = async ({
    databaseId,
    assetId,
    relativePath,
}: {
    databaseId: string;
    assetId: string;
    relativePath: string;
}): Promise<[boolean, PhysnaViewerMetadataResponse | string]> => {
    try {
        const response = await apiClient.get("addon/physna/viewer", {
            queryStringParameters: {
                databaseId,
                assetId,
                relativePath,
            },
        });
        if (response && typeof response === "object" && "status" in response) {
            return [true, response as PhysnaViewerMetadataResponse];
        }
        if (response?.message) {
            console.log(response.message);
            return [false, response.message];
        }
        return [false, "Unexpected response shape from viewer endpoint"];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message || "Failed to load Physna viewer"];
    }
};
