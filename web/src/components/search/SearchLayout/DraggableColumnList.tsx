/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import {
    DndContext,
    closestCenter,
    KeyboardSensor,
    PointerSensor,
    useSensor,
    useSensors,
    DragEndEvent,
} from "@dnd-kit/core";
import {
    arrayMove,
    SortableContext,
    sortableKeyboardCoordinates,
    useSortable,
    verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
    Box,
    SpaceBetween,
    Button,
    Icon,
    Container,
    Header,
    Multiselect,
    FormField,
} from "@cloudscape-design/components";
import { FIELD_MAPPINGS } from "../types";

interface DraggableColumnListProps {
    selectedColumns: string[];
    availableColumns: Array<{ label: string; value: string }>;
    onColumnsChange: (columns: string[]) => void;
    disabled?: boolean;
}

interface SortableItemProps {
    id: string;
    label: string;
    onRemove: () => void;
    disabled?: boolean;
}

const SortableItem: React.FC<SortableItemProps> = ({ id, label, onRemove, disabled }) => {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
        id,
        disabled,
    });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
        backgroundColor: isDragging ? "#f0f0f0" : "white",
        border: "1px solid #d5dbdb",
        borderRadius: "4px",
        padding: "8px 12px",
        marginBottom: "4px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        cursor: disabled ? "default" : "grab",
    };

    return (
        <div ref={setNodeRef} style={style}>
            <div style={{ display: "flex", alignItems: "center" }} {...attributes} {...listeners}>
                <Icon name="drag-indicator" variant="subtle" />
                <Box margin={{ left: "xs" }}>{label}</Box>
            </div>
            <Button
                iconName="close"
                variant="icon"
                onClick={() => onRemove()}
                disabled={disabled}
            />
        </div>
    );
};

const DraggableColumnList: React.FC<DraggableColumnListProps> = ({
    selectedColumns,
    availableColumns,
    onColumnsChange,
    disabled = false,
}) => {
    const sensors = useSensors(
        useSensor(PointerSensor),
        useSensor(KeyboardSensor, {
            coordinateGetter: sortableKeyboardCoordinates,
        })
    );

    const handleDragEnd = (event: DragEndEvent) => {
        const { active, over } = event;

        if (over && active.id !== over.id) {
            const oldIndex = selectedColumns.indexOf(active.id as string);
            const newIndex = selectedColumns.indexOf(over.id as string);

            const newColumns = arrayMove(selectedColumns, oldIndex, newIndex);
            onColumnsChange(newColumns);
        }
    };

    const handleRemoveColumn = (columnId: string) => {
        const newColumns = selectedColumns.filter((col) => col !== columnId);
        onColumnsChange(newColumns);
    };

    const handleAddColumn = (columnId: string) => {
        if (!selectedColumns.includes(columnId)) {
            onColumnsChange([...selectedColumns, columnId]);
        }
    };

    // Get available columns that aren't already selected
    const unselectedColumns = availableColumns.filter(
        (col) => !selectedColumns.includes(col.value)
    );

    return (
        <SpaceBetween direction="vertical" size="m">
            {/* Available Columns - Multiselect Dropdown */}
            <Multiselect
                selectedOptions={[]}
                onChange={({ detail }) => {
                    // Add newly selected columns
                    const newColumns = detail.selectedOptions
                        .map((opt) => opt.value)
                        .filter((val): val is string => val !== undefined);
                    
                    if (newColumns.length > 0) {
                        // Add the new columns to the end of the list
                        const updatedColumns = [...selectedColumns, ...newColumns];
                        onColumnsChange(updatedColumns);
                    }
                }}
                options={unselectedColumns}
                placeholder="Select columns to add"
                disabled={disabled}
                filteringType="auto"
            />

            {/* Selected Columns - Draggable */}
            <Box>
                {selectedColumns.length === 0 ? (
                    <Box textAlign="center" color="text-body-secondary" padding="m">
                        No columns selected. Add columns from the dropdown above.
                    </Box>
                ) : (
                    <DndContext
                        sensors={sensors}
                        collisionDetection={closestCenter}
                        onDragEnd={handleDragEnd}
                    >
                        <SortableContext
                            items={selectedColumns}
                            strategy={verticalListSortingStrategy}
                        >
                            {selectedColumns.map((columnId) => {
                                const mapping = FIELD_MAPPINGS[columnId];
                                return (
                                    <SortableItem
                                        key={columnId}
                                        id={columnId}
                                        label={mapping?.label || columnId}
                                        onRemove={() => handleRemoveColumn(columnId)}
                                        disabled={disabled}
                                    />
                                );
                            })}
                        </SortableContext>
                    </DndContext>
                )}
            </Box>
        </SpaceBetween>
    );
};

export default DraggableColumnList;
