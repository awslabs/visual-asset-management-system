/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { AuditRow } from "./auditSearchTypes";
import AuditValueTree from "./AuditValueTree";
import OutcomeIndicator from "./OutcomeIndicator";
import { auditTimeMs, clipText, hasContent, visibleText } from "./auditText";

/** A hash route to an asset, built from validated ids only. */
export const ID_FOR_LINK = /^[-_A-Za-z0-9]{3,64}$/;

export const assetHref = (databaseId?: string | null, assetId?: string | null): string | null =>
    databaseId && assetId && ID_FOR_LINK.test(databaseId) && ID_FOR_LINK.test(assetId)
        ? `#/databases/${encodeURIComponent(databaseId)}/assets/${encodeURIComponent(assetId)}`
        : null;

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
    <div>
        <Box variant="awsui-key-label">{label}</Box>
        <div style={{ overflowWrap: "anywhere" }}>{children}</div>
    </div>
);

const text = (value: unknown): string => clipText(visibleText(value));

const timeFields = (iso: string | null | undefined) => {
    if (!iso) return { local: "-", utc: "-" };
    const ms = auditTimeMs(iso);
    if (Number.isNaN(ms)) return { local: visibleText(iso), utc: "-" };
    const date = new Date(ms);
    return { local: date.toLocaleString(), utc: date.toISOString() };
};

export interface AuditDetailPanelProps {
    row: AuditRow;
    onClose: () => void;
    /** Starts a search for the records of this request ("records of this request"). */
    onShowRequest: (requestId: string) => void;
    /** Whether the caller may open the asset page (the allowed-routes listing says so). */
    canOpenAsset: boolean;
}

/**
 * One record in full: its facts as key-value pairs, then its structured sections. A record the
 * server could not read in full says so here and shows only the masked preview it kept.
 */
export const AuditDetailPanel: React.FC<AuditDetailPanelProps> = ({
    row,
    onClose,
    onShowRequest,
    canOpenAsset,
}) => {
    const headingRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        // The panel opens below the table: move focus into it so keyboard users land on it.
        headingRef.current?.focus();
    }, [row]);

    const when = timeFields(row.timestamp);
    const request = (row.request ?? {}) as Record<string, unknown>;
    const requestId = typeof request.requestId === "string" ? request.requestId : null;
    const href = canOpenAsset ? assetHref(row.databaseId, row.assetId) : null;
    const status = row.parseStatus ?? "parsed";
    const readable = status === "parsed";

    return (
        <Container
            header={
                <Header
                    variant="h2"
                    actions={
                        <SpaceBetween direction="horizontal" size="xs">
                            {requestId && (
                                <Button onClick={() => onShowRequest(requestId)}>
                                    Show records of this request
                                </Button>
                            )}
                            <Button
                                iconName="close"
                                variant="icon"
                                ariaLabel="Close record details"
                                onClick={onClose}
                            />
                        </SpaceBetween>
                    }
                >
                    <span ref={headingRef} tabIndex={-1} style={{ outline: "none" }}>
                        Record details
                    </span>
                </Header>
            }
        >
            <SpaceBetween size="m">
                {status === "partial" && (
                    <Alert type="warning" header="This record was cut short">
                        The record was longer than the log keeps, so only the part that could be
                        read is shown.
                    </Alert>
                )}
                {status === "unparsed" && (
                    <Alert type="warning" header="This record could not be read">
                        Its format is not one this page knows. The start of the record is shown with
                        secrets masked.
                    </Alert>
                )}
                {status === "unsupportedVersion" && (
                    <Alert type="warning" header="This record has a newer format">
                        It was written by a newer version of VAMS than this page reads. The start of
                        the record is shown with secrets masked.
                    </Alert>
                )}
                {row.truncated && readable && (
                    <Alert type="info" header="Some values were cut short">
                        Values longer than the audit log keeps show a fingerprint instead of the
                        full value.
                    </Alert>
                )}

                <ColumnLayout columns={3} variant="text-grid">
                    <Field label="Time (local)">{when.local}</Field>
                    <Field label="Time (UTC)">{when.utc}</Field>
                    <Field label="Log group">{text(row.groupLabel ?? row.groupKey)}</Field>
                    <Field label="Action">
                        {text(row.action)}
                        {row.legacyType ? ` (${text(row.legacyType)})` : ""}
                    </Field>
                    <Field label="Outcome">
                        <OutcomeIndicator outcome={row.outcome} />
                    </Field>
                    <Field label="Reason">{text(row.reason)}</Field>
                    <Field label="User">{text(row.user)}</Field>
                    <Field label="On behalf of">{text(row.onBehalfOf)}</Field>
                    <Field label="Sign-in method">{text(row.authMethod)}</Field>
                    <Field label="API key ID">{text(row.apiKeyId)}</Field>
                    <Field label="Entity">
                        {row.entity?.type || row.entity?.id
                            ? `${text(row.entity?.type)} ${text(row.entity?.id)}`
                            : "-"}
                    </Field>
                    <Field label="Database">{text(row.databaseId)}</Field>
                    <Field label="Asset">
                        {href ? <Link href={href}>{text(row.assetId)}</Link> : text(row.assetId)}
                    </Field>
                    <Field label="File path">{text(row.filePath)}</Field>
                    <Field label="Request ID">{text(requestId)}</Field>
                    <Field label="Request">
                        {request.method || request.route
                            ? `${text(request.method)} ${text(request.route)}`
                            : "-"}
                    </Field>
                    <Field label="Source IP">{text(request.sourceIp)}</Field>
                    <Field label="Items / files / bytes">
                        {[row.itemCount, row.fileCount, row.bytes]
                            .map((n) => (typeof n === "number" ? String(n) : "-"))
                            .join(" / ")}
                    </Field>
                    <Field label="Duration">
                        {typeof row.durationMs === "number" ? `${row.durationMs} ms` : "-"}
                    </Field>
                    <Field label="Record format">{text(row.formatVersion)}</Field>
                </ColumnLayout>

                {!readable && row.preview && (
                    <div>
                        <Box variant="awsui-key-label">Start of the record (masked)</Box>
                        <pre
                            data-testid="audit-preview"
                            style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: 0 }}
                        >
                            {clipText(visibleText(row.preview, ""))}
                        </pre>
                    </div>
                )}

                {readable && hasContent(row.ids) && (
                    <Section title="Other identifiers" value={row.ids} />
                )}
                {readable && hasContent(row.requestParameters) && (
                    <Section title="Request parameters" value={row.requestParameters} />
                )}
                {readable && hasContent(row.details) && (
                    <Section title="Details" value={row.details} />
                )}
                {readable && hasContent(row.changes) && (
                    <Section title="Changes" value={row.changes} />
                )}
                {readable && hasContent(row.previous) && (
                    <Section title="Previous values" value={row.previous} />
                )}
            </SpaceBetween>
        </Container>
    );
};

const Section: React.FC<{ title: string; value: unknown }> = ({ title, value }) => (
    <div>
        <Box variant="awsui-key-label">{title}</Box>
        <AuditValueTree value={value} />
    </div>
);

export default AuditDetailPanel;
