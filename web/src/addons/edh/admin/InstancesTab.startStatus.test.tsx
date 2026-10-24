/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import InstancesTab from "./InstancesTab";
import * as adminQueries from "../api/adminQueries";
import type { EdhAdminInstance } from "../api/admin";

jest.mock("../api/adminQueries", () => ({
    useEdhAdminInstances: jest.fn(),
    useEdhStaleInstances: jest.fn(),
    useEdhAdminMutations: jest.fn(),
}));

const session = (overrides: Partial<EdhAdminInstance>): EdhAdminInstance => ({
    sessionId: "s-1",
    edhState: "stopped",
    owner: "vabcdefghij23456",
    name: "Render box",
    createdAt: "2026-10-23T10:00:00Z",
    updatedAt: "2026-10-23T15:19:13Z",
    userId: "user@example.com",
    bootstrapId: "b-1",
    state: "STOPPED",
    lastError: null,
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.16xlarge",
    activity: null,
    startFailure: null,
    ...overrides,
});

const query = (data: EdhAdminInstance[]) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const rowOf = (name: string) => within(screen.getByText(name).closest("tr") as HTMLElement);

describe("InstancesTab start status", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (adminQueries.useEdhStaleInstances as jest.Mock).mockReturnValue(query([]));
        (adminQueries.useEdhAdminMutations as jest.Mock).mockReturnValue({
            stopInstance: { mutateAsync: jest.fn(), isPending: false },
            deactivateInstance: { mutateAsync: jest.fn(), isPending: false },
        });
    });

    it("shows Starting and Stopping from the session's activity, as My Workstations does", () => {
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(
            query([
                session({ activity: "starting" }),
                session({
                    sessionId: "s-2",
                    bootstrapId: "b-2",
                    name: "Closing box",
                    edhState: "stopping",
                    activity: "stopping",
                }),
            ])
        );
        render(<InstancesTab can={() => true} />);

        expect(rowOf("Render box").getByText("Starting", { selector: "span" })).toBeInTheDocument();
        expect(
            rowOf("Closing box").getByText("Stopping", { selector: "span" })
        ).toBeInTheDocument();
        // The EDH state column still names EDH's own state; the badge alone follows the activity.
        expect(screen.queryByText("Stopped", { selector: "span" })).not.toBeInTheDocument();
    });

    it("shows a start refused for capacity beneath the Stopped badge, with the AWS detail line", () => {
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(
            query([
                session({
                    lastError: "CAPACITY_UNAVAILABLE",
                    startFailure: {
                        errorCode: "InsufficientInstanceCapacity",
                        availabilityZone: "us-west-2b",
                        observedAt: "2026-10-23T15:19:30Z",
                    },
                }),
            ])
        );
        render(<InstancesTab can={() => true} />);

        const row = rowOf("Render box");
        expect(row.getByText("Stopped", { selector: "span" })).toBeInTheDocument();
        expect(
            row.getByText(
                "AWS had no capacity for g5.16xlarge in this workstation's Availability Zone (us-west-2b) when it was started. A stopped workstation keeps its disk and starts only in that zone, so it stays stopped; start it again later."
            )
        ).toBeInTheDocument();
        expect(
            row.getByText("AWS: InsufficientInstanceCapacity in us-west-2b")
        ).toBeInTheDocument();
    });
});
