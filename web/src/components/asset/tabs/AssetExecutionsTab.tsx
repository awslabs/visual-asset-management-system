/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ErrorBoundary from "../../common/ErrorBoundary";
import ExecutionsBoard from "../../../features/orchestration/executions/ExecutionsBoard";

interface AssetExecutionsTabProps {
    databaseId: string;
    assetId: string;
    isActive: boolean;
}

/**
 * Asset "Executions" tab — shows this asset's executions. The "Execute workflow" action lives in the
 * ExecutionsBoard toolbar (shared with the global Executions page), so there is no separate control
 * here; launching from the board presets this asset as the input.
 */
export const AssetExecutionsTab: React.FC<AssetExecutionsTabProps> = ({
    databaseId,
    assetId,
    isActive,
}) => {
    return (
        <ErrorBoundary componentName="Asset Executions">
            {isActive && <ExecutionsBoard scope={{ kind: "asset", databaseId, assetId }} />}
        </ErrorBoundary>
    );
};

export default AssetExecutionsTab;
