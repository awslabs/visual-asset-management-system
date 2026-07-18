/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Textarea from "@cloudscape-design/components/textarea";
import Toggle from "@cloudscape-design/components/toggle";
import { usePipelines, useWorkflow, useWorkflowMutations } from "../api/queries";
import PipelineOrderList from "./PipelineOrderList";
import DagPreview from "./DagPreview";
import { validateWorkflow } from "./workflowValidation";
import type { Workflow, SpecifiedPipelineRef, InputFileArity, ConcurrencyRestriction, OutputLocationType } from "../types";

interface WorkflowBuilderProps {
    mode: "create" | "edit";
    databaseId: string;
    workflowId?: string;
}

const WorkflowBuilder: React.FC<WorkflowBuilderProps> = ({ mode, databaseId, workflowId }) => {
    const navigate = useNavigate();
    const { data: pipelines = [] } = usePipelines(databaseId);
    const { data: workflow } = useWorkflow(databaseId, workflowId || "");
    const { createWorkflow, updateWorkflow } = useWorkflowMutations();

    const [workflowIdValue, setWorkflowIdValue] = useState("");
    const [workflowName, setWorkflowName] = useState("");
    const [category, setCategory] = useState("");
    const [description, setDescription] = useState("");
    const [subDashboardUrl, setSubDashboardUrl] = useState("");
    const [enabled, setEnabled] = useState(true);

    const [inputFileArity, setInputFileArity] = useState<InputFileArity>("one");
    const [assetScope, setAssetScope] = useState<Record<string, boolean>>({});
    const [metadataInputs, setMetadataInputs] = useState<Record<string, boolean>>({});
    const [allowFilters, setAllowFilters] = useState("");
    const [excludeFilters, setExcludeFilters] = useState("");
    const [concurrencyRestriction, setConcurrencyRestriction] = useState<ConcurrencyRestriction>("none");
    const [locationType, setLocationType] = useState<OutputLocationType>("asset");
    const [allowOverride, setAllowOverride] = useState(false);

    const [specifiedPipelines, setSpecifiedPipelines] = useState<SpecifiedPipelineRef[]>([]);

    const [validationErrors, setValidationErrors] = useState<string[]>([]);
    const [validationWarnings, setValidationWarnings] = useState<string[]>([]);
    const [backendWarnings, setBackendWarnings] = useState<string[]>([]);
    const [saving, setSaving] = useState(false);

    // Load workflow data in edit mode
    useEffect(() => {
        if (mode === "edit" && workflow) {
            setWorkflowIdValue(workflow.workflowId || "");
            setWorkflowName(workflow.workflowName || "");
            setCategory(workflow.category || "");
            setDescription(workflow.description || "");
            setSubDashboardUrl(workflow.subDashboardUrl || "");
            setEnabled(workflow.enabled ?? true);
            setSpecifiedPipelines(workflow.specifiedPipelines || []);

            const sc = workflow.systemConfig || {};
            setInputFileArity(sc.inputFileArity || "one");
            setAssetScope(sc.assetScope || {});
            setMetadataInputs(sc.metadataInputs || {});
            setAllowFilters((sc.inputFileFilters?.allow || []).join(", "));
            setExcludeFilters((sc.inputFileFilters?.exclude || []).join(", "));
            setConcurrencyRestriction(sc.concurrencyRestriction || "none");
            setLocationType(sc.outputTarget?.locationType || "asset");
            setAllowOverride(sc.outputTarget?.allowOverride ?? false);
        }
    }, [mode, workflow]);

    // HARD COUPLING: when locationType is "none", force inputFileArity to "none"
    useEffect(() => {
        if (locationType === "none" && inputFileArity !== "none") {
            setInputFileArity("none");
        }
    }, [locationType, inputFileArity]);

    // Run validation on change
    useEffect(() => {
        const assembled: Workflow = {
            databaseId,
            workflowId: workflowIdValue || "temp",
            workflowName,
            category,
            description,
            subDashboardUrl,
            enabled,
            specifiedPipelines,
            systemConfig: {
                inputFileArity,
                assetScope,
                metadataInputs,
                inputFileFilters: {
                    allow: allowFilters.split(",").map(s => s.trim()).filter(Boolean),
                    exclude: excludeFilters.split(",").map(s => s.trim()).filter(Boolean),
                },
                concurrencyRestriction,
                outputTarget: { locationType, allowOverride },
            },
        };

        const pipelinesById = pipelines.reduce((acc: Record<string, any>, p: any) => {
            const key = `${p.databaseId}:${p.pipelineId}`;
            acc[key] = p;
            return acc;
        }, {});

        const result = validateWorkflow(assembled, pipelinesById);
        setValidationErrors(result.errors);
        setValidationWarnings(result.warnings);
    }, [
        databaseId,
        workflowIdValue,
        workflowName,
        category,
        description,
        subDashboardUrl,
        enabled,
        specifiedPipelines,
        inputFileArity,
        assetScope,
        metadataInputs,
        allowFilters,
        excludeFilters,
        concurrencyRestriction,
        locationType,
        allowOverride,
        pipelines,
    ]);

    const handleSave = async () => {
        if (validationErrors.length > 0) return;

        setSaving(true);
        try {
            const body: Workflow = {
                databaseId,
                workflowId: workflowIdValue,
                workflowName,
                category,
                description,
                subDashboardUrl,
                enabled,
                specifiedPipelines,
                systemConfig: {
                    inputFileArity,
                    assetScope,
                    metadataInputs,
                    inputFileFilters: {
                        allow: allowFilters.split(",").map(s => s.trim()).filter(Boolean),
                        exclude: excludeFilters.split(",").map(s => s.trim()).filter(Boolean),
                    },
                    concurrencyRestriction,
                    outputTarget: { locationType, allowOverride },
                },
            };

            if (mode === "create") {
                const result = await createWorkflow.mutateAsync(body);
                // Extract backend warnings if present
                if (result?.warnings) {
                    setBackendWarnings(result.warnings);
                }
                navigate(`/databases/${databaseId}/workflows`);
            } else {
                const result = await updateWorkflow.mutateAsync({ databaseId, workflowId: workflowIdValue, body });
                if (result?.warnings) {
                    setBackendWarnings(result.warnings);
                }
                navigate(`/databases/${databaseId}/workflows`);
            }
        } catch (err: any) {
            console.error("Save failed:", err);
        } finally {
            setSaving(false);
        }
    };

    const arityOptions = [
        { label: "none", value: "none" },
        { label: "one", value: "one" },
        { label: "multi", value: "multi" },
    ];

    const concurrencyOptions = [
        { label: "none", value: "none" },
        { label: "perAsset", value: "perAsset" },
        { label: "perInputFile", value: "perInputFile" },
    ];

    const locationTypeOptions = [
        { label: "asset", value: "asset" },
        { label: "none", value: "none" },
    ];

    const isArityDisabled = locationType === "none";
    const isSaveDisabled = validationErrors.length > 0 || saving;

    return (
        <SpaceBetween size="l">
            <Header variant="h1">{mode === "create" ? "Create Workflow" : "Edit Workflow"}</Header>

            <Container header={<Header variant="h2">Basic Information</Header>}>
                <SpaceBetween size="m">
                    {mode === "create" && (
                        <FormField label="Workflow ID" description="Unique identifier (3-63 chars, letters, numbers, hyphens, underscores)">
                            <Input value={workflowIdValue} onChange={({ detail }) => setWorkflowIdValue(detail.value)} />
                        </FormField>
                    )}
                    {mode === "edit" && (
                        <FormField label="Workflow ID">
                            <Input value={workflowIdValue} disabled />
                        </FormField>
                    )}
                    <FormField label="Workflow Name">
                        <Input value={workflowName} onChange={({ detail }) => setWorkflowName(detail.value)} />
                    </FormField>
                    <FormField label="Category (optional)">
                        <Input value={category} onChange={({ detail }) => setCategory(detail.value)} />
                    </FormField>
                    <FormField label="Description (optional)">
                        <Textarea value={description} onChange={({ detail }) => setDescription(detail.value)} />
                    </FormField>
                    <FormField label="Sub-Dashboard URL (optional)">
                        <Input value={subDashboardUrl} onChange={({ detail }) => setSubDashboardUrl(detail.value)} />
                    </FormField>
                    <FormField label="Enabled">
                        <Toggle checked={enabled} onChange={({ detail }) => setEnabled(detail.checked)}>
                            {enabled ? "Enabled" : "Disabled"}
                        </Toggle>
                    </FormField>
                </SpaceBetween>
            </Container>

            <Container header={<Header variant="h2">System Configuration</Header>}>
                <SpaceBetween size="m">
                    <FormField
                        label="Input File Arity"
                        description={isArityDisabled ? "Locked to 'none' when output location is 'none' (results-only workflows require no input files)" : ""}
                    >
                        <Select
                            selectedOption={arityOptions.find(o => o.value === inputFileArity) || null}
                            onChange={({ detail }) => setInputFileArity(detail.selectedOption?.value as InputFileArity)}
                            options={arityOptions}
                            disabled={isArityDisabled}
                        />
                    </FormField>

                    <FormField label="Asset Scope">
                        <SpaceBetween size="xs">
                            <Checkbox
                                checked={assetScope.asset || false}
                                onChange={({ detail }) => setAssetScope({ ...assetScope, asset: detail.checked })}
                            >
                                Asset
                            </Checkbox>
                            <Checkbox
                                checked={assetScope.pipeline || false}
                                onChange={({ detail }) => setAssetScope({ ...assetScope, pipeline: detail.checked })}
                            >
                                Pipeline
                            </Checkbox>
                        </SpaceBetween>
                    </FormField>

                    <FormField label="Metadata Inputs">
                        <SpaceBetween size="xs">
                            <Checkbox
                                checked={metadataInputs.asset || false}
                                onChange={({ detail }) => setMetadataInputs({ ...metadataInputs, asset: detail.checked })}
                            >
                                Asset Metadata
                            </Checkbox>
                            <Checkbox
                                checked={metadataInputs.file || false}
                                onChange={({ detail }) => setMetadataInputs({ ...metadataInputs, file: detail.checked })}
                            >
                                File Metadata
                            </Checkbox>
                        </SpaceBetween>
                    </FormField>

                    <FormField label="Input File Filters - Allow (comma-separated)">
                        <Input value={allowFilters} onChange={({ detail }) => setAllowFilters(detail.value)} placeholder="e.g., *.jpg, *.png" />
                    </FormField>

                    <FormField label="Input File Filters - Exclude (comma-separated)">
                        <Input value={excludeFilters} onChange={({ detail }) => setExcludeFilters(detail.value)} placeholder="e.g., *.tmp, *.bak" />
                    </FormField>

                    <FormField label="Concurrency Restriction">
                        <Select
                            selectedOption={concurrencyOptions.find(o => o.value === concurrencyRestriction) || null}
                            onChange={({ detail }) => setConcurrencyRestriction(detail.selectedOption?.value as ConcurrencyRestriction)}
                            options={concurrencyOptions}
                        />
                    </FormField>

                    <FormField label="Output Target - Location Type">
                        <Select
                            selectedOption={locationTypeOptions.find(o => o.value === locationType) || null}
                            onChange={({ detail }) => setLocationType(detail.selectedOption?.value as OutputLocationType)}
                            options={locationTypeOptions}
                        />
                    </FormField>

                    <FormField label="Output Target - Allow Override">
                        <Toggle checked={allowOverride} onChange={({ detail }) => setAllowOverride(detail.checked)}>
                            {allowOverride ? "Allowed" : "Not Allowed"}
                        </Toggle>
                    </FormField>
                </SpaceBetween>
            </Container>

            <Container header={<Header variant="h2">Pipeline Order</Header>}>
                <SpaceBetween size="m">
                    <PipelineOrderList
                        value={specifiedPipelines}
                        pipelineOptions={pipelines}
                        templatesByPipeline={{}}
                        onChange={setSpecifiedPipelines}
                    />
                    <DagPreview refs={specifiedPipelines} />
                </SpaceBetween>
            </Container>

            <Container header={<Header variant="h2">Validation</Header>}>
                <SpaceBetween size="m">
                    {validationErrors.length > 0 && (
                        <Box color="text-status-error">
                            <strong>Errors (blocking save):</strong>
                            <ul>
                                {validationErrors.map((e, i) => (
                                    <li key={i}>{e}</li>
                                ))}
                            </ul>
                        </Box>
                    )}
                    {validationWarnings.length > 0 && (
                        <Box color="text-status-warning">
                            <strong>Warnings:</strong>
                            <ul>
                                {validationWarnings.map((w, i) => (
                                    <li key={i}>{w}</li>
                                ))}
                            </ul>
                        </Box>
                    )}
                    {backendWarnings.length > 0 && (
                        <Box color="text-status-warning">
                            <strong>Backend Warnings:</strong>
                            <ul>
                                {backendWarnings.map((w, i) => (
                                    <li key={i}>{w}</li>
                                ))}
                            </ul>
                        </Box>
                    )}
                    {validationErrors.length === 0 && validationWarnings.length === 0 && backendWarnings.length === 0 && (
                        <Box color="text-status-success">All validations passed</Box>
                    )}
                </SpaceBetween>
            </Container>

            <Box float="right">
                <SpaceBetween direction="horizontal" size="xs">
                    <Button onClick={() => navigate(-1)}>Cancel</Button>
                    <Button variant="primary" onClick={handleSave} disabled={isSaveDisabled} loading={saving}>
                        Save
                    </Button>
                </SpaceBetween>
            </Box>
        </SpaceBetween>
    );
};

export default WorkflowBuilder;
