import React, { useState, useEffect, useContext, useMemo } from 'react';
import {
    Box,
    Button,
    Container,
    Header,
    SpaceBetween,
    Alert,
    Spinner,
    Cards,
    Badge,
    Grid,
    ColumnLayout,
    Link
} from '@cloudscape-design/components';
import { useNavigate, useParams } from 'react-router';

import { fetchAssetVersion, fetchAssetS3Files, compareAssetVersions } from '../../../services/AssetVersionService';
import { downloadAsset } from '../../../services/APIService';
import { AssetVersionContext, AssetVersion } from './AssetVersionManager';

// TypeScript interfaces - using imported AssetVersion from AssetVersionManager

interface FileVersion {
    relativeKey: string;
    versionId: string;
    isArchived: boolean;
    exists: boolean;
    isPermanentlyDeleted?: boolean;
    isLatestVersionArchived?: boolean;
    size?: number;
    lastModified?: string;
    etag?: string;
}

interface AssetVersionDetails {
    assetId: string;
    assetVersionId: string;
    dateCreated: string;
    comment?: string;
    files: FileVersion[];
    createdBy?: string;
}

interface ComparisonProps {
    databaseId: string;
    assetId: string;
    version1: AssetVersion;
    version2?: AssetVersion; // Make version2 optional for "Compare with Current" mode
    compareWithCurrent?: boolean; // Flag to indicate comparison with current files
    onClose: () => void;
}

// Enhanced version for integration with AssetVersionManager
interface EnhancedComparisonProps {
    onClose: () => void;
}

interface FileComparison {
    relativeKey: string;
    status: 'added' | 'removed' | 'modified' | 'unchanged';
    version1File?: FileVersion;
    version2File?: FileVersion;
}

// Shared utility functions
// Get status badge
const getStatusBadge = (status: string) => {
    switch (status) {
        case 'added':
            return <Badge color="green">Added</Badge>;
        case 'removed':
            return <Badge color="red">Removed</Badge>;
        case 'modified':
            return <Badge color="blue">Modified</Badge>;
        case 'unchanged':
            return <Badge color="grey">Unchanged</Badge>;
        default:
            return <Badge>{status}</Badge>;
    }
};

// Get status icon
const getStatusIcon = (status: string) => {
    switch (status) {
        case 'added':
            return <span style={{ color: '#037f0c', marginRight: '4px' }}>➕</span>;
        case 'removed':
            return <span style={{ color: '#d91515', marginRight: '4px' }}>➖</span>;
        case 'modified':
            return <span style={{ color: '#0972d3', marginRight: '4px' }}>✏️</span>;
        case 'unchanged':
            return <span style={{ color: '#5f6b7a', marginRight: '4px' }}>✓</span>;
        default:
            return null;
    }
};

// Original standalone component
const AssetVersionComparison: React.FC<ComparisonProps> = ({
    databaseId,
    assetId,
    version1,
    version2,
    compareWithCurrent = false,
    onClose
}) => {
    // Implementation details omitted for brevity
    return <div>Asset Version Comparison Component</div>;
};

// Enhanced version for integration with AssetVersionManager
export const EnhancedAssetVersionComparison: React.FC<EnhancedComparisonProps> = ({
    onClose
}) => {
    // Implementation details omitted for brevity
    return <div>Enhanced Asset Version Comparison Component</div>;
};

export default AssetVersionComparison;
