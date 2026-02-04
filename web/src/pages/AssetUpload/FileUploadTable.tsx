//create a react functional component named FileUploadTable

import React, { useState } from "react";
import { useCollection } from "@cloudscape-design/collection-hooks";
import {
    Box,
    Button,
    CollectionPreferences,
    Header,
    Pagination,
    SpaceBetween,
    Table,
    TextFilter,
    Popover,
} from "@cloudscape-design/components";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import StatusIndicator from "@cloudscape-design/components/status-indicator";

const FileUploadTableColumnDefinitions = [
    {
        id: "progress",
        header: "Progress",
        cell: (item: FileUploadTableItem) => (
            <ProgressBar
                label={item.relativePath}
                value={item.progress}
                additionalInfo={" Time Remaining: " + getTimeRemaining(item)}
            />
        ),
        sortingField: "progress",
        sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) =>
            a.progress - b.progress,
        isRowHeader: true,
    },
    {
        id: "filepath",
        header: "Path",
        cell: (item: FileUploadTableItem) => item.relativePath,
        sortingField: "relativePath",
        sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) =>
            a.relativePath.localeCompare(b.relativePath),
        isRowHeader: true,
    },
    {
        id: "filesize",
        header: "Size",
        cell: (item: FileUploadTableItem) => (item.total ? shortenBytes(item.total) : "0b"),
        sortingField: "total",
        sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) => a.total - b.total,
        isRowHeader: true,
    },
    {
        id: "status",
        header: "Status",
        cell: (item: FileUploadTableItem) => (
            <StatusIndicator type={getStatusIndicator(item.status)}>
                {" "}
                {item.status}{" "}
            </StatusIndicator>
        ),
        sortingField: "status",
        sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) =>
            statusPriority[a.status] - statusPriority[b.status],
        isRowHeader: true,
    },
];

interface FileUploadTableProps {
    allItems: FileUploadTableItem[];
    onRetry?: () => void;
    onRetryItem?: (index: number) => void;
    onCancelItem?: (index: number) => void;
    cancelConfirmFileIndex?: number | null;
    onConfirmCancel?: (index: number) => void;
    onDismissCancel?: () => void;
    resume: boolean;
    columnDefinitions?: typeof FileUploadTableColumnDefinitions;
    showCount?: boolean;
    mode?: "Upload" | "Download" | "Delete";
    onRemoveItem?: (index: number) => void;
    onRemoveAll?: () => void;
    allowRemoval?: boolean;
    displayMode?: "selection" | "upload";
}

/**
 * Borrowed from : https://stackoverflow.com/a/42408230
 * @param {*} n : Number of Bytes to shorten
 * @returns : Readable Bytes count
 */
export function shortenBytes(n: number) {
    const k = n > 0 ? Math.floor(Math.log2(n) / 10) : 0;
    const rank = (k > 0 ? "KMGT"[k - 1] : "") + "b";
    const count = Math.floor(n / Math.pow(1024, k));
    return count + rank;
}

export interface FileUploadTableItem {
    name: string;
    handle?: any;
    index: number;
    size: number;
    relativePath: string;
    status: "Queued" | "In Progress" | "Completed" | "Failed" | "Cancelled";
    progress: number;
    startedAt?: number;
    loaded: number;
    total: number;
    error?: string; // Error message for failed uploads
    versionId?: string; // Version ID for download mode
    finalDownloadPath?: string; // Final download path for download mode
}

const getStatusIndicator = (status?: string) => {
    switch (status) {
        case "Queued":
            return "pending";
        case "In Progress":
            return "info";
        case "Completed":
            return "success";
        case "Failed":
            return "error";
        case "Cancelled":
            return "stopped";
        default:
            return "info";
    }
};

// Status priority for sorting (lower number = higher priority)
const statusPriority: Record<string, number> = {
    Failed: 0,
    "In Progress": 1,
    Queued: 2,
    Cancelled: 3,
    Completed: 4,
};

function formatTime(remainingTime: number) {
    const days = Math.floor(remainingTime / (24 * 60 * 60));
    const hours = Math.floor((remainingTime % (24 * 60 * 60)) / (60 * 60));
    const minutes = Math.floor((remainingTime % (60 * 60)) / 60);
    const seconds = Math.floor(remainingTime % 60);

    const daysStr = days > 0 ? `${days}d` : "";
    const hoursStr = hours > 0 ? `${hours}h` : "";
    const minutesStr = minutes > 0 ? `${minutes}m` : "";
    const secondsStr = seconds > 0 ? `${seconds}s` : "";

    // Join the non-empty parts with commas and "and"
    const timeParts = [daysStr, hoursStr, minutesStr, secondsStr].filter((part) => part !== "");
    const formattedTime = timeParts.join(":");

    return formattedTime;
}

