/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Box from "@cloudscape-design/components/box";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";

interface ComplianceAssetCellProps {
    databaseId: string;
    assetId: string;
    assetName?: string | null;
    /** Whether the caller may open the asset page; the name is a link only then. */
    linked: boolean;
}

/**
 * An asset in a compliance table: its name (a link to the asset page when the caller may open
 * it) with its ID beneath in secondary text, in one column, as the Cascade Approvals and Audit
 * Log pages show the assets they list. One column instead of a name column and an ID column keeps
 * the tables within the page at 1280 px, where the pinned Actions column otherwise covered the
 * columns before it. An asset without a name shows its ID only.
 */
export const ComplianceAssetCell: React.FC<ComplianceAssetCellProps> = ({
    databaseId,
    assetId,
    assetName,
    linked,
}) => {
    const label = assetName || assetId;
    const title = linked ? (
        <Link href={`#/databases/${databaseId}/assets/${assetId}`}>{label}</Link>
    ) : (
        label
    );
    if (!assetName) {
        return <>{title}</>;
    }
    return (
        <SpaceBetween size="xxxs">
            {title}
            <Box variant="small" color="text-body-secondary">
                {assetId}
            </Box>
        </SpaceBetween>
    );
};

export default ComplianceAssetCell;
