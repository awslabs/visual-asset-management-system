/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import WorkstationStatusBadge, {
    LAST_ERROR_COPY,
    LAST_ERROR_FALLBACK_COPY,
    describeEdhFailure,
    describeLastError,
} from "./WorkstationStatusBadge";
import type { EdhFailure } from "../types";

const OBSERVED_AT = "2026-10-22T10:00:00Z";

describe("WorkstationStatusBadge", () => {
    it.each([
        ["CREATING", "Creating"],
        ["ACTIVE", "Active"],
        ["STOPPED", "Stopped"],
        ["DELETED", "Deleted"],
        ["ORPHANED", "Orphaned"],
    ] as const)("renders %s as %s", (state, label) => {
        render(<WorkstationStatusBadge state={state} />);
        expect(screen.getByText(label)).toBeInTheDocument();
    });

    it("renders an active desktop EDH does not yet report connectable as Starting, with the moving dot", () => {
        const { unmount } = render(
            <WorkstationStatusBadge state="ACTIVE" connectionAvailable={false} edhState="pending" />
        );
        expect(screen.getByTitle("pending")).toHaveTextContent("Starting");
        expect(screen.getByText("Starting").previousSibling).toHaveClass("animate-pulse");
        unmount();
        render(<WorkstationStatusBadge state="ACTIVE" connectionAvailable={true} />);
        expect(screen.getByText("Active")).toBeInTheDocument();
        expect(screen.queryByText("Starting")).not.toBeInTheDocument();
    });

    it("renders a creating row that carries a session as Starting: a stopped desktop being started again", () => {
        const { unmount } = render(
            <WorkstationStatusBadge state="CREATING" sessionId="s-2" edhState="pending" />
        );
        expect(screen.getByTitle("pending")).toHaveTextContent("Starting");
        expect(screen.getByText("Starting").previousSibling).toHaveClass("animate-pulse");
        unmount();
        // A launch has no session yet and is being created.
        render(<WorkstationStatusBadge state="CREATING" sessionId={null} />);
        expect(screen.getByText("Creating")).toBeInTheDocument();
        expect(screen.getByText("Creating").previousSibling).toHaveClass("animate-pulse");
    });

    it("follows the service's activity whatever the state says, with the moving dot", () => {
        const { unmount } = render(
            <WorkstationStatusBadge state="STOPPED" edhState="stopped" activity="starting" />
        );
        expect(screen.getByTitle("stopped")).toHaveTextContent("Starting");
        expect(screen.getByText("Starting").previousSibling).toHaveClass("animate-pulse");
        unmount();
        const second = render(
            <WorkstationStatusBadge state="STOPPED" edhState="stopping" activity="stopping" />
        );
        expect(screen.getByTitle("stopping")).toHaveTextContent("Stopping");
        expect(screen.getByText("Stopping").previousSibling).toHaveClass("animate-pulse");
        second.unmount();
        // A null activity is the service saying neither: the state decides, and a creating row with a
        // session is no longer guessed to be starting.
        render(
            <WorkstationStatusBadge state="ACTIVE" connectionAvailable={false} activity={null} />
        );
        expect(screen.getByText("Active")).toBeInTheDocument();
    });

    it("titles a stopped badge whose start was refused with the start-failure sentence", () => {
        render(
            <WorkstationStatusBadge
                state="STOPPED"
                edhState="stopped"
                activity={null}
                lastError="START_FAILED"
                instanceType="g5.16xlarge"
            />
        );
        expect(screen.getByTitle(LAST_ERROR_COPY.START_FAILED)).toHaveTextContent("Stopped");
        expect(describeLastError("START_FAILED")).toBe(
            "The workstation did not start; it is still stopped with its disk. Start it again, or ask an administrator."
        );
    });

    it("renders an unmapped state as its raw value without throwing", () => {
        render(<WorkstationStatusBadge state={"RESIZING" as any} />);
        expect(screen.getByText("RESIZING")).toBeInTheDocument();
    });

    it("renders an empty state as Unknown", () => {
        render(<WorkstationStatusBadge state={"" as any} />);
        expect(screen.getByText("Unknown")).toBeInTheDocument();
    });

    it("exposes the raw EDH state as the badge title", () => {
        render(<WorkstationStatusBadge state="ACTIVE" edhState="running" />);
        expect(screen.getByTitle("running")).toHaveTextContent("Active");
    });

    it("carries no title when EDH has not reported a state", () => {
        render(<WorkstationStatusBadge state="CREATING" edhState={null} />);
        expect(screen.getByText("Creating").closest("span[title]")).toBeNull();
    });

    it("titles an orphaned badge with the explanation for its failure code", () => {
        render(<WorkstationStatusBadge state="ORPHANED" lastError="QUOTA_EXCEEDED" />);
        expect(screen.getByTitle(LAST_ERROR_COPY.QUOTA_EXCEEDED)).toHaveTextContent("Orphaned");
    });

    it("titles an orphaned badge EDH found no capacity for with the retry advice", () => {
        render(<WorkstationStatusBadge state="ORPHANED" lastError="CAPACITY_UNAVAILABLE" />);
        expect(
            screen.getByTitle(
                "EDH found no available AWS capacity for the chosen instance type. Launch again later or choose another instance type."
            )
        ).toHaveTextContent("Orphaned");
        expect(describeLastError("CAPACITY_UNAVAILABLE")).not.toBe(LAST_ERROR_FALLBACK_COPY);
    });

    it("titles a stopped badge with the lease explanation when the lease stopped it", () => {
        render(
            <WorkstationStatusBadge state="STOPPED" edhState="stopped" lastError="LEASE_EXPIRED" />
        );
        expect(screen.getByTitle(LAST_ERROR_COPY.LEASE_EXPIRED)).toHaveTextContent("Stopped");
    });

    it("keeps the raw EDH state as the title of a stopped badge that carries no code", () => {
        render(<WorkstationStatusBadge state="STOPPED" edhState="stopped" />);
        expect(screen.getByTitle("stopped")).toHaveTextContent("Stopped");
    });

    it("falls back to the generic explanation for a missing or unknown code", () => {
        const { unmount } = render(<WorkstationStatusBadge state="ORPHANED" lastError={null} />);
        expect(screen.getByTitle(LAST_ERROR_FALLBACK_COPY)).toHaveTextContent("Orphaned");
        unmount();
        render(<WorkstationStatusBadge state="ORPHANED" lastError="SOMETHING_NEW" />);
        expect(screen.getByTitle(LAST_ERROR_FALLBACK_COPY)).toHaveTextContent("Orphaned");
        expect(describeLastError(undefined)).toBe(LAST_ERROR_FALLBACK_COPY);
    });
});

