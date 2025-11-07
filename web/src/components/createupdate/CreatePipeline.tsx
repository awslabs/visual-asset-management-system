/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Modal,
    Box,
    SpaceBetween,
    Button,
    Form,
    FormField,
    Input,
    Textarea,
    MultiselectProps,
    Select,
    RadioGroup,
} from "@cloudscape-design/components";
import { useState, useEffect } from "react";
import { API } from "aws-amplify";
import OptionDefinition from "./form-definitions/types/OptionDefinition";
import {
    pipelineTypeOptions,
    pipelineExecutionTypeOptions,
} from "./form-definitions/PipelineFormDefinition";
import DatabaseSelector from "../selectors/DatabaseSelector";
import { dataBind } from "jodit/types/core/helpers";
import { check } from "prettier";

// Type definition for string dictionary (was imported from babylonjs)
type StringDictionary = { [key: string]: string };

interface CreatePipelineProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    // initState?: PipelineFields;
    initState?: any;
}

interface PipelineFields {
    pipelineId: string;
    databaseId: OptionDefinition;
    pipelineTypeSelected: OptionDefinition;
    pipelineExecutionType: OptionDefinition;
    waitForCallback: OptionDefinition;
    taskTimeout: string;
    taskHeartbeatTimeout: string;
    lambdaName: string;
    description: string;
    assetType: string;
    outputType: string;
    inputParameters: string;
}

// when a string is all lower case, return null, otherwise return the string "All lower case letters only"
function validatePipelineNameLowercase(name: string) {
    return name.match(/^[a-zA-Z0-9_-]+$/) !== null
        ? null
        : "No special characters or spaces except - and _";
}

// when a string is between 4 and 64 characters, return null, otherwise return the string "Between 4 and 64 characters"
function validatePipelineNameLength(name: string) {
    return name.length >= 4 && name.length <= 64 ? null : "Between 4 and 64 characters";
}

// chain together the above three functions, when they return null, then return null
function validatePipelineName(name: string) {
    return validatePipelineNameLowercase(name) || validatePipelineNameLength(name);
}

// when a string is between the given min and max characters, return null, otherwise return an error message including the range
function validatePipelineDescriptionLength(description: string) {
    const min = 4,
        max = 256;
    return description.length >= min && description.length <= max
        ? null
        : `Between ${min} and ${max} characters`;
}

const waitForCallbackOptions = [
    {
        label: "Yes",
        value: "Enabled",
    },
    {
        label: "No",
        value: "Disabled",
    },
];

