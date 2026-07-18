/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Toggle from "@cloudscape-design/components/toggle";
import { useTriggers } from "../api/queries";
import { setTrigger, deleteTrigger } from "../api/workflows";
import type { SpecifiedPipelineRef, WorkflowTrigger } from "../types";

interface TriggersEditorProps {
    databaseId: string;
    workflowId: string;
    pipelineRefs: SpecifiedPipelineRef[];
}

const TriggersEditor: React.FC<TriggersEditorProps> = ({ databaseId, workflowId, pipelineRefs }) => {
    const queryClient = useQueryClient();
    const { data: triggers = [] } = useTriggers(databaseId, workflowId);

    const [editing, setEditing] = useState(false);
    const [enabled, setEnabled] = useState(false);
    const [allowFilters, setAllowFilters] = useState("");
    const [excludeFilters, setExcludeFilters] = useState("");
    const [defaultTemplateIds, setDefaultTemplateIds] = useState<Record<string, string>>({});

    const setTriggerMutation = useMutation({
        mutationFn: (body: WorkflowTrigger) => {
            return new Promise<any>(async (resolve, reject) => {
                const [ok, data] = await setTrigger(databaseId, workflowId, "fileUpload", body);
                if (!ok) reject(new Error(typeof data === "string" ? data : "Failed to set trigger"));
                else resolve(data);
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["triggers", databaseId, workflowId] });
            setEditing(false);
        },
    });

    const deleteTriggerMutation = useMutation({
        mutationFn: () => {
            return new Promise<any>(async (resolve, reject) => {
                const [ok, data] = await deleteTrigger(databaseId, workflowId, "fileUpload");
                if (!ok) reject(new Error(typeof data === "string" ? data : "Failed to delete trigger"));
                else resolve(data);
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["triggers", databaseId, workflowId] });
        },
    });

    // Load existing trigger when editing
    useEffect(() => {
        const fileUploadTrigger = triggers.find((t: WorkflowTrigger) => t.triggerType === "fileUpload");
        if (fileUploadTrigger) {
            setEnabled(fileUploadTrigger.enabled ?? false);
            setAllowFilters((fileUploadTrigger.inputFileFilters?.allow || []).join(", "));
            setExcludeFilters((fileUploadTrigger.inputFileFilters?.exclude || []).join(", "));
            setDefaultTemplateIds(fileUploadTrigger.defaultTemplateIds || {});
        }
    }, [triggers]);

    const handleSave = () => {
        const body: WorkflowTrigger = {
            triggerType: "fileUpload",
            enabled,
            inputFileFilters: {
                allow: allowFilters.split(",").map(s => s.trim()).filter(Boolean),
                exclude: excludeFilters.split(",").map(s => s.trim()).filter(Boolean),
            },
            defaultTemplateIds,
        };
        setTriggerMutation.mutate(body);
    };

    const handleDelete = () => {
        if (confirm("Delete this trigger?")) {
            deleteTriggerMutation.mutate();
        }
    };

    const handleTemplateIdChange = (compositeKey: string, templateId: string) => {
        setDefaultTemplateIds({
            ...defaultTemplateIds,
            [compositeKey]: templateId,
        });
    };

    const fileUploadTrigger = triggers.find((t: WorkflowTrigger) => t.triggerType === "fileUpload");

    if (!editing && !fileUploadTrigger) {
        return (
            <Container header={<Header variant="h2">Triggers</Header>}>
                <SpaceBetween size="m">
                    <Box textAlign="center" color="inherit" padding="xl">
                        <SpaceBetween size="s">
                            <Box variant="strong">No file upload trigger configured</Box>
                            <Button onClick={() => setEditing(true)}>Create File Upload Trigger</Button>
                        </SpaceBetween>
                    </Box>
                </SpaceBetween>
            </Container>
        );
    }

    if (!editing && fileUploadTrigger) {
        return (
            <Container header={<Header variant="h2">Triggers</Header>}>
                <SpaceBetween size="m">
                    <Table
                        items={[fileUploadTrigger]}
                        columnDefinitions={[
                            { id: "type", header: "Type", cell: () => "File Upload" },
                            { id: "enabled", header: "Enabled", cell: (item: WorkflowTrigger) => (item.enabled ? "Yes" : "No") },
                            {
                                id: "actions",
                                header: "Actions",
                                cell: () => (
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <Button onClick={() => setEditing(true)}>Edit</Button>
                                        <Button onClick={handleDelete}>Delete</Button>
                                    </SpaceBetween>
                                ),
                            },
                        ]}
                    />
                </SpaceBetween>
            </Container>
        );
    }

    return (
        <Container header={<Header variant="h2">Edit File Upload Trigger</Header>}>
            <SpaceBetween size="m">
                <FormField label="Enabled">
                    <Toggle checked={enabled} onChange={({ detail }) => setEnabled(detail.checked)}>
                        {enabled ? "Enabled" : "Disabled"}
                    </Toggle>
                </FormField>

                <FormField label="Input File Filters - Allow (comma-separated)">
                    <Input value={allowFilters} onChange={({ detail }) => setAllowFilters(detail.value)} placeholder="e.g., *.jpg, *.png" />
                </FormField>

                <FormField label="Input File Filters - Exclude (comma-separated)">
                    <Input value={excludeFilters} onChange={({ detail }) => setExcludeFilters(detail.value)} placeholder="e.g., *.tmp, *.bak" />
                </FormField>

                <FormField label="Default Template IDs (per pipeline)">
                    <Table
                        items={pipelineRefs}
                        columnDefinitions={[
                            {
                                id: "pipeline",
                                header: "Pipeline",
                                cell: (item: SpecifiedPipelineRef) => `${item.pipelineDatabaseId || ""}:${item.pipelineId}`,
                            },
                            {
                                id: "templateId",
                                header: "Template ID",
                                cell: (item: SpecifiedPipelineRef) => {
                                    const compositeKey = `${item.pipelineDatabaseId}:${item.pipelineId}`;
                                    return (
                                        <Input
                                            value={defaultTemplateIds[compositeKey] || ""}
                                            onChange={({ detail }) => handleTemplateIdChange(compositeKey, detail.value)}
                                            placeholder="Template ID"
                                        />
                                    );
                                },
                            },
                        ]}
                    />
                </FormField>

                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={() => setEditing(false)}>Cancel</Button>
                        <Button variant="primary" onClick={handleSave} loading={setTriggerMutation.isPending}>
                            Save
                        </Button>
                    </SpaceBetween>
                </Box>
            </SpaceBetween>
        </Container>
    );
};

export default TriggersEditor;
