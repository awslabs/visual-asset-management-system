//create a react functional component named FileUploadTable

import React, {useState} from 'react';
import { useCollection } from '@cloudscape-design/collection-hooks';
import {
    Box,
    Button,
    CollectionPreferences,
    Header, Link,
    Pagination,
    Table,
    TextFilter,
} from '@cloudscape-design/components';
import ProgressBar from "@cloudscape-design/components/progress-bar";
interface FileUploadTableProps {
    allItems: FileUploadTableItem[];
    onRetry?: () => void;
}

/**
 * Borrowed from : https://stackoverflow.com/a/42408230
 * @param {*} n : Number of Bytes to shorten
 * @returns : Readable Bytes count
 */
function shortenBytes(n: number) {
    const k = n > 0 ? Math.floor((Math.log2(n) / 10)) : 0;
    const rank = (k > 0 ? 'KMGT'[k - 1] : '') + 'b';
    const count = Math.floor(n / Math.pow(1024, k));
    return count + rank;
}

export interface FileUploadTableItem {
    file: File;
    index? : number;
    name?: string;
    size?: number;
    relativePath?: string;
    status?: "Queued" | "In Progress" | "Completed" | "Failed";
    progress?: number;
}
const FileUploadTableColumnDefinitions = [
    {
        id: 'filepath',
        header: 'Path',
        cell: (item: FileUploadTableItem) => item.relativePath,
        sortingField: 'filepath',
        isRowHeader: true,
    },
    {
        id: 'filesize',
        header: 'Size',
        cell: (item: FileUploadTableItem) => shortenBytes(item.file.size),
        sortingField: 'filesize',
        isRowHeader: true,
    },
    {
        id: 'status',
        header: 'Upload Status',
        cell: (item: FileUploadTableItem) => item.status,
        sortingField: 'status',
        isRowHeader: true,
    },
    {
        id: 'progress',
        header: 'Upload Progress',
        cell: (item: FileUploadTableItem) => <ProgressBar value={item.progress} />,
        sortingField: 'progress',
        isRowHeader: true,
    },

]

export const paginationLabels = {
    nextPageLabel: 'Next page',
    pageLabel: (pageNumber: number) => `Go to page ${pageNumber}`,
    previousPageLabel: 'Previous page',
};

const pageSizePreference = {
    title: 'Select page size',
    options: [
        { value: 10, label: '10 Files' },
        { value: 20, label: '20 Files' },
    ],
};

const visibleContentPreference = {
    title: 'Select visible content',
    options: [
        {
            label: 'Main properties',
            options: FileUploadTableColumnDefinitions.map(({ id, header }) => ({ id, label: header, editable: id !== 'id' })),
        },
    ],
};
export const collectionPreferencesProps = {
    pageSizePreference,
    visibleContentPreference,
    cancelLabel: 'Cancel',
    confirmLabel: 'Confirm',
    title: 'Preferences',
};

interface EmptyStateProps {
    title: string;
    subtitle: string;
}
function EmptyState({ title, subtitle}: EmptyStateProps) {
    return (
        <Box textAlign="center" color="inherit">
            <Box variant="strong" textAlign="center" color="inherit">
                {title}
            </Box>
            <Box variant="p" padding={{ bottom: 's' }} color="inherit">
                {subtitle}
            </Box>
        </Box>
    );
}

function getFailedItemsCount(allItems: FileUploadTableItem[]) {
    return allItems.filter(item => item.status === 'Failed').length;
}

function getCompletedItemsCount(allItems: FileUploadTableItem[]) {
    return allItems.filter(item => item.status === 'Completed').length;
}

export const FileUploadTable = ( { allItems, onRetry }: FileUploadTableProps) => {
    const [preferences, setPreferences] = useState({ pageSize: 10, visibleContent: [ 'filepath', 'filesize', 'status', 'progress', ] });
    const { items, filterProps, filteredItemsCount, paginationProps } = useCollection(
        allItems,
        {
            filtering: {
                empty: (
                    <EmptyState
                        title="No matches"
                        subtitle="No Files to display."
                    />
                ),
                noMatch: (
                    <EmptyState
                        title="No matches"
                        subtitle="We can’t find a match."
                    />
                ),
            },
            pagination: { pageSize: preferences.pageSize },
            sorting: {},
            selection: {},
        }
    );
    return (
        <Table
            header={
                <Header
                    counter={`${getCompletedItemsCount(allItems)}/${allItems.length}`}
                    actions={

                        <Button onClick={onRetry}>
                            <Link href="/">Reupload {getFailedItemsCount(allItems)} failed</Link>
                        </Button>
                    }
                >
                    Files to upload
                </Header>
            }
            columnDefinitions={FileUploadTableColumnDefinitions}
            visibleColumns={preferences.visibleContent}
            items={items}
            pagination={<Pagination {...paginationProps} ariaLabels={paginationLabels} />}
            filter={
                <TextFilter
                    {...filterProps}
                    filteringAriaLabel="Filter Files"
                />
            }
            preferences={
                <CollectionPreferences
                    {...collectionPreferencesProps}
                    preferences={preferences}
                    // onConfirm={({ detail }) => setPreferences(detail)}
                />
            }
        />
    );
};