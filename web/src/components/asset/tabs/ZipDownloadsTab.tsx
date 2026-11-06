/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import ZipDownloadsTable from "../../zipDownloads/ZipDownloadsTable";

interface ZipDownloadsTabProps {
    databaseId: string;
    assetId: string;
    /** Only the active tab loads and polls. */
    isActive: boolean;
}

/** The asset page's "My zip downloads" tab: the caller's own zip downloads for this asset. */
const ZipDownloadsTab: React.FC<ZipDownloadsTabProps> = ({ databaseId, assetId, isActive }) => (
    <ZipDownloadsTable databaseId={databaseId} assetId={assetId} isActive={isActive} />
);

export default ZipDownloadsTab;
