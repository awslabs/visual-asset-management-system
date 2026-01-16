/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback } from "react";
import AssetUploadService from "../../../services/AssetUploadService";
import { Metadata } from "../../../components/single/Metadata";
import { AssetDetail } from "../AssetUpload";
import { extractErrorMessage, extractStatusCode } from "../uploadRetry";

export function useAssetOperations() {
    /**
     * Create a new asset
     */
    const createAsset = useCallback(async (assetDetail: AssetDetail) => {
        const assetData = {
            assetName: assetDetail.assetName || assetDetail.assetId || "",
            databaseId: assetDetail.databaseId || "",
            description: assetDetail.description || "",
            isDistributable: assetDetail.isDistributable || false,
            tags: assetDetail.tags || [],
        };

        return await AssetUploadService.createAsset(assetData);
    }, []);

    /**
     * Add metadata to an asset
     */
    const addMetadata = useCallback(
        async (databaseId: string, assetId: string, metadata: Metadata) => {
            if (Object.keys(metadata).length === 0) {
                return;
            }

            return await AssetUploadService.addMetadata(databaseId, assetId, metadata);
        },
        []
    );

    /**
     * Create asset links
     */
    const createAssetLinks = useCallback(async (assetId: string, assetDetail: AssetDetail) => {
        if (
            !assetDetail.assetLinksFe ||
            (!assetDetail.assetLinksFe.parents?.length &&
                !assetDetail.assetLinksFe.child?.length &&
                !assetDetail.assetLinksFe.related?.length)
        ) {
            return { success: true, errors: [] };
        }

        const linkPromises = [];
        const errors: string[] = [];
        const createdLinks: {
            assetLinkId: string;
            assetId: string;
            relationshipType: string;
        }[] = [];

        // Create parent links
        if (assetDetail.assetLinksFe.parents?.length) {
            for (const parentAsset of assetDetail.assetLinksFe.parents) {
                if (!parentAsset.assetId || !parentAsset.databaseId) {
                    errors.push(`Parent asset missing required fields`);
                    continue;
                }

                linkPromises.push(
                    AssetUploadService.createAssetLink({
                        fromAssetId: parentAsset.assetId,
                        fromAssetDatabaseId: parentAsset.databaseId,
                        toAssetId: assetId,
                        toAssetDatabaseId: assetDetail.databaseId || "",
                        relationshipType: "parentChild",
                        ...(parentAsset.assetLinkAliasId
                            ? { assetLinkAliasId: parentAsset.assetLinkAliasId }
                            : {}),
                    })
                        .then((response) => {
                            createdLinks.push({
                                assetLinkId: response.assetLinkId,
                                assetId: parentAsset.assetId,
                                relationshipType: "parents",
                            });
                            return response;
                        })
                        .catch((error) => {
                            const errorMessage = extractErrorMessage(error);
                            const statusCode = extractStatusCode(error);
                            errors.push(
                                `Parent link failed (${statusCode || "unknown"}): ${errorMessage}`
                            );
                        })
                );
            }
        }

        // Create child links
        if (assetDetail.assetLinksFe.child?.length) {
            for (const childAsset of assetDetail.assetLinksFe.child) {
                if (!childAsset.assetId || !childAsset.databaseId) {
                    errors.push(`Child asset missing required fields`);
                    continue;
                }

                linkPromises.push(
                    AssetUploadService.createAssetLink({
                        fromAssetId: assetId,
                        fromAssetDatabaseId: assetDetail.databaseId || "",
                        toAssetId: childAsset.assetId,
                        toAssetDatabaseId: childAsset.databaseId,
                        relationshipType: "parentChild",
                        ...(childAsset.assetLinkAliasId
                            ? { assetLinkAliasId: childAsset.assetLinkAliasId }
                            : {}),
                    })
                        .then((response) => {
                            createdLinks.push({
                                assetLinkId: response.assetLinkId,
                                assetId: childAsset.assetId,
                                relationshipType: "child",
                            });
                            return response;
                        })
                        .catch((error) => {
                            const errorMessage = extractErrorMessage(error);
                            const statusCode = extractStatusCode(error);
                            errors.push(
                                `Child link failed (${statusCode || "unknown"}): ${errorMessage}`
                            );
                        })
                );
            }
        }

        // Create related links
        if (assetDetail.assetLinksFe.related?.length) {
            for (const relatedAsset of assetDetail.assetLinksFe.related) {
                if (!relatedAsset.assetId || !relatedAsset.databaseId) {
                    errors.push(`Related asset missing required fields`);
                    continue;
                }

                linkPromises.push(
                    AssetUploadService.createAssetLink({
                        fromAssetId: assetId,
                        fromAssetDatabaseId: assetDetail.databaseId || "",
                        toAssetId: relatedAsset.assetId,
                        toAssetDatabaseId: relatedAsset.databaseId,
                        relationshipType: "related",
                    })
                        .then((response) => {
                            createdLinks.push({
                                assetLinkId: response.assetLinkId,
                                assetId: relatedAsset.assetId,
                                relationshipType: "related",
                            });
                            return response;
                        })
                        .catch((error) => {
                            const errorMessage = extractErrorMessage(error);
                            const statusCode = extractStatusCode(error);
                            errors.push(
                                `Related link failed (${statusCode || "unknown"}): ${errorMessage}`
                            );
                        })
                );
            }
        }

        await Promise.allSettled(linkPromises);

        // Create metadata for successfully created links using bulk API
        if (createdLinks.length > 0 && assetDetail.assetLinksMetadata) {
            const metadataPromises = [];

            for (const link of createdLinks) {
                // Get metadata from assetLinksMetadata using the relationship type and assetId
                let metadataForLink: any[] = [];

                if (link.relationshipType === "parents" && assetDetail.assetLinksMetadata.parents) {
                    metadataForLink = assetDetail.assetLinksMetadata.parents[link.assetId] || [];
                } else if (
                    link.relationshipType === "child" &&
                    assetDetail.assetLinksMetadata.child
                ) {
                    metadataForLink = assetDetail.assetLinksMetadata.child[link.assetId] || [];
                } else if (
                    link.relationshipType === "related" &&
                    assetDetail.assetLinksMetadata.related
                ) {
                    metadataForLink = assetDetail.assetLinksMetadata.related[link.assetId] || [];
                }

                console.log(
                    `[useAssetOperations] Metadata for link ${link.assetLinkId} (${link.relationshipType}, assetId: ${link.assetId}):`,
                    metadataForLink
                );

                // Batch all metadata for this link into a single API call
                if (metadataForLink && metadataForLink.length > 0) {
                    const metadataArray = metadataForLink.map((item: any) => ({
                        metadataKey: item.metadataKey,
                        metadataValue: item.metadataValue,
                        metadataValueType: item.metadataValueType || "string",
                    }));

                    console.log(
                        `[useAssetOperations] Creating metadata for link ${link.assetLinkId}:`,
                        metadataArray
                    );

                    metadataPromises.push(
                        AssetUploadService.createAssetLinkMetadata(
                            link.assetLinkId,
                            metadataArray
                        ).catch((error) => {
                            const errorMessage = extractErrorMessage(error);
                            const statusCode = extractStatusCode(error);
                            errors.push(
                                `Asset link metadata creation failed for ${link.assetLinkId} (${
                                    statusCode || "unknown"
                                }): ${errorMessage}`
                            );
                        })
                    );
                }
            }

            if (metadataPromises.length > 0) {
                console.log(
                    `[useAssetOperations] Creating metadata for ${metadataPromises.length} asset links`
                );
                await Promise.allSettled(metadataPromises);
            }
        }

        return {
            success: errors.length === 0,
            errors,
            createdLinks,
        };
    }, []);

    return {
        createAsset,
        addMetadata,
        createAssetLinks,
    };
}
