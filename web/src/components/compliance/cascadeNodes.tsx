/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import { StatusIndicatorProps } from "@cloudscape-design/components/status-indicator";
import { CascadeRecord } from "../../services/ComplianceService";
import { COMPLIANCE_STATE_INDICATORS, StatusLabel } from "./complianceStateBadge";
import Synonyms from "../../synonyms";

/**
 * What happened to one downstream node of a cascade, read from its state in the row's `nodes` map:
 * `evaluated` (the state is the verdict its evaluation produced), `skipped` (not evaluated, for the
 * reason in `skipReason`), `failed` (`error`) or `in_progress` (`pending`, `evaluating`).
 */
export type CascadeNodeOutcomeKind = "evaluated" | "skipped" | "failed" | "in_progress";

export interface CascadeNodeOutcome {
    kind: CascadeNodeOutcomeKind;
    /** The status label shown for the node, e.g. "Evaluated: Compliant", "Skipped: archived". */
    label: string;
    type: StatusIndicatorProps.Type;
    /** A skipped node's reason, as the progress summary names it ("not authorized"). */
    skipReason?: string;
    /** What the outcome means, in one sentence. */
    description: string;
}

/** One downstream node of a cascade, in evaluation order. */
export interface CascadeNodeEntry {
    /** The node's `databaseId:assetId` key, as the API returns it. */
    key: string;
    databaseId: string;
    assetId: string;
    /** The node asset's name from `nodeNames`; "" when the cascade recorded none for it. */
    name: string;
    /** The node's state as the API returns it. */
    state: string;
    outcome: CascadeNodeOutcome;
}

/**
 * The node states that mean the node was not evaluated, with the reason the page gives, in the
 * order the progress summary lists them. `skipped` covers both a node with no bound schema and one
 * that no longer exists: the API records the two alike.
 */
export const CASCADE_SKIPPED_NODE_STATES: Record<string, { reason: string; description: string }> =
    {
        skipped_not_authorized: {
            reason: "not authorized",
            description: `In a ${Synonyms.database} the person who started the cascade may not evaluate. It was not evaluated.`,
        },
        skipped_archived: {
            reason: "archived",
            description: `The ${Synonyms.asset} was archived when the cascade reached it. It was not evaluated.`,
        },
        skipped: {
            reason: "no bound schema or removed",
            description: `No schema is bound to the ${Synonyms.asset}, or the ${Synonyms.asset} no longer exists. It was not evaluated.`,
        },
        superseded: {
            reason: "superseded",
            description: `A newer evaluation of the ${Synonyms.asset} had already started and sets its state. This one did not run.`,
        },
    };

const IN_PROGRESS_OUTCOMES: Record<string, CascadeNodeOutcome> = {
    pending: {
        kind: "in_progress",
        label: "Pending",
        type: "pending",
        description: "Waiting to be evaluated.",
    },
    evaluating: {
        kind: "in_progress",
        label: "Evaluating",
        type: "in-progress",
        description: "Being evaluated now.",
    },
};

const FAILED_OUTCOME: CascadeNodeOutcome = {
    kind: "failed",
    label: "Failed",
    type: "error",
    description: "The evaluation ended in an error.",
};

/** The verdicts an evaluated node can carry, with their status indicator. */
const VERDICT_INDICATORS: Record<string, { label: string; type: StatusIndicatorProps.Type }> = {
    compliant: COMPLIANCE_STATE_INDICATORS.compliant,
    non_compliant: COMPLIANCE_STATE_INDICATORS.non_compliant,
    quarantined: COMPLIANCE_STATE_INDICATORS.quarantined,
    pending_pipeline: { label: "Pipeline pending", type: "in-progress" },
};

/** The outcome of a node state. A state the page does not know is a verdict: it was evaluated. */
export const cascadeNodeOutcome = (state: string): CascadeNodeOutcome => {
    if (state in IN_PROGRESS_OUTCOMES) return IN_PROGRESS_OUTCOMES[state];
    if (state === "error") return FAILED_OUTCOME;
    const skipped = CASCADE_SKIPPED_NODE_STATES[state];
    if (skipped) {
        return {
            kind: "skipped",
            label: `Skipped: ${skipped.reason}`,
            type: "stopped",
            skipReason: skipped.reason,
            description: skipped.description,
        };
    }
    const verdict = VERDICT_INDICATORS[state] || { label: state || "Unknown", type: "info" };
    return {
        kind: "evaluated",
        label: `Evaluated: ${verdict.label}`,
        type: verdict.type,
        description:
            state === "pending_pipeline"
                ? "Evaluated; the verdict waits for its pipeline rules."
                : "Evaluated against its bound schema.",
    };
};

