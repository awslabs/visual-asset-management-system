/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TemplateEditor from "./TemplateEditor";
import type { Template } from "../types";

const mockDelete = jest.fn();

jest.mock("../api/queries", () => ({
    useTemplates: jest.fn(),
    usePipeline: jest.fn(() => ({ data: { pipelineName: "P1" } })),
    useTemplateMutations: jest.fn(() => ({
        createTemplate: { mutateAsync: jest.fn(), isPending: false },
        updateTemplate: { mutateAsync: jest.fn(), isPending: false },
        deleteTemplate: { mutateAsync: mockDelete, isPending: false },
    })),
}));

jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(() => ({ loading: false, can: () => true })),
}));

jest.mock("react-router-dom", () => ({
    useNavigate: () => jest.fn(),
    Link: ({ children }: any) => <span>{children}</span>,
}));

const mockToast = { success: jest.fn(), error: jest.fn(), warning: jest.fn(), info: jest.fn() };
jest.mock("../components/ToastProvider", () => ({
    ...jest.requireActual("../components/ToastProvider"),
    useToast: () => mockToast,
}));

const T_REF: Template = {
    pipelineDatabaseId: "db1",
    pipelineId: "p1",
    templateId: "t-referenced",
    templateName: "Referenced",
    description: "named by a trigger",
    configFormat: "json",
} as any;

const T_FREE: Template = {
    pipelineDatabaseId: "db1",
    pipelineId: "p1",
    templateId: "t-free",
    templateName: "Free",
    description: "referenced by nothing",
    configFormat: "json",
} as any;

const TRIGGER_WARNING =
    "this template was chosen as a default template by the trigger(s) of auto-triggered " +
    "workflow(s) 'db1:wf1' (trigger 'fileUpload'). Triggered executions of those workflows will " +
    "fail until each trigger picks a different default template for this pipeline.";

const setup = (templates: Template[]) => {
    const { useTemplates } = require("../api/queries");
    useTemplates.mockReturnValue({
        data: templates,
        isLoading: false,
        error: null,
        refetch: jest.fn(),
        isFetching: false,
    });
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
        <QueryClientProvider client={queryClient}>
            <TemplateEditor databaseId="db1" pipelineId="p1" />
        </QueryClientProvider>
    );
};

/** Click the Delete button on the row whose template name is `name`. */
const clickDelete = async (name: string) => {
    const row = screen.getByText(name).closest("div.orch-outline") as HTMLElement;
    expect(row).not.toBeNull();
    await userEvent.click(await within(row).findByRole("button", { name: "Delete" }));
};

/**
 * Deleting a template that a trigger still names as a default must SAY so.
 *
 * The delete is never blocked — the reference lives on the trigger row, which the template handler
 * does not own — so the response carries a `warnings` array beside `message`. Two things have to
 * happen for that to reach the operator: the api reader has to keep the array (covered in
 * api/pipelines.test.ts), and this board has to surface it. A trigger left naming a template that is
 * gone fails at template resolution on every subsequent upload, with nothing but a CloudWatch
 * warning to attribute it to.
 */
describe("TemplateEditor delete warnings", () => {
    let confirmSpy: jest.SpyInstance;

    beforeEach(() => {
        jest.clearAllMocks();
        // handleDelete gates on the NATIVE window.confirm. Unstubbed, jsdom returns undefined and
        // mutateAsync is never reached, so every assertion below would pass having deleted nothing.
        confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(true);
    });

    afterEach(() => {
        confirmSpy.mockRestore();
    });

    it("raises a WARNING toast, not a plain success, when the response carries warnings", async () => {
        mockDelete.mockResolvedValue({ message: "Template deleted", warnings: [TRIGGER_WARNING] });
        setup([T_REF]);
        await clickDelete("Referenced");

        await waitFor(() => expect(mockToast.warning).toHaveBeenCalled());
        expect(mockToast.success).not.toHaveBeenCalled();
        expect(mockToast.warning.mock.calls[0][1].description).toContain("default template");
        // The control that proves the click reached the mutation at all: without the confirm stub the
        // toast assertions above would be about a delete that never happened.
        expect(mockDelete).toHaveBeenCalledWith({
            databaseId: "db1",
            pipelineId: "p1",
            templateId: "t-referenced",
        });
        expect(mockDelete.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("leaves a durable role=status notice on the board naming the referencing workflow", async () => {
        // The warning toast expires after 5 seconds (ToastProvider's `warning` duration). A
        // permanently dead trigger needs a record that outlives it.
        mockDelete.mockResolvedValue({ message: "Template deleted", warnings: [TRIGGER_WARNING] });
        setup([T_REF]);
        await clickDelete("Referenced");

        const notice = await screen.findByRole("status");
        expect(notice).toHaveTextContent("db1:wf1");
        expect(notice).toHaveTextContent("fileUpload");
        expect(notice).toHaveTextContent(/will fail until each trigger picks a different default/i);
    });

    it("renders NO notice and a plain success toast for a delete referenced by nothing", async () => {
        // The paired arm. Without it, an always-rendered banner and a warning-toast-on-every-delete
        // both satisfy the two arms above, and neither is response-driven.
        mockDelete.mockResolvedValue({ message: "Template deleted", warnings: [] });
        setup([T_FREE]);
        await clickDelete("Free");

        await waitFor(() => expect(mockToast.success).toHaveBeenCalled());
        expect(mockToast.warning).not.toHaveBeenCalled();
        expect(screen.queryByRole("status")).toBeNull();
    });

    it("does not delete when the confirm is declined", async () => {
        // The confirm gate is load-bearing: a stub that always returns true would hide a regression
        // that removed it, and this board's delete is permanent.
        confirmSpy.mockReturnValue(false);
        setup([T_FREE]);
        await clickDelete("Free");
        expect(mockDelete).not.toHaveBeenCalled();
        expect(mockToast.success).not.toHaveBeenCalled();
        expect(mockToast.warning).not.toHaveBeenCalled();
    });
});
