/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ExecutionQuickView from "./ExecutionQuickView";

jest.mock("../api/queries", () => ({
    useExecutionDetails: jest.fn(),
}));

const detail = (over: Record<string, any> = {}) => ({
    workflowExecutionId: "e1",
    workflowId: "wf-1",
    workflowDatabaseId: "db-1",
    executionStatus: "SUCCEEDED",
    outputLocationType: "asset",
    outputDatabaseId: "out-db",
    outputAssetId: "out-asset",
    inputFiles: [{ databaseId: "db-1", assetId: "a-1", inputAssetFileKey: "/a-1/models/pump.laz" }],
    outputs: { files: [{ relativeFilePath: "pump.previewFile.gif" }] },
    ...over,
});

const renderPanel = (over: Record<string, any> = {}) => {
    const { useExecutionDetails } = require("../api/queries");
    useExecutionDetails.mockReturnValue({ data: detail(over), isLoading: false, error: null });
    render(
        <MemoryRouter>
            <ExecutionQuickView open onClose={jest.fn()} executionId="e1" />
        </MemoryRouter>
    );
};

describe("ExecutionQuickView", () => {
    beforeEach(() => jest.clearAllMocks());

    // The side panel is a distinct component from the full detail page, so the output-target and
    // input-path presentation has to be asserted here too — fixing only the detail page left the panel
    // showing the old shape.
    it("states the output target before the output file list", () => {
        renderPanel();
        expect(screen.getByText("Output Target")).toBeInTheDocument();
        expect(screen.getByText("Output Type")).toBeInTheDocument();
        // Scoped to the Output Type row: the Output Path Prefix row renders "None (asset root)" for a
        // run with no prefix, so a bare "asset" lookup is now ambiguous.
        const typeRow = screen.getByText("Output Type").closest("div")!;
        expect(typeRow).toHaveTextContent("asset");
        expect(screen.getByText("Output Database ID")).toBeInTheDocument();
        expect(screen.getByText("out-db")).toBeInTheDocument();
        expect(screen.getByText("Output Asset ID")).toBeInTheDocument();
        expect(screen.getByText("out-asset")).toBeInTheDocument();
    });

    it("labels a results-only run rather than showing an empty target", () => {
        renderPanel({ outputLocationType: "none", outputDatabaseId: "", outputAssetId: "" });
        expect(screen.getByText("Results only (no asset output)")).toBeInTheDocument();
    });

    it("shows an input file's path within the asset, without the asset id", () => {
        // The stored key is "/{assetId}/path"; the panel additionally prepended "{assetId}:", so the
        // id appeared twice and pushed the actual path out of view.
        renderPanel();
        expect(screen.getByText("/models/pump.laz")).toBeInTheDocument();
        expect(screen.queryByText(/a-1:/)).not.toBeInTheDocument();
        expect(screen.queryByText("/a-1/models/pump.laz")).not.toBeInTheDocument();
    });

    it("leaves a path that does not start with the asset id untouched", () => {
        renderPanel({
            inputFiles: [{ assetId: "a-1", inputAssetFileKey: "/other/file.glb" }],
        });
        expect(screen.getByText("/other/file.glb")).toBeInTheDocument();
    });

    it("renders a whole-asset selection as '/'", () => {
        renderPanel({ inputFiles: [{ assetId: "a-1", inputAssetFileKey: "/a-1" }] });
        expect(screen.getByText("/")).toBeInTheDocument();
    });
});
