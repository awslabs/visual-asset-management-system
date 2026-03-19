/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
    Box,
    Button,
    Container,
    Grid,
    Header,
    SpaceBetween,
    Select,
    FormField,
    Popover,
    Icon,
    Spinner,
} from "@cloudscape-design/components";
import { useNavigate } from "react-router";
import {
    createSubscription,
    checkSubscription,
    unsubscribeFromAsset,
    downloadAsset,
} from "../../services/APIService";
import PreviewModal from "../filemanager/components/PreviewModal";
import BellIcon from "../../resources/img/bellIcon.svg";
import { useStatusMessage } from "../common/StatusMessage";
import ErrorBoundary from "../common/ErrorBoundary";

interface AssetDetailsPaneProps {
    asset: any;
    databaseId: string;
    onOpenUpdateAsset: () => void;
    onOpenDeleteModal: () => void;
    // Version selector props (passed through from ViewAsset)
    versions?: any[];
    versionsLoading?: boolean;
    selectedVersionId?: string | null;
    onVersionChange?: (versionId: string | null) => void;
}

export const AssetDetailsPane: React.FC<AssetDetailsPaneProps> = ({
    asset,
    databaseId,
    onOpenUpdateAsset,
    onOpenDeleteModal,
    versions = [],
    versionsLoading = false,
    selectedVersionId,
    onVersionChange,
}) => {
    const navigate = useNavigate();
    const { showMessage } = useStatusMessage();
    const [subscribed, setSubscribed] = useState<boolean>(false);
    const [isSubscribing, setIsSubscribing] = useState<boolean>(false);
    const [userName, setUserName] = useState<string>("");

    // Asset preview thumbnail state
    const previewKey = asset?.previewLocation?.Key || asset?.previewLocation?.key || "";
    const hasPreview = !!previewKey;
    const [thumbnailUrl, setThumbnailUrl] = useState<string | null>(null);
    const [thumbnailLoading, setThumbnailLoading] = useState<boolean>(false);
    const [thumbnailError, setThumbnailError] = useState<boolean>(false);
    const [showPreviewModal, setShowPreviewModal] = useState<boolean>(false);

    // Load preview thumbnail when asset changes
    useEffect(() => {
        if (!hasPreview || !asset?.assetId || !databaseId) {
            setThumbnailUrl(null);
            setThumbnailLoading(false);
            return;
        }

        let cancelled = false;
        setThumbnailLoading(true);
        setThumbnailError(false);
        setThumbnailUrl(null);

        (async () => {
            try {
                const response = await downloadAsset({
                    databaseId,
                    assetId: asset.assetId,
                    key: previewKey,
                    versionId: "",
                    downloadType: "assetPreview",
                });
                if (cancelled) return;
                if (response !== false && Array.isArray(response) && response[0] !== false) {
                    setThumbnailUrl(response[1]);
                } else {
                    setThumbnailError(true);
                }
            } catch {
                if (!cancelled) setThumbnailError(true);
            } finally {
                if (!cancelled) setThumbnailLoading(false);
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [asset?.assetId, databaseId, previewKey, hasPreview]);

    // Get username and check subscription status on mount or when asset changes
    useEffect(() => {
        const user = JSON.parse(localStorage.getItem("user") || '{"username": ""}');
        setUserName(user.username);

        if (asset?.assetId && user.username) {
            checkSubscriptionStatus(user.username);
        }
    }, [asset?.assetId]);

    // Check if user is subscribed to this asset
    const checkSubscriptionStatus = async (username: string): Promise<void> => {
        if (!asset?.assetId) return;

        try {
            const result = (await checkSubscription({
                userId: username,
                assetId: asset.assetId,
            })) as [boolean, any];

            if (result[0] && result[1] === "success") {
                setSubscribed(true);
            } else {
                setSubscribed(false);
            }
        } catch (error) {
            console.error("Error checking subscription status:", error);
            setSubscribed(false);
        }
    };

    // Subscription handling
    const handleSubscriptionToggle = async () => {
        if (!asset?.assetId) return;

        setIsSubscribing(true);

        const subscriptionBody = {
            eventName: "Asset Version Change",
            entityName: "Asset",
            subscribers: [userName],
            entityId: asset.assetId,
        };

        try {
            if (subscribed) {
                // Unsubscribe
                const result = await unsubscribeFromAsset(subscriptionBody);

                if (result && result[0]) {
                    setSubscribed(false);
                    showMessage({
                        type: "success",
                        message: (
                            <span>
                                You've successfully unsubscribed from{" "}
                                <i>{subscriptionBody.eventName}</i> updates for{" "}
                                <i>{asset.assetName}</i>. To resume receiving updates, please
                                subscribe again.
                            </span>
                        ),
                        autoDismiss: true,
                        dismissible: true,
                    });
                }
            } else {
                // Subscribe
                const response = await createSubscription(subscriptionBody);

                if (response) {
                    setSubscribed(true);
                    showMessage({
                        type: "success",
                        message: (
                            <span>
                                You've successfully signed up for receiving updates on{" "}
                                <i>{subscriptionBody.eventName}</i> for <i>{asset.assetName}</i>.
                                Please check your inbox and confirm the subscription.
                            </span>
                        ),
                        autoDismiss: true,
                        dismissible: true,
                    });
                }
            }
        } catch (error: any) {
            console.error("Subscription error:", error);
            showMessage({
                type: "error",
                message: `Failed to ${subscribed ? "unsubscribe" : "subscribe"}: ${
                    error.message || "Unknown error"
                }`,
                dismissible: true,
            });
        } finally {
            setIsSubscribing(false);
        }
    };

    return (
        <ErrorBoundary componentName="Asset Details">
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button onClick={onOpenDeleteModal}>Delete</Button>
                                <Button onClick={onOpenUpdateAsset}>Edit</Button>
                                {asset && (
                                    <Button
                                        iconAlign="right"
                                        iconUrl={subscribed ? BellIcon : ""}
                                        variant={subscribed ? "normal" : "primary"}
                                        onClick={handleSubscriptionToggle}
                                        loading={isSubscribing}
                                    >
                                        {subscribed ? "Subscribed" : "Subscribe"}
                                    </Button>
                                )}
                            </SpaceBetween>
                        }
                    >
                        <span style={{ fontSize: "1.2em" }}>
                            {asset?.assetName || "Asset Details"}
                        </span>
                    </Header>
                }
            >
                <div style={{ marginBottom: "-10px" }}>
                    <div style={{ display: "flex", gap: "20px" }}>
                        {/* Optional preview thumbnail — only takes the space it needs */}
                        {hasPreview && thumbnailUrl && (
                            <div
                                style={{
                                    flexShrink: 0,
                                    display: "flex",
                                    alignItems: "center",
                                }}
                            >
                                {thumbnailLoading && <Spinner size="normal" />}
                                {!thumbnailLoading && !thumbnailError && thumbnailUrl && (
                                    <img
                                        src={thumbnailUrl}
                                        alt={`Preview of ${asset?.assetName || "asset"}`}
                                        onClick={() => setShowPreviewModal(true)}
                                        onError={() => setThumbnailError(true)}
                                        style={{
                                            maxHeight: "150px",
                                            cursor: "pointer",
                                            borderRadius: "4px",
                                            objectFit: "contain",
                                            marginBottom: "6px",
                                        }}
                                    />
                                )}
                            </div>
                        )}

                        {/* Info columns — fill remaining space */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <Grid gridDefinition={[{ colspan: 4 }, { colspan: 3 }, { colspan: 5 }]}>
                                {/* Col: Asset Id */}
                                <div>
                                    <div
                                        style={{
                                            fontSize: "16px",
                                            fontWeight: "bold",
                                            marginBottom: "4px",
                                        }}
                                    >
                                        Asset Id
                                    </div>
                                    <div style={{ marginBottom: "16px" }}>{asset?.assetId}</div>
                                    <div
                                        style={{
                                            fontSize: "16px",
                                            fontWeight: "bold",
                                            marginBottom: "4px",
                                        }}
                                    >
                                        Description
                                    </div>
                                    <div style={{ marginBottom: "16px" }}>{asset?.description}</div>
                                </div>

                                {/* Row 1, Col 2: Type / Distributable (combined) + Tags */}
                                <div>
                                    <div
                                        style={{
                                            fontSize: "16px",
                                            fontWeight: "bold",
                                            marginBottom: "4px",
                                            display: "flex",
                                            alignItems: "center",
                                            gap: "4px",
                                        }}
                                    >
                                        Type / Distributable
                                        <Popover
                                            dismissButton={false}
                                            position="top"
                                            size="medium"
                                            triggerType="custom"
                                            content={
                                                <Box padding="s">
                                                    <strong>Type:</strong> "folder" means the asset
                                                    contains multiple files. Otherwise it shows the
                                                    file extension of the single file contained.
                                                    <br />
                                                    <br />
                                                    <strong>Distributable:</strong> Indicates
                                                    whether the asset is currently enabled to allow
                                                    file downloads.
                                                </Box>
                                            }
                                        >
                                            <span
                                                style={{
                                                    cursor: "help",
                                                    color: "var(--vams-color-info)",
                                                }}
                                            >
                                                <Icon name="status-info" size="small" />
                                            </span>
                                        </Popover>
                                    </div>
                                    <div style={{ marginBottom: "16px" }}>
                                        {asset?.assetType} /{" "}
                                        {asset?.isDistributable === true ? "Yes" : "No"}
                                    </div>
                                    <div
                                        style={{
                                            fontSize: "16px",
                                            fontWeight: "bold",
                                            marginBottom: "4px",
                                        }}
                                    >
                                        Tags
                                    </div>
                                    <div style={{ marginBottom: "16px" }}>
                                        {Array.isArray(asset?.tags) && asset.tags.length > 0
                                            ? asset.tags
                                                  .map((tag: any) => {
                                                      const tagType = JSON.parse(
                                                          localStorage.getItem("tagTypes") ||
                                                              '{"tagTypeName": "", "tags": []}'
                                                      ).find((type: any) =>
                                                          type.tags.includes(tag)
                                                      );
                                                      if (tagType && tagType.required === "True") {
                                                          tagType.tagTypeName += " [R]";
                                                      }
                                                      return tagType
                                                          ? `${tag} (${tagType.tagTypeName})`
                                                          : tag;
                                                  })
                                                  .join(", ")
                                            : "No tags assigned"}
                                    </div>
                                </div>

                                {/* Row 1, Col 3: Latest Version + Version Selector */}
                                <div>
                                    <div
                                        style={{
                                            fontSize: "16px",
                                            fontWeight: "bold",
                                            marginBottom: "4px",
                                        }}
                                    >
                                        Current Last Version
                                    </div>
                                    <div style={{ marginBottom: "16px" }}>
                                        v{asset?.currentVersion?.Version}
                                        {(() => {
                                            const currentVer = versions.find(
                                                (v: any) =>
                                                    v.Version === asset?.currentVersion?.Version
                                            );
                                            return currentVer?.versionAlias
                                                ? ` (${currentVer.versionAlias})`
                                                : "";
                                        })()}
                                        {asset?.currentVersion?.DateModified && (
                                            <span
                                                style={{
                                                    color: "var(--vams-text-secondary)",
                                                    marginLeft: "4px",
                                                }}
                                            >
                                                [
                                                {new Date(
                                                    asset.currentVersion.DateModified
                                                ).toLocaleString("en-US", {
                                                    year: "numeric",
                                                    month: "numeric",
                                                    day: "numeric",
                                                    hour: "numeric",
                                                    minute: "numeric",
                                                    second: "numeric",
                                                    hour12: true,
                                                })}
                                                ]
                                            </span>
                                        )}
                                    </div>
                                    {/* Version selector */}
                                    {versions.length > 0 && onVersionChange && (
                                        <FormField label="Version Selection">
                                            <Select
                                                selectedOption={
                                                    selectedVersionId
                                                        ? {
                                                              label: `v${selectedVersionId}${
                                                                  versions.find(
                                                                      (v: any) =>
                                                                          v.Version ===
                                                                          selectedVersionId
                                                                  )?.versionAlias
                                                                      ? ` (${
                                                                            versions.find(
                                                                                (v: any) =>
                                                                                    v.Version ===
                                                                                    selectedVersionId
                                                                            )?.versionAlias
                                                                        })`
                                                                      : ""
                                                              }`,
                                                              value: selectedVersionId,
                                                          }
                                                        : {
                                                              label: "LATEST (Non-Versioned)",
                                                              value: "__LATEST__",
                                                          }
                                                }
                                                onChange={({ detail }) => {
                                                    const val = detail.selectedOption.value;
                                                    onVersionChange(
                                                        val === "__LATEST__" ? null : val || null
                                                    );
                                                }}
                                                options={[
                                                    {
                                                        label: "LATEST (Non-Versioned)",
                                                        value: "__LATEST__",
                                                    },
                                                    ...versions.map((v: any) => ({
                                                        label: `v${v.Version}${
                                                            v.versionAlias
                                                                ? ` (${v.versionAlias})`
                                                                : ""
                                                        } - ${
                                                            v.Comment || "No comment"
                                                        } (${new Date(
                                                            v.DateModified
                                                        ).toLocaleDateString()})`,
                                                        value: v.Version,
                                                    })),
                                                ]}
                                                placeholder="Select version"
                                                loadingText="Loading versions..."
                                                statusType={
                                                    versionsLoading ? "loading" : "finished"
                                                }
                                            />
                                        </FormField>
                                    )}
                                </div>
                            </Grid>
                        </div>
                    </div>
                </div>
            </Container>

            {/* Full-size preview modal */}
            {hasPreview && (
                <PreviewModal
                    visible={showPreviewModal}
                    onDismiss={() => setShowPreviewModal(false)}
                    assetId={asset?.assetId || ""}
                    databaseId={databaseId}
                    previewKey={previewKey}
                    preloadedUrl={thumbnailUrl || undefined}
                />
            )}
        </ErrorBoundary>
    );
};

export default AssetDetailsPane;
