/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Modal from "@cloudscape-design/components/modal";
import RadioGroup from "@cloudscape-design/components/radio-group";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { collectExport, downloadBlob, ExportFormat } from "./auditExport";

export interface AuditExportDialogProps {
    visible: boolean;
    onDismiss: () => void;
    queryId: string | null;
    /** The number of records the export holds, when the server said; shown for confirmation. */
    rowCount?: number;
    /** Whether the search found all it will find (an export of a running search is refused). */
    finished: boolean;
}

/**
 * Asks for a format, says how many records the file will hold, then collects the export chunk by
 * chunk and downloads it. The file is the server's: the page does not rewrite any cell.
 */
export const AuditExportDialog: React.FC<AuditExportDialogProps> = ({
    visible,
    onDismiss,
    queryId,
    rowCount,
    finished,
}) => {
    const [format, setFormat] = useState<ExportFormat>("csv");
    const [working, setWorking] = useState(false);
    const [chunks, setChunks] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const cancelled = useRef(false);

    const close = () => {
        cancelled.current = true;
        setWorking(false);
        setError(null);
        setChunks(0);
        onDismiss();
    };

    const start = async () => {
        if (!queryId) return;
        cancelled.current = false;
        setWorking(true);
        setError(null);
        setChunks(0);
        const result = await collectExport({
            queryId,
            format,
            isCancelled: () => cancelled.current,
            onProgress: setChunks,
        });
        if (cancelled.current) return;
        setWorking(false);
        if (result.ok) {
            downloadBlob(result.blob, result.fileName);
            onDismiss();
        } else {
            setError(
                result.status === 404
                    ? "These results expired. Run the search again."
                    : result.status === 403
                    ? "You do not have access to audit logs."
                    : result.message
            );
        }
    };

    return (
        <Modal
            visible={visible}
            onDismiss={close}
            header="Export audit records"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={close}>
                            {working ? "Cancel export" : "Close"}
                        </Button>
                        <Button
                            variant="primary"
                            onClick={start}
                            loading={working}
                            loadingText="Exporting"
                            disabled={!finished || !queryId}
                        >
                            Export
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween size="m">
                <Box variant="p">
                    {typeof rowCount === "number"
                        ? `The file holds ${rowCount} record${
                              rowCount === 1 ? "" : "s"
                          }: every record this search found, not only the page shown.`
                        : "The file holds every record this search found, not only the page shown."}
                </Box>
                {!finished && (
                    <Alert type="info">Wait for the search to finish before exporting.</Alert>
                )}
                <RadioGroup
                    value={format}
                    onChange={({ detail }) => setFormat(detail.value as ExportFormat)}
                    items={[
                        {
                            value: "csv",
                            label: "CSV",
                            description: "One row per record; opens in a spreadsheet.",
                        },
                        {
                            value: "json",
                            label: "JSON",
                            description: "A list of records with all their fields.",
                        },
                    ]}
                />
                {working && (
                    <Box color="text-status-info" aria-live="polite">
                        Collecting the export
                        {chunks > 0 ? ` (${chunks} part${chunks === 1 ? "" : "s"} received)` : ""}.
                    </Box>
                )}
                {error && (
                    <Alert type="error" header="The export failed">
                        {error}
                    </Alert>
                )}
            </SpaceBetween>
        </Modal>
    );
};

export default AuditExportDialog;
