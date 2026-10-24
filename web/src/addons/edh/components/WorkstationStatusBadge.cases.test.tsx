/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The web half of the CLI/web consistency check: every record in workstationStatusCases.json, which
 * tools/VamsCLI/tests/addon/test_edh_start_status.py reads too, shows the agreed badge label and the
 * agreed start-failure sentence and detail line. The CLI test asserts its State column and `get`
 * lines against the same file, so the two clients cannot drift apart.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import WorkstationStatusBadge, {
    StartFailureNotice,
    describeStartFailure,
} from "./WorkstationStatusBadge";
import fixture from "../workstationStatusCases.json";
import type { StartFailure, WorkstationActivity, WorkstationState } from "../types";

interface StatusCase {
    name: string;
    record: {
        state: string;
        edhState: string | null;
        activity: string | null;
        lastError: string | null;
        startFailure: StartFailure | null;
        sessionId: string | null;
        connectionAvailable: boolean;
        instanceType: string;
    };
    badge: string;
    state: string;
    startFailureMessage: string | null;
    startFailureDetail: string | null;
}

const CASES = fixture.cases as StatusCase[];
const table = CASES.map((c) => [c.name, c] as const);

describe("workstationStatusCases.json (shared with the CLI tests)", () => {
    it("covers every state, every activity and every start failure code (control)", () => {
        const states = new Set(CASES.map((c) => c.record.state));
        expect([...states].sort()).toEqual(
            ["ACTIVE", "CREATING", "DELETED", "ORPHANED", "STOPPED"].sort()
        );
        const activities = new Set(CASES.map((c) => c.record.activity));
        expect(activities).toEqual(new Set(["starting", "stopping", null]));
        const stoppedCodes = new Set(
            CASES.filter((c) => c.record.state === "STOPPED").map((c) => c.record.lastError)
        );
        for (const code of [
            "CAPACITY_UNAVAILABLE",
            "RESERVATION_FULL",
            "START_FAILED",
            "LEASE_EXPIRED",
            null,
        ]) {
            expect(stoppedCodes).toContain(code);
        }
        // A start EDH accepted and never finished is a CREATING row the sweep marked stalled.
        const creatingCodes = new Set(
            CASES.filter((c) => c.record.state === "CREATING").map((c) => c.record.lastError)
        );
        expect(creatingCodes).toContain("START_STALLED");
        expect(CASES.filter((c) => c.badge === "Start stalled").length).toBeGreaterThanOrEqual(1);
    });

    it.each(table)("%s: the badge reads as agreed", (_name, c) => {
        render(
            <WorkstationStatusBadge
                state={c.record.state as WorkstationState}
                edhState={c.record.edhState}
                lastError={c.record.lastError}
                connectionAvailable={c.record.connectionAvailable}
                sessionId={c.record.sessionId}
                activity={c.record.activity as WorkstationActivity | null}
                instanceType={c.record.instanceType}
                startFailure={c.record.startFailure}
            />
        );
        expect(screen.getByText(c.badge)).toBeInTheDocument();
        // The phases in motion carry the pulsing dot.
        const moving = ["Starting", "Stopping", "Creating"].includes(c.badge);
        const dot = screen.getByText(c.badge).parentElement?.querySelector(".animate-pulse");
        if (moving) {
            expect(dot).not.toBeNull();
        } else {
            expect(dot).toBeNull();
        }
    });

    it.each(table)("%s: the start-failure sentence and detail line are as agreed", (_name, c) => {
        const failure = describeStartFailure({
            ...c.record,
            state: c.record.state as WorkstationState,
            activity: c.record.activity as WorkstationActivity | null,
        });
        expect(failure?.message ?? null).toBe(c.startFailureMessage);
        expect(failure?.detail ?? null).toBe(c.startFailureDetail);

        const { container } = render(
            <StartFailureNotice
                row={{
                    ...c.record,
                    state: c.record.state as WorkstationState,
                    activity: c.record.activity as WorkstationActivity | null,
                }}
            />
        );
        if (c.startFailureMessage === null) {
            expect(container).toBeEmptyDOMElement();
        } else {
            expect(screen.getByText(c.startFailureMessage)).toBeInTheDocument();
            if (c.startFailureDetail) {
                expect(screen.getByText(c.startFailureDetail)).toBeInTheDocument();
            } else {
                expect(screen.queryByText(/^AWS:/)).not.toBeInTheDocument();
            }
        }
    });
});
