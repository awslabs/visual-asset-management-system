/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { clipText, isTruncatedMarker, visibleText } from "./auditText";

/** The tree stops here, whatever the server sent, so a hostile or huge record cannot freeze the tab. */
export const TREE_MAX_DEPTH = 8;
export const TREE_MAX_NODES = 500;

interface Budget {
    nodes: number;
}

const Scalar: React.FC<{ value: unknown }> = ({ value }) => {
    if (value === null || value === undefined) return <i>none</i>;
    if (typeof value === "string") return <span>{clipText(visibleText(value, "(empty)"))}</span>;
    return <span>{String(value)}</span>;
};

const Node: React.FC<{ value: unknown; depth: number; budget: Budget }> = ({
    value,
    depth,
    budget,
}) => {
    budget.nodes += 1;
    if (budget.nodes > TREE_MAX_NODES) return <i>(more values not shown)</i>;
    if (isTruncatedMarker(value)) {
        // The server cut this value: show what is left and the fingerprint of the whole.
        return (
            <span data-truncated-value="">
                <i>cut short</i>
                {value.preview !== undefined && <> {clipText(visibleText(value.preview, ""))}</>}
                {value.bytes !== undefined && <> ({String(value.bytes)} bytes)</>}
                {value.sha256 !== undefined && (
                    <>
                        {" "}
                        SHA-256 <code>{visibleText(value.sha256)}</code>
                    </>
                )}
            </span>
        );
    }
    if (Array.isArray(value)) {
        if (value.length === 0) return <i>empty list</i>;
        if (depth >= TREE_MAX_DEPTH) return <i>(nested too deep)</i>;
        return (
            <ul style={{ margin: 0, paddingLeft: "1.25em" }}>
                {value.map((item, index) => (
                    <li key={index}>
                        <Node value={item} depth={depth + 1} budget={budget} />
                    </li>
                ))}
            </ul>
        );
    }
    if (typeof value === "object" && value !== null) {
        const entries = Object.entries(value as Record<string, unknown>);
        if (entries.length === 0) return <i>empty</i>;
        if (depth >= TREE_MAX_DEPTH) return <i>(nested too deep)</i>;
        return (
            <dl style={{ margin: 0 }}>
                {entries.map(([key, item]) => (
                    <div key={key} style={{ display: "flex", gap: "0.5em", flexWrap: "wrap" }}>
                        <dt style={{ fontWeight: 700 }}>{visibleText(key)}</dt>
                        <dd style={{ margin: 0, minWidth: 0, overflowWrap: "anywhere" }}>
                            <Node value={item} depth={depth + 1} budget={budget} />
                        </dd>
                    </div>
                ))}
            </dl>
        );
    }
    return <Scalar value={value} />;
};

/**
 * A record's structured values (request parameters, details, changes) as nested lists. Every string
 * is a React text node with control and bidi characters made visible; nothing is ever interpreted
 * as HTML or as a link.
 */
export const AuditValueTree: React.FC<{ value: unknown }> = ({ value }) => (
    <div data-testid="audit-value-tree" style={{ fontSize: "0.9em" }}>
        <Node value={value} depth={0} budget={{ nodes: 0 }} />
    </div>
);

export default AuditValueTree;
