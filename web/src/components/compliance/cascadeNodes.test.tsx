/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import {
    CascadeNodeList,
    CascadeNodesModal,
    cascadeNodeEntries,
    cascadeNodeOutcome,
    cascadeProgress,
    cascadeUnauthorizedNodes,
} from "./cascadeNodes";
import { CascadeRecord } from "../../services/ComplianceService";

const record = (fields: Partial<CascadeRecord>): CascadeRecord => ({
    cascadeId: "0f6f9c1e-1111-4222-8333-444455556666",
    state: "completed",
    triggeredByDatabaseId: "db1",
    triggeredByAssetId: "parent",
    requireApproval: true,
    createdAt: "2026-01-01T00:00:00Z",
    ...fields,
});

// The cascade of the reported case: a Compliance Admin of db1 approves a cascade whose children
// are in db1 and db2. The child in db2 is skipped as not authorized; the API returns its key and
// state, and records no name for it.
const crossDatabaseCascade = () =>
    record({
        nodes: JSON.stringify({
            "db1:child-a": "compliant",
            "db2:child-b": "skipped_not_authorized",
        }),
        executionOrder: JSON.stringify(["db1:child-a", "db2:child-b"]),
        nodeNames: JSON.stringify({ "db1:child-a": "Rotor" }),
        totalNodes: 2,
    });

/** The entries of a record that carries a node map. */
const entriesOf = (cascade: CascadeRecord) => {
    const entries = cascadeNodeEntries(cascade);
    if (!entries) throw new Error("The record carries no readable node map");
    return entries;
};

describe("cascadeProgress", () => {
    it("does not count a node skipped as not authorized as evaluated", () => {
        expect(cascadeProgress(crossDatabaseCascade())).toBe(
            "1 of 2 nodes evaluated; 1 skipped: not authorized"
        );
    });

    it("counts each skip reason, the failed nodes and those still to go apart", () => {
        expect(
            cascadeProgress(
                record({
                    state: "executing",
                    nodes: JSON.stringify({
                        "db1:a": "compliant",
                        "db1:b": "quarantined",
                        "db2:c": "skipped_not_authorized",
                        "db2:d": "skipped_not_authorized",
                        "db1:e": "skipped_archived",
                        "db1:f": "skipped",
                        "db1:g": "superseded",
                        "db1:h": "error",
                        "db1:i": "evaluating",
                        "db1:j": "pending",
                    }),
                    totalNodes: 10,
                })
            )
        ).toBe(
            "2 of 10 nodes evaluated; 5 skipped: 2 not authorized, 1 archived, " +
                "1 no bound schema or removed, 1 superseded; 1 failed; 2 remaining"
        );
    });

    it("counts a pending-pipeline verdict as evaluated", () => {
        expect(
            cascadeProgress(record({ nodes: JSON.stringify({ "db1:a": "pending_pipeline" }) }))
        ).toBe("1 of 1 nodes evaluated");
    });

    it("counts an archived node the reader may see as skipped: archived", () => {
        expect(
            cascadeProgress(
                record({
                    nodes: JSON.stringify({ "db1:a": "compliant", "db1:b": "skipped_archived" }),
                    totalNodes: 2,
                })
            )
        ).toBe("1 of 2 nodes evaluated; 1 skipped: archived");
    });

    it("counts only the nodes the API returns: an archived node hidden from the reader is in neither the counts nor the total", () => {
        // The API leaves such a node out of `nodes`, `executionOrder`, `nodeNames` and `results`
        // and takes it off `totalNodes`, so the page has nothing of it to count.
        const shown = record({
            nodes: JSON.stringify({ "db1:a": "compliant" }),
            executionOrder: JSON.stringify(["db1:a"]),
            totalNodes: 1,
        });
        expect(cascadeProgress(shown)).toBe("1 of 1 nodes evaluated");
        expect(cascadeNodeEntries(shown)?.map((entry) => entry.key)).toEqual(["db1:a"]);
    });

    it("falls back to the number of nodes without totalNodes", () => {
        expect(
            cascadeProgress(
                record({ nodes: JSON.stringify({ "db1:a": "compliant", "db1:b": "skipped" }) })
            )
        ).toBe("1 of 2 nodes evaluated; 1 skipped: no bound schema or removed");
    });

    it("returns null without a node map or with an unreadable one", () => {
        expect(cascadeProgress(null)).toBeNull();
        expect(cascadeProgress(record({}))).toBeNull();
        expect(cascadeProgress(record({ nodes: "{not json" }))).toBeNull();
        expect(cascadeProgress(record({ nodes: "[]" }))).toBeNull();
    });
});

