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
    /** Called after a version is created, reverted to, edited or archived. */
    onVersionsChanged?: () => void;
}

export const VersionsTab: React.FC<VersionsTabProps> = ({
    databaseId,
    assetId,
    isActive,
    onVersionsChanged,
}) => {
    // Only render the component when the tab is active to improve performance
    if (!isActive) {
        return null;
    }

    return (
        <ErrorBoundary componentName="Asset Versions">
            <AssetVersionManager onVersionsChanged={onVersionsChanged} />
        </ErrorBoundary>
    );
};

export default VersionsTab;
