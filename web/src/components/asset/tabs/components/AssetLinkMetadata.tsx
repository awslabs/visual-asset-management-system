/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import "./AssetLinkMetadata.css";
import { AssetLinkMetadata as AssetLinkMetadataType, TreeNodeItem } from "../types/AssetLinksTypes";
import { MetadataContainer } from "../../../metadataV2";
import { useAllowedRoutes } from "../../../../features/orchestration/permissions/useAllowedRoutes";

// Removing a stored field of an asset link, by row delete or by a bulk-edit save that leaves it out,
// needs DELETE on this route.
const ASSET_LINK_METADATA_ROUTE = "/asset-links/{assetLinkId}/metadata";

interface AssetLinkMetadataProps {
    assetLinkId: string;
    selectedNode: TreeNodeItem | null;
    mode?: "upload" | "view";
    databaseId?: string;
    restrictMetadataOutsideSchemas?: boolean;
    onMetadataChange?: (metadata: AssetLinkMetadataType[]) => void;
    initialMetadata?: AssetLinkMetadataType[];
}

/**
 * AssetLinkMetadata component - Now uses MetadataV2 Container
 *
 * This component has been refactored to use the new MetadataContainer component
 * which provides enhanced functionality including:
 * - Schema integration with visual indicators
 * - Bulk edit mode
 * - Better validation
 * - Change tracking
 * - Performance optimizations
 */
export const AssetLinkMetadata: React.FC<AssetLinkMetadataProps> = ({
    assetLinkId,
    selectedNode,
    mode = "view",
    databaseId,
    restrictMetadataOutsideSchemas = false,
    onMetadataChange,
    initialMetadata = [],
}) => {
    const { can } = useAllowedRoutes();
    // Convert old format to new format if needed
    const convertedInitialData = initialMetadata.map((item) => ({
        metadataKey: item.metadataKey,
        metadataValue: item.metadataValue,
        metadataValueType: item.metadataValueType,
    }));

    // Handle metadata changes for offline mode
    const handleMetadataChange = (data: any[]) => {
        if (onMetadataChange) {
            const converted = data.map((item) => ({
                assetLinkId: assetLinkId,
                metadataKey: item.metadataKey,
                metadataValue: item.metadataValue,
                metadataValueType: item.metadataValueType,
            }));
            onMetadataChange(converted);
        }
    };

    return (
        <MetadataContainer
            entityType="assetLink"
            entityId={assetLinkId}
            databaseId={databaseId}
            mode={mode === "upload" ? "offline" : "online"}
            initialData={mode === "upload" ? convertedInitialData : undefined}
            onDataChange={mode === "upload" ? handleMetadataChange : undefined}
            restrictMetadataOutsideSchemas={restrictMetadataOutsideSchemas}
            // A draft in the upload flow has nothing stored yet, so nothing to remove.
            canDelete={mode === "upload" || can("DELETE", ASSET_LINK_METADATA_ROUTE)}
        />
    );
};

export default AssetLinkMetadata;
