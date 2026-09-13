/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { EnhancedFileManager } from "./EnhancedFileManager";
import { AssetDetailContext } from "../../context/AssetDetailContext";

jest.mock("../../services/APIService", () => ({
    fetchAssetS3FilesStreaming: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
}));

// The quick view fetches its own execution through this hook; the manager is what is under test,
// so the hook is stubbed the same way ExecutionQuickView's own suite stubs it.
jest.mock("../../features/orchestration/api/queries", () => ({
    useExecutionDetails: jest.fn(),
}));

// The details panel is replaced by a stand-in that follows the provenance link through the
// context the manager provides, which is the contract this suite exercises.
jest.mock("./components/FileDetailsPanel", () => ({
    FileDetailsPanel: () => {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const { useContext: useCtx } = require("react");
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const { FileManagerContext: Ctx } = require("./components/FileTreeView");
        const ctx = useCtx(Ctx);
        return (
            <button onClick={() => ctx?.onViewExecution?.("571a47bf03d645f08f9c11606f4f251e")}>
                View execution
            </button>
        );
    },
}));
jest.mock("./modals/AssetPreviewModal", () => ({
    __esModule: true,
    default: () => null,
}));

const { fetchAssetS3FilesStreaming } = require("../../services/APIService");
const { useExecutionDetails } = require("../../features/orchestration/api/queries");

const ExecutionProbe: React.FC = () => {
    const { executionId } = useParams();
    return <div data-testid="execution-page">{executionId}</div>;
};

function renderFileManager() {
    return render(
        <MemoryRouter initialEntries={["/databases/db1/assets/asset1"]}>
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId"
                    element={
                        <AssetDetailContext.Provider
                            value={{ state: {} as any, dispatch: jest.fn() }}
                        >
                            <EnhancedFileManager assetName="TestAsset" />
                        </AssetDetailContext.Provider>
                    }
                />
                <Route path="/executions/:executionId" element={<ExecutionProbe />} />
            </Routes>
        </MemoryRouter>
    );
}

describe("EnhancedFileManager hosts the execution quick view", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAssetS3FilesStreaming as jest.Mock).mockImplementation(async function* () {
            yield {
                success: true,
                items: [],
                nextToken: null,
                error: null,
                pageNumber: 1,
                isLastPage: true,
            };
        });
        useExecutionDetails.mockReturnValue({
            data: {
                workflowExecutionId: "571a47bf03d645f08f9c11606f4f251e",
                workflowId: "conversion-3d-basic",
                workflowDatabaseId: "GLOBAL",
                executionStatus: "SUCCEEDED",
                outputLocationType: "asset",
                outputDatabaseId: "db1",
                outputAssetId: "asset1",
                inputFiles: [],
                outputs: { files: [{ relativeFilePath: "/optimized/BoomBox.glb" }] },
            },
            isLoading: false,
            error: null,
        });
    });

    it("does not mount the quick view until a provenance link is followed", async () => {
        renderFileManager();
        await screen.findByText("View execution");
        expect(screen.queryByText("Execution Details")).not.toBeInTheDocument();
        expect(useExecutionDetails).not.toHaveBeenCalled();
    });

    it("opens the quick view for the linked execution and closes it again", async () => {
        renderFileManager();
        await userEvent.click(await screen.findByText("View execution"));

        // The drawer is a dialog portalled to document.body, titled by the quick view.
        const dialog = await screen.findByRole("dialog");
        expect(dialog).toHaveTextContent("Execution Details");
        expect(dialog).toHaveTextContent("571a47bf03d645f08f9c11606f4f251e");
        expect(dialog).toHaveTextContent("/optimized/BoomBox.glb");
        expect(useExecutionDetails).toHaveBeenCalledWith("571a47bf03d645f08f9c11606f4f251e");
        // The module's styles are scoped to this root; the panel carries it so it renders themed.
        expect(dialog.className).toContain("orchestration-root");

        await userEvent.click(screen.getByRole("button", { name: /close panel/i }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("closes on Escape", async () => {
        renderFileManager();
        await userEvent.click(await screen.findByText("View execution"));
        const dialog = await screen.findByRole("dialog");

        fireEvent.keyDown(dialog, { key: "Escape" });
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("keeps the quick view's own full-details navigation working from the asset page", async () => {
        renderFileManager();
        await userEvent.click(await screen.findByText("View execution"));
        await screen.findByRole("dialog");

        await userEvent.click(screen.getByText(/Open full details/));

        const page = await screen.findByTestId("execution-page");
        expect(page).toHaveTextContent("571a47bf03d645f08f9c11606f4f251e");
        // Following the link closes the panel, as it does on the executions board.
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });
});
