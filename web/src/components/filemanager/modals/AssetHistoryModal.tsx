/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import { AssetHistoryTable } from "../components/AssetHistoryTable";
import Synonyms from "../../../synonyms";

interface AssetHistoryModalProps {
    visible: boolean;
    onDismiss: () => void;
    databaseId: string;
    assetId: string;
    assetName?: string;
}

export const AssetHistoryModal: React.FC<AssetHistoryModalProps> = ({
    visible,
    onDismiss,
    databaseId,
    assetId,
    assetName,
}) => {
    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`${Synonyms.Asset} History${assetName ? ` - ${assetName}` : ""}`}
            size="max"
            footer={
                <Box float="right">
                    <Button variant="link" onClick={onDismiss}>
                        Close
                    </Button>
                </Box>
            }
        >
            <AssetHistoryTable databaseId={databaseId} assetId={assetId} visible={visible} />
        </Modal>
    );
};

export default AssetHistoryModal;
