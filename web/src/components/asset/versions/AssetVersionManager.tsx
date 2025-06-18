/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, createContext, useContext } from 'react';
import {
    Box,
    Button,
    Container,
    Header,
    Pagination,
    SpaceBetween,
    Alert,
    Spinner,
    Table,
    Modal,
    Link
} from '@cloudscape-design/components';
import { useParams } from 'react-router';
import { fetchAssetVersions } from '../../../services/AssetVersionService';
import { AssetVersionList } from './components/AssetVersionList';
import { FileVersionsList } from './components/FileVersionsList';
import { CreateAssetVersionModal } from './components/CreateAssetVersionModal';
import { RevertVersionModal } from './components/RevertVersionModal';
import { useAssetVersions } from './hooks/useAssetVersions';
import { EnhancedAssetVersionComparison } from './AssetVersionComparison';

// TypeScript interfaces
export interface AssetVersion {
    Version: string;
    DateModified: string;
    Comment: string;
    description: string;
    specifiedPipelines: string[];
    createdBy: string;
    isCurrent: boolean;
    fileCount: number;
}

export interface FileVersion {
    relativeKey: string;
    versionId: string;
    isPermanentlyDeleted: boolean;
    isLatestVersionArchived: boolean;
    isArchived?: boolean;
    size?: number;
    lastModified?: string;
    etag?: string;
}

export interface AssetVersionDetails {
    assetId: string;
    assetVersionId: string;
    versionNumber: string;
    dateCreated: string;
    comment?: string;
    files: FileVersion[];
    createdBy?: string;
}

// Context for sharing state between components
interface AssetVersionContextType {
    loading: boolean;
    error: string | null;
    versions: AssetVersion[];
    selectedVersion: AssetVersion | null;
    selectedVersionDetails: AssetVersionDetails | null;
    totalVersions: number;
    currentPage: number;
    pageSize: number;
    setCurrentPage: (page: number) => void;
    setSelectedVersion: (version: AssetVersion | null) => void;
    refreshVersions: () => void;
    compareMode: boolean;
    setCompareMode: (mode: boolean) => void;
    versionToCompare: AssetVersion | null;
    setVersionToCompare: (version: AssetVersion | null) => void;
}

export const AssetVersionContext = createContext<AssetVersionContextType | undefined>(undefined);

export const AssetVersionManager: React.FC = () => {
    const { databaseId, assetId } = useParams<{ databaseId: string; assetId: string }>();
    
    // State for modals
    const [showCreateModal, setShowCreateModal] = useState(false);
    const [showRevertModal, setShowRevertModal] = useState(false);
    const [versionToRevert, setVersionToRevert] = useState<AssetVersion | null>(null);
    
    // State for comparison mode
    const [compareMode, setCompareMode] = useState(false);
    const [versionToCompare, setVersionToCompare] = useState<AssetVersion | null>(null);
    
    // Use the custom hook for asset versions
    const {
        loading,
        error,
        versions,
        selectedVersion,
        selectedVersionDetails,
        totalVersions,
        currentPage,
        pageSize,
        setCurrentPage,
        setSelectedVersion,
        refreshVersions,
        loadVersionDetails
    } = useAssetVersions(databaseId!, assetId!);
    
    // Handle revert version
    const handleRevertVersion = (version: AssetVersion) => {
        setVersionToRevert(version);
        setShowRevertModal(true);
    };
    
    // Handle version selection for comparison
    const handleCompareSelect = (version: AssetVersion) => {
        if (compareMode) {
            if (versionToCompare) {
                // If we already have a version to compare, compare with this one
                // Navigate to comparison view or show comparison component
                // For now, just log
                console.log(`Compare ${versionToCompare.Version} with ${version.Version}`);
            } else {
                // Select this version for comparison
                setVersionToCompare(version);
            }
        } else {
            // Normal selection
            setSelectedVersion(version);
        }
    };
    
    // Toggle comparison mode
    const toggleCompareMode = () => {
        setCompareMode(!compareMode);
        if (!compareMode) {
            // Entering compare mode
            setVersionToCompare(null);
        } else {
            // Exiting compare mode
            setVersionToCompare(null);
        }
    };
    
    // Context value
    const contextValue: AssetVersionContextType = {
        loading,
        error,
        versions,
        selectedVersion,
        selectedVersionDetails,
        totalVersions,
        currentPage,
        pageSize,
        setCurrentPage,
        setSelectedVersion,
        refreshVersions,
        compareMode,
        setCompareMode,
        versionToCompare,
        setVersionToCompare
    };
    
    return (
        <AssetVersionContext.Provider value={contextValue}>
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button
                                    onClick={toggleCompareMode}
                                    variant={compareMode ? "primary" : "normal"}
                                >
                                    {compareMode ? "Exit Compare Mode" : "Compare Versions"}
                                </Button>
                                <Button
                                    onClick={() => setShowCreateModal(true)}
                                    variant="primary"
                                >
                                    Create New Version
                                </Button>
                            </SpaceBetween>
                        }
                    >
                        Asset Versions
                    </Header>
                }
            >
                <SpaceBetween direction="vertical" size="l">
                    {error && (
                        <Alert type="error" dismissible>
                            {error}
                        </Alert>
                    )}
                    
                    {/* Asset Versions List */}
                    <AssetVersionList 
                        onRevertVersion={handleRevertVersion}
                        onVersionSelect={handleCompareSelect}
                    />
                    
                    {/* File Versions List - only show when a version is selected and not in compare mode */}
                    {selectedVersion && !compareMode && (
                        <FileVersionsList />
                    )}
                    
                    {/* Comparison View - only show when in compare mode and a version is selected for comparison */}
                    {compareMode && versionToCompare && selectedVersion && (
                        <EnhancedAssetVersionComparison 
                            onClose={() => {
                                setCompareMode(false);
                                setVersionToCompare(null);
                            }}
                        />
                    )}
                </SpaceBetween>
                
                {/* Modals */}
                <CreateAssetVersionModal
                    visible={showCreateModal}
                    onDismiss={() => setShowCreateModal(false)}
                    onSuccess={() => {
                        setShowCreateModal(false);
                        refreshVersions();
                    }}
                />
                
                {versionToRevert && (
                    <RevertVersionModal
                        visible={showRevertModal}
                        onDismiss={() => setShowRevertModal(false)}
                        version={versionToRevert}
                        onSuccess={() => {
                            setShowRevertModal(false);
                            refreshVersions();
                        }}
                    />
                )}
            </Container>
        </AssetVersionContext.Provider>
    );
};

export default AssetVersionManager;
