/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Textarea from "@cloudscape-design/components/textarea";

export const REASON_REQUIRED_MESSAGE = "A reason is required.";

export interface ReasonModalProps {
    visible: boolean;
    /** Dialog title, e.g. "Reject cascade". */
    header: string;
    /** Label of the reason field, e.g. "Reason for rejection". */
    label: string;
    /** Help text under the field label. */
    description?: string;
    /** Label of the confirming button, e.g. "Reject". */
    confirmLabel: string;
    /** Placeholder shown in the empty field. */
    placeholder?: string;
    /** Disables the buttons and shows the confirm button as busy while the action runs. */
    loading?: boolean;
    /** Called with the trimmed, non-empty reason. */
    onConfirm: (reason: string) => void;
    onDismiss: () => void;
}

/**
 * Collects a mandatory free-text reason for a compliance action (rejecting a cascade, granting a
 * quarantine exception). Confirming with an empty field keeps the dialog open and flags the field.
 */
const ReasonModal: React.FC<ReasonModalProps> = ({
    visible,
    header,
    label,
    description,
    confirmLabel,
    placeholder,
    loading = false,
    onConfirm,
    onDismiss,
}) => {
    const [reason, setReason] = useState("");
    const [errorText, setErrorText] = useState<string | undefined>(undefined);

    // Each opening starts from an empty, unflagged field.
    useEffect(() => {
        if (visible) {
            setReason("");
            setErrorText(undefined);
        }
    }, [visible]);

    const handleConfirm = () => {
        const trimmed = reason.trim();
        if (!trimmed) {
            setErrorText(REASON_REQUIRED_MESSAGE);
            return;
        }
        onConfirm(trimmed);
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={header}
            closeAriaLabel="Close dialog"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss} disabled={loading}>
                            Cancel
                        </Button>
                        <Button variant="primary" onClick={handleConfirm} loading={loading}>
                            {confirmLabel}
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <FormField
                label={label}
                description={description}
                errorText={errorText}
                constraintText="Required."
                stretch
            >
                <Textarea
                    value={reason}
                    onChange={({ detail }) => {
                        setReason(detail.value);
                        if (errorText && detail.value.trim()) {
                            setErrorText(undefined);
                        }
                    }}
                    placeholder={placeholder}
                    ariaRequired
                    invalid={!!errorText}
                    rows={3}
                    autoFocus
                />
            </FormField>
        </Modal>
    );
};

export default ReasonModal;
