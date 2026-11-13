/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The asset Compliance tab shares each compliance state it reads from the API with the asset page
 * header (D131): the header's badge read the state once per asset, so after Evaluate Now or another
 * tab action it kept the old state until the page reloaded. The tab announces the state it read for
 * its own asset, and only a state the API returned.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import { ComplianceTab } from "./ComplianceTab";

jest.mock("@cloudscape-design/components/table", () => ({
    __esModule: true,
    default: () => null,
}));

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useNavigate: () => jest.fn(),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../../../services/ComplianceService", () => ({
    ...jest.requireActual("../../../services/ComplianceService"),
    fetchComplianceState: jest.fn(),
    fetchEvaluationHistory: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../../../services/ComplianceService");

beforeEach(() => {
    jest.clearAllMocks();
    service().fetchEvaluationHistory.mockResolvedValue([
        true,
        { evaluations: [], nextToken: undefined },
    ]);
});

it("announces the state it read for its asset", async () => {
    const read = { databaseId: "db1", assetId: "asset-1", complianceState: "pending_evaluation" };
    service().fetchComplianceState.mockResolvedValue([true, read]);
    const heard: any[] = [];
    const stop = service().onComplianceStateAnnounced((...args: any[]) => heard.push(args));
    try {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(heard).toHaveLength(1));
        expect(heard[0]).toEqual(["db1", "asset-1", read]);
    } finally {
        stop();
    }
});

it("announces nothing when the state could not be read", async () => {
    service().fetchComplianceState.mockResolvedValue([false, "Access denied"]);
    const heard: any[] = [];
    const stop = service().onComplianceStateAnnounced((...args: any[]) => heard.push(args));
    try {
        render(<ComplianceTab databaseId="db1" assetId="asset-1" isActive />);
        await waitFor(() => expect(service().fetchEvaluationHistory).toHaveBeenCalled());
        expect(heard).toEqual([]);
    } finally {
        stop();
    }
});
