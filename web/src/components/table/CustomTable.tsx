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

    // Calculate pagination
    const totalItems = items.length;
    const totalPages = Math.ceil(totalItems / pageSize);
    const startIndex = (currentPageIndex - 1) * pageSize;
    const endIndex = Math.min(startIndex + pageSize, totalItems);
    const paginatedItems = enablePagination ? items.slice(startIndex, endIndex) : items;

    const handlePageChange = ({ detail }: { detail: { currentPageIndex: number } }) => {
        setCurrentPageIndex(detail.currentPageIndex);
        // Clear selection when changing pages
        setSelectedItems([]);
    };

    return (
        <div style={{ width: "100%", overflow: "hidden" }}>
            <SpaceBetween direction="vertical" size="s">
                <div style={{ width: "100%", overflowX: "auto" }}>
                    <Table
                        onSelectionChange={({ detail }) => {
                            setSelectedItems(detail.selectedItems);
                        }}
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
