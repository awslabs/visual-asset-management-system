/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Form from "@cloudscape-design/components/form";
import FormField from "@cloudscape-design/components/form-field";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Alert from "@cloudscape-design/components/alert";
import DatePicker from "@cloudscape-design/components/date-picker";
import TimeInput from "@cloudscape-design/components/time-input";
import { createApiKey } from "../../services/APIService";

interface CreateApiKeyProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
}

interface FormState {
    name: string;
    userId: string;
    description: string;
    expiresDate: string;
    expiresTime: string;
}

const initialFormState: FormState = {
    name: "",
    userId: "",
    description: "",
    expiresDate: "",
    expiresTime: "23:59:59",
};

export default function CreateApiKey({ open, setOpen, setReload }: CreateApiKeyProps) {
    const [inProgress, setInProgress] = useState(false);
    const [formError, setFormError] = useState("");
    const [formState, setFormState] = useState<FormState>({ ...initialFormState });
    const [createdKeyValue, setCreatedKeyValue] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        if (open) {
            setFormState({ ...initialFormState });
            setFormError("");
            setCreatedKeyValue(null);
            setCopied(false);
        }
    }, [open]);

    const handleSubmit = async () => {
        setInProgress(true);
        setFormError("");

        try {
            const body: any = {
                apiKeyName: formState.name,
                userId: formState.userId,
            };
            if (formState.description) {
                body.description = formState.description;
            }
            if (formState.expiresDate) {
                const time = formState.expiresTime || "23:59:59";
                body.expiresAt = `${formState.expiresDate}T${time}Z`;
            }

            const response = await createApiKey(body);

            if (response && response[0] === true) {
                const data = response[1];
                // The API should return the key value in the response
                const keyValue = data?.apiKey || data?.apiKeyValue || data?.key || "";
                setCreatedKeyValue(keyValue);
                setReload(true);
            } else {
                const errorMessage =
                    response && response[1] ? response[1] : "Failed to create API key";
                setFormError(errorMessage);
            }
        } catch (error: any) {
            console.log("Error:", error);
            const errorMessage =
                error?.response?.data?.message || error?.message || "An error occurred";
            setFormError(errorMessage);
        } finally {
            setInProgress(false);
        }
    };

    const handleCopy = () => {
        if (createdKeyValue) {
            navigator.clipboard.writeText(createdKeyValue).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
            });
        }
    };

    const handleClose = () => {
        setOpen(false);
        setFormState({ ...initialFormState });
        setFormError("");
        setCreatedKeyValue(null);
        setCopied(false);
    };

    const isFormValid = () => {
        return (
            formState.name.trim().length >= 1 &&
            formState.userId.trim().length >= 1 &&
            formState.description.trim().length >= 1
        );
    };

    // If the key was created, show the key display view
    if (createdKeyValue !== null) {
        return (
            <Modal
                visible={open}
                onDismiss={handleClose}
                size="large"
                header="API Key Created"
                footer={
                    <Box float="right">
                        <Button variant="primary" onClick={handleClose}>
                            Done
                        </Button>
                    </Box>
                }
            >
                <SpaceBetween direction="vertical" size="l">
                    <Alert type="warning">Save this key now. It will not be shown again.</Alert>
                    <Box>
                        <Box variant="awsui-key-label" margin={{ bottom: "xxs" }}>
                            API Key
                        </Box>
                        <div
                            style={{
                                fontFamily: "monospace",
                                fontSize: "14px",
                                padding: "12px 16px",
                                backgroundColor: "#f2f3f3",
                                border: "1px solid #d5dbdb",
                                borderRadius: "4px",
                                wordBreak: "break-all",
                                userSelect: "all",
                            }}
                        >
                            {createdKeyValue}
                        </div>
                        <Box margin={{ top: "s" }}>
                            <Button onClick={handleCopy} iconName="copy">
                                {copied ? "Copied!" : "Copy to Clipboard"}
                            </Button>
                        </Box>
                    </Box>
                </SpaceBetween>
            </Modal>
        );
    }

    return (
        <Modal
            visible={open}
            onDismiss={handleClose}
            size="large"
            header="Create API Key"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={handleClose}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleSubmit}
                            disabled={inProgress || !isFormValid()}
                            data-testid="create-api-key-button"
                        >
                            Create API Key
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={formError}>
                <SpaceBetween direction="vertical" size="l">
                    <FormField
                        label="Name"
                        constraintText="Required. Enter a name for the API key."
                    >
                        <Input
                            value={formState.name}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, name: detail.value });
                            }}
                            placeholder="Enter API key name"
                            data-testid="api-key-name"
                        />
                    </FormField>
                    <FormField
                        label="User ID"
                        constraintText="Required. Enter the user ID this key will be associated with."
                    >
                        <Input
                            value={formState.userId}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, userId: detail.value });
                            }}
                            placeholder="Enter User ID"
                            data-testid="api-key-userId"
                        />
                    </FormField>
                    <FormField
                        label="Description"
                        constraintText="Required. Enter a description for the API key."
                    >
                        <Textarea
                            value={formState.description}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, description: detail.value });
                            }}
                            placeholder="Enter description"
                            data-testid="api-key-description"
                        />
                    </FormField>
                    <FormField
                        label="Expiration Date"
                        constraintText="Optional. Select an expiration date for the API key."
                    >
                        <DatePicker
                            value={formState.expiresDate}
                            onChange={({ detail }) => {
                                setFormState({
                                    ...formState,
                                    expiresDate: detail.value,
                                    expiresTime: detail.value
                                        ? formState.expiresTime || "23:59:59"
                                        : "23:59:59",
                                });
                            }}
                            placeholder="YYYY/MM/DD"
                            data-testid="api-key-expiresDate"
                        />
                    </FormField>
                    <FormField
                        label="Expiration Time (UTC)"
                        constraintText="Optional. Set the time of expiration (defaults to 23:59:59 UTC)."
                    >
                        <TimeInput
                            value={formState.expiresDate ? formState.expiresTime : ""}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, expiresTime: detail.value });
                            }}
                            disabled={!formState.expiresDate}
                            format="hh:mm:ss"
                            placeholder="HH:mm:ss"
                            data-testid="api-key-expiresTime"
                        />
                    </FormField>
                </SpaceBetween>
            </Form>
        </Modal>
    );
}
