/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Icon from "@cloudscape-design/components/icon";
import { SpecifiedPipelineRef, Pipeline, Template } from "../types";

export function moveItem<T>(list: T[], from: number, to: number): T[] {
    const newList = [...list];
    const [item] = newList.splice(from, 1);
    newList.splice(to, 0, item);
    return newList;
}

interface PipelineCardProps {
    pipelineRef: SpecifiedPipelineRef;
    index: number;
    pipelineOptions: Pipeline[];
    templateOptions: Template[];
    onUpdate: (index: number, updated: SpecifiedPipelineRef) => void;
    onRemove: (index: number) => void;
}

const PipelineCard: React.FC<PipelineCardProps> = ({
    pipelineRef,
    index,
    pipelineOptions,
    templateOptions,
    onUpdate,
    onRemove,
}) => {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
        id: index.toString(),
    });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
    };

    const selectedPipeline = pipelineOptions.find(
        (p) => p.pipelineId === pipelineRef.pipelineId && p.databaseId === pipelineRef.pipelineDatabaseId
    );

    const pipelineSelectOptions = pipelineOptions.map((p) => ({
        label: p.pipelineName,
        value: `${p.databaseId}:${p.pipelineId}`,
    }));

    const templateSelectOptions = templateOptions.map((t) => ({
        label: t.templateName,
        value: t.templateId,
    }));

    const handlePipelineChange = (detail: any) => {
        const compositeKey = detail.selectedOption?.value || "";
        const [databaseId, pipelineId] = compositeKey.split(":");
        onUpdate(index, {
            ...pipelineRef,
            pipelineId,
            pipelineDatabaseId: databaseId,
            defaultTemplateId: undefined, // Reset template when pipeline changes
        });
    };

    const handleTemplateChange = (detail: any) => {
        onUpdate(index, {
            ...pipelineRef,
            defaultTemplateId: detail.selectedOption?.value || undefined,
        });
    };

    const handleJobNameChange = (detail: any) => {
        onUpdate(index, {
            ...pipelineRef,
            jobName: detail.value || undefined,
        });
    };

    return (
        <div ref={setNodeRef} style={style}>
            <div className="tw-border tw-border-gray-300 tw-rounded tw-p-4 tw-mb-2 tw-bg-white dark:tw-bg-gray-800 dark:tw-border-gray-600">
                <div className="tw-flex tw-items-start tw-gap-3">
                    <div {...attributes} {...listeners} className="tw-cursor-grab tw-pt-2">
                        <Icon name="drag-indicator" variant="subtle" />
                    </div>
                    <div className="tw-flex-1">
                        <SpaceBetween size="s">
                            <FormField label="Pipeline">
                                <Select
                                    selectedOption={
                                        selectedPipeline
                                            ? {
                                                  label: selectedPipeline.pipelineName,
                                                  value: `${selectedPipeline.databaseId}:${selectedPipeline.pipelineId}`,
                                              }
                                            : null
                                    }
                                    onChange={({ detail }) => handlePipelineChange(detail)}
                                    options={pipelineSelectOptions}
                                    placeholder="Select a pipeline"
                                    filteringType="auto"
                                />
                            </FormField>
                            {templateOptions.length > 0 && (
                                <FormField label="Default Template (optional)">
                                    <Select
                                        selectedOption={
                                            pipelineRef.defaultTemplateId
                                                ? templateSelectOptions.find(
                                                      (t) => t.value === pipelineRef.defaultTemplateId
                                                  ) || null
                                                : null
                                        }
                                        onChange={({ detail }) => handleTemplateChange(detail)}
                                        options={templateSelectOptions}
                                        placeholder="Select a template"
                                        filteringType="auto"
                                    />
                                </FormField>
                            )}
                            <FormField label="Job Name (optional)">
                                <Input
                                    value={pipelineRef.jobName || ""}
                                    onChange={({ detail }) => handleJobNameChange(detail)}
                                    placeholder="Enter job name"
                                />
                            </FormField>
                        </SpaceBetween>
                    </div>
                    <Button
                        iconName="close"
                        variant="icon"
                        onClick={() => onRemove(index)}
                        ariaLabel="Remove pipeline"
                    />
                </div>
            </div>
        </div>
    );
};

interface PipelineOrderListProps {
    value: SpecifiedPipelineRef[];
    pipelineOptions: Pipeline[];
    templatesByPipeline: Record<string, Template[]>;
    onChange: (refs: SpecifiedPipelineRef[]) => void;
}

const PipelineOrderList: React.FC<PipelineOrderListProps> = ({
    value,
    pipelineOptions,
    templatesByPipeline,
    onChange,
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
            const oldIndex = parseInt(active.id.toString());
            const newIndex = parseInt(over.id.toString());

            const newRefs = moveItem(value, oldIndex, newIndex);
            onChange(newRefs);
        }
    };

    const handleUpdate = (index: number, updated: SpecifiedPipelineRef) => {
        const newRefs = [...value];
        newRefs[index] = updated;
        onChange(newRefs);
    };

    const handleRemove = (index: number) => {
        const newRefs = value.filter((_, i) => i !== index);
        onChange(newRefs);
    };

    const handleAdd = () => {
        onChange([...value, { pipelineId: "", pipelineDatabaseId: "" }]);
    };

    if (value.length === 0) {
        return (
            <SpaceBetween size="m">
                <Box textAlign="center" color="inherit" padding="xl">
                    <SpaceBetween size="s">
                        <Box variant="strong">No pipelines added</Box>
                        <Box color="text-body-secondary">
                            Add a pipeline to start building your workflow
                        </Box>
                        <Button iconName="add-plus" onClick={handleAdd}>
                            Add Pipeline
                        </Button>
                    </SpaceBetween>
                </Box>
            </SpaceBetween>
        );
    }

    return (
        <SpaceBetween size="m">
            <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={handleDragEnd}
            >
                <SortableContext items={value.map((_, i) => i.toString())} strategy={verticalListSortingStrategy}>
                    {value.map((pipelineRef, index) => {
                        const compositeKey = `${pipelineRef.pipelineDatabaseId}:${pipelineRef.pipelineId}`;
                        const templateOptions = templatesByPipeline[compositeKey] || [];
                        return (
                            <PipelineCard
                                key={index}
                                pipelineRef={pipelineRef}
                                index={index}
                                pipelineOptions={pipelineOptions}
                                templateOptions={templateOptions}
                                onUpdate={handleUpdate}
                                onRemove={handleRemove}
                            />
                        );
                    })}
                </SortableContext>
            </DndContext>
            <Button iconName="add-plus" onClick={handleAdd}>
                Add Pipeline
            </Button>
        </SpaceBetween>
    );
};

export default PipelineOrderList;
