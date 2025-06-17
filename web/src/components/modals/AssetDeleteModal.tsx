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
    SegmentedControl,
    Alert,
    Spinner,
    Toggle
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import Synonyms from "../../synonyms";

interface AssetDeleteModalProps {
    visible: boolean;
    onDismiss: () => void;
    onSuccess: (operation: 'archive' | 'delete') => void;
    
    // Mode selection (defaults to 'asset' for backward compatibility)
    mode?: 'asset' | 'file';
    
    // Asset mode props
    selectedAssets?: any[];
    
    // File mode props
    selectedFiles?: any[];
    assetId?: string;
    
    // Common props
    databaseId?: string;
    
    // Force delete mode for archived files
    forceDeleteMode?: boolean;
}

interface AssetDeleteState {
    operation: 'archive' | 'delete';
    reason: string;
    confirmationText: string;
    loading: boolean;
    error: string | null;
    currentItemIndex: number;
    processedCount: number;
}

const AssetDeleteModal: React.FC<AssetDeleteModalProps> = ({
    visible,
    onDismiss,
    onSuccess,
    mode = 'asset',
    selectedAssets = [],
    selectedFiles = [],
    databaseId,
    assetId,
    forceDeleteMode = false
}) => {
    const [state, setState] = useState<AssetDeleteState>({
        operation: forceDeleteMode ? 'delete' : 'archive',
        reason: '',
        confirmationText: '',
        loading: false,
        error: null,
        currentItemIndex: 0,
        processedCount: 0
    });

    // Reset state when modal opens/closes
    useEffect(() => {
        if (visible) {
            setState({
                operation: forceDeleteMode ? 'delete' : 'archive',
                reason: '',
                confirmationText: '',
                loading: false,
                error: null,
                currentItemIndex: 0,
                processedCount: 0
            });
        }
    }, [visible, forceDeleteMode]);

    // Determine if we're dealing with assets or files
    const isAssetMode = mode === 'asset';
    const items = isAssetMode ? selectedAssets : selectedFiles;
    
    // Get display information
    const isMultipleItems = items.length > 1;
    const itemName = isMultipleItems 
        ? `${items.length} ${isAssetMode ? Synonyms.Assets : 'Files'}` 
        : isAssetMode 
            ? (items[0]?.assetName || items[0]?.str_assetname)
            : (items[0]?.name || items[0]?.displayName || 'file');

    const handleOperationChange = (operation: 'archive' | 'delete') => {
        setState(prev => ({ ...prev, operation }));
    };

    const handleReasonChange = (value: string) => {
        setState(prev => ({ ...prev, reason: value }));
    };

    const handleConfirmationTextChange = (value: string) => {
        setState(prev => ({ ...prev, confirmationText: value }));
    };

    const validateForm = (): boolean => {
        // For asset archive, reason is required
        if (isAssetMode && state.operation === 'archive' && !state.reason.trim()) {
            setState(prev => ({ ...prev, error: "Please provide a reason for archiving." }));
            return false;
        }

        // For both asset and file delete, confirmation is required
        if (state.operation === 'delete' && state.confirmationText !== 'delete') {
            setState(prev => ({ 
                ...prev, 
                error: "Please type 'delete' to confirm permanent deletion." 
            }));
            return false;
        }

        return true;
    };

    // Check if the form is valid for enabling/disabling the submit button
    const isFormValid = (): boolean => {
        if (state.operation === 'archive') {
            // For asset archive, reason is required; for file archive, no reason needed
            return !isAssetMode || state.reason.trim().length > 0;
        }
        
        if (state.operation === 'delete') {
            return state.confirmationText === 'delete';
        }
        
        return false;
    };

    const processAsset = async (asset: any) => {
        try {
            // Get the database ID either from props or from the asset itself
            const dbId = databaseId || asset.databaseId || asset.str_databaseid;
            const assetId = asset.assetId || asset.str_assetid;
            
            if (!dbId || !assetId) {
                throw new Error("Missing database ID or asset ID");
            }

            let endpoint = '';
            let body = {};

            if (state.operation === 'archive') {
                endpoint = `database/${dbId}/assets/${assetId}/archiveAsset`;
                body = {
                    confirmArchive: true,
                    reason: state.reason
                };
            } else {
                endpoint = `database/${dbId}/assets/${assetId}/deleteAsset`;
                body = {
                    confirmPermanentDelete: true
                };
            }

            const response = await API.del("api", endpoint, {
                body: body
            });

            return response;
        } catch (error) {
            console.error("Error processing asset:", error);
            throw error;
        }
    };

    const processFile = async (file: any) => {
        try {
            // For files, we need both databaseId and assetId
            if (!databaseId || !assetId) {
                throw new Error("Missing database ID or asset ID for file operation");
            }

            // Get the file path
            const filePath = file.relativePath;
            const isFolder = file.isFolder || file.keyPrefix?.endsWith('/') || false;
            
            let endpoint = '';
            let body = {};

            if (state.operation === 'archive') {
                endpoint = `database/${databaseId}/assets/${assetId}/archiveFile`;
                body = {
                    filePath: filePath,
                    isPrefix: isFolder
                };
            } else {
                endpoint = `database/${databaseId}/assets/${assetId}/deleteFile`;
                body = {
                    filePath: filePath,
                    isPrefix: isFolder,
                    confirmPermanentDelete: true
                };
            }

            const response = await API.del("api", endpoint, {
                body: body
            });

            return response;
        } catch (error) {
            console.error("Error processing file:", error);
            throw error;
        }
    };

    const handleSubmit = async () => {
        if (!validateForm()) {
            return;
        }

        setState(prev => ({ ...prev, loading: true, error: null }));

        try {
            // Process each item sequentially
            for (let i = 0; i < items.length; i++) {
                setState(prev => ({ 
                    ...prev, 
                    currentItemIndex: i,
                    processedCount: i
                }));
                
                // Process asset or file based on mode
                if (isAssetMode) {
                    await processAsset(items[i]);
                } else {
                    await processFile(items[i]);
                }
            }

            // All items processed successfully
            setState(prev => ({ 
                ...prev, 
                loading: false,
                processedCount: items.length
            }));
            
            // Call the success callback
            onSuccess(state.operation);
        } catch (error: any) {
            console.error(`Error processing ${isAssetMode ? 'assets' : 'files'}:`, error);
            setState(prev => ({ 
                ...prev, 
                loading: false, 
                error: error.message || `An error occurred while processing the ${isAssetMode ? 'assets' : 'files'}.`
            }));
        }
    };

    const getModalTitle = () => {
        const itemType = isAssetMode 
            ? (isMultipleItems ? Synonyms.Assets : Synonyms.Asset)
            : (isMultipleItems ? 'Files' : 'File');
            
        if (state.operation === 'archive') {
            return `Archive ${itemType}`;
        } else {
            return `Permanently Delete ${itemType}`;
        }
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={getModalTitle()}
            size="medium"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            variant="link"
                            onClick={onDismiss}
                            disabled={state.loading}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleSubmit}
                            disabled={state.loading || !isFormValid()}
                        >
                            {state.loading ? (
                                <SpaceBetween direction="horizontal" size="xs">
                                    <Spinner />
                                    {`Processing ${state.processedCount + 1}/${items.length}`}
                                </SpaceBetween>
                            ) : (
                                state.operation === 'archive' ? 'Archive' : 'Delete'
                            )}
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {state.error && (
                    <Alert type="error" dismissible onDismiss={() => setState(prev => ({ ...prev, error: null }))}>
                        {state.error}
                    </Alert>
                )}

                <Box variant="p">
                    {state.operation === 'archive' ? (
                        <>
                            Are you sure you want to archive <b>{itemName}</b>?
                            <br />
                            {isAssetMode ? 
                                'Archived assets will not appear in normal results but can be restored later.' :
                                'Archived files will not appear in normal results but can be restored later.'}
                        </>
                    ) : (
                        <>
                            Are you sure you want to permanently delete <b>{itemName}</b>?
                            <br />
                            <b>Warning:</b> This action cannot be undone. All data will be permanently removed.
                        </>
                    )}
                </Box>

                {!forceDeleteMode && (
                    <Toggle
                        onChange={({ detail }) => 
                            handleOperationChange(detail.checked ? 'delete' : 'archive')
                        }
                        checked={state.operation === 'delete'}
                    >
                        Permanently delete {isAssetMode ? 'asset' : 'file'} instead of archiving. This will remove all versions and sub-versions associated with the entity.
                    </Toggle>
                )}

                {/* Only show reason field for asset archiving */}
                {isAssetMode && state.operation === 'archive' && (
                    <FormField
                        label="Reason for archiving"
                        description="Please provide a reason for archiving this asset."
                        errorText={state.error && !state.reason.trim() ? "Reason is required" : undefined}
                    >
                        <Input
                            value={state.reason}
                            onChange={({ detail }) => handleReasonChange(detail.value)}
                            placeholder="Enter reason for archiving"
                            disabled={state.loading}
                        />
                    </FormField>
                )}

                {state.operation === 'delete' && (
                    <FormField
                        label="Confirmation"
                        description="Type 'delete' to confirm permanent deletion."
                        errorText={
                            state.error && state.confirmationText !== 'delete' 
                                ? "Please type 'delete' to confirm" 
                                : undefined
                        }
                    >
                        <Input
                            value={state.confirmationText}
                            onChange={({ detail }) => handleConfirmationTextChange(detail.value)}
                            placeholder="Type 'delete' to confirm"
                            disabled={state.loading}
                        />
                    </FormField>
                )}
            </SpaceBetween>
        </Modal>
    );
};

export default AssetDeleteModal;
