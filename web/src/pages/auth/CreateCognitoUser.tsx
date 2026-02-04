/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Box,
    Button,
    Form,
    FormField,
    Modal,
    SpaceBetween,
    Input,
} from "@cloudscape-design/components";
import { useState, useEffect } from "react";
import { createCognitoUser, updateCognitoUser } from "../../services/APIService";

interface CognitoUserFields {
    userId: string;
    email: string;
    phone: string;
}

interface CreateCognitoUserProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    initState: any;
}

function validateEmail(email: string): string | null {
    if (typeof email !== "string" || email.trim().length === 0) {
        return "Required. Please enter an email address.";
    }

    // RFC 5322 simplified email validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
        return "Please enter a valid email address.";
    }

    return null;
}

function validateUserId(userId: string): string | null {
    if (typeof userId !== "string" || userId.trim().length === 0) {
        return "Required. Please enter a User ID.";
    }

    // Valid user regex: at least 3 characters alphanumeric with support for special characters: . + - @
    const userIdRegex = /^[\w\-\.\+\@]{3,256}$/;
    if (!userIdRegex.test(userId)) {
        return "User ID should be at least 3 characters alphanumeric with support for special characters: . + - @";
    }

    return null;
}

function validatePhoneNumber(phoneNumber: string): string | null {
    if (!phoneNumber || phoneNumber.trim().length === 0) {
        return null; // Phone number is optional
    }

    // E.164 format validation: +[country code][number]
    const phoneRegex = /^\+[1-9]\d{1,14}$/;
    if (!phoneRegex.test(phoneNumber)) {
        return "Phone number must be in E.164 format (e.g., +12345678900)";
    }

    return null;
}

export default function CreateCognitoUser({
    open,
    setOpen,
    setReload,
    initState,
}: CreateCognitoUserProps) {
    const [inProgress, setInProgress] = useState(false);
    const [formError, setFormError] = useState("");
    const [userIdError, setUserIdError] = useState<string | null>(null);
    const [emailError, setEmailError] = useState<string | null>(null);
    const [phoneError, setPhoneError] = useState<string | null>(null);
    const createOrUpdate = (initState && "Update") || "Create";
    const [formState, setFormState] = useState<CognitoUserFields>({
        userId: initState?.userId || "",
        email: initState?.email || "",
        phone: initState?.phone || "",
    });

    // Update form state when initState changes (e.g., after data reload)
    useEffect(() => {
        if (open) {
            setFormState({
                userId: initState?.userId || "",
                email: initState?.email || "",
                phone: initState?.phone || "",
            });
            // Reset errors when opening
            setUserIdError(null);
            setEmailError(null);
            setPhoneError(null);
            setFormError("");
        }
    }, [open, initState]);

    const handleSubmit = async () => {
        setInProgress(true);
        setFormError("");

        try {
            let response;
            if (createOrUpdate === "Create") {
                const params: any = {
                    userId: formState.userId,
                    email: formState.email,
                };
                if (formState.phone) {
                    params.phone = formState.phone;
                }
                response = await createCognitoUser(params);
            } else {
                const params: any = {
                    userId: formState.userId,
                };
                if (formState.email) {
                    params.email = formState.email;
                }
                if (formState.phone) {
                    params.phone = formState.phone;
                }
                response = await updateCognitoUser(params);
            }

            if (response && response[0]) {
                setOpen(false);
                setReload(true);
                setFormState({
                    userId: "",
                    email: "",
                    phone: "",
                });
                setUserIdError(null);
                setEmailError(null);
                setPhoneError(null);
            } else {
                const errorMessage = response ? response[1] : "Unknown error occurred";

                // Check for specific error types
                if (errorMessage.toLowerCase().includes("already exists")) {
                    setUserIdError("A user with this User ID already exists");
                } else if (errorMessage.toLowerCase().includes("invalid email")) {
                    setEmailError("Invalid email address");
                } else if (errorMessage.toLowerCase().includes("invalid phone")) {
                    setPhoneError("Invalid phone number");
                } else {
                    setFormError(errorMessage);
                }
            }
        } catch (error: any) {
            console.log("Error:", error);
            // Extract the actual error message from the API response
            const errorMessage =
                error?.response?.data?.message || error?.message || "An error occurred";
            setFormError(errorMessage);
        } finally {
            setInProgress(false);
        }
    };

    const isFormValid = () => {
        // Email is always required for both Create and Update
        return (
            validateEmail(formState.email) === null &&
            validatePhoneNumber(formState.phone) === null &&
            (createOrUpdate === "Create" ? validateUserId(formState.userId) === null : true)
        );
    };

    return (
        <Modal
            visible={open}
            onDismiss={() => {
                setOpen(false);
                setFormState({
                    userId: "",
                    email: "",
                    phone: "",
                });
                setUserIdError(null);
                setEmailError(null);
                setPhoneError(null);
                setFormError("");
            }}
            size="large"
            header={`${createOrUpdate} Cognito User`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            variant="link"
                            onClick={() => {
                                setOpen(false);
                                setFormState({
                                    userId: "",
                                    email: "",
                                    phone: "",
                                });
                                setUserIdError(null);
                                setEmailError(null);
                                setPhoneError(null);
                                setFormError("");
                            }}
                        >
                            Cancel
                        </Button>

                        <Button
                            variant="primary"
                            onClick={handleSubmit}
                            disabled={inProgress || !isFormValid()}
                            data-testid={`${createOrUpdate}-cognito-user-button`}
                        >
                            {createOrUpdate} Cognito User
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={formError}>
                <SpaceBetween direction="vertical" size="l">
                    <FormField
                        label="User ID"
                        constraintText="Required. Enter user ID (3-256 characters, alphanumeric with . + - @)"
                        errorText={userIdError}
                    >
                        <Input
                            value={formState.userId}
                            disabled={createOrUpdate === "Update"}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, userId: detail.value });
                                setUserIdError(validateUserId(detail.value));
                            }}
                            placeholder="Enter User ID"
                            data-testid="userId"
                        />
                    </FormField>
                    <FormField
                        label="Email"
                        constraintText="Required. Enter email address"
                        errorText={emailError}
                    >
                        <Input
                            value={formState.email}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, email: detail.value });
                                setEmailError(validateEmail(detail.value));
                            }}
                            placeholder="user@example.com"
                            data-testid="email"
                            type="email"
                        />
                    </FormField>
                    <FormField
                        label="Phone Number"
                        constraintText="Optional. Enter phone number in E.164 format (e.g., +12345678900)"
                        errorText={phoneError}
                    >
                        <Input
                            value={formState.phone}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, phone: detail.value });
                                setPhoneError(validatePhoneNumber(detail.value));
                            }}
                            placeholder="+12345678900"
                            data-testid="phoneNumber"
                        />
                    </FormField>
                    {createOrUpdate === "Create" && (
                        <Box color="text-status-info" fontSize="body-s">
                            <strong>Note:</strong> A temporary password will be generated and sent
                            to the user's email address. The user will be required to change their
                            password on first login.
                        </Box>
                    )}
                </SpaceBetween>
            </Form>
        </Modal>
    );
}
