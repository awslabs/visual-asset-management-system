/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Synonyms from "../../synonyms";

export interface RevokeExceptionModalProps {
    visible: boolean;
    /** Shows the Revoke button as busy and disables both buttons while the revocation runs. */
    revoking: boolean;
    onConfirm: () => void;
    onDismiss: () => void;
}

/**
 * The confirmation before an active compliance exception is revoked, shared by the asset
 * Compliance tab and the database compliance overview so both say the same thing.
 */
const RevokeExceptionModal: React.FC<RevokeExceptionModalProps> = ({
    visible,
    revoking,
    onConfirm,
    onDismiss,
}) => (
    <Modal
        visible={visible}
        onDismiss={onDismiss}
        header="Revoke compliance exception"
        closeAriaLabel="Close dialog"
        footer={
            <Box float="right">
                <SpaceBetween direction="horizontal" size="xs">
                    <Button variant="link" onClick={onDismiss} disabled={revoking}>
                        Cancel
                    </Button>
                    <Button variant="primary" onClick={onConfirm} loading={revoking}>
                        Revoke
                    </Button>
                </SpaceBetween>
            </Box>
        }
    >
        The {Synonyms.asset} returns to the state of its last evaluation. When that evaluation
        quarantined it, it is quarantined again. The revocation is recorded in the compliance audit
        log.
    </Modal>
);

export default RevokeExceptionModal;
