/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Modal, Select, SpaceBetween, FormField, Button, Box, Alert } from "@cloudscape-design/components";
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { fetchDatabaseWorkflows, runWorkflow } from "../../services/APIService";

// Helper function to check if a file has an extension (is a file, not a folder)
const isFile = (file) => {
    // Check if the fileName contains a dot (.) which indicates it has an extension
    return file.fileName.includes(".");
};

export default function WorkflowSelectorWithModal(props) {
    const { databaseId, assetId, setOpen, open, assetFiles = [], onWorkflowExecuted } = props;
    const [reload, setReload] = useState(true);
    const [allItems, setAllItems] = useState([]);
    const [selectedWorkflow, setSelectedWorkflow] = useState(null);
    const [selectedFileKey, setSelectedFileKey] = useState(null);
    const [filteredFiles, setFilteredFiles] = useState([]);
    const [apiError, setApiError] = useState(null);
    const [isExecuting, setIsExecuting] = useState(false);
    const navigate = useNavigate();

    useEffect(() => {
        const getData = async () => {
            const itemsGlobal = await fetchDatabaseWorkflows({ databaseId: "GLOBAL" });
            const itemsDb = await fetchDatabaseWorkflows({ databaseId: databaseId });
            const items = [...itemsDb, ...itemsGlobal]
            if (items !== false && Array.isArray(items)) {
                setReload(false);
                setAllItems(items);
            }
        };
        if (reload) {
            getData();
        }
    }, [databaseId, reload]);

    // Initialize filtered files and find primary file when asset files change
    useEffect(() => {
        if (assetFiles && assetFiles.length > 0) {
            // Filter out folders, only keep files with extensions
            const onlyFiles = assetFiles.filter((file) => isFile(file));

            setFilteredFiles(onlyFiles);

            // Only set the selected file key if there are files available
            if (onlyFiles.length > 0) {
                setSelectedFileKey(onlyFiles[0].key);
            }
        }
    }, [assetFiles]);

    const handleWorkflowSelection = (event) => {
        const selectedOption = event.detail.selectedOption.value;
        setSelectedWorkflow(selectedOption);
    };

    const handleExecuteWorkflow = async () => {
        if (!selectedWorkflow) return;

        // Clear any previous errors
        setApiError(null);
        setIsExecuting(true);

        const isGlobalWorkflow = selectedWorkflow.databaseId === "GLOBAL";
        
        try {
            const result = await runWorkflow({
                databaseId: databaseId,
                assetId: assetId,
                workflowId: selectedWorkflow.workflowId,
                fileKey: selectedFileKey, // Pass the selected file key
                isGlobalWorkflow: isGlobalWorkflow
            });
            
            if (result !== false && Array.isArray(result)) {
                if (result[0] === false) {
                    // Handle error from API
                    const errorMessage = result[1] || "Failed to execute workflow. Please try again.";
                    setApiError(errorMessage);
                } else {
                    // Success case - call the callback and close the modal
                    if (typeof onWorkflowExecuted === "function") {
                        onWorkflowExecuted();
                    }
                    handleClose();
                }
            } else {
                // Handle unexpected response format
                setApiError("Received an invalid response from the server. Please try again.");
            }
        } catch (error) {
            // Handle unexpected errors
            console.error("Error executing workflow:", error);
            setApiError(`An unexpected error occurred: ${error.message || "Unknown error"}`);
        } finally {
            setIsExecuting(false);
        }
    };

    const handleFileSelection = (event) => {
        setSelectedFileKey(event.detail.selectedOption.value);
    };

    const handleClose = () => {
        // Clear any errors when closing the modal
        setApiError(null);
        setOpen(false);
    };

    return (
        <Modal
            onDismiss={handleClose}
            visible={open}
            closeAriaLabel="Close modal"
            size="medium"
            header="Execute Workflow"
        >
            <SpaceBetween direction="vertical" size="l">
                {apiError && (
                    <Alert type="error" header="Error executing workflow">
                        {apiError}
                    </Alert>
                )}
                <FormField label="Select Workflow">
                    <Select
                        onChange={handleWorkflowSelection}
                        options={allItems.map((item) => {
                            return {
                                label: `${item.workflowId} (${item.databaseId})`,
                                value: {workflowId: item.workflowId, databaseId: item.databaseId},
                            };
                        })}
                        selectedOption={
                            selectedWorkflow
                                ? {
                                      value: selectedWorkflow,
                                      label:`${selectedWorkflow.workflowId} (${selectedWorkflow.databaseId})`,
                                  }
                                : null
                        }
                        filteringType="auto"
                        selectedAriaLabel="Selected"
                    />
                </FormField>

                {assetFiles && assetFiles.length > 0 && (
                    <FormField label="Select File to Process (Optional)">
                        <Select
                            onChange={handleFileSelection}
                            options={filteredFiles.map((file) => {
                                return {
                                    label: file.relativePath || file.fileName,
                                    value: file.key,
                                    description: "",
                                };
                            })}
                            selectedOption={
                                selectedFileKey
                                    ? {
                                          value: selectedFileKey,
                                          label:
                                              filteredFiles.find((f) => f.key === selectedFileKey)
                                                  ?.relativePath ||
                                              filteredFiles.find((f) => f.key === selectedFileKey)
                                                  ?.fileName ||
                                              selectedFileKey,
                                      }
                                    : null
                            }
                            filteringType="auto"
                            selectedAriaLabel="Selected"
                            empty="No files match your search"
                        />
                    </FormField>
                )}

                <Box textAlign="right">
                    <Button
                        variant="primary"
                        onClick={handleExecuteWorkflow}
                        disabled={!selectedWorkflow || isExecuting}
                        loading={isExecuting}
                    >
                        Execute Workflow
                    </Button>
                </Box>
            </SpaceBetween>
        </Modal>
    );
}