/** A JSON-encoded cascade attribute decoded; `fallback` when it is absent or of another shape. */
const decoded = <T,>(raw: unknown, fallback: T, isShape: (value: unknown) => boolean): T => {
    if (raw === undefined || raw === null || raw === "") return fallback;
    let value: unknown = raw;
    if (typeof raw === "string") {
        try {
            value = JSON.parse(raw);
        } catch {
            return fallback;
        }
    }
    return isShape(value) ? (value as T) : fallback;
};

const isMap = (value: unknown) => !!value && typeof value === "object" && !Array.isArray(value);

/**
 * The cascade's downstream nodes in evaluation order (`executionOrder`, then any node it does not
 * list), each with its name and outcome; null when the row has no readable node map. The list is
 * the API's: a node the reader may not see is not in the response. A node in a database whose
 * compliance the reader may not read, or an asset the reader may not read, is counted in
 * `unauthorizedNodes` only (`cascadeUnauthorizedNodes`); an archived node the reader may not see
 * is neither listed nor counted.
 */
export const cascadeNodeEntries = (record: CascadeRecord | null): CascadeNodeEntry[] | null => {
    if (!record?.nodes) return null;
    const nodes = decoded<Record<string, unknown> | null>(record.nodes, null, isMap);
    if (!nodes) return null;
    const names = decoded<Record<string, unknown>>(record.nodeNames, {}, isMap);
    const order = decoded<unknown[]>(record.executionOrder, [], Array.isArray);
    const keys = order.filter(
        (key, index): key is string =>
            typeof key === "string" && key in nodes && order.indexOf(key) === index
    );
    keys.push(...Object.keys(nodes).filter((key) => !keys.includes(key)));
    return keys.map((key) => {
        const separator = key.indexOf(":");
        const state = typeof nodes[key] === "string" ? (nodes[key] as string) : "";
        const name = names[key];
        return {
            key,
            databaseId: separator >= 0 ? key.substring(0, separator) : "",
            assetId: separator >= 0 ? key.substring(separator + 1) : key,
            name: typeof name === "string" ? name : "",
            state,
            outcome: cascadeNodeOutcome(state),
        };
    });
};

/** How many of a cascade's nodes had each outcome; `skipped` per reason, in a fixed order. */
export interface CascadeNodeCounts {
    total: number;
    evaluated: number;
    failed: number;
    inProgress: number;
    skipped: { reason: string; count: number }[];
}

export const cascadeNodeCounts = (
    entries: CascadeNodeEntry[],
    totalNodes?: number
): CascadeNodeCounts => {
    const countOf = (kind: CascadeNodeOutcomeKind) =>
        entries.filter((entry) => entry.outcome.kind === kind).length;
    const skipped = Object.values(CASCADE_SKIPPED_NODE_STATES)
        .map(({ reason }) => ({
            reason,
            count: entries.filter((entry) => entry.outcome.skipReason === reason).length,
        }))
        .filter((bucket) => bucket.count > 0);
    return {
        total: totalNodes ?? entries.length,
        evaluated: countOf("evaluated"),
        failed: countOf("failed"),
        inProgress: countOf("in_progress"),
        skipped,
    };
};

/**
 * How many of the cascade's nodes the reader may not read (`unauthorizedNodes`): in a database
 * whose compliance the reader may not read, or an asset the reader may not read. The API leaves
 * them out of the node map and the total and counts them there only, without their ids, names,
 * verdicts or which of the two applies. 0 when the row carries no count.
 */
export const cascadeUnauthorizedNodes = (record: CascadeRecord | null): number => {
    const count = record?.unauthorizedNodes;
    return typeof count === "number" && Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
};

/** "1 node you cannot access", "2 nodes you cannot access". */
export const unauthorizedNodesText = (count: number): string =>
    `${count} ${count === 1 ? "node" : "nodes"} you cannot access`;

/**
 * The cascade's progress, read from the row's node map: the nodes evaluated of the total, then the
 * skipped ones with their reasons, the failed ones and those still to go, e.g. "1 of 2 nodes
 * evaluated; 1 skipped: not authorized", and last the nodes the reader cannot access ("1 node you
 * cannot access"), which are in neither the counts nor the total. A skipped or superseded node is
 * not counted as evaluated. Null when the row has no readable node map.
 */
