/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ModernSearchContainer from "../../../components/search/ModernSearchContainer";
import type { SearchProviderProps } from "../../core/types";

/**
 * Keyword (OpenSearch) and natural-language (vector) search in one container. The page's title row
 * carries the h1; the container adds its result count and Clear all filters to that row.
 */
const UnifiedSearchProviderComponent: React.FC<SearchProviderProps> = ({
    databaseId,
    assetActionDisabledReasons,
    titleSlots,
}) => {
    return (
        <ModernSearchContainer
            mode="full"
            databaseId={databaseId}
            allowedViews={["table", "card", "map"]}
            showPreferences={true}
            showBulkActions={true}
            assetActionDisabledReasons={assetActionDisabledReasons}
            titleSlots={titleSlots}
        />
    );
};

export default UnifiedSearchProviderComponent;