describe("describeEdhFailure", () => {
    it("names the AWS error code and the operation EDH called", () => {
        expect(
            describeEdhFailure({
                event: "placement_failed",
                errorCode: "InsufficientInstanceCapacity",
                operation: "CreateCapacityReservation",
                observedAt: OBSERVED_AT,
            })
        ).toBe("AWS: InsufficientInstanceCapacity (CreateCapacityReservation)");
    });

    it("names the AWS error code alone when no operation was captured", () => {
        expect(
            describeEdhFailure({
                event: "placement_failed",
                errorCode: "VcpuLimitExceeded",
                observedAt: OBSERVED_AT,
            })
        ).toBe("AWS: VcpuLimitExceeded");
    });

    it("names the checkpoint of a desktop setup failure", () => {
        expect(
            describeEdhFailure({
                event: "session-failed",
                checkpoint: "dcv_session_create",
                observedAt: OBSERVED_AT,
            })
        ).toBe("Desktop setup failed at dcv_session_create");
    });

    it("prefers the AWS error code to the checkpoint when both were captured", () => {
        expect(
            describeEdhFailure({
                event: "session-failed",
                errorCode: "InsufficientInstanceCapacity",
                operation: "RunInstances",
                checkpoint: "instance_launch",
                observedAt: OBSERVED_AT,
            })
        ).toBe("AWS: InsufficientInstanceCapacity (RunInstances)");
    });

    it.each<[string, EdhFailure | null | undefined]>([
        ["null", null],
        ["absent", undefined],
        [
            "a failure with neither an error code nor a checkpoint",
            {
                event: "placement_failed",
                observedAt: OBSERVED_AT,
            },
        ],
        [
            "empty and null fields",
            {
                event: "session-failed",
                errorCode: "",
                operation: null,
                checkpoint: null,
                observedAt: OBSERVED_AT,
            },
        ],
    ])("returns null for %s", (_label, failure) => {
        expect(describeEdhFailure(failure)).toBeNull();
    });
});
