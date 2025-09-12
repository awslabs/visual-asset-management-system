/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ErrorBoundary from "../../common/ErrorBoundary";
import { LoadingSpinner } from "../../common/LoadingSpinner";
import AssetVersionManager from "../versions/AssetVersionManager";

interface VersionsTabProps {
    databaseId: string;
    assetId: string;
    isActive: boolean;
}

export const VersionsTab: React.FC<VersionsTabProps> = ({ databaseId, assetId, isActive }) => {
    // Only render the component when the tab is active to improve performance
    if (!isActive) {
        return null;
    }

    return (
        <ErrorBoundary componentName="Asset Versions">
            <AssetVersionManager />
        </ErrorBoundary>
    );
};

export default VersionsTab;