const getTimeRemaining = (item: FileUploadTableItem) => {
    if (
        item.status === "In Progress" &&
        item.startedAt &&
        item.total &&
        item.loaded &&
        item.progress
    ) {
        const timeElapsed = Math.floor(new Date().getTime() / 1000) - item.startedAt;
        const decimalProgress = item.loaded / item.total;
        const totalTime = timeElapsed / decimalProgress;
        const remainingTime = totalTime - timeElapsed;
        return formatTime(remainingTime);
        //return <StatusIndicator type={"stopped"}> {formatTime(remainingTime)} </StatusIndicator>
    }
    return "Unknown";
    //return <StatusIndicator type={"stopped"}>  Unknown </StatusIndicator>;
};

export const paginationLabels = {
    nextPageLabel: "Next page",
    pageLabel: (pageNumber: number) => `Go to page ${pageNumber}`,
    previousPageLabel: "Previous page",
};

const pageSizePreference = {
    title: "Select page size",
    options: [
        { value: 10, label: "10 Files" },
        { value: 20, label: "20 Files" },
    ],
};

const visibleContentPreference = {
    title: "Select visible content",
    options: [
        {
            label: "Main properties",
            options: FileUploadTableColumnDefinitions.map(({ id, header }) => ({
                id,
                label: header,
                editable: id !== "id",
            })),
        },
    ],
};
export const collectionPreferencesProps = {
    pageSizePreference,
    visibleContentPreference,
    cancelLabel: "Cancel",
    confirmLabel: "Confirm",
    title: "Preferences",
};

interface EmptyStateProps {
    title: string;
    subtitle: string;
}

function EmptyState({ title, subtitle }: EmptyStateProps) {
    return (
        <Box textAlign="center" color="inherit">
            <Box variant="strong" textAlign="center" color="inherit">
                {title}
            </Box>
            <Box variant="p" padding={{ bottom: "s" }} color="inherit">
                {subtitle}
            </Box>
        </Box>
    );
}

function getCompletedItemsCount(allItems: FileUploadTableItem[]) {
    return allItems.filter((item) => item.status === "Completed").length;
}

function getActions(
    allItems: FileUploadTableItem[],
    resume: boolean,
    onRetry?: () => void,
    onRemoveAll?: () => void,
    mode: "Upload" | "Download" | "Delete" = "Upload"
) {
    const actions = [];

    // Add retry button for failed items when onRetry is provided
    const failed = allItems.filter((item) => item.status === "Failed").length;
    if (failed > 0 && onRetry) {
        actions.push(
            <Button key="retry" variant={"primary"} onClick={onRetry}>
                Retry {failed} failed Items
            </Button>
        );
    }

    // Add remove all button when onRemoveAll is provided and there are items
    if (onRemoveAll && allItems.length > 0) {
        actions.push(
            <Button key="removeAll" variant={"normal"} onClick={onRemoveAll}>
                Remove All Files
            </Button>
        );
    }

    if (actions.length === 0) {
        return <></>;
    }

    return (
        <SpaceBetween direction="horizontal" size="xs">
            {actions}
        </SpaceBetween>
    );
}

