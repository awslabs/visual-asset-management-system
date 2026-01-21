/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useCallback, useEffect } from "react";
import { fetchMetadata } from "../utils/apiHelpers";
import {
    EntityType,
    FileMetadataType,
    MetadataRecord,
    MetadataAPIResponse,
} from "../types/metadata.types";

interface UseMetadataFetchResult {
    data: MetadataRecord[];
    loading: boolean;
    error: string | null;
    refetch: () => Promise<void>;
    restrictMetadataOutsideSchemas?: boolean;
    attributeCount?: number;
    metadataCount?: number;
}

/**
 * Custom hook for fetching metadata with loading and error states
 */
export const useMetadataFetch = (
    entityType: EntityType,
    entityId: string,
    databaseId?: string,
    filePath?: string,
    fileType?: FileMetadataType,
    mode: "online" | "offline" = "online"
): UseMetadataFetchResult => {
    const [data, setData] = useState<MetadataRecord[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [restrictMetadataOutsideSchemas, setRestrictMetadataOutsideSchemas] = useState<
        boolean | undefined
    >(undefined);
    const [attributeCount, setAttributeCount] = useState<number | undefined>(undefined);
    const [metadataCount, setMetadataCount] = useState<number | undefined>(undefined);

    // Fetch counts for the OTHER file type (not the current one being displayed)
    const fetchOtherFileTypeCount = useCallback(
        async (currentFileType: FileMetadataType) => {
            if (
                mode === "offline" ||
                entityType !== "file" ||
                !entityId ||
                !databaseId ||
                !filePath
            ) {
                return;
            }

            try {
                console.log("[useMetadataFetch] Fetching count for other file type");

                // Fetch count for the OTHER type (not the one we just fetched)
                const otherType: FileMetadataType =
                    currentFileType === "attribute" ? "metadata" : "attribute";
                const otherResponse = await fetchMetadata(
                    entityType,
                    entityId,
                    databaseId,
                    filePath,
                    otherType
                );

                if (otherType === "attribute") {
                    setAttributeCount(otherResponse.metadata?.length || 0);
                } else {
                    setMetadataCount(otherResponse.metadata?.length || 0);
                }

                console.log(
                    "[useMetadataFetch] Other type count updated:",
                    otherType,
                    otherResponse.metadata?.length || 0
                );
            } catch (err) {
                console.error("[useMetadataFetch] Error fetching other file type count:", err);
                // Don't set error state for count fetching failures
            }
        },
        [entityType, entityId, databaseId, filePath, mode]
    );

    const refetch = useCallback(async () => {
        // Skip fetch in offline mode
        if (mode === "offline") {
            return;
        }

        // Validate required parameters
        if (!entityId) {
            setError("Entity ID is required");
            return;
        }

        if (entityType === "asset" && !databaseId) {
            setError("Database ID is required for asset metadata");
            return;
        }

        if (entityType === "file" && (!databaseId || !filePath || !fileType)) {
            setError("Database ID, file path, and file type are required for file metadata");
            return;
        }

        setLoading(true);
        setError(null);

        try {
            console.log("[useMetadataFetch] Fetching metadata:", {
                entityType,
                entityId,
                databaseId,
                filePath,
                fileType,
            });

            const response: MetadataAPIResponse = await fetchMetadata(
                entityType,
                entityId,
                databaseId,
                filePath,
                fileType
            );

            console.log("[useMetadataFetch] Response:", response);

            if (response.metadata) {
                setData(response.metadata);
            } else {
                setData([]);
            }

            // Extract restrictMetadataOutsideSchemas from response
            if (response.restrictMetadataOutsideSchemas !== undefined) {
                setRestrictMetadataOutsideSchemas(response.restrictMetadataOutsideSchemas);
            }

            // Check for error messages in response
            if (response.message && response.message.toLowerCase().includes("error")) {
                setError(response.message);
            }

            // For file entities, update the count for the current type and fetch the other type's count
            if (entityType === "file" && fileType) {
                // Update count for the type we just fetched
                if (fileType === "attribute") {
                    setAttributeCount(response.metadata?.length || 0);
                } else {
                    setMetadataCount(response.metadata?.length || 0);
                }

                // Fetch count for the OTHER type (only 1 additional call)
                await fetchOtherFileTypeCount(fileType);
            }
        } catch (err) {
            console.error("[useMetadataFetch] Error fetching metadata:", err);
            setError(err instanceof Error ? err.message : "Failed to fetch metadata");
            setData([]);
        } finally {
            setLoading(false);
        }
    }, [entityType, entityId, databaseId, filePath, fileType, mode, fetchOtherFileTypeCount]);

    // Auto-fetch on mount and when dependencies change
    useEffect(() => {
        refetch();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [entityType, entityId, databaseId, filePath, fileType, mode]);

    return {
        data,
        loading,
        error,
        refetch,
        restrictMetadataOutsideSchemas,
        attributeCount,
        metadataCount,
    };
};

export default useMetadataFetch;