describe("cascadeNodeEntries", () => {
    it("lists the nodes in evaluation order with their names and outcomes", () => {
        const entries = cascadeNodeEntries(
            record({
                nodes: JSON.stringify({
                    "db1:b": "skipped",
                    "db1:a": "compliant",
                    "db1:c": "error",
                }),
                executionOrder: JSON.stringify(["db1:a", "db1:b"]),
                nodeNames: JSON.stringify({ "db1:a": "Rotor", "db1:b": "Blade" }),
            })
        );
        expect(entries?.map((entry) => [entry.key, entry.name, entry.outcome.label])).toEqual([
            ["db1:a", "Rotor", "Evaluated: Compliant"],
            ["db1:b", "Blade", "Skipped: no bound schema or removed"],
            // A node the order does not list comes last.
            ["db1:c", "", "Failed"],
        ]);
    });

    it("splits the key at its first colon into database and asset id", () => {
        const [entry] = entriesOf(record({ nodes: JSON.stringify({ "db2:x:y": "compliant" }) }));
        expect(entry.databaseId).toBe("db2");
        expect(entry.assetId).toBe("x:y");
    });

    it("keeps a node the cascade recorded no name for unnamed", () => {
        const entries = entriesOf(crossDatabaseCascade());
        expect(entries[1]).toMatchObject({
            key: "db2:child-b",
            databaseId: "db2",
            assetId: "child-b",
            name: "",
            state: "skipped_not_authorized",
        });
        expect(entries[1].outcome.kind).toBe("skipped");
    });
});

describe("cascadeNodeOutcome", () => {
    it.each([
        ["compliant", "evaluated", "Evaluated: Compliant"],
        ["non_compliant", "evaluated", "Evaluated: Non-Compliant"],
        ["quarantined", "evaluated", "Evaluated: Quarantined"],
        ["pending_pipeline", "evaluated", "Evaluated: Pipeline pending"],
        ["skipped_not_authorized", "skipped", "Skipped: not authorized"],
        ["skipped_archived", "skipped", "Skipped: archived"],
        ["skipped", "skipped", "Skipped: no bound schema or removed"],
        ["superseded", "skipped", "Skipped: superseded"],
        ["error", "failed", "Failed"],
        ["pending", "in_progress", "Pending"],
        ["evaluating", "in_progress", "Evaluating"],
    ])("%s is %s (%s)", (state, kind, label) => {
        expect(cascadeNodeOutcome(state)).toMatchObject({ kind, label });
    });
});

describe("CascadeNodeList", () => {
    it("shows each node's outcome, and a node without a name by its database and id", () => {
        render(<CascadeNodeList entries={entriesOf(crossDatabaseCascade())} />);
        const rows = screen.getAllByRole("row").slice(1);
        expect(rows).toHaveLength(2);
        expect(within(rows[0]).getByText("Rotor")).toBeInTheDocument();
        expect(within(rows[0]).getByText("db1 / child-a")).toBeInTheDocument();
        expect(within(rows[0]).getByText("Evaluated: Compliant")).toBeInTheDocument();
        expect(within(rows[1]).getByText("child-b")).toBeInTheDocument();
        expect(within(rows[1]).getByText("db2")).toBeInTheDocument();
        expect(within(rows[1]).getByText("Skipped: not authorized")).toBeInTheDocument();
        expect(
            within(rows[1]).getByText(/the person who started the cascade may not evaluate/)
        ).toBeInTheDocument();
    });

    it("shows an archived node the reader may see as skipped: archived", () => {
        render(
            <CascadeNodeList
                entries={entriesOf(
                    record({
                        nodes: JSON.stringify({ "db1:old": "skipped_archived" }),
                        nodeNames: JSON.stringify({ "db1:old": "Old Blade" }),
                    })
                )}
            />
        );
        expect(screen.getByText("Old Blade")).toBeInTheDocument();
        expect(screen.getByText("Skipped: archived")).toBeInTheDocument();
        expect(screen.getByText(/archived when the cascade reached it/)).toBeInTheDocument();
    });
});

