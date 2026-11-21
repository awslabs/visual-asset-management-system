/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PipelineForm from "./PipelineForm";
import type { Pipeline } from "../types";

const mockUpdate = jest.fn();
jest.mock("../api/queries", () => ({
    useCreatePipeline: jest.fn(() => ({ mutateAsync: jest.fn(), isPending: false })),
    useUpdatePipeline: jest.fn(() => ({ mutateAsync: mockUpdate, isPending: false })),
}));
jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));
// The role's route listing: by default every route, as for an administrator.
let mockCan: (method: string, path: string) => boolean = () => true;
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        failed: false,
        retry: jest.fn(),
        can: (method: string, path: string) => mockCan(method, path),
    }),
}));
const mockToast = { success: jest.fn(), error: jest.fn(), warning: jest.fn(), info: jest.fn() };
jest.mock("../components/ToastProvider", () => ({
    ...jest.requireActual("../components/ToastProvider"),
    useToast: () => mockToast,
}));

const systemPipeline: Pipeline = {
    databaseId: "GLOBAL",
    pipelineId: "system-sample",
    pipelineName: "System Sample",
    category: "SYSTEM - Sample",
    description: "Shipped pipeline",
    enabled: true,
    archived: false,
    isSystem: true,
    executionConfig: { executionType: "Lambda", waitForCallback: "Enabled", taskTimeout: "900" },
    systemConfig: {
        inputFileArity: "one",
        assetScope: {},
        metadataInputs: {},
        requireTemplate: true,
    },
};

const renderEdit = (initial: Pipeline) =>
    render(<PipelineForm mode="edit" databaseId="GLOBAL" initial={initial} onDone={jest.fn()} />);

describe("PipelineForm for a system pipeline", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockUpdate.mockResolvedValue({});
        mockCan = () => true;
    });

    it("locks every field except Enabled and says why", () => {
        renderEdit(systemPipeline);
        expect(screen.getByText(/System pipeline:/)).toBeInTheDocument();
        expect(screen.getByLabelText("Pipeline Name *")).toBeDisabled();
        expect(screen.getByLabelText("Category")).toBeDisabled();
        expect(screen.getByLabelText("Execution Type *")).toBeDisabled();
        expect(screen.getByLabelText("Enabled")).not.toBeDisabled();
    });

    it("saves only the enabled flag", async () => {
        const user = userEvent.setup();
        renderEdit(systemPipeline);
        await user.click(screen.getByLabelText("Enabled"));
        await user.click(screen.getByRole("button", { name: "Update" }));
        await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
        expect(mockUpdate).toHaveBeenCalledWith({
            databaseId: "GLOBAL",
            pipelineId: "system-sample",
            body: { enabled: false },
        });
        expect(mockToast.success).toHaveBeenCalled();
    });

    it("keeps an ordinary pipeline fully editable and sends the full body", async () => {
        const user = userEvent.setup();
        renderEdit({ ...systemPipeline, pipelineId: "mine", isSystem: false });
        expect(screen.queryByText(/System pipeline:/)).toBeNull();
        expect(screen.getByLabelText("Pipeline Name *")).not.toBeDisabled();
        await user.click(screen.getByRole("button", { name: "Update" }));
        await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
        expect(mockUpdate.mock.calls[0][0].body.pipelineName).toBe("System Sample");
        expect(mockUpdate.mock.calls[0][0].body.executionConfig).toBeDefined();
    });

    it("titles a system pipeline View, and an ordinary one Edit", () => {
        const { unmount } = renderEdit(systemPipeline);
        expect(screen.getByText("View Pipeline")).toBeInTheDocument();
        expect(screen.queryByText("Edit Pipeline")).toBeNull();
        unmount();
        renderEdit({ ...systemPipeline, pipelineId: "mine", isSystem: false });
        expect(screen.getByText("Edit Pipeline")).toBeInTheDocument();
        expect(screen.queryByText("View Pipeline")).toBeNull();
    });

    it("offers Enabled and Update only to a role that may update the pipeline", async () => {
        // A role from the database-readonly template lists the pipeline routes for GET only.
        mockCan = (method) => method === "GET";
        const user = userEvent.setup();
        renderEdit(systemPipeline);

        const enabled = screen.getByLabelText("Enabled");
        const update = screen.getByRole("button", { name: "Update" });
        const reason = "Your role does not allow changing this pipeline.";
        expect(enabled).toHaveAttribute("aria-disabled", "true");
        expect(enabled).toHaveAttribute("aria-description", reason);
        expect(update).toHaveAttribute("aria-disabled", "true");
        expect(update).toHaveAttribute("aria-description", reason);

        await user.click(enabled);
        expect(enabled).toBeChecked();
        await user.click(update);
        expect(mockUpdate).not.toHaveBeenCalled();
    });
});
