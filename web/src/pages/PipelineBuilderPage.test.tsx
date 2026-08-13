/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import PipelineBuilderPage from "./PipelineBuilderPage";

const mockPipelineForm = jest.fn();
jest.mock("../features/orchestration/pipelines/PipelineForm", () => ({
    __esModule: true,
    default: (props: any) => {
        mockPipelineForm(props);
        return <div data-testid="pipeline-form" />;
    },
}));

const mockUsePipeline = jest.fn();
jest.mock("../features/orchestration/api/queries", () => ({
    usePipeline: (databaseId: string, pipelineId: string) =>
        mockUsePipeline(databaseId, pipelineId),
}));

const renderAt = (path: string) =>
    render(
        <MemoryRouter initialEntries={[path]}>
            <Routes>
                <Route
                    path="/databases/:databaseId/pipelines/create"
                    element={<PipelineBuilderPage />}
                />
                <Route
                    path="/databases/:databaseId/pipelines/:pipelineId/edit"
                    element={<PipelineBuilderPage />}
                />
            </Routes>
        </MemoryRouter>
    );

describe("PipelineBuilderPage", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("renders a loading state while the pipeline is fetched in edit mode", () => {
        mockUsePipeline.mockReturnValue({ data: undefined, isLoading: true, isError: false });

        renderAt("/databases/db1/pipelines/pipe-1/edit");

        expect(screen.getByText(/Loading pipeline/i)).toBeInTheDocument();
        expect(mockPipelineForm).not.toHaveBeenCalled();
    });

    it("does not render the edit form when the pipeline fetch errors", () => {
        mockUsePipeline.mockReturnValue({ data: undefined, isLoading: false, isError: true });

        renderAt("/databases/db1/pipelines/pipe-1/edit");

        expect(screen.getByText("Pipeline not found")).toBeInTheDocument();
        expect(screen.queryByTestId("pipeline-form")).not.toBeInTheDocument();
        expect(mockPipelineForm).not.toHaveBeenCalled();
    });

    it("does not render the edit form when the fetch resolves with no pipeline", () => {
        mockUsePipeline.mockReturnValue({ data: undefined, isLoading: false, isError: false });

        renderAt("/databases/db1/pipelines/pipe-1/edit");

        expect(screen.getByText("Pipeline not found")).toBeInTheDocument();
        expect(mockPipelineForm).not.toHaveBeenCalled();
    });

    it("renders the edit form seeded with the loaded pipeline", () => {
        const pipeline = { databaseId: "db1", pipelineId: "pipe-1", pipelineName: "Convert" };
        mockUsePipeline.mockReturnValue({ data: pipeline, isLoading: false, isError: false });

        renderAt("/databases/db1/pipelines/pipe-1/edit");

        expect(mockPipelineForm).toHaveBeenCalledWith(
            expect.objectContaining({ mode: "edit", databaseId: "db1", initial: pipeline })
        );
    });

    it("renders the create form without waiting on a pipeline fetch", () => {
        mockUsePipeline.mockReturnValue({ data: undefined, isLoading: false, isError: false });

        renderAt("/databases/db1/pipelines/create");

        expect(screen.queryByText("Pipeline not found")).not.toBeInTheDocument();
        expect(mockPipelineForm).toHaveBeenCalledWith(
            expect.objectContaining({ mode: "create", databaseId: "db1", initial: undefined })
        );
    });
});
