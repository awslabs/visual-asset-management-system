/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TriggersEditor from "./TriggersEditor";
import { setTrigger } from "../api/workflows";

jest.mock("../api/queries", () => ({
    useTriggers: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [{ templateId: "t1", templateName: "Default" }] })),
}));
jest.mock("../api/workflows", () => ({
    setTrigger: jest.fn(),
    deleteTrigger: jest.fn(),
}));
const mockToast = { success: jest.fn(), error: jest.fn(), warning: jest.fn(), info: jest.fn() };
jest.mock("../components/ToastProvider", () => ({
    ...jest.requireActual("../components/ToastProvider"),
    useToast: () => mockToast,
}));

const storedTrigger = {
    triggerType: "fileUpload",
    triggerBaseType: "fileUpload",
    triggerId: "",
    enabled: true,
    inputFileFilters: { allow: ["*.glb", "*.laz"], exclude: ["*.tmp"] },
    defaultTemplateIds: { "GLOBAL:system-genai-metadata": "t1" },
};
const pipelineRefs = [{ pipelineId: "system-genai-metadata", pipelineDatabaseId: "GLOBAL" }];

const renderEditor = (systemLocked: boolean) => {
    const { useTriggers } = require("../api/queries");
    useTriggers.mockReturnValue({ data: [storedTrigger], isLoading: false });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={queryClient}>
            <TriggersEditor
                databaseId="GLOBAL"
                workflowId="system-genai-metadata"
                pipelineRefs={pipelineRefs}
                systemLocked={systemLocked}
            />
        </QueryClientProvider>
    );
};

describe("TriggersEditor on a system workflow", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (setTrigger as jest.Mock).mockResolvedValue([true, {}]);
    });

    it("withholds Add and Delete and keeps Edit", () => {
        renderEditor(true);
        expect(screen.queryByRole("button", { name: /Add file upload trigger/ })).toBeNull();
        expect(screen.queryByRole("button", { name: /Delete trigger/ })).toBeNull();
        expect(screen.getByRole("button", { name: "Edit trigger fileUpload" })).toBeInTheDocument();
    });

    it("lets only Enabled change and resends the stored filters and templates", async () => {
        const user = userEvent.setup();
        renderEditor(true);
        await user.click(screen.getByRole("button", { name: "Edit trigger fileUpload" }));
        expect(screen.getByLabelText("Add trigger allow filter")).toBeDisabled();
        expect(screen.getByLabelText("Trigger name")).toBeDisabled();
        const enabled = screen.getByLabelText("Enabled");
        expect(enabled).not.toBeDisabled();
        await user.click(enabled);
        await user.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() => expect(setTrigger).toHaveBeenCalledTimes(1));
        expect(setTrigger).toHaveBeenCalledWith("GLOBAL", "system-genai-metadata", "fileUpload", {
            triggerType: "fileUpload",
            enabled: false,
            inputFileFilters: { allow: ["*.glb", "*.laz"], exclude: ["*.tmp"] },
            defaultTemplateIds: { "GLOBAL:system-genai-metadata": "t1" },
        });
    });

    it("offers the full editor on an ordinary workflow", () => {
        renderEditor(false);
        expect(screen.getByRole("button", { name: /Add file upload trigger/ })).toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: "Delete trigger fileUpload" })
        ).toBeInTheDocument();
    });
});
