import React, { useState, useEffect } from "react";
import {
    Modal,
    Box,
    SpaceBetween,
    Button,
    Table,
    Spinner,
    Alert,
    StatusIndicator,
    Pagination,
    TextContent,
    Popover,
} from "@cloudscape-design/components";
import { TableProps } from "@cloudscape-design/components/table";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import { generatePresignedUrls } from "../../../services/FileOperationsService";
import { FileTree } from "../types/FileManagerTypes";

interface ShareUrlsModalProps {
    visible: boolean;
    onDismiss: () => void;
    selectedFiles: FileTree[];
    databaseId: string;
    assetId: string;
}

interface UrlItem {
    fileName: string;
    url: string;
    copied: boolean;
    error?: string;
}

export const ShareUrlsModal: React.FC<ShareUrlsModalProps> = ({
    visible,
    onDismiss,
    selectedFiles,
    databaseId,
    assetId,
}) => {
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [urls, setUrls] = useState<UrlItem[]>([]);
    const [currentPage, setCurrentPage] = useState(1);
    const [itemsPerPage] = useState(10);
    const [allCopied, setAllCopied] = useState(false);
    const DEFAULT_EXPIRATION_MESSAGE =
        "These URLs provide temporary access to the selected files. They will expire based on system expiration configuration.";
    const [expirationMessage, setExpirationMessage] = useState<string>(DEFAULT_EXPIRATION_MESSAGE);

    // Generate URLs when modal becomes visible
    useEffect(() => {
        if (visible && selectedFiles.length > 0) {
            generateUrls();
        }
    }, [visible, selectedFiles]);

    const generateUrls = async () => {
        setIsLoading(true);
        setError(null);
        setUrls([]);

        try {
            // Process both files and folders
            const filesToShare: { key: string; name: string; versionId?: string }[] = [];

            // Process each selected item
            for (const item of selectedFiles) {
                if (item.isFolder) {
                    // For folders, include all files within the folder
                    // We'll use the subTree to find all files in the folder
                    const collectFilesFromFolder = (folder: FileTree) => {
                        for (const subItem of folder.subTree) {
                            if (subItem.isFolder) {
                                // Recursively process subfolders
                                collectFilesFromFolder(subItem);
                            } else {
                                // Add file to the list
                                filesToShare.push({
                                    key: subItem.keyPrefix,
                                    name: subItem.name,
                                    versionId: subItem.versionId,
                                });
                            }
                        }
                    };

                    collectFilesFromFolder(item);
                } else {
                    // Regular file
                    filesToShare.push({
                        key: item.keyPrefix,
                        name: item.name,
                        versionId: item.versionId,
                    });
                }
            }

            if (filesToShare.length === 0) {
                setError("No valid files found to share");
                setIsLoading(false);
                return;
            }

            // Generate presigned URLs for all files
            const [success, response] = await generatePresignedUrls({
                databaseId,
                assetId,
                files: filesToShare,
            });

            if (!success) {
                setError(typeof response === "string" ? response : "Failed to generate URLs");
                setIsLoading(false);
                return;
            }

            // Format the response into our URL items
            const urlItems: UrlItem[] = [];

            if (Array.isArray(response)) {
                response.forEach((item) => {
                    urlItems.push({
                        fileName: item.fileName || "Unknown file",
                        url: item.url,
                        copied: false,
                        error: item.error,
                    });
                });
            }

            setUrls(urlItems);

            // Reset to default message first
            setExpirationMessage(DEFAULT_EXPIRATION_MESSAGE);

            // Try to parse expiration time from the first URL
            if (urlItems.length > 0 && !urlItems[0].error) {
                try {
                    const url = urlItems[0].url;
                    const expiresMatch = url.match(/X-Amz-Expires=(\d+)/);

                    if (expiresMatch && expiresMatch[1]) {
                        const expiresSeconds = parseInt(expiresMatch[1], 10);
                        let expirationText = "";

                        if (expiresSeconds < 3600) {
                            // Less than an hour
                            const minutes = Math.floor(expiresSeconds / 60);
                            expirationText = `${minutes} minute${minutes !== 1 ? "s" : ""}`;
                        } else if (expiresSeconds < 86400) {
                            // Less than a day
                            const hours = Math.floor(expiresSeconds / 3600);
                            expirationText = `${hours} hour${hours !== 1 ? "s" : ""}`;
                        } else {
                            // Days
                            const days = Math.floor(expiresSeconds / 86400);
                            expirationText = `${days} day${days !== 1 ? "s" : ""}`;
                        }

                        setExpirationMessage(
                            `These URLs provide temporary access to the selected files. They will expire in ${expirationText}.`
                        );
                    }
                } catch (parseErr) {
                    console.error("Error parsing URL expiration:", parseErr);
                    // Explicitly reset to default message
                    setExpirationMessage(DEFAULT_EXPIRATION_MESSAGE);
                }
            }
        } catch (err) {
            console.error("Error generating presigned URLs:", err);
            setError("An unexpected error occurred while generating URLs");
        } finally {
            setIsLoading(false);
        }
    };

    const handleCopy = (index: number) => {
        setUrls((prev) => prev.map((item, i) => (i === index ? { ...item, copied: true } : item)));

        // Reset copied status after 2 seconds
        setTimeout(() => {
            setUrls((prev) =>
                prev.map((item, i) => (i === index ? { ...item, copied: false } : item))
            );
        }, 2000);
    };

    const handleCopyAll = () => {
        // Create a comma-delimited list of just the URLs
        const allUrls = urls
            .filter((item) => !item.error)
            .map((item) => item.url)
            .join(",");

        // Copy to clipboard
        navigator.clipboard.writeText(allUrls).then(
            () => {
                // Mark all as copied
                setUrls((prev) => prev.map((item) => ({ ...item, copied: true })));
                setAllCopied(true);

                // Reset copied status after 2 seconds
                setTimeout(() => {
                    setUrls((prev) => prev.map((item) => ({ ...item, copied: false })));
                    setAllCopied(false);
                }, 2000);
            },
            (err) => {
                console.error("Could not copy text: ", err);
                setError("Failed to copy URLs to clipboard");
            }
        );
    };

    const columnDefinitions: TableProps.ColumnDefinition<UrlItem>[] = [
        {
            id: "fileName",
            header: "File Name",
            cell: (item: UrlItem) => item.fileName,
            width: 180,
        },
        {
            id: "url",
            header: "Shareable URL",
            cell: (item: UrlItem) =>
                item.error ? (
                    <StatusIndicator type="error">{item.error}</StatusIndicator>
                ) : (
                    <Popover
                        dismissButton={false}
                        position="top"
                        size="large"
                        triggerType="custom"
                        content={
                            <div style={{ maxWidth: "600px", padding: "8px" }}>
                                <TextContent>
                                    <p style={{ wordBreak: "break-all" }}>{item.url}</p>
                                </TextContent>
                                <Button
                                    iconName="copy"
                                    onClick={() => {
                                        navigator.clipboard.writeText(item.url);
                                        const index = urls.findIndex(
                                            (u) =>
                                                u.fileName === item.fileName && u.url === item.url
                                        );
                                        handleCopy(index);
                                    }}
                                >
                                    {item.copied ? "Copied!" : "Copy URL"}
                                </Button>
                            </div>
                        }
                    >
                        <div
                            style={{
                                whiteSpace: "nowrap",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                maxWidth: "1250px",
                                cursor: "pointer",
                            }}
                            title="Click to see full URL"
                        >
                            {item.url}
                        </div>
                    </Popover>
                ),
        },
        {
            id: "actions",
            header: "Actions",
            cell: (item: UrlItem) => {
                const index = urls.findIndex(
                    (u) => u.fileName === item.fileName && u.url === item.url
                );
                return item.error ? (
                    <Button iconName="refresh" onClick={() => generateUrls()}>
                        Retry
                    </Button>
                ) : (
                    <Button
                        iconName="copy"
                        onClick={() => {
                            navigator.clipboard.writeText(item.url);
                            handleCopy(index);
                        }}
                        wrapText={false}
                    >
                        {item.copied ? "Copied!" : "Copy URL"}
                    </Button>
                );
            },
            width: 100,
        },
    ];

    // Get current items for pagination
    const indexOfLastItem = currentPage * itemsPerPage;
    const indexOfFirstItem = indexOfLastItem - itemsPerPage;
    const currentItems = urls.slice(indexOfFirstItem, indexOfLastItem);

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header="Share File URLs"
            size="max"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss}>
                            Close
                        </Button>
                        {urls.length > 0 && (
                            <Button
                                onClick={handleCopyAll}
                                disabled={urls.every((item) => !!item.error)}
                            >
                                {allCopied ? "Copied!" : "Copy All URLs"}
                            </Button>
                        )}
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="m">
                <Box>{expirationMessage}</Box>

                {error && (
                    <Alert type="error" dismissible onDismiss={() => setError(null)}>
                        {error}
                    </Alert>
                )}

                {isLoading ? (
                    <Box textAlign="center" padding="l">
                        <SpaceBetween direction="vertical" size="m">
                            <Spinner size="large" />
                            <div>Generating shareable URLs...</div>
                        </SpaceBetween>
                    </Box>
                ) : (
                    <>
                        <Table
                            columnDefinitions={columnDefinitions}
                            items={currentItems}
                            loadingText="Generating URLs..."
                            empty={
                                <Box textAlign="center" padding="l">
                                    <b>No URLs generated</b>
                                    <Box variant="p" color="inherit">
                                        {error || "No files were selected for sharing."}
                                    </Box>
                                </Box>
                            }
                        />
                        {urls.length > itemsPerPage && (
                            <Box textAlign="center" margin={{ top: "m" }}>
                                <Pagination
                                    currentPageIndex={currentPage}
                                    pagesCount={Math.ceil(urls.length / itemsPerPage)}
                                    onChange={({ detail }) =>
                                        setCurrentPage(detail.currentPageIndex)
                                    }
                                />
                            </Box>
                        )}
                    </>
                )}
            </SpaceBetween>
        </Modal>
    );
};

export default ShareUrlsModal;
