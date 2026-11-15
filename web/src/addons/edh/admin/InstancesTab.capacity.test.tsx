/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
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
    edhState: "running",
    owner: "vabcdefghij23456",
    name: "Reserved box",
    createdAt: "2026-10-23T10:00:00Z",
    updatedAt: "2026-10-23T10:05:00Z",
    userId: "user@example.com",
    bootstrapId: "b-1",
    state: "ACTIVE",
    lastError: null,
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.16xlarge",
    ...overrides,
});

const query = (data: EdhAdminInstance[]) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

describe("InstancesTab capacity", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (adminQueries.useEdhStaleInstances as jest.Mock).mockReturnValue(query([]));
        (adminQueries.useEdhAdminMutations as jest.Mock).mockReturnValue({
            stopInstance: { mutateAsync: jest.fn(), isPending: false },
            deactivateInstance: { mutateAsync: jest.fn(), isPending: false },
        });
    });

    it("shows each session's capacity as My Workstations does", () => {
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(
            query([
                session({
                    capacityReservationId: "cr-0123456789abcdef0",
                    capacityReservationLabel: "Render pool",
                    capacityReservationFallback: false,
                }),
                session({
                    sessionId: "s-2",
                    bootstrapId: "b-2",
                    name: "Overflow box",
                    capacityReservationId: null,
                    capacityReservationLabel: null,
                    capacityReservationFallback: true,
                }),
                session({ sessionId: "s-3", bootstrapId: "b-3", name: "Plain box" }),
            ])
        );
        render(<InstancesTab can={() => true} />);
        expect(
            screen.getByText("Capacity: Render pool (cr-0123456789abcdef0)")
        ).toBeInTheDocument();
        expect(screen.getByText("Capacity: On-demand (reservation full)")).toBeInTheDocument();
        expect(screen.getAllByText(/^Capacity:/)).toHaveLength(2);
    });

    it("explains an orphaned launch refused for a full reservation", () => {
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(
            query([session({ state: "ORPHANED", sessionId: null, lastError: "RESERVATION_FULL" })])
        );
        render(<InstancesTab can={() => true} />);
        expect(screen.getByText(/capacity reservation had no free capacity/i)).toBeInTheDocument();
    });
});
