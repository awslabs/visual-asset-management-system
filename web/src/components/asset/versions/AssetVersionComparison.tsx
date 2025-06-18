import React, { useState, useEffect, useContext } from 'react';
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

import { fetchAssetVersion, fetchAssetS3Files } from '../../../services/AssetVersionService';
import { downloadAsset } from '../../../services/APIService';
import { AssetVersionContext, AssetVersion } from './AssetVersionManager';

// TypeScript interfaces - using imported AssetVersion from AssetVersionManager

interface FileVersion {
    relativeKey: string;
    versionId: string;
    isArchived: boolean;
    exists: boolean;
    size?: number;
    lastModified?: string;
    etag?: string;
}

interface AssetVersionDetails {
    assetId: string;
    assetVersionId: string;
    versionNumber: string;
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

// Original standalone component
const AssetVersionComparison: React.FC<ComparisonProps> = ({
    databaseId,
    assetId,
    version1,
    version2,
    compareWithCurrent = false,
    onClose
}) => {
    const navigate = useNavigate();
    
    // State management
    const [version1Details, setVersion1Details] = useState<AssetVersionDetails | null>(null);
    const [version2Details, setVersion2Details] = useState<AssetVersionDetails | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [fileComparisons, setFileComparisons] = useState<FileComparison[]>([]);

    // Load version details
    useEffect(() => {
        const loadVersionDetails = async () => {
            setLoading(true);
            setError(null);

            try {
                // Always load version1 details
                const [success1, response1] = await fetchAssetVersion({
                    databaseId,
                    assetId,
                    assetVersionId: `v${version1.Version}`
                });

                if (!success1 || !response1) {
                    setError('Failed to load version details for comparison');
                    setLoading(false);
                    return;
                }

                setVersion1Details(response1);

                // If comparing with another version (not current)
                if (version2) {
                    const [success2, response2] = await fetchAssetVersion({
                        databaseId,
                        assetId,
                        assetVersionId: `v${version2.Version}`
                    });

                    if (success2 && response2) {
                        setVersion2Details(response2);
                        
                        // Generate file comparisons between two versions
                        const comparisons = generateFileComparisons(response1.files, response2.files);
                        setFileComparisons(comparisons);
                    } else {
                        setError('Failed to load second version details for comparison');
                    }
                } else if (compareWithCurrent) {
                    // If comparing with current files, load current files
                    const [successCurrent, currentFiles] = await fetchAssetS3Files({
                        databaseId,
                        assetId,
                        includeArchived: false
                    });

                    if (successCurrent && currentFiles) {
                        // Convert S3Files to FileVersion format for comparison
                        const currentFileVersions: FileVersion[] = currentFiles.map((file: any) => ({
                            relativeKey: file.relativePath,
                            versionId: file.versionId,
                            isArchived: file.isArchived,
                            exists: true,
                            size: file.size,
                            lastModified: file.dateCreatedCurrentVersion
                        }));

                        // Generate comparisons between version1 and current files
                        const comparisons = generateFileComparisons(response1.files, currentFileVersions);
                        setFileComparisons(comparisons);
                    } else {
                        setError('Failed to load current files for comparison');
                    }
                }
            } catch (err) {
                setError('An error occurred while loading version details');
                console.error('Error loading version details:', err);
            } finally {
                setLoading(false);
            }
        };

        loadVersionDetails();
    }, [databaseId, assetId, version1.Version, version2?.Version, compareWithCurrent]);

    // Generate file comparisons
    const generateFileComparisons = (files1: FileVersion[], files2: FileVersion[]): FileComparison[] => {
        const comparisons: FileComparison[] = [];
        const files1Map = new Map(files1.map(f => [f.relativeKey, f]));
        const files2Map = new Map(files2.map(f => [f.relativeKey, f]));
        
        // Get all unique file keys
        const allKeys = Array.from(new Set([...Array.from(files1Map.keys()), ...Array.from(files2Map.keys())]));
        
        for (const key of allKeys) {
            const file1 = files1Map.get(key);
            const file2 = files2Map.get(key);
            
            let status: 'added' | 'removed' | 'modified' | 'unchanged';
            
            if (!file1 && file2) {
                status = 'added';
            } else if (file1 && !file2) {
                status = 'removed';
            } else if (file1 && file2) {
                // Check if files are different
                if (file1.versionId !== file2.versionId || 
                    file1.size !== file2.size || 
                    file1.etag !== file2.etag) {
                    status = 'modified';
                } else {
                    status = 'unchanged';
                }
            } else {
                status = 'unchanged'; // This shouldn't happen
            }
            
            comparisons.push({
                relativeKey: key,
                status,
                version1File: file1,
                version2File: file2
            });
        }
        
        // Sort by status priority and then by file name
        const statusPriority = { 'added': 1, 'removed': 2, 'modified': 3, 'unchanged': 4 };
        comparisons.sort((a, b) => {
            const priorityDiff = statusPriority[a.status] - statusPriority[b.status];
            if (priorityDiff !== 0) return priorityDiff;
            return a.relativeKey.localeCompare(b.relativeKey);
        });
        
        return comparisons;
    };

    // Handle file view
    const handleViewFile = (file: FileVersion) => {
        navigate(`/databases/${databaseId}/assets/${assetId}/file`, {
            state: {
                filename: file.relativeKey.split('/').pop() || file.relativeKey,
                key: file.relativeKey,
                isDirectory: false,
                versionId: file.versionId,
                size: file.size,
                dateCreatedCurrentVersion: file.lastModified,
                isArchived: file.isArchived
            }
        });
    };

    // Handle file download
    const handleDownloadFile = async (file: FileVersion) => {
        try {
            const response = await downloadAsset({
                databaseId,
                assetId,
                key: file.relativeKey,
                versionId: file.versionId,
                downloadType: "assetFile"
            });

            if (response !== false && Array.isArray(response) && response[0] !== false) {
                const link = document.createElement('a');
                link.href = response[1];
                link.click();
            } else {
                setError('Failed to download file');
            }
        } catch (err) {
            setError('An error occurred while downloading the file');
            console.error('Error downloading file:', err);
        }
    };

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

    // Get summary statistics
    const getSummaryStats = () => {
        const stats = {
            added: fileComparisons.filter(f => f.status === 'added').length,
            removed: fileComparisons.filter(f => f.status === 'removed').length,
            modified: fileComparisons.filter(f => f.status === 'modified').length,
            unchanged: fileComparisons.filter(f => f.status === 'unchanged').length
        };
        return stats;
    };

    if (loading) {
        return (
            <Container>
                <Box textAlign="center" padding="xl">
                    <Spinner size="large" />
                    <div>Loading version comparison...</div>
                </Box>
            </Container>
        );
    }

    if (error) {
        return (
            <Container>
                <Alert type="error" dismissible onDismiss={() => setError(null)}>
                    {error}
                </Alert>
            </Container>
        );
    }

    const stats = getSummaryStats();

    return (
        <Container
            header={
                <Header
                    variant="h2"
                    actions={
                        <Button onClick={onClose}>
                            Close Comparison
                        </Button>
                    }
                >
                    {version2 
                        ? `Compare Versions: v${version1.Version} vs v${version2.Version}`
                        : `Compare Version v${version1.Version} with Current Files`
                    }
                </Header>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {/* Version Information */}
                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                    <Container header={<Header variant="h3">Version {version1.Version}</Header>}>
                        <ColumnLayout columns={1} variant="text-grid">
                            <div>
                                <Box variant="awsui-key-label">Date Created</Box>
                                <div>{new Date(version1.DateModified).toLocaleString()}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Created By</Box>
                                <div>{version1.createdBy || 'System'}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Comment</Box>
                                <div>{version1.Comment || '-'}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Files Count</Box>
                                <div>{version1Details?.files.length || 0}</div>
                            </div>
                        </ColumnLayout>
                    </Container>
                    
                    {version2 ? (
                        <Container header={<Header variant="h3">Version {version2.Version}</Header>}>
                            <ColumnLayout columns={1} variant="text-grid">
                                <div>
                                    <Box variant="awsui-key-label">Date Created</Box>
                                    <div>{new Date(version2.DateModified).toLocaleString()}</div>
                                </div>
                                <div>
                                    <Box variant="awsui-key-label">Created By</Box>
                                    <div>{version2.createdBy || 'System'}</div>
                                </div>
                                <div>
                                    <Box variant="awsui-key-label">Comment</Box>
                                    <div>{version2.Comment || '-'}</div>
                                </div>
                                <div>
                                    <Box variant="awsui-key-label">Files Count</Box>
                                    <div>{version2Details?.files.length || 0}</div>
                                </div>
                            </ColumnLayout>
                        </Container>
                    ) : (
                        <Container header={<Header variant="h3">Current Files</Header>}>
                            <ColumnLayout columns={1} variant="text-grid">
                                <div>
                                    <Box variant="awsui-key-label">Status</Box>
                                    <div>Latest files in the asset</div>
                                </div>
                                <div>
                                    <Box variant="awsui-key-label">Files Count</Box>
                                    <div>{fileComparisons.filter(f => f.version2File).length}</div>
                                </div>
                            </ColumnLayout>
                        </Container>
                    )}
                </Grid>

                {/* Summary Statistics */}
                <Container header={<Header variant="h3">Comparison Summary</Header>}>
                    <ColumnLayout columns={4} variant="text-grid">
                        <div>
                            <Box variant="awsui-key-label">Added Files</Box>
                            <div style={{ color: '#037f0c' }}>{stats.added}</div>
                        </div>
                        <div>
                            <Box variant="awsui-key-label">Removed Files</Box>
                            <div style={{ color: '#d91515' }}>{stats.removed}</div>
                        </div>
                        <div>
                            <Box variant="awsui-key-label">Modified Files</Box>
                            <div style={{ color: '#0972d3' }}>{stats.modified}</div>
                        </div>
                        <div>
                            <Box variant="awsui-key-label">Unchanged Files</Box>
                            <div style={{ color: '#5f6b7a' }}>{stats.unchanged}</div>
                        </div>
                    </ColumnLayout>
                </Container>

                {/* File Comparison */}
                <Container header={<Header variant="h3">File Comparison</Header>}>
                    {fileComparisons.length === 0 ? (
                        <Box textAlign="center" padding="l">
                            No files to compare.
                        </Box>
                    ) : (
                        <Cards
                            cardDefinition={{
                                header: (item: FileComparison) => (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        <strong>{item.relativeKey.split('/').pop() || item.relativeKey}</strong>
                                        {getStatusBadge(item.status)}
                                    </div>
                                ),
                                sections: [
                                    {
                                        id: 'path',
                                        header: 'File Path',
                                        content: (item: FileComparison) => item.relativeKey
                                    },
                                    {
                                        id: 'version1',
                                        header: `Version ${version1.Version}`,
                                        content: (item: FileComparison) => {
                                            if (!item.version1File) {
                                                return <span style={{ color: '#d91515' }}>File not present</span>;
                                            }
                                            return (
                                                <div>
                                                    <div>Version ID: {item.version1File.versionId}</div>
                                                    <div>Size: {item.version1File.size ? `${(item.version1File.size / 1024 / 1024).toFixed(2)} MB` : 'Unknown'}</div>
                                                    {item.version1File.exists && (
                                                        <SpaceBetween direction="horizontal" size="xs">
                                                            <Link onFollow={() => handleViewFile(item.version1File!)}>
                                                                View
                                                            </Link>
                                                            <Link onFollow={() => handleDownloadFile(item.version1File!)}>
                                                                Download
                                                            </Link>
                                                        </SpaceBetween>
                                                    )}
                                                </div>
                                            );
                                        }
                                    },
                                    {
                                        id: 'version2',
                                        header: version2 ? `Version ${version2.Version}` : 'Current Files',
                                        content: (item: FileComparison) => {
                                            if (!item.version2File) {
                                                return <span style={{ color: '#d91515' }}>File not present</span>;
                                            }
                                            return (
                                                <div>
                                                    <div>Version ID: {item.version2File.versionId}</div>
                                                    <div>Size: {item.version2File.size ? `${(item.version2File.size / 1024 / 1024).toFixed(2)} MB` : 'Unknown'}</div>
                                                    {item.version2File.exists && (
                                                        <SpaceBetween direction="horizontal" size="xs">
                                                            <Link onFollow={() => handleViewFile(item.version2File!)}>
                                                                View
                                                            </Link>
                                                            <Link onFollow={() => handleDownloadFile(item.version2File!)}>
                                                                Download
                                                            </Link>
                                                        </SpaceBetween>
                                                    )}
                                                </div>
                                            );
                                        }
                                    }
                                ]
                            }}
                            items={fileComparisons}
                            loading={false}
                            empty={
                                <Box textAlign="center" color="inherit">
                                    No file differences found.
                                </Box>
                            }
                        />
                    )}
                </Container>
            </SpaceBetween>
        </Container>
    );
};

// Enhanced version for integration with AssetVersionManager
export const EnhancedAssetVersionComparison: React.FC<EnhancedComparisonProps> = ({
    onClose
}) => {
    const { databaseId, assetId } = useParams<{ databaseId: string; assetId: string }>();
    const navigate = useNavigate();
    
    // Get context values
    const context = useContext(AssetVersionContext);
    
    if (!context) {
        throw new Error('EnhancedAssetVersionComparison must be used within an AssetVersionContext.Provider');
    }
    
    const {
        selectedVersion,
        versionToCompare,
        compareMode
    } = context;
    
    // State management
    const [version1Details, setVersion1Details] = useState<AssetVersionDetails | null>(null);
    const [version2Details, setVersion2Details] = useState<AssetVersionDetails | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [fileComparisons, setFileComparisons] = useState<FileComparison[]>([]);
    
    // Load version details
    useEffect(() => {
        const loadVersionDetails = async () => {
            if (!selectedVersion || !versionToCompare || !compareMode) {
                return;
            }
            
            setLoading(true);
            setError(null);

            try {
                // Load version1 details (versionToCompare)
                const [success1, response1] = await fetchAssetVersion({
                    databaseId: databaseId!,
                    assetId: assetId!,
                    assetVersionId: `v${versionToCompare.Version}`
                });

                if (!success1 || !response1) {
                    setError('Failed to load version details for comparison');
                    setLoading(false);
                    return;
                }

                setVersion1Details(response1);

                // Load version2 details (selectedVersion)
                const [success2, response2] = await fetchAssetVersion({
                    databaseId: databaseId!,
                    assetId: assetId!,
                    assetVersionId: `v${selectedVersion.Version}`
                });

                if (success2 && response2) {
                    setVersion2Details(response2);
                    
                    // Generate file comparisons between two versions
                    const comparisons = generateFileComparisons(response1.files, response2.files);
                    setFileComparisons(comparisons);
                } else {
                    setError('Failed to load second version details for comparison');
                }
            } catch (err) {
                setError('An error occurred while loading version details');
                console.error('Error loading version details:', err);
            } finally {
                setLoading(false);
            }
        };

        loadVersionDetails();
    }, [databaseId, assetId, selectedVersion, versionToCompare, compareMode]);
    
    // Generate file comparisons
    const generateFileComparisons = (files1: FileVersion[], files2: FileVersion[]): FileComparison[] => {
        const comparisons: FileComparison[] = [];
        const files1Map = new Map(files1.map(f => [f.relativeKey, f]));
        const files2Map = new Map(files2.map(f => [f.relativeKey, f]));
        
        // Get all unique file keys
        const allKeys = Array.from(new Set([...Array.from(files1Map.keys()), ...Array.from(files2Map.keys())]));
        
        for (const key of allKeys) {
            const file1 = files1Map.get(key);
            const file2 = files2Map.get(key);
            
            let status: 'added' | 'removed' | 'modified' | 'unchanged';
            
            if (!file1 && file2) {
                status = 'added';
            } else if (file1 && !file2) {
                status = 'removed';
            } else if (file1 && file2) {
                // Check if files are different
                if (file1.versionId !== file2.versionId || 
                    file1.size !== file2.size || 
                    file1.etag !== file2.etag) {
                    status = 'modified';
                } else {
                    status = 'unchanged';
                }
            } else {
                status = 'unchanged'; // This shouldn't happen
            }
            
            comparisons.push({
                relativeKey: key,
                status,
                version1File: file1,
                version2File: file2
            });
        }
        
        // Sort by status priority and then by file name
        const statusPriority = { 'added': 1, 'removed': 2, 'modified': 3, 'unchanged': 4 };
        comparisons.sort((a, b) => {
            const priorityDiff = statusPriority[a.status] - statusPriority[b.status];
            if (priorityDiff !== 0) return priorityDiff;
            return a.relativeKey.localeCompare(b.relativeKey);
        });
        
        return comparisons;
    };
    
    // Handle file view
    const handleViewFile = (file: FileVersion) => {
        navigate(`/databases/${databaseId}/assets/${assetId}/file`, {
            state: {
                filename: file.relativeKey.split('/').pop() || file.relativeKey,
                key: file.relativeKey,
                isDirectory: false,
                versionId: file.versionId,
                size: file.size,
                dateCreatedCurrentVersion: file.lastModified,
                isArchived: file.isArchived
            }
        });
    };

    // Handle file download
    const handleDownloadFile = async (file: FileVersion) => {
        try {
            const response = await downloadAsset({
                assetId: assetId!,
                databaseId: databaseId!,
                key: file.relativeKey,
                versionId: file.versionId,
                downloadType: "assetFile"
            });

            if (response !== false && Array.isArray(response) && response[0] !== false) {
                const link = document.createElement('a');
                link.href = response[1];
                link.click();
            } else {
                setError('Failed to download file');
            }
        } catch (err) {
            setError('An error occurred while downloading the file');
            console.error('Error downloading file:', err);
        }
    };

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

    // Get summary statistics
    const getSummaryStats = () => {
        const stats = {
            added: fileComparisons.filter(f => f.status === 'added').length,
            removed: fileComparisons.filter(f => f.status === 'removed').length,
            modified: fileComparisons.filter(f => f.status === 'modified').length,
            unchanged: fileComparisons.filter(f => f.status === 'unchanged').length
        };
        return stats;
    };

    if (loading) {
        return (
            <Container>
                <Box textAlign="center" padding="xl">
                    <Spinner size="large" />
                    <div>Loading version comparison...</div>
                </Box>
            </Container>
        );
    }

    if (error) {
        return (
            <Container>
                <Alert type="error" dismissible onDismiss={() => setError(null)}>
                    {error}
                </Alert>
            </Container>
        );
    }
    
    if (!selectedVersion || !versionToCompare || !version1Details || !version2Details) {
        return (
            <Container>
                <Box textAlign="center" padding="xl">
                    <div>Select two versions to compare</div>
                </Box>
            </Container>
        );
    }

    const stats = getSummaryStats();

    return (
        <Container
            header={
                <Header
                    variant="h2"
                    actions={
                        <Button onClick={onClose}>
                            Close Comparison
                        </Button>
                    }
                >
                    Compare Versions: v{versionToCompare.Version} vs v{selectedVersion.Version}
                </Header>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {/* Version Information */}
                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                    <Container header={<Header variant="h3">Version {versionToCompare.Version}</Header>}>
                        <ColumnLayout columns={1} variant="text-grid">
                            <div>
                                <Box variant="awsui-key-label">Date Created</Box>
                                <div>{new Date(versionToCompare.DateModified).toLocaleString()}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Created By</Box>
                                <div>{versionToCompare.createdBy || 'System'}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Comment</Box>
                                <div>{versionToCompare.Comment || '-'}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Files Count</Box>
                                <div>{version1Details?.files.length || 0}</div>
                            </div>
                        </ColumnLayout>
                    </Container>
                    
                    <Container header={<Header variant="h3">Version {selectedVersion.Version}</Header>}>
                        <ColumnLayout columns={1} variant="text-grid">
                            <div>
                                <Box variant="awsui-key-label">Date Created</Box>
                                <div>{new Date(selectedVersion.DateModified).toLocaleString()}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Created By</Box>
                                <div>{selectedVersion.createdBy || 'System'}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Comment</Box>
                                <div>{selectedVersion.Comment || '-'}</div>
                            </div>
                            <div>
                                <Box variant="awsui-key-label">Files Count</Box>
                                <div>{version2Details?.files.length || 0}</div>
                            </div>
                        </ColumnLayout>
                    </Container>
                </Grid>

                {/* Summary Statistics */}
                <Container header={<Header variant="h3">Comparison Summary</Header>}>
                    <ColumnLayout columns={4} variant="text-grid">
                        <div>
                            <Box variant="awsui-key-label">Added Files</Box>
                            <div style={{ color: '#037f0c' }}>{stats.added}</div>
                        </div>
                        <div>
                            <Box variant="awsui-key-label">Removed Files</Box>
                            <div style={{ color: '#d91515' }}>{stats.removed}</div>
                        </div>
                        <div>
                            <Box variant="awsui-key-label">Modified Files</Box>
                            <div style={{ color: '#0972d3' }}>{stats.modified}</div>
                        </div>
                        <div>
                            <Box variant="awsui-key-label">Unchanged Files</Box>
                            <div style={{ color: '#5f6b7a' }}>{stats.unchanged}</div>
                        </div>
                    </ColumnLayout>
                </Container>

                {/* File Comparison */}
                <Container header={<Header variant="h3">File Comparison</Header>}>
                    {fileComparisons.length === 0 ? (
                        <Box textAlign="center" padding="l">
                            No files to compare.
                        </Box>
                    ) : (
                        <Cards
                            cardDefinition={{
                                header: (item: FileComparison) => (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        <strong>{item.relativeKey.split('/').pop() || item.relativeKey}</strong>
                                        {getStatusBadge(item.status)}
                                    </div>
                                ),
                                sections: [
                                    {
                                        id: 'path',
                                        header: 'File Path',
                                        content: (item: FileComparison) => item.relativeKey
                                    },
                                    {
                                        id: 'version1',
                                        header: `Version ${versionToCompare.Version}`,
                                        content: (item: FileComparison) => {
                                            if (!item.version1File) {
                                                return <span style={{ color: '#d91515' }}>File not present</span>;
                                            }
                                            return (
                                                <div>
                                                    <div>Version ID: {item.version1File.versionId}</div>
                                                    <div>Size: {item.version1File.size ? `${(item.version1File.size / 1024 / 1024).toFixed(2)} MB` : 'Unknown'}</div>
                                                    {item.version1File.exists && (
                                                        <SpaceBetween direction="horizontal" size="xs">
                                                            <Link onFollow={() => handleViewFile(item.version1File!)}>
                                                                View
                                                            </Link>
                                                            <Link onFollow={() => handleDownloadFile(item.version1File!)}>
                                                                Download
                                                            </Link>
                                                        </SpaceBetween>
                                                    )}
                                                </div>
                                            );
                                        }
                                    },
                                    {
                                        id: 'version2',
                                        header: `Version ${selectedVersion.Version}`,
                                        content: (item: FileComparison) => {
                                            if (!item.version2File) {
                                                return <span style={{ color: '#d91515' }}>File not present</span>;
                                            }
                                            return (
                                                <div>
                                                    <div>Version ID: {item.version2File.versionId}</div>
                                                    <div>Size: {item.version2File.size ? `${(item.version2File.size / 1024 / 1024).toFixed(2)} MB` : 'Unknown'}</div>
                                                    {item.version2File.exists && (
                                                        <SpaceBetween direction="horizontal" size="xs">
                                                            <Link onFollow={() => handleViewFile(item.version2File!)}>
                                                                View
                                                            </Link>
                                                            <Link onFollow={() => handleDownloadFile(item.version2File!)}>
                                                                Download
                                                            </Link>
                                                        </SpaceBetween>
                                                    )}
                                                </div>
                                            );
                                        }
                                    }
                                ]
                            }}
                            items={fileComparisons}
                            loading={false}
                            empty={
                                <Box textAlign="center" color="inherit">
                                    No file differences found.
                                </Box>
                            }
                        />
                    )}
                </Container>
            </SpaceBetween>
        </Container>
    );
};

export default AssetVersionComparison;
