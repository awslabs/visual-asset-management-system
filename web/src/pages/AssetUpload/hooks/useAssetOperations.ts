/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback } from "react";
import AssetUploadService from "../../../services/AssetUploadService";
import { Metadata } from "../../../components/single/Metadata";
import { AssetDetail } from "../AssetUpload";

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
                            errors.push(`Parent link failed: ${error.message}`);
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
                            errors.push(`Child link failed: ${error.message}`);
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
                            errors.push(`Related link failed: ${error.message}`);
                        })
                );
            }
        }

        await Promise.allSettled(linkPromises);

        // Create metadata for successfully created links
        if (createdLinks.length > 0) {
            const metadataPromises = [];

            for (const link of createdLinks) {
                let originalAsset = null;

                if (link.relationshipType === "parents") {
                    originalAsset = assetDetail.assetLinksFe.parents?.find(
                        (asset) => asset.assetId === link.assetId
                    );
                } else if (link.relationshipType === "child") {
                    originalAsset = assetDetail.assetLinksFe.child?.find(
                        (asset) => asset.assetId === link.assetId
                    );
                } else if (link.relationshipType === "related") {
                    originalAsset = assetDetail.assetLinksFe.related?.find(
                        (asset) => asset.assetId === link.assetId
                    );
                }

                if (originalAsset && originalAsset.metadata && originalAsset.metadata.length > 0) {
                    for (const metadataItem of originalAsset.metadata) {
                        metadataPromises.push(
                            AssetUploadService.createAssetLinkMetadata(link.assetLinkId, {
                                metadataKey: metadataItem.metadataKey,
                                metadataValue: metadataItem.metadataValue,
                                metadataValueType: metadataItem.metadataValueType,
                            }).catch((error) => {
                                errors.push(
                                    `Asset link metadata creation failed: ${error.message}`
                                );
                            })
                        );
                    }
                }
            }

            if (metadataPromises.length > 0) {
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
