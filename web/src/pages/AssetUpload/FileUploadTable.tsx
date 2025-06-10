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
        isRowHeader: true,
    },
    {
        id: "filepath",
        header: "Path",
        cell: (item: FileUploadTableItem) => item.relativePath,
        sortingField: "filepath",
        isRowHeader: true,
    },
    {
        id: "filesize",
        header: "Size",
        cell: (item: FileUploadTableItem) => (item.total ? shortenBytes(item.total) : "0b"),
        sortingField: "filesize",
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
        isRowHeader: true,
    },
];

interface FileUploadTableProps {
    allItems: FileUploadTableItem[];
    onRetry?: () => void;
    onRetryItem?: (index: number) => void;
    resume: boolean;
    columnDefinitions?: typeof FileUploadTableColumnDefinitions;
    showCount?: boolean;
    mode?: "Upload" | "Download" | "Delete";
    onRemoveItem?: (index: number) => void;
    allowRemoval?: boolean;
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
    status: "Queued" | "In Progress" | "Completed" | "Failed";
    progress: number;
    startedAt?: number;
    loaded: number;
    total: number;
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
        default:
            return "info";
    }
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
    mode: "Upload" | "Download" | "Delete" = "Upload"
) {
    // Only show retry button for failed items when onRetry is provided
    const failed = allItems.filter((item) => item.status === "Failed").length;
    if (failed > 0 && onRetry) {
        return (
            <Button variant={"primary"} onClick={onRetry}>
                Retry {failed} failed Items
            </Button>
        );
    } else {
        return <></>;
    }
}

export const FileUploadTable = ({
    allItems,
    onRetry,
    onRetryItem,
    resume,
    columnDefinitions,
    showCount,
    mode = "Upload",
    onRemoveItem,
    allowRemoval = false,
}: FileUploadTableProps) => {
    let visibleContent = ["filesize", "status", "progress"];
    
    // If no custom column definitions are provided, add actions column if needed
    if (!columnDefinitions) {
        // Start with the default column definitions
        let customColumnDefinitions = [...FileUploadTableColumnDefinitions];
        
        // Add actions column if we need retry or removal functionality
        if ((onRetryItem || (allowRemoval && onRemoveItem))) {
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
                sortingField: "actions",
                isRowHeader: false,
            });
        }
        
        columnDefinitions = customColumnDefinitions;
    }
    
    if (columnDefinitions) {
        visibleContent = columnDefinitions.map((definition) => definition.id);
    }
    const [preferences, setPreferences] = useState({
        pageSize: 10,
        visibleContent: visibleContent,
    });
    const { items, filterProps, paginationProps } = useCollection(allItems, {
        filtering: {
            empty: <EmptyState title="No matches" subtitle="No Files to display." />,
            noMatch: <EmptyState title="No matches" subtitle="We can’t find a match." />,
        },
        pagination: { pageSize: preferences.pageSize },
        sorting: {},
        selection: {},
    });
    return (
        <Box>
            <SpaceBetween size="l" direction={"vertical"}>
                <Table
                    header={
                        <Header
                            counter={
                                showCount
                                    ? `${getCompletedItemsCount(allItems)}/${allItems.length}`
                                    : `(${allItems.length})`
                            }
                            actions={getActions(allItems, true, onRetry, mode)}
                        >
                            Files
                        </Header>
                    }
                    columnDefinitions={columnDefinitions}
                    visibleColumns={preferences.visibleContent}
                    items={items}
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
