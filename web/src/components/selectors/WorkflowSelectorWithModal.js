/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Modal, Select, SpaceBetween, FormField, Button, Box } from "@cloudscape-design/components";
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { fetchDatabaseWorkflows, runWorkflow } from "../../services/APIService";

// Helper function to check if a file has an extension (is a file, not a folder)
const isFile = (file) => {
    // Check if the fileName contains a dot (.) which indicates it has an extension
    return file.fileName.includes('.');
};

export default function WorkflowSelectorWithModal(props) {
    const { databaseId, assetId, setOpen, open, assetFiles = [], onWorkflowExecuted } = props;
    const [reload, setReload] = useState(true);
    const [allItems, setAllItems] = useState([]);
    const [workflowId, setWorkflowId] = useState(null);
    const [selectedFileKey, setSelectedFileKey] = useState(null);
    const [filteredFiles, setFilteredFiles] = useState([]);
    const navigate = useNavigate();

    useEffect(() => {
        const getData = async () => {
            const items = await fetchDatabaseWorkflows({ databaseId: databaseId });
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
            const onlyFiles = assetFiles.filter(file => isFile(file));
            
            setFilteredFiles(onlyFiles);
            
            // Only set the selected file key if there are files available
            if (onlyFiles.length > 0) {
                setSelectedFileKey(onlyFiles[0].key);
            }
        }
    }, [assetFiles]);


    const handleWorkflowSelection = (event) => {
        const newWorkflowId = event.detail.selectedOption.value;
        setWorkflowId(newWorkflowId);
    };

    const handleExecuteWorkflow = async () => {
        if (!workflowId) return;
        
        const result = await runWorkflow({
            databaseId: databaseId,
            assetId: assetId,
            workflowId: workflowId,
            fileKey: selectedFileKey, // Pass the selected file key
        });
        if (result !== false && Array.isArray(result)) {
            if (result[0] === false) {
                // TODO: error handling
            } else {
                // Instead of reloading the entire page, call the onWorkflowExecuted callback if provided
                if (typeof onWorkflowExecuted === 'function') {
                    onWorkflowExecuted();
                }
            }
        }
        handleClose();
    };

    const handleFileSelection = (event) => {
        setSelectedFileKey(event.detail.selectedOption.value);
    };


    const handleClose = () => {
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
                <FormField label="Select Workflow">
                    <Select
                        onChange={handleWorkflowSelection}
                        options={allItems.map((item) => {
                            return {
                                label: item.workflowId,
                                value: item.workflowId,
                            };
                        })}
                        selectedOption={
                            workflowId
                                ? {
                                      value: workflowId,
                                      label: workflowId,
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
                                          label: filteredFiles.find(f => f.key === selectedFileKey)?.relativePath || 
                                                 filteredFiles.find(f => f.key === selectedFileKey)?.fileName || 
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
                        disabled={!workflowId}
                    >
                        Execute Workflow
                    </Button>
                </Box>
            </SpaceBetween>
        </Modal>
    );
}
