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
import Textarea from "@cloudscape-design/components/textarea";
import Toggle from "@cloudscape-design/components/toggle";
import DatePicker from "@cloudscape-design/components/date-picker";
import TimeInput from "@cloudscape-design/components/time-input";
import { updateApiKey } from "../../services/APIService";

interface UpdateApiKeyProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    apiKey: any;
}

interface FormState {
    description: string;
    expiresDate: string;
    expiresTime: string;
    isActive: boolean;
}

export default function UpdateApiKey({ open, setOpen, setReload, apiKey }: UpdateApiKeyProps) {
    const [inProgress, setInProgress] = useState(false);
    const [formError, setFormError] = useState("");
    const [descriptionError, setDescriptionError] = useState("");
    const [formState, setFormState] = useState<FormState>({
        description: "",
        expiresDate: "",
        expiresTime: "23:59:59",
        isActive: true,
    });

    useEffect(() => {
        if (open && apiKey) {
            const expiresAt = apiKey.expiresAt || "";
            let expiresDate = "";
            let expiresTime = "23:59:59";
            if (expiresAt) {
                const parts = expiresAt.split("T");
                expiresDate = parts[0] || "";
                if (parts[1]) {
                    expiresTime =
                        parts[1].replace("Z", "").replace(/\+.*$/, "").substring(0, 8) ||
                        "23:59:59";
                }
            }
            setFormState({
                description: apiKey.description || "",
                expiresDate: expiresDate,
                expiresTime: expiresTime,
                isActive: apiKey.isActive === "true",
            });
            setFormError("");
        }
    }, [open, apiKey]);

    const handleSubmit = async () => {
        setInProgress(true);
        setFormError("");

        try {
            const body: any = {
                apiKeyId: apiKey.apiKeyId,
            };
            if (formState.description !== undefined) {
                body.description = formState.description;
            }
            if (formState.expiresDate) {
                const time = formState.expiresTime || "23:59:59";
                body.expiresAt = `${formState.expiresDate}T${time}Z`;
            } else {
                // Send empty string to clear expiration
                body.expiresAt = "";
            }
            body.isActive = formState.isActive ? "true" : "false";

            const response = await updateApiKey(body);

            if (response && response[0] === true) {
                setOpen(false);
                setReload(true);
            } else {
                const errorMessage =
                    response && response[1] ? response[1] : "Failed to update API key";
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

    const handleClose = () => {
        setOpen(false);
        setFormError("");
    };

    return (
        <Modal
            visible={open}
            onDismiss={handleClose}
            size="large"
            header="Update API Key"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={handleClose}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleSubmit}
                            disabled={inProgress || formState.description.length > 256}
                            data-testid="update-api-key-button"
                        >
                            Update API Key
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={formError}>
                <SpaceBetween direction="vertical" size="l">
                    <FormField label="Name">
                        <Box>{apiKey?.apiKeyName || "-"}</Box>
                    </FormField>
                    <FormField label="Key ID">
                        <Box>{apiKey?.apiKeyId || "-"}</Box>
                    </FormField>
                    <FormField
                        label="Description"
                        constraintText="Optional. Max 256 characters."
                        errorText={descriptionError}
                    >
                        <Textarea
                            value={formState.description}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, description: detail.value });
                                if (detail.value.length > 256) {
                                    setDescriptionError(
                                        "Description must be 256 characters or less"
                                    );
                                } else {
                                    setDescriptionError("");
                                }
                            }}
                            placeholder="Enter description"
                            data-testid="update-api-key-description"
                        />
                    </FormField>
                    <FormField
                        label="Expiration Date"
                        constraintText="Optional. Update the expiration date for this API key. Clear to remove expiration."
                    >
                        <SpaceBetween direction="horizontal" size="xs">
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
                                data-testid="update-api-key-expiresDate"
                            />
                            {formState.expiresDate && (
                                <Button
                                    onClick={() =>
                                        setFormState({
                                            ...formState,
                                            expiresDate: "",
                                            expiresTime: "23:59:59",
                                        })
                                    }
                                    variant="normal"
                                >
                                    Clear
                                </Button>
                            )}
                        </SpaceBetween>
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
                            data-testid="update-api-key-expiresTime"
                        />
                    </FormField>
                    <FormField label="Active">
                        <Toggle
                            onChange={({ detail }) => {
                                setFormState({ ...formState, isActive: detail.checked });
                            }}
                            checked={formState.isActive}
                            data-testid="update-api-key-active"
                        >
                            {formState.isActive ? "Active" : "Inactive"}
                        </Toggle>
                    </FormField>
                </SpaceBetween>
            </Form>
        </Modal>
    );
}