export const FileUploadTable = ({
    allItems,
    onRetry,
    onRetryItem,
    onCancelItem,
    cancelConfirmFileIndex,
    onConfirmCancel,
    onDismissCancel,
    resume,
    columnDefinitions,
    showCount,
    mode = "Upload",
    onRemoveItem,
    onRemoveAll,
    allowRemoval = false,
    displayMode = "upload",
}: FileUploadTableProps) => {
    let visibleContent = ["filesize", "status", "progress"];

    // If no custom column definitions are provided, add actions column if needed
    if (!columnDefinitions) {
        // Start with the default column definitions
        let customColumnDefinitions = [...FileUploadTableColumnDefinitions];

        // Find the index of the status column to insert version ID before it
        const statusColumnIndex = customColumnDefinitions.findIndex((col) => col.id === "status");

        // Add Download-specific columns if in Download mode
        if (mode === "Download") {
            // Update the existing filepath column to show "Asset Preview Path"
            const filepathColumnIndex = customColumnDefinitions.findIndex(
                (col) => col.id === "filepath"
            );
            if (filepathColumnIndex !== -1) {
                customColumnDefinitions[filepathColumnIndex] = {
                    ...customColumnDefinitions[filepathColumnIndex],
                    header: "Asset Preview Path",
                };
            }

            // Add Final Download Path column
            const finalDownloadPathColumn = {
                id: "finalDownloadPath",
                header: "Final Download Path",
                cell: (item: FileUploadTableItem) => (
                    <div>{item.finalDownloadPath || "Select folder first"}</div>
                ),
                sortingField: "finalDownloadPath",
                sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) =>
                    (a.finalDownloadPath || "").localeCompare(b.finalDownloadPath || ""),
                isRowHeader: false,
            };

            // Add Version ID column
            const versionColumn = {
                id: "versionId",
                header: "Version ID",
                cell: (item: FileUploadTableItem) => (
                    <div>{item.versionId && item.versionId.trim() ? item.versionId : "Latest"}</div>
                ),
                sortingField: "versionId",
                sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) =>
                    (a.versionId || "").localeCompare(b.versionId || ""),
                isRowHeader: false,
            };

            if (statusColumnIndex !== -1) {
                // Insert final download path and version columns before status column
                customColumnDefinitions.splice(
                    statusColumnIndex,
                    0,
                    finalDownloadPathColumn,
                    versionColumn
                );
            } else {
                // Fallback: add to the end if status column not found
                customColumnDefinitions.push(finalDownloadPathColumn, versionColumn);
            }
        }

        // Add actions column if we need retry, cancel, or removal functionality
        if (onRetryItem || onCancelItem || (allowRemoval && onRemoveItem)) {
            customColumnDefinitions.push({
                id: "actions",
                header: "Actions",
                cell: (item: FileUploadTableItem) => (
                    <SpaceBetween direction="horizontal" size="xs">
                        {item.status === "Failed" && onRetryItem && (
                            <Button
                                iconName="refresh"
                                variant="icon"
                                onClick={() => onRetryItem(item.index)}
                                ariaLabel={`Retry ${item.name}`}
                            />
                        )}
                        {(item.status === "Queued" || item.status === "In Progress") &&
                            onCancelItem &&
                            onConfirmCancel &&
                            onDismissCancel &&
                            (cancelConfirmFileIndex === item.index ? (
                                <Popover
                                    dismissButton={false}
                                    position="top"
                                    size="medium"
                                    triggerType="custom"
                                    content={
                                        <SpaceBetween direction="vertical" size="xs">
                                            <Box variant="p">
                                                Are you sure you want to cancel this file upload?
                                            </Box>
                                            <SpaceBetween direction="horizontal" size="xs">
                                                <Button
                                                    variant="primary"
                                                    onClick={() => onConfirmCancel(item.index)}
                                                >
                                                    Confirm Cancel
                                                </Button>
                                                <Button variant="normal" onClick={onDismissCancel}>
                                                    Keep Uploading
                                                </Button>
                                            </SpaceBetween>
                                        </SpaceBetween>
                                    }
                                >
                                    <Button
                                        iconName="close"
                                        variant="icon"
                                        ariaLabel={`Cancel ${item.name}`}
                                    />
                                </Popover>
                            ) : (
                                <Button
                                    iconName="close"
                                    variant="icon"
                                    onClick={() => onCancelItem(item.index)}
                                    ariaLabel={`Cancel ${item.name}`}
                                />
                            ))}
                        {allowRemoval && onRemoveItem && (
                            <Button
                                iconName="remove"
                                variant="icon"
                                onClick={() => onRemoveItem(item.index)}
                                ariaLabel={`Remove ${item.name}`}
                            />
                        )}
                    </SpaceBetween>
                ),
                sortingField: "index",
                sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) =>
                    a.index - b.index,
                isRowHeader: false,
            });
        }

        columnDefinitions = customColumnDefinitions;
    }

    // Filter columns based on displayMode
    if (displayMode === "selection") {
        // In selection mode, only show filepath, filesize, and actions (no progress or status)
        columnDefinitions = columnDefinitions.filter(
            (col) => col.id === "filepath" || col.id === "filesize" || col.id === "actions"
        );
    }

    if (columnDefinitions) {
        visibleContent = columnDefinitions.map((definition) => definition.id);
    }
    const [preferences, setPreferences] = useState({
        pageSize: 10,
        visibleContent: visibleContent,
    });
    const { items, filterProps, paginationProps, collectionProps } = useCollection(allItems, {
        filtering: {
            empty: <EmptyState title="No matches" subtitle="No Files to display." />,
            noMatch: <EmptyState title="No matches" subtitle="We can't find a match." />,
        },
        pagination: { pageSize: preferences.pageSize },
        sorting: {
            defaultState: {
                sortingColumn: { sortingField: "status" },
                isDescending: false,
            },
        },
        selection: {},
    });
    return (
        <Box>
            <SpaceBetween size="l" direction={"vertical"}>
                <Table
                    {...collectionProps}
                    header={
                        <Header
                            counter={
                                showCount
                                    ? `${getCompletedItemsCount(allItems)}/${allItems.length}`
                                    : `(${allItems.length})`
                            }
                            actions={getActions(allItems, true, onRetry, onRemoveAll, mode)}
                        >
                            Files
                        </Header>
                    }
                    columnDefinitions={columnDefinitions}
                    visibleColumns={preferences.visibleContent}
                    items={items}
                    sortingDescending={collectionProps.sortingDescending}
                    sortingColumn={collectionProps.sortingColumn}
                    onSortingChange={collectionProps.onSortingChange}
                    pagination={<Pagination {...paginationProps} ariaLabels={paginationLabels} />}
                    filter={<TextFilter {...filterProps} filteringAriaLabel="Filter Files" />}
                    preferences={
                        <CollectionPreferences
                            {...collectionPreferencesProps}
                            preferences={preferences}
                            contentDisplayPreference={{ options: [] }}
                            //@ts-ignore
                            onConfirm={({ detail }) => setPreferences(detail)}
                        />
                    }
                />
            </SpaceBetween>
        </Box>
    );
};
