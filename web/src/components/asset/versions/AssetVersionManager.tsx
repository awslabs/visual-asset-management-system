/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, createContext, useContext, useMemo } from 'react';
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
    Link,
    SegmentedControl,
    Toggle,
    ColumnLayout
} from '@cloudscape-design/components';
import { useParams } from 'react-router';
import { fetchAssetVersions, fetchAssetS3Files } from '../../../services/AssetVersionService';
import { AssetVersionList } from './components/AssetVersionList';
import { FileVersionsList } from './components/FileVersionsList';
import { CreateAssetVersionModal } from './components/CreateAssetVersionModal';
import { RevertVersionModal } from './components/RevertVersionModal';
import { useAssetVersions } from './hooks/useAssetVersions';
import AssetVersionComparison, { EnhancedAssetVersionComparison } from './AssetVersionComparison';

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
    setPageSize: (size: number) => void;
    setSelectedVersion: (version: AssetVersion | null) => void;
    refreshVersions: () => void;
    compareMode: boolean;
    setCompareMode: (mode: boolean) => void;
    versionToCompare: AssetVersion | null;
    setVersionToCompare: (version: AssetVersion | null) => void;
    compareWithCurrent?: boolean;
    setCompareWithCurrent?: (compare: boolean) => void;
    currentFiles?: any[];
    handleCompareWithCurrent?: () => void;
    // New properties for enhanced functionality
    showArchivedFiles: boolean;
    setShowArchivedFiles: (show: boolean) => void;
    showMismatchedOnly: boolean;
    setShowMismatchedOnly: (show: boolean) => void;
    versionsForComparison: AssetVersion[];
    setVersionsForComparison: (versions: AssetVersion[]) => void;
    handleVersionSelectionForComparison: (version: AssetVersion, forceWithCurrent?: boolean) => void;
    startComparison: () => void;
    startComparisonWithCurrent: () => void;
    clearComparisonSelections: () => void;
    // Comparison panel properties
    showComparisonOptions: boolean;
    comparisonType: 'two-versions' | 'with-current';
    // Filtering properties
    filterText: string;
    setFilterText: (text: string) => void;
    // File versions pagination and search properties
    fileFilterText: string;
    setFileFilterText: (text: string) => void;
    fileCurrentPage: number;
    setFileCurrentPage: (page: number) => void;
    filePageSize: number;
    setFilePageSize: (size: number) => void;
    filteredFiles: FileVersion[];
    totalFiles: number;
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
    const [compareWithCurrent, setCompareWithCurrent] = useState(false);
    const [currentFiles, setCurrentFiles] = useState<any[]>([]);
    
    // New state for enhanced functionality
    const [showArchivedFiles, setShowArchivedFiles] = useState(false);
    const [showMismatchedOnly, setShowMismatchedOnly] = useState(false);
    const [versionsForComparison, setVersionsForComparison] = useState<AssetVersion[]>([]);
    const [comparisonError, setComparisonError] = useState<string | null>(null);
    const [showComparisonOptions, setShowComparisonOptions] = useState(false);
    const [comparisonType, setComparisonType] = useState<'two-versions' | 'with-current'>('two-versions');
    
    // State for file versions pagination and search
    const [fileFilterText, setFileFilterText] = useState<string>('');
    const [fileCurrentPage, setFileCurrentPage] = useState<number>(1);
    const [filePageSize, setFilePageSize] = useState<number>(10);
    
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
        setPageSize,
        setSelectedVersion,
        refreshVersions,
        loadVersionDetails,
        filterText,
        setFilterText
    } = useAssetVersions(databaseId!, assetId!);
    
    // Debug effect to track re-renders
    useEffect(() => {
        console.log('AssetVersionManager - Component re-rendered');
    }, []);
    
    // Debug effect to track selectedVersion changes
    useEffect(() => {
        console.log('AssetVersionManager - selectedVersion changed:', selectedVersion?.Version);
    }, [selectedVersion]);
    
    // Debug effect to track versionsForComparison changes
    useEffect(() => {
        console.log('AssetVersionManager - versionsForComparison changed:', versionsForComparison);
    }, [versionsForComparison]);
    
    // Debug effect to track comparisonType changes
    useEffect(() => {
        console.log('AssetVersionManager - comparisonType changed:', comparisonType);
    }, [comparisonType]);
    
    // Handle revert version
    const handleRevertVersion = (version: AssetVersion) => {
        setVersionToRevert(version);
        setShowRevertModal(true);
    };
    
    // Handle version selection for comparison
    const handleCompareSelect = (version: AssetVersion) => {
        console.log('AssetVersionManager - handleCompareSelect called with version:', version);
        
        if (compareMode) {
            if (versionToCompare) {
                // If we already have a version to compare, compare with this one
                console.log('AssetVersionManager - Already have versionToCompare, setting selectedVersion');
                setSelectedVersion(version);
                setCompareWithCurrent(false);
            } else {
                // Select this version for comparison
                console.log('AssetVersionManager - Setting versionToCompare');
                setVersionToCompare(version);
            }
        } else {
            // Normal selection - ensure we're not resetting the selection if it's the same version
            console.log('AssetVersionManager - Normal selection, setting selectedVersion');
            
            // Store the version in a local variable to ensure it's not lost during state updates
            const versionToSelect = version;
            
            // Use a callback to ensure we have the latest state
            setSelectedVersion((currentSelectedVersion) => {
                // If it's the same version, keep the reference to avoid unnecessary re-renders
                if (currentSelectedVersion && currentSelectedVersion.Version === versionToSelect.Version) {
                    return currentSelectedVersion;
                }
                return versionToSelect;
            });
            
            // If the comparison options panel is open, also update versionsForComparison
            if (showComparisonOptions) {
                handleVersionSelectionForComparison(version);
            }
        }
    };
    
    // Toggle comparison options panel
    const toggleComparisonOptions = () => {
        console.log('toggleComparisonOptions called');
        console.log('Current versionsForComparison:', versionsForComparison);
        console.log('Current comparisonType:', comparisonType);
        console.log('showComparisonOptions:', showComparisonOptions);
        
        // If we already have versions selected for comparison and the panel is not showing,
        // start the comparison directly instead of showing the panel
        if (!showComparisonOptions && versionsForComparison.length > 0) {
            console.log('We have versions selected and panel is not showing');
            
            // For two-versions mode, we need exactly 2 versions
            if (comparisonType === 'two-versions' && versionsForComparison.length === 2) {
                console.log('Starting comparison for two-versions mode');
                startComparison();
                return;
            }
            // For with-current mode, we need at least 1 version
            else if (comparisonType === 'with-current' && versionsForComparison.length > 0) {
                console.log('Starting comparison for with-current mode');
                startComparison();
                return;
            }
        }
        
        // Otherwise, toggle the panel as usual
        console.log('Toggling comparison options panel');
        setShowComparisonOptions(!showComparisonOptions);
        if (!showComparisonOptions) {
            // Only clear error message when opening panel
            setComparisonError(null);
            
            // When opening the panel, if we have a selected version but no versions for comparison,
            // add the selected version to versionsForComparison
            if (selectedVersion && versionsForComparison.length === 0) {
                console.log('Adding selected version to versionsForComparison when opening panel');
                setVersionsForComparison([selectedVersion]);
            }
        }
    };
    
    // Enhanced version selection for comparison
    const handleVersionSelectionForComparison = (version: AssetVersion, forceWithCurrent = false) => {
        console.log('handleVersionSelectionForComparison called with version:', version);
        console.log('forceWithCurrent:', forceWithCurrent);
        console.log('Current versionsForComparison:', versionsForComparison);
        console.log('Current comparisonType:', comparisonType);
        
        // If forceWithCurrent is true, set the comparison type to 'with-current'
        if (forceWithCurrent && comparisonType !== 'with-current') {
            console.log('Forcing comparison type to with-current');
            setComparisonType('with-current');
        }
        
        // Check if version is already selected
        const alreadySelected = versionsForComparison.some(v => v.Version === version.Version);
        console.log('Is version already selected?', alreadySelected);
        
        if (alreadySelected) {
            // Remove from selection
            console.log('Removing version from selection');
            const newVersions = versionsForComparison.filter(v => v.Version !== version.Version);
            console.log('New versions after removal:', newVersions);
            setVersionsForComparison(newVersions);
        } else if (forceWithCurrent || comparisonType === 'with-current') {
            // For comparison with current, only allow one selection
            console.log('Setting version for comparison with current');
            setVersionsForComparison([version]);
            
            // If forceWithCurrent is true, start the comparison immediately
            if (forceWithCurrent) {
                console.log('Force with current - starting comparison immediately');
                setTimeout(() => {
                    setVersionToCompare(version);
                    setCompareMode(true);
                    setCompareWithCurrent(true);
                    handleCompareWithCurrent();
                }, 100);
            }
        } else if (versionsForComparison.length < 2) {
            // Add to selection (max 2)
            console.log('Adding version to selection (current count < 2)');
            const newVersions = [...versionsForComparison, version];
            console.log('New versions after addition:', newVersions);
            setVersionsForComparison(newVersions);
            
            // If we now have exactly 2 versions selected, log this for debugging
            if (newVersions.length === 2) {
                console.log('Now have exactly 2 versions selected:', newVersions);
            }
        } else {
            // Replace the second version
            console.log('Replacing second version in selection');
            const newVersions = [versionsForComparison[0], version];
            console.log('New versions after replacement:', newVersions);
            setVersionsForComparison(newVersions);
        }
        
        // Also update the selectedVersion to ensure it's reflected in the UI
        if (!alreadySelected) {
            setSelectedVersion(version);
        }
    };
    
    // Start comparison between two selected versions
    const startComparison = () => {
        console.log('startComparison called');
        console.log('Current versionsForComparison:', versionsForComparison);
        console.log('Current comparisonType:', comparisonType);
        
        if (comparisonType === 'two-versions') {
            if (versionsForComparison.length === 2) {
                console.log('Starting two-versions comparison with versions:', versionsForComparison);
                setVersionToCompare(versionsForComparison[0]);
                setSelectedVersion(versionsForComparison[1]);
                setCompareMode(true);
                setCompareWithCurrent(false);
                setShowComparisonOptions(false);
            } else {
                console.log('Cannot start two-versions comparison, need exactly 2 versions');
                setComparisonError('Please select two versions to compare');
                setTimeout(() => setComparisonError(null), 5000); // Auto-dismiss after 5 seconds
            }
        } else {
            if (versionsForComparison.length > 0) {
                console.log('Starting with-current comparison with version:', versionsForComparison[0]);
                setVersionToCompare(versionsForComparison[0]);
                setCompareMode(true);
                setCompareWithCurrent(true);
                handleCompareWithCurrent();
                setShowComparisonOptions(false);
            } else {
                console.log('Cannot start with-current comparison, need at least 1 version');
                setComparisonError('Please select a version to compare with current files');
                setTimeout(() => setComparisonError(null), 5000); // Auto-dismiss after 5 seconds
            }
        }
    };
    
    // Start comparison with current files
    const startComparisonWithCurrent = () => {
        if (versionsForComparison.length > 0) {
            setVersionToCompare(versionsForComparison[0]);
            setCompareMode(true);
            setCompareWithCurrent(true);
            handleCompareWithCurrent();
            setShowComparisonOptions(false);
        } else {
            setComparisonError('Please select a version to compare with current files');
            setTimeout(() => setComparisonError(null), 5000); // Auto-dismiss after 5 seconds
        }
    };
    
    // Clear comparison selections
    const clearComparisonSelections = () => {
        setVersionsForComparison([]);
    };
    
    // Handle compare with current files
    const handleCompareWithCurrent = async () => {
        if (!versionToCompare || !databaseId || !assetId) return;
        
        try {
            // Load current files
            const [success, files] = await fetchAssetS3Files({
                databaseId,
                assetId,
                includeArchived: false
            });
            
            if (success && files) {
                setCurrentFiles(files);
                setCompareWithCurrent(true);
            } else {
                console.error('Failed to load current files for comparison');
            }
        } catch (error) {
            console.error('Error loading current files:', error);
        }
    };
    
    // Toggle comparison mode
    const toggleCompareMode = () => {
        setCompareMode(!compareMode);
        if (!compareMode) {
            // Entering compare mode
            setVersionToCompare(null);
            setCompareWithCurrent(false);
        } else {
            // Exiting compare mode
            setVersionToCompare(null);
            setCompareWithCurrent(false);
            // Reset versions for comparison when exiting compare mode
            setVersionsForComparison([]);
        }
    };
    
    // Compute filtered files
    const filteredFiles = useMemo(() => {
        if (!selectedVersionDetails?.files) return [];
        
        const files = selectedVersionDetails.files;
        
        if (fileFilterText.trim() === '') {
            return files;
        }
        
        const lowerFilter = fileFilterText.toLowerCase();
        return files.filter(file => 
            file.relativeKey.toLowerCase().includes(lowerFilter) ||
            file.versionId.toLowerCase().includes(lowerFilter) ||
            (file.lastModified && file.lastModified.toLowerCase().includes(lowerFilter))
        );
    }, [selectedVersionDetails, fileFilterText]);

    // Calculate total files and paginated files
    const totalFiles = filteredFiles.length;
    const paginatedFiles = useMemo(() => {
        const startIndex = (fileCurrentPage - 1) * filePageSize;
        return filteredFiles.slice(startIndex, startIndex + filePageSize);
    }, [filteredFiles, fileCurrentPage, filePageSize]);
    
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
        setPageSize,
        setSelectedVersion,
        refreshVersions,
        compareMode,
        setCompareMode,
        versionToCompare,
        setVersionToCompare,
        compareWithCurrent,
        setCompareWithCurrent,
        currentFiles,
        handleCompareWithCurrent,
        // New properties for enhanced functionality
        showArchivedFiles,
        setShowArchivedFiles,
        showMismatchedOnly,
        setShowMismatchedOnly,
        versionsForComparison,
        setVersionsForComparison,
        handleVersionSelectionForComparison,
        startComparison,
        startComparisonWithCurrent,
        clearComparisonSelections,
        // Comparison panel properties
        showComparisonOptions,
        comparisonType,
        // Filtering properties
        filterText,
        setFilterText,
        // File versions pagination and search properties
        fileFilterText,
        setFileFilterText,
        fileCurrentPage,
        setFileCurrentPage,
        filePageSize,
        setFilePageSize,
        filteredFiles: paginatedFiles,
        totalFiles
    };
    
    return (
        <AssetVersionContext.Provider value={contextValue}>
            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                {compareMode && versionToCompare && (
                                    <Button
                                        onClick={handleCompareWithCurrent}
                                        variant={compareWithCurrent ? "primary" : "normal"}
                                    >
                                        Compare with Current Files
                                    </Button>
                                )}
                                {compareMode ? (
                                    <Button
                                        onClick={toggleCompareMode}
                                        variant="primary"
                                    >
                                        Exit Compare Mode
                                    </Button>
                                ) : (
                                    <Button
                                        onClick={toggleComparisonOptions}
                                        variant={showComparisonOptions ? "primary" : "normal"}
                                        iconName="copy"
                                    >
                                        Compare Versions
                                    </Button>
                                )}
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
                    {comparisonError && (
                        <Alert type="error" dismissible onDismiss={() => setComparisonError(null)}>
                            {comparisonError}
                        </Alert>
                    )}
                    
                    {/* Comparison Options Panel */}
                    {showComparisonOptions && !compareMode && (
                        <Container
                            header={<Header variant="h3">Version Comparison Options</Header>}
                        >
                            <SpaceBetween direction="vertical" size="l">
                                <SegmentedControl
                                    selectedId={comparisonType}
                                    onChange={({ detail }) => {
                                        const newType = detail.selectedId as 'two-versions' | 'with-current';
                                        console.log('SegmentedControl onChange - new type:', newType);
                                        // Only reset selections if actually changing the type
                                        if (newType !== comparisonType) {
                                            console.log('SegmentedControl onChange - changing type from', comparisonType, 'to', newType);
                                            setComparisonType(newType);
                                            // Reset selections when changing comparison type
                                            setVersionsForComparison([]);
                                        }
                                    }}
                                    options={[
                                        { text: 'Compare Two Versions', id: 'two-versions' },
                                        { text: 'Compare with Current Files', id: 'with-current' }
                                    ]}
                                />
                                
                                <ColumnLayout columns={2}>
                                    <div>
                                        <SpaceBetween direction="vertical" size="s">
                                            <Box variant="h4">Selected Versions</Box>
                                            {versionsForComparison.length === 0 ? (
                                                <Box>No versions selected. Select versions from the list below.</Box>
                                            ) : (
                                                <SpaceBetween direction="vertical" size="xs">
                                                    {versionsForComparison.map((version, index) => (
                                                        <Box key={version.Version}>
                                                            <div style={{ 
                                                                display: 'flex', 
                                                                alignItems: 'center', 
                                                                gap: '8px',
                                                                padding: '4px 8px',
                                                                backgroundColor: '#f2f8fd',
                                                                borderRadius: '4px',
                                                                border: '1px solid #d1e4f8'
                                                            }}>
                                                                <span style={{ fontWeight: 'bold' }}>
                                                                    {comparisonType === 'two-versions' ? `${index + 1}.` : ''} Version {version.Version}
                                                                </span>
                                                                <span>({new Date(version.DateModified).toLocaleDateString()})</span>
                                                                <Button
                                                                    iconName="close"
                                                                    variant="icon"
                                                                    onClick={() => setVersionsForComparison(
                                                                        versionsForComparison.filter(v => v.Version !== version.Version)
                                                                    )}
                                                                    ariaLabel={`Remove version ${version.Version} from selection`}
                                                                />
                                                            </div>
                                                        </Box>
                                                    ))}
                                                </SpaceBetween>
                                            )}
                                            
                                            {versionsForComparison.length > 0 && (
                                                <Button
                                                    onClick={clearComparisonSelections}
                                                    variant="link"
                                                >
                                                    Clear Selection
                                                </Button>
                                            )}
                                        </SpaceBetween>
                                    </div>
                                    
                                    <div>
                                        <SpaceBetween direction="vertical" size="s">
                                            <Box variant="h4">Instructions</Box>
                                            {comparisonType === 'two-versions' ? (
                                                <Box>
                                                    <p>Select two versions from the list below to compare their files.</p>
                                                    <p>The comparison will show:</p>
                                                    <ul>
                                                        <li><span style={{ color: '#037f0c' }}>Added files</span> - Files present in the second version but not in the first</li>
                                                        <li><span style={{ color: '#d91515' }}>Removed files</span> - Files present in the first version but not in the second</li>
                                                        <li><span style={{ color: '#0972d3' }}>Modified files</span> - Files present in both versions but with different content</li>
                                                        <li><span style={{ color: '#5f6b7a' }}>Unchanged files</span> - Files identical in both versions</li>
                                                    </ul>
                                                </Box>
                                            ) : (
                                                <Box>
                                                    <p>Select one version from the list below to compare with the current files.</p>
                                                    <p>This will show what has changed since the selected version, including:</p>
                                                    <ul>
                                                        <li><span style={{ color: '#037f0c' }}>Added files</span> - New files added since the selected version</li>
                                                        <li><span style={{ color: '#d91515' }}>Removed files</span> - Files that existed in the selected version but are no longer present</li>
                                                        <li><span style={{ color: '#0972d3' }}>Modified files</span> - Files that have been changed since the selected version</li>
                                                        <li><span style={{ color: '#5f6b7a' }}>Unchanged files</span> - Files that remain the same</li>
                                                    </ul>
                                                </Box>
                                            )}
                                            
                                            <Button
                                                onClick={startComparison}
                                                variant="primary"
                                                disabled={
                                                    (comparisonType === 'two-versions' && versionsForComparison.length !== 2) ||
                                                    (comparisonType === 'with-current' && versionsForComparison.length === 0)
                                                }
                                            >
                                                Start Comparison
                                            </Button>
                                        </SpaceBetween>
                                    </div>
                                </ColumnLayout>
                            </SpaceBetween>
                        </Container>
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
                    {compareMode && versionToCompare && (
                        compareWithCurrent ? (
                            <AssetVersionComparison 
                                databaseId={databaseId!}
                                assetId={assetId!}
                                version1={versionToCompare}
                                compareWithCurrent={true}
                                onClose={() => {
                                    setCompareWithCurrent(false);
                                    setCompareMode(false);
                                    setVersionToCompare(null);
                                    // Reset versions for comparison when closing comparison view
                                    setVersionsForComparison([]);
                                }}
                            />
                        ) : selectedVersion && (
                            <EnhancedAssetVersionComparison 
                                onClose={() => {
                                    setCompareMode(false);
                                    setVersionToCompare(null);
                                    // Reset versions for comparison when closing comparison view
                                    setVersionsForComparison([]);
                                }}
                            />
                        )
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
