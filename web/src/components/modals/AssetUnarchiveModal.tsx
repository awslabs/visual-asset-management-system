/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import {
    Modal,
    Box,
    SpaceBetween,
    Button,
    FormField,
    Input,
    Alert,
    Spinner,
    Container,
    ColumnLayout,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import Synonyms from "../../synonyms";

interface AssetUnarchiveModalProps {
    visible: boolean;
    onDismiss: () => void;
    onSuccess: () => void;
    selectedAsset: any;
    databaseId?: string;
}

interface AssetUnarchiveState {
    reason: string;
    loading: boolean;
    error: string | null;
    assetDetails: any | null;
    loadingDetails: boolean;
}

const AssetUnarchiveModal: React.FC<AssetUnarchiveModalProps> = ({
    visible,
    onDismiss,
    onSuccess,
    selectedAsset,
    databaseId,
}) => {
    const [state, setState] = useState<AssetUnarchiveState>({
        reason: "",
        loading: false,
        error: null,
        assetDetails: null,
        loadingDetails: false,
    });

    // Fetch asset details when modal opens
    useEffect(() => {
        if (visible && selectedAsset) {
            fetchAssetDetails();
        }
    }, [visible, selectedAsset]);

    // Reset state when modal closes
    useEffect(() => {
        if (!visible) {
            setState({
                reason: "",
                loading: false,
                error: null,
                assetDetails: null,
                loadingDetails: false,
            });
        }
    }, [visible]);

    const fetchAssetDetails = async () => {
        setState((prev) => ({ ...prev, loadingDetails: true, error: null }));

        try {
            const dbId = databaseId || selectedAsset.databaseId || selectedAsset.str_databaseid;
            const assetId = selectedAsset.assetId || selectedAsset.str_assetid;

            if (!dbId || !assetId) {
                throw new Error("Missing database ID or asset ID");
            }

            // Fetch asset details with showArchived=true to get archived assets
            const endpoint = `database/${dbId}/assets/${assetId}?showArchived=true`;
            const response = await API.get("api", endpoint, {});

            setState((prev) => ({
                ...prev,
                assetDetails: response,
                loadingDetails: false,
            }));
        } catch (error: any) {
            console.error("Error fetching asset details:", error);
            setState((prev) => ({
                ...prev,
                error: error.message || "Failed to fetch asset details",
                loadingDetails: false,
            }));
        }
    };

    const handleReasonChange = (value: string) => {
        setState((prev) => ({ ...prev, reason: value }));
    };

    const validateForm = (): boolean => {
        if (!state.reason.trim()) {
            setState((prev) => ({
                ...prev,
                error: "Please provide a reason for unarchiving.",
            }));
            return false;
        }
        return true;
    };

    const isFormValid = (): boolean => {
        return state.reason.trim().length > 0;
    };

    const handleSubmit = async () => {
        if (!validateForm()) {
            return;
        }

        setState((prev) => ({ ...prev, loading: true, error: null }));

        try {
            const dbId = databaseId || selectedAsset.databaseId || selectedAsset.str_databaseid;
            const assetId = selectedAsset.assetId || selectedAsset.str_assetid;

            if (!dbId || !assetId) {
                throw new Error("Missing database ID or asset ID");
            }

            const endpoint = `database/${dbId}/assets/${assetId}/unarchiveAsset`;
            const body = {
                confirmUnarchive: true,
                reason: state.reason,
            };

            await API.put("api", endpoint, {
                body: body,
            });

            setState((prev) => ({ ...prev, loading: false }));
            onSuccess();
        } catch (error: any) {
            console.error("Error unarchiving asset:", error);
            setState((prev) => ({
                ...prev,
                loading: false,
                error: error.message || "An error occurred while unarchiving the asset.",
            }));
        }
    };

    const assetName = selectedAsset?.assetName || selectedAsset?.str_assetname || "Unknown Asset";

    const formatDate = (dateString: string | undefined) => {
        if (!dateString) return "N/A";
        try {
            return new Date(dateString).toLocaleString();
        } catch {
            return dateString;
        }
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`Unarchive ${Synonyms.Asset}`}
            size="medium"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss} disabled={state.loading}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleSubmit}
                            disabled={state.loading || !isFormValid() || state.loadingDetails}
                        >
                            {state.loading ? (
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Spinner />
                                    Unarchiving...
                                </SpaceBetween>
                            ) : (
                                "Unarchive"
                            )}
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {state.error && (
                    <Alert
                        type="error"
                        dismissible
                        onDismiss={() => setState((prev) => ({ ...prev, error: null }))}
                    >
                        {state.error}
                    </Alert>
                )}

                <Box variant="p">
                    Are you sure you want to unarchive <b>{assetName}</b>?
                    <br />
                    This will restore the asset and make it visible in normal search results.
                </Box>

                {/* Archive Information */}
                {state.loadingDetails ? (
                    <Container>
                        <SpaceBetween direction="horizontal" size="xs">
                            <Spinner />
                            <Box>Loading archive information...</Box>
                        </SpaceBetween>
                    </Container>
                ) : state.assetDetails ? (
                    <Container header={<Box variant="h3">Archive Information</Box>}>
                        <ColumnLayout columns={1} variant="text-grid">
                            <div>
                                <Box variant="awsui-key-label">Archived By</Box>
                                <div>{state.assetDetails.archivedBy || "N/A"}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Archived At</Box>
                                <div>{formatDate(state.assetDetails.archivedAt)}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Archive Reason</Box>
                                <div>{state.assetDetails.archivedReason || "N/A"}</div>
                            </div>
                        </ColumnLayout>
                    </Container>
                ) : null}

                {/* Reason for Unarchiving */}
                <FormField
                    label="Reason for unarchiving"
                    description="Please provide a reason for unarchiving this asset."
                    errorText={
                        state.error && !state.reason.trim() ? "Reason is required" : undefined
                    }
                >
                    <Input
                        value={state.reason}
                        onChange={({ detail }) => handleReasonChange(detail.value)}
                        placeholder="Enter reason for unarchiving"
                        disabled={state.loading}
                    />
                </FormField>
            </SpaceBetween>
        </Modal>
    );
};

export default AssetUnarchiveModal;
