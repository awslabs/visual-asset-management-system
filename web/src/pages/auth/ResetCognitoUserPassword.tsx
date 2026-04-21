/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Box, Button, Modal, SpaceBetween, Alert } from "@cloudscape-design/components";
import { useState } from "react";
import { resetCognitoUserPassword } from "../../services/APIService";

interface ResetCognitoUserPasswordProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: () => void;
    user: any;
}

export default function ResetCognitoUserPassword({
    open,
    setOpen,
    setReload,
    user,
}: ResetCognitoUserPasswordProps) {
    const [inProgress, setInProgress] = useState(false);
    const [error, setError] = useState("");
    const [success, setSuccess] = useState(false);

    const handleReset = async () => {
        setInProgress(true);
        setError("");
        setSuccess(false);

        try {
            const response = await resetCognitoUserPassword({ userId: user.userId });

            if (response && response[0]) {
                setSuccess(true);
                setTimeout(() => {
                    setOpen(false);
                    setReload();
                    setSuccess(false);
                }, 2000);
            } else {
                setError(response ? response[1] : "Failed to reset password");
            }
        } catch (err: any) {
            console.log("Error resetting password:", err);
            setError(err?.message || "An error occurred while resetting the password");
        } finally {
            setInProgress(false);
        }
    };

    return (
        <Modal
            visible={open}
            onDismiss={() => {
                setOpen(false);
                setError("");
                setSuccess(false);
            }}
            size="medium"
            header="Reset User Password"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            variant="link"
                            onClick={() => {
                                setOpen(false);
                                setError("");
                                setSuccess(false);
                            }}
                            disabled={inProgress}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleReset}
                            disabled={inProgress || success}
                            loading={inProgress}
                            data-testid="reset-password-confirm-button"
                        >
                            Reset Password
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="m">
                {success && (
                    <Alert type="success" dismissible={false}>
                        Password reset successfully! A temporary password has been sent to the
                        user's email address.
                    </Alert>
                )}
                {error && (
                    <Alert type="error" dismissible onDismiss={() => setError("")}>
                        {error}
                    </Alert>
                )}
                <Box>
                    <SpaceBetween direction="vertical" size="s">
                        <div>
                            <strong>User ID:</strong> {user.userId}
                        </div>
                        <div>
                            <strong>Email:</strong> {user.email}
                        </div>
                    </SpaceBetween>
                </Box>
                <Alert type="warning">
                    <SpaceBetween direction="vertical" size="xs">
                        <div>
                            <strong>Warning:</strong> This action will reset the user's password.
                        </div>
                        <ul style={{ marginTop: "8px", marginBottom: "0" }}>
                            <li>
                                A temporary password will be generated and sent to the user's email
                            </li>
                            <li>
                                The user will be required to change their password on next login
                            </li>
                            <li>The user's current password will no longer work</li>
                        </ul>
                    </SpaceBetween>
                </Alert>
            </SpaceBetween>
        </Modal>
    );
}
