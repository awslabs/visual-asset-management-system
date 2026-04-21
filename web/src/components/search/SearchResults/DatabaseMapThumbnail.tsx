/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import Spinner from "@cloudscape-design/components/spinner";
import { fetchMetadata } from "../../../services/MetadataService";
import { extractLocationData } from "../utils/locationUtils";
import MapThumbnail from "./MapThumbnail";

interface DatabaseMapThumbnailProps {
    databaseId: string;
    mapStyleUrl: string;
    width?: number;
    height?: number;
}

/**
 * Fetches metadata for a database record and renders a MapThumbnail
 * if location data (location, latitude/longitude, GeoJSON) is found.
 */
const DatabaseMapThumbnail: React.FC<DatabaseMapThumbnailProps> = ({
    databaseId,
    mapStyleUrl,
    width = 200,
    height = 150,
}) => {
    const [loading, setLoading] = useState(true);
    const [assetData, setAssetData] = useState<any>(null);

    useEffect(() => {
        let cancelled = false;

        const loadMetadata = async () => {
            setLoading(true);
            try {
                const response = await fetchMetadata("database", databaseId);
                if (cancelled) return;

                if (response.metadata && response.metadata.length > 0) {
                    // Convert MetadataRecord[] to the MD_ object format
                    // that extractLocationData expects
                    const md: Record<string, string> = {};
                    response.metadata.forEach((record) => {
                        md[record.metadataKey] = record.metadataValue;
                    });
                    setAssetData({ MD_: md });
                } else {
                    setAssetData(null);
                }
            } catch (err) {
                console.log("[DatabaseMapThumbnail] Error fetching metadata for", databaseId, err);
                if (!cancelled) {
                    setAssetData(null);
                }
            } finally {
                if (!cancelled) {
                    setLoading(false);
                }
            }
        };

        loadMetadata();

        return () => {
            cancelled = true;
        };
    }, [databaseId]);

    if (loading) {
        return <Spinner size="normal" />;
    }

    if (!assetData) {
        return null;
    }

    // Check if location data can be extracted
    const locationData = extractLocationData(assetData);
    if (!locationData) {
        return null;
    }

    return (
        <MapThumbnail
            assetData={assetData}
            mapStyleUrl={mapStyleUrl}
            width={width}
            height={height}
        />
    );
};

export default DatabaseMapThumbnail;
