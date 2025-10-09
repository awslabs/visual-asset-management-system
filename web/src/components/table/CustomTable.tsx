import React, { useState } from "react";
import Table from "@cloudscape-design/components/table";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Pagination from "@cloudscape-design/components/pagination";

interface ColumnDefinition {
    id: string;
    header: string;
    cell: (item: any) => React.ReactNode;
    sortingField?: string;
    isRowHeader?: boolean;
}

interface CustomTableProps {
    columns: ColumnDefinition[];
    items: { [key: string]: any }[];
    selectedItems: any[];
    setSelectedItems: (s: any[]) => void;
    trackBy: string;
    enablePagination?: boolean;
    pageSize?: number;
    selectionType?: "single" | "multi";
}

const CustomTable: React.FC<CustomTableProps> = ({
    columns,
    items,
    selectedItems,
    setSelectedItems,
    trackBy,
    enablePagination = false,
    pageSize = 15,
    selectionType = "single",
}) => {
    const [currentPageIndex, setCurrentPageIndex] = useState(1);
    const [sortingColumn, setSortingColumn] = useState<ColumnDefinition | undefined>(undefined);
    const [sortingDescending, setSortingDescending] = useState(false);

    // Sort items if sorting is active
    const sortedItems = React.useMemo(() => {
        if (!sortingColumn || !sortingColumn.sortingField) {
            return items;
        }

        const sorted = [...items].sort((a, b) => {
            const aValue = a[sortingColumn.sortingField!];
            const bValue = b[sortingColumn.sortingField!];

            // Handle null/undefined values
            if (aValue == null && bValue == null) return 0;
            if (aValue == null) return 1;
            if (bValue == null) return -1;

            // String comparison
            if (typeof aValue === "string" && typeof bValue === "string") {
                return aValue.localeCompare(bValue);
            }

            // Numeric comparison
            if (typeof aValue === "number" && typeof bValue === "number") {
                return aValue - bValue;
            }

            // Default string conversion
            return String(aValue).localeCompare(String(bValue));
        });

        return sortingDescending ? sorted.reverse() : sorted;
    }, [items, sortingColumn, sortingDescending]);

    // Calculate pagination
    const totalItems = sortedItems.length;
    const totalPages = Math.ceil(totalItems / pageSize);
    const startIndex = (currentPageIndex - 1) * pageSize;
    const endIndex = Math.min(startIndex + pageSize, totalItems);
    const paginatedItems = enablePagination ? sortedItems.slice(startIndex, endIndex) : sortedItems;

    const handlePageChange = ({ detail }: { detail: { currentPageIndex: number } }) => {
        setCurrentPageIndex(detail.currentPageIndex);
        // Clear selection when changing pages
        setSelectedItems([]);
    };

    const handleSortingChange = ({ detail }: any) => {
        const column = columns.find((col) => col.id === detail.sortingColumn.sortingField);
        setSortingColumn(column);
        setSortingDescending(detail.isDescending || false);
    };

    return (
        <div style={{ width: "100%", overflow: "hidden" }}>
            <SpaceBetween direction="vertical" size="s">
                <div style={{ width: "100%", overflowX: "auto" }}>
                    <Table
                        onSelectionChange={({ detail }) => {
                            setSelectedItems(detail.selectedItems);
                        }}
                        onSortingChange={handleSortingChange}
                        sortingColumn={sortingColumn}
                        sortingDescending={sortingDescending}
                        trackBy={trackBy}
                        selectedItems={selectedItems}
                        columnDefinitions={columns}
                        columnDisplay={columns.map((col) => ({ id: col.id, visible: true }))}
                        items={paginatedItems}
                        loadingText="Loading resources"
                        selectionType={selectionType}
                        variant="full-page"
                        empty={
                            <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                                <SpaceBetween size="m">
                                    <b>No Entity</b>
                                </SpaceBetween>
                            </Box>
                        }
                    />
                </div>
                {enablePagination && totalPages > 1 && (
                    <Pagination
                        currentPageIndex={currentPageIndex}
                        pagesCount={totalPages}
                        onChange={handlePageChange}
                    />
                )}
            </SpaceBetween>
        </div>
    );
};

export default CustomTable;
