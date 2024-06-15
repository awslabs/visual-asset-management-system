import Table from "@cloudscape-design/components/table";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";

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
}

const CustomTable: React.FC<CustomTableProps> = ({
    columns,
    items,
    selectedItems,
    setSelectedItems,
    trackBy,
}) => {
    return (
        <Table
            onSelectionChange={({ detail }) => {
                setSelectedItems(detail.selectedItems);
            }}
            trackBy={trackBy}
            selectedItems={selectedItems}
            columnDefinitions={columns}
            columnDisplay={columns.map((col) => ({ id: col.id, visible: true }))}
            items={items}
            loadingText="Loading resources"
            selectionType="single"
            empty={
                <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                    <SpaceBetween size="m">
                        <b>No Entity</b>
                    </SpaceBetween>
                </Box>
            }
        />
    );
};

export default CustomTable;