// A Compliance Admin of db1 only reads a cascade whose children are in db1 and db2. The API leaves
// the db2 child out of the node map and the total and counts it in `unauthorizedNodes`.
const oneDatabaseReaderView = (fields: Partial<CascadeRecord> = {}) =>
    record({
        nodes: JSON.stringify({ "db1:child-a": "compliant" }),
        executionOrder: JSON.stringify(["db1:child-a"]),
        nodeNames: JSON.stringify({ "db1:child-a": "Rotor" }),
        totalNodes: 1,
        unauthorizedNodes: 1,
        ...fields,
    });

describe("nodes the reader cannot access", () => {
    it("counts them after the progress, outside the total", () => {
        expect(cascadeProgress(oneDatabaseReaderView())).toBe(
            "1 of 1 nodes evaluated; 1 node you cannot access"
        );
        expect(cascadeProgress(oneDatabaseReaderView({ unauthorizedNodes: 3 }))).toBe(
            "1 of 1 nodes evaluated; 3 nodes you cannot access"
        );
    });

    it("counts them when the reader may access none of the nodes", () => {
        expect(
            cascadeProgress(
                record({ nodes: "{}", executionOrder: "[]", totalNodes: 0, unauthorizedNodes: 2 })
            )
        ).toBe("0 of 0 nodes evaluated; 2 nodes you cannot access");
    });

    it("adds nothing without a count, or with a zero or unreadable one", () => {
        for (const unauthorizedNodes of [undefined, 0, -1, Number.NaN]) {
            expect(cascadeProgress(oneDatabaseReaderView({ unauthorizedNodes }))).toBe(
                "1 of 1 nodes evaluated"
            );
            expect(cascadeUnauthorizedNodes(oneDatabaseReaderView({ unauthorizedNodes }))).toBe(0);
        }
    });

    it("still lists, by its id, a node the approver could not evaluate that this reader may read", () => {
        const view = record({ ...crossDatabaseCascade(), unauthorizedNodes: 0 });
        expect(cascadeProgress(view)).toBe("1 of 2 nodes evaluated; 1 skipped: not authorized");
        expect(entriesOf(view).map((entry) => entry.assetId)).toEqual(["child-a", "child-b"]);
    });

    it("lists only the nodes the reader may access, and says so when there are none", () => {
        const { unmount } = render(
            <CascadeNodeList entries={entriesOf(oneDatabaseReaderView())} unauthorizedNodes={1} />
        );
        expect(screen.getAllByRole("row").slice(1)).toHaveLength(1);
        expect(screen.queryByText(/db2/)).not.toBeInTheDocument();
        unmount();
        render(<CascadeNodeList entries={[]} unauthorizedNodes={1} />);
        expect(
            screen.getByText("The cascade has no downstream assets you can access.")
        ).toBeInTheDocument();
    });

    it("shows the count in the View nodes dialog", () => {
        render(<CascadeNodesModal record={oneDatabaseReaderView()} onDismiss={() => undefined} />);
        expect(
            screen.getByText("1 of 1 nodes evaluated; 1 node you cannot access")
        ).toBeInTheDocument();
        expect(
            screen.getByText(
                "1 node you cannot access is not listed: it is in a database whose compliance you may not read, or is an asset you may not read."
            )
        ).toBeInTheDocument();
    });

    it("gives both reasons in the View nodes dialog without saying which applies to which node", () => {
        render(
            <CascadeNodesModal
                record={oneDatabaseReaderView({ unauthorizedNodes: 2 })}
                onDismiss={() => undefined}
            />
        );
        expect(
            screen.getByText(
                "2 nodes you cannot access are not listed: each is in a database whose compliance you may not read, or is an asset you may not read."
            )
        ).toBeInTheDocument();
    });
});
