/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { FileVersionsTable } from "./FileVersionsTable";

jest.mock("../../../services/APIService", () => ({
    downloadAsset: jest.fn(),
    revertFileVersion: jest.fn(),
}));
jest.mock("../../../services/AssetVersionService", () => ({
    fetchFileVersions: jest.fn(),
}));

const mockCan = jest.fn();
jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: mockCan }),
}));

const { fetchFileVersions } = require("../../../services/AssetVersionService");

const version = (over: Record<string, any> = {}) => ({
    versionId: "v-current",
    lastModified: "2026-09-12T15:30:18+00:00",
    size: 188336,
    isLatest: true,
    storageClass: "STANDARD",
    isArchived: false,
    assetVersionIds: [],
    changeSource: "workflowExecution",
    changeUserId: "scheurik",
    changeWorkflowId: "conversion-3d-basic",
    changeWorkflowExecutionId: "571a47bf03d645f08f9c11606f4f251e",
    ...over,
});

/** Stands in for the execution detail page so the navigation itself is what gets asserted. */
const ExecutionProbe: React.FC = () => {
    const { executionId } = useParams();
    return <div data-testid="execution-page">{executionId}</div>;
};

function renderTable(versions: any[], onNavigateAway?: () => void) {
    fetchFileVersions.mockResolvedValue([true, { versions }]);
    return render(
        <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId"
                    element={
                        <FileVersionsTable
                            databaseId="db1"
                            assetId="a1"
                            filePath="a1/optimized/BoomBox.glb"
                            fileName="BoomBox.glb"
                            displayMode="modal"
                            visible
                            onNavigateAway={onNavigateAway}
                        />
                    }
                />
                <Route path="/executions/:executionId" element={<ExecutionProbe />} />
            </Routes>
        </MemoryRouter>
    );
}

describe("FileVersionsTable — View execution link", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockCan.mockReturnValue(true);
    });

    it("dismisses the host first, then navigates to the execution detail route", async () => {
        const onNavigateAway = jest.fn();
        renderTable([version()], onNavigateAway);

        const link = await screen.findByText("View execution");
        // The existing provenance rendering stays: the source is the primary text of the cell.
        expect(screen.getByText("Workflow Execution")).toBeInTheDocument();

        await userEvent.click(link);

        expect(onNavigateAway).toHaveBeenCalledTimes(1);
        const page = await screen.findByTestId("execution-page");
        expect(page).toHaveTextContent("571a47bf03d645f08f9c11606f4f251e");
    });

    it("renders the link only on rows written by a workflow execution", async () => {
        renderTable([
            version(),
            version({
                versionId: "v-older",
                isLatest: false,
                changeSource: "upload",
                changeUserId: "alice",
                changeWorkflowId: undefined,
                changeWorkflowExecutionId: undefined,
            }),
            version({
                versionId: "v-oldest",
                isLatest: false,
                changeSource: "fileCopy",
                changeWorkflowExecutionId: undefined,
                changeAssetIdFrom: "a0",
                changeAssetFilePathFrom: "/src/BoomBox.glb",
            }),
        ]);

        await screen.findByText("Upload");
        expect(screen.getAllByText("View execution")).toHaveLength(1);
    });

    it("hides the link when the caller may not read execution details", async () => {
        mockCan.mockReturnValue(false);
        renderTable([version()]);

        await screen.findByText("Workflow Execution");
        expect(screen.queryByText("View execution")).not.toBeInTheDocument();
        expect(mockCan).toHaveBeenCalledWith("GET", "/workflows/executions/{executionId}/details");
    });

    it("navigates without a host callback in container mode", async () => {
        renderTable([version()], undefined);
        await userEvent.click(await screen.findByText("View execution"));
        await waitFor(() => expect(screen.getByTestId("execution-page")).toBeInTheDocument());
    });
});