export const cascadeProgress = (record: CascadeRecord | null): string | null => {
    const entries = cascadeNodeEntries(record);
    if (!entries) return null;
    const counts = cascadeNodeCounts(entries, record?.totalNodes);
    const parts = [`${counts.evaluated} of ${counts.total} nodes evaluated`];
    const skippedTotal = counts.skipped.reduce((sum, { count }) => sum + count, 0);
    if (skippedTotal > 0) {
        const reasons =
            counts.skipped.length === 1
                ? counts.skipped[0].reason
                : counts.skipped.map(({ reason, count }) => `${count} ${reason}`).join(", ");
        parts.push(`${skippedTotal} skipped: ${reasons}`);
    }
    if (counts.failed > 0) parts.push(`${counts.failed} failed`);
    if (counts.inProgress > 0) parts.push(`${counts.inProgress} remaining`);
    const unauthorized = cascadeUnauthorizedNodes(record);
    if (unauthorized > 0) parts.push(unauthorizedNodesText(unauthorized));
    return parts.join("; ");
};

interface CascadeNodeListProps {
    entries: CascadeNodeEntry[];
    /** How many nodes the reader cannot access (`cascadeUnauthorizedNodes`); not listed. */
    unauthorizedNodes?: number;
}

/** Each node of a cascade with its outcome: the node's name, its database and ID beneath. */
export const CascadeNodeList: React.FC<CascadeNodeListProps> = ({
    entries,
    unauthorizedNodes = 0,
}) => (
    <Table
        variant="embedded"
        wrapLines
        items={entries}
        trackBy="key"
        ariaLabels={{ tableLabel: "Cascade nodes" }}
        empty={
            <Box textAlign="center">
                {unauthorizedNodes > 0
                    ? `The cascade has no downstream ${Synonyms.assets} you can access.`
                    : `The cascade has no downstream ${Synonyms.assets}.`}
            </Box>
        }
        columnDefinitions={[
            {
                id: "node",
                header: Synonyms.Asset,
                cell: (entry) => (
                    <SpaceBetween size="xxxs">
                        <span>{entry.name || entry.assetId}</span>
                        <Box variant="small" color="text-body-secondary">
                            {[entry.databaseId, entry.name ? entry.assetId : ""]
                                .filter(Boolean)
                                .join(" / ")}
                        </Box>
                    </SpaceBetween>
                ),
            },
            {
                id: "outcome",
                header: "Outcome",
                cell: (entry) => (
                    <SpaceBetween size="xxxs">
                        <StatusLabel type={entry.outcome.type}>{entry.outcome.label}</StatusLabel>
                        <Box variant="small" color="text-body-secondary">
                            {entry.outcome.description}
                        </Box>
                    </SpaceBetween>
                ),
            },
        ]}
    />
);

interface CascadeNodesModalProps {
    /** The cascade whose nodes are shown; the dialog is open while it is set. */
    record: CascadeRecord;
    onDismiss: () => void;
}

/** A cascade's progress and each node's outcome, in a dialog. */
export const CascadeNodesModal: React.FC<CascadeNodesModalProps> = ({ record, onDismiss }) => {
    const entries = cascadeNodeEntries(record) || [];
    const unauthorized = cascadeUnauthorizedNodes(record);
    return (
        <Modal
            visible
            size="large"
            onDismiss={onDismiss}
            header={`Nodes of cascade ${record.cascadeId.substring(0, 8)}...`}
            footer={
                <Box float="right">
                    <Button variant="primary" onClick={onDismiss}>
                        Close
                    </Button>
                </Box>
            }
        >
            <SpaceBetween size="m">
                <div>{cascadeProgress(record)}</div>
                <CascadeNodeList entries={entries} unauthorizedNodes={unauthorized} />
                {unauthorized > 0 && (
                    <Box variant="small" color="text-body-secondary">
                        {`${unauthorizedNodesText(unauthorized)} ${
                            unauthorized === 1 ? "is not listed: it is" : "are not listed: each is"
                        } in a ${Synonyms.database} whose compliance you may not read, or is an ${
                            Synonyms.asset
                        } you may not read.`}
                    </Box>
                )}
            </SpaceBetween>
        </Modal>
    );
};