export default function CreatePipeline({
    open,
    setOpen,
    setReload,
    initState,
}: CreatePipelineProps) {
    const [formState, setFormState] = useState<PipelineFields>({
        pipelineId: "",
        databaseId: { label: "", value: "" },
        pipelineTypeSelected: pipelineTypeOptions[0],
        pipelineExecutionType: pipelineExecutionTypeOptions[0],
        waitForCallback: waitForCallbackOptions[0],
        taskTimeout: "",
        taskHeartbeatTimeout: "",
        lambdaName: "",
        description: "",
        assetType: initState?.assetType || ".all",
        outputType: initState?.outputType || ".all",
        inputParameters: "",
        ...initState,
    });

    // if initState is detected, format initial state definitions, and set form state
    useEffect(() => {
        let initPipelineType: OptionDefinition = {
            label: pipelineTypeOptions[0].label,
            value: pipelineTypeOptions[0].value,
        };
        let initPipelineExecutionType: OptionDefinition = {
            label: pipelineExecutionTypeOptions[0].label,
            value: pipelineExecutionTypeOptions[0].value,
        };
        let initWaitForCallback: OptionDefinition = {
            label: waitForCallbackOptions[0].label,
            value: waitForCallbackOptions[0].value,
        };
        let initAssetType: string = ".all";
        let initOutputType: string = ".all";
        let initDatabase: OptionDefinition = { label: null, value: null };
        let initLambdaName: string = "";

        if (initState) {
            let type = pipelineTypeOptions.find((item) => item.value === initState.pipelineType);
            initPipelineType = { label: type?.label, value: type?.value };
            type = pipelineExecutionTypeOptions.find(
                (item) => item.value === initState.pipelineExecutionType
            );
            initPipelineExecutionType = { label: type?.label, value: type?.value };
            type = waitForCallbackOptions.find((item) => item.value === initState.waitForCallback);
            initWaitForCallback = { label: type?.label, value: type?.value };
            initAssetType = initState.assetType || ".all";
            initOutputType = initState.outputType || ".all";
            initDatabase = { label: initState.databaseId, value: initState.databaseId };
            let obj = JSON.parse(initState.userProvidedResource);
            initLambdaName = obj.resourceId;
        }
        setFormState((prev) => ({
            ...prev,
            pipelineTypeSelected: initPipelineType,
            pipelineExecutionType: initPipelineExecutionType,
            waitForCallback: initWaitForCallback,
            assetType: initAssetType,
            outputType: initOutputType,
            databaseId: initDatabase,
            lambdaName: initLambdaName,
        }));
    }, [initState]);

    // TODO: can refactor this approach, move handlers to separate file (utils.js) or combine into one change function
    const handlePipelineTypeChange = (e: any) => {
        setFormState((prev) => ({
            ...prev,
            pipelineTypeSelected: e.detail.selectedOption,
        }));
    };

    const handlePipelineExecutionTypeChange = (e: any) => {
        setFormState((prev) => ({
            ...prev,
            pipelineExecutionType: e.detail.selectedOption,
        }));
    };

    const handleWaitForCallbackChange = (e: any) => {
        setFormState((prev) => ({
            ...prev,
            waitForCallback: e.detail.selectedOption,
        }));
    };

    const handleDatabaseChange = (e: any) => {
        setFormState((prev) => ({
            ...prev,
            databaseId: e.detail.selectedOption,
        }));
    };

    // eslint-disable-next-line no-mixed-operators
    const createOrUpdate = (initState && initState.pipelineId && "Update") || "Create";
    const [inProgress, setInProgress] = useState(false);
    const [formError, setFormError] = useState("");
    const [openWorkflowModal, setOpenWorkflowModal] = useState(false);
    const [radioValue, setRadioValue] = useState("yes");
    const [pipeline, setPipeline] = useState<PipelineFields>({
        pipelineId: "",
        databaseId: { label: "", value: "" },
        pipelineTypeSelected: pipelineTypeOptions[0],
        pipelineExecutionType: pipelineExecutionTypeOptions[0],
        waitForCallback: waitForCallbackOptions[0],
        taskTimeout: "",
        taskHeartbeatTimeout: "",
        lambdaName: "",
        description: "",
        assetType: initState?.assetType || ".all",
        outputType: initState?.outputType || ".all",
        inputParameters: "",
        ...initState,
    });

    return (
        <div>
            <Modal
                onDismiss={() => {
                    setOpen(false);
                    setFormState({
                        pipelineId: "",
                        databaseId: { label: "", value: "" },
                        pipelineTypeSelected: pipelineTypeOptions[0],
                        pipelineExecutionType: pipelineExecutionTypeOptions[0],
                        waitForCallback: waitForCallbackOptions[0],
                        taskTimeout: "",
                        taskHeartbeatTimeout: "",
                        lambdaName: "",
                        description: "",
                        assetType: ".all",
                        outputType: ".all",
                        inputParameters: "",
                        ...initState,
                    });
                    setFormError("");
                }}
                visible={open}
                closeAriaLabel="Close modal"
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="link"
                                onClick={() => {
                                    setOpen(false);
                                    setFormState({
                                        pipelineId: "",
                                        databaseId: { label: "", value: "" },
                                        pipelineTypeSelected: pipelineTypeOptions[0],
                                        pipelineExecutionType: pipelineExecutionTypeOptions[0],
                                        waitForCallback: waitForCallbackOptions[0],
                                        taskTimeout: "",
                                        taskHeartbeatTimeout: "",
                                        lambdaName: "",
                                        description: "",
                                        assetType: ".all",
                                        outputType: ".all",
                                        inputParameters: "",
                                    });
                                    setFormError("");
                                }}
                            >
                                Cancel
                            </Button>
                            <Button
                                variant="primary"
                                onClick={() => {
                                    if (createOrUpdate == "Create") {
                                        setInProgress(true);
                                        API.put("api", `pipelines`, {
                                            body: {
                                                pipelineId: formState.pipelineId,
                                                databaseId: formState.databaseId.value,
                                                pipelineType: formState.pipelineTypeSelected.value,
                                                pipelineExecutionType:
                                                    formState.pipelineExecutionType.value,
                                                waitForCallback: formState.waitForCallback.value,
                                                taskTimeout: formState.taskTimeout,
                                                taskHeartbeatTimeout:
                                                    formState.taskHeartbeatTimeout,
                                                lambdaName: formState.lambdaName,
                                                description: formState.description,
                                                assetType: formState.assetType,
                                                outputType: formState.outputType,
                                                inputParameters: formState.inputParameters,
                                                updateAssociatedWorkflows: false,
                                            },
                                        })
                                            .then((res) => {
                                                console.log("Create/Update pipeline: ", res);
                                                setReload(true);
                                                setOpen(false);
                                            })
                                            .catch((err) => {
                                                console.log("create pipeline error", err);
                                                let msg = `Unable to ${createOrUpdate} pipeline. Error: Request failed with status code ${err.response.status}`;
                                                setFormError(msg);
                                            })
                                            .finally(() => {
                                                setInProgress(false);
                                            });
                                    } else {
                                        setPipeline(formState);
                                        setOpenWorkflowModal(true);
                                        setOpen(false);
                                    }
                                }}
                                disabled={
                                    inProgress ||
                                    !(
                                        validatePipelineName(formState.pipelineId) === null &&
                                        validatePipelineDescriptionLength(formState.description) ===
                                            null &&
                                        formState.assetType.trim() !== "" &&
                                        formState.outputType.trim() !== ""
                                    )
                                }
                                data-testid={`${createOrUpdate}-pipeline-button`}
                            >
                                {createOrUpdate} Pipeline
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
                header={`${createOrUpdate} Pipeline`}
            >
                <form onSubmit={(e) => e.preventDefault()}>
                    <Form errorText={formError}>
                        <SpaceBetween direction="vertical" size="s">
                            <FormField
                                label="Pipeline Name"
                                errorText={validatePipelineName(formState.pipelineId)}
                                constraintText="Required. No special chars or spaces except - and _ min 3 and max 64"
                            >
                                <Input
                                    value={formState.pipelineId}
                                    disabled={
                                        inProgress ||
                                        (initState && initState.pipelineId && true) ||
                                        false
                                    }
                                    onChange={({ detail }) =>
                                        setFormState({ ...formState, pipelineId: detail.value })
                                    }
                                    placeholder="Pipeline Name"
                                    data-testid="pipeline-name"
                                />
                            </FormField>
                            <FormField label="Database Name">
                                <DatabaseSelector
                                    disabled={
                                        inProgress ||
                                        (initState && initState.databaseId && true) ||
                                        false
                                    }
                                    selectedOption={formState.databaseId}
                                    onChange={handleDatabaseChange}
                                    showGlobal={true}
                                />
                            </FormField>
                            <FormField label="Pipeline Type">
                                <Select
                                    controlId="pipelineType"
                                    options={pipelineTypeOptions}
                                    selectedOption={formState.pipelineTypeSelected}
                                    onChange={handlePipelineTypeChange}
                                    filteringType="auto"
                                    selectedAriaLabel="Selected"
                                    data-testid="pipelineType"
                                    disabled={
                                        inProgress ||
                                        (initState && initState.pipelineType && true) ||
                                        false
                                    }
                                />
                            </FormField>
                            <FormField label="Pipeline Execution Type">
                                <Select
                                    options={pipelineExecutionTypeOptions}
                                    selectedOption={formState.pipelineExecutionType}
                                    onChange={handlePipelineExecutionTypeChange}
                                    filteringType="auto"
                                    selectedAriaLabel="Selected"
                                    data-testid="pipelineExecutionType"
                                    disabled={
                                        inProgress ||
                                        (initState && initState.pipelineExecutionType && true) ||
                                        false
                                    }
                                />
                            </FormField>
                            <FormField label="Wait for a Callback with the Task Token">
                                <Select
                                    options={waitForCallbackOptions}
                                    selectedOption={formState.waitForCallback}
                                    onChange={handleWaitForCallbackChange}
                                    filteringType="auto"
                                    selectedAriaLabel="Selected"
                                    data-testid="waitForCallback-select"
                                    disabled={
                                        inProgress ||
                                        (initState && initState.waitForCallback && true) ||
                                        false
                                    }
                                />
                            </FormField>
                            <FormField
                                label="Task Timeout"
                                constraintText="If the task runs longer than the specified seconds, this state fails with a States.Timeout error name. Must be a positive, non-zero integer."
                            >
                                <Input
                                    value={formState.taskTimeout}
                                    onChange={({ detail }) =>
                                        setFormState({ ...formState, taskTimeout: detail.value })
                                    }
                                    autoFocus={false}
                                    type="number"
                                    inputMode="numeric"
                                    placeholder="86400"
                                    data-testid="task-timeout"
                                />
                            </FormField>
                            <FormField
                                label="Task Heartbeat Timeout"
                                constraintText="If more time than the specified seconds elapses between heartbeats from the task, this state fails with a States.Timeout error name. Must be a positive, non-zero integer less than the number of seconds specified in the TimeoutSeconds field."
                            >
                                <Input
                                    value={formState.taskHeartbeatTimeout}
                                    onChange={({ detail }) =>
                                        setFormState({
                                            ...formState,
                                            taskHeartbeatTimeout: detail.value,
                                        })
                                    }
                                    autoFocus={false}
                                    type="number"
                                    inputMode="numeric"
                                    placeholder="3600"
                                    data-testid="task-heartbeat-timeout"
                                />
                            </FormField>
                            <FormField
                                label="Lambda Function Name (Optional)"
                                constraintText="If no name is provided a template lambda function will be deployed on your behalf."
                            >
                                <Input
                                    value={formState.lambdaName}
                                    disabled={
                                        inProgress ||
                                        (initState && initState.lambdaName && true) ||
                                        false
                                    }
                                    onChange={({ detail }) =>
                                        setFormState({ ...formState, lambdaName: detail.value })
                                    }
                                    autoFocus={false}
                                    data-testid="task-heartbeat-timeout"
                                />
                            </FormField>
                            <FormField
                                label="Pipeline Description"
                                constraintText="Required. Max 256 characters."
                                errorText={validatePipelineDescriptionLength(formState.description)}
                            >
                                <Textarea
                                    value={formState.description}
                                    disabled={inProgress}
                                    onChange={({ detail }) =>
                                        setFormState({ ...formState, description: detail.value })
                                    }
                                    rows={4}
                                    placeholder="Pipeline Description"
                                    data-testid="pipeline-desc"
                                />
                            </FormField>
                            <FormField
                                label="Asset Type"
                                constraintText="Required. Specify the asset type (e.g., .all, .jpg, .png). The pipeline itself determines how this field is used. Does not restrict pipeline use as part of VAMS execution."
                                errorText={
                                    formState.assetType.trim() === ""
                                        ? "Asset Type is required"
                                        : null
                                }
                            >
                                <Input
                                    value={formState.assetType}
                                    disabled={inProgress}
                                    onChange={({ detail }) =>
                                        setFormState({ ...formState, assetType: detail.value })
                                    }
                                    placeholder=".all"
                                    data-testid="inputFileType-input"
                                />
                            </FormField>
                            <FormField
                                label="Output Type"
                                constraintText="Required. Specify the output type (e.g., .all, .jpg, .png). The pipeline itself determines how this field is used."
                                errorText={
                                    formState.outputType.trim() === ""
                                        ? "Output Type is required"
                                        : null
                                }
                            >
                                <Input
                                    value={formState.outputType}
                                    disabled={inProgress}
                                    onChange={({ detail }) =>
                                        setFormState({ ...formState, outputType: detail.value })
                                    }
                                    placeholder=".all"
                                    data-testid="outputFileType-input"
                                />
                            </FormField>
                            <FormField label="Input Parameters (Optional)">
                                <Textarea
                                    value={formState.inputParameters}
                                    disabled={inProgress}
                                    onChange={({ detail }) =>
                                        setFormState({
                                            ...formState,
                                            inputParameters: detail.value,
                                        })
                                    }
                                    rows={4}
                                    placeholder="Input Parameters (Optional)"
                                    data-testid="inputParameters"
                                />
                            </FormField>
                        </SpaceBetween>
                    </Form>
                </form>
            </Modal>
            <Modal
                onDismiss={() => {
                    setOpenWorkflowModal(false);
                    setReload(true);
                }}
                visible={openWorkflowModal}
                closeAriaLabel="Close modal"
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="primary"
                                onClick={() => {
                                    if (radioValue == "yes") {
                                        setInProgress(true);
                                        API.put("api", `pipelines`, {
                                            body: {
                                                pipelineId: pipeline.pipelineId,
                                                databaseId: pipeline.databaseId.value,
                                                pipelineType: pipeline.pipelineTypeSelected.value,
                                                pipelineExecutionType:
                                                    pipeline.pipelineExecutionType.value,
                                                waitForCallback: pipeline.waitForCallback.value,
                                                taskTimeout: pipeline.taskTimeout,
                                                taskHeartbeatTimeout: pipeline.taskHeartbeatTimeout,
                                                lambdaName: pipeline.lambdaName,
                                                description: pipeline.description,
                                                assetType: pipeline.assetType,
                                                outputType: pipeline.outputType,
                                                inputParameters: pipeline.inputParameters,
                                                updateAssociatedWorkflows: true,
                                            },
                                        })
                                            .then((res) => {
                                                console.log(
                                                    "Update pipeline and associated workflows: ",
                                                    res
                                                );
                                                setOpenWorkflowModal(false);
                                                setReload(true);
                                            })
                                            .catch((err) => {
                                                console.log("update workflow error", err);
                                                let msg = `Unable to update workflow. Error: Request failed with status code ${err.response.status}`;
                                                setFormError(msg);
                                            })
                                            .finally(() => {
                                                setInProgress(false);
                                            });
                                    } else {
                                        setInProgress(true);
                                        API.put("api", `pipelines`, {
                                            body: {
                                                pipelineId: pipeline.pipelineId,
                                                databaseId: pipeline.databaseId.value,
                                                pipelineType: pipeline.pipelineTypeSelected.value,
                                                pipelineExecutionType:
                                                    pipeline.pipelineExecutionType.value,
                                                waitForCallback: pipeline.waitForCallback.value,
                                                taskTimeout: pipeline.taskTimeout,
                                                taskHeartbeatTimeout: pipeline.taskHeartbeatTimeout,
                                                lambdaName: pipeline.lambdaName,
                                                description: pipeline.description,
                                                assetType: pipeline.assetType,
                                                outputType: pipeline.outputType,
                                                inputParameters: pipeline.inputParameters,
                                                updateAssociatedWorkflows: false,
                                            },
                                        })
                                            .then((res) => {
                                                console.log("Update pipeline: ", res);
                                                setReload(true);
                                                setOpenWorkflowModal(false);
                                            })
                                            .catch((err) => {
                                                console.log("create pipeline error", err);
                                                let msg = `Unable to ${createOrUpdate} pipeline. Error: Request failed with status code ${err.response.status}`;
                                                setFormError(msg);
                                            })
                                            .finally(() => {
                                                setInProgress(false);
                                            });
                                    }
                                }}
                                data-testid={`update-workflow-button`}
                            >
                                Submit
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
                header={`Update Workflows`}
            >
                <form onSubmit={(e) => e.preventDefault()}>
                    <Form errorText={formError}>
                        <SpaceBetween direction="vertical" size="s">
                            <FormField label="Would you like to update all workflows associated with this pipeline?">
                                <RadioGroup
                                    onChange={({ detail }) => setRadioValue(detail.value)}
                                    value={radioValue}
                                    items={[
                                        { value: "yes", label: "Yes" },
                                        { value: "no", label: "No" },
                                    ]}
                                />
                            </FormField>
                        </SpaceBetween>
                    </Form>
                </form>
            </Modal>
        </div>
    );
}
