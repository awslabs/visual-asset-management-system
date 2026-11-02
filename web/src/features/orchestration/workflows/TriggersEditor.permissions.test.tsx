/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The trigger list offers Add and Edit only to a role whose API routes allow a trigger PUT, and
 * Delete only to one that allows a trigger DELETE, on an ordinary workflow and on a system one. The
 * listings are what `GET /auth/routes/api/allowed` reports for these routes: the shipped read-only,
 * database-user and global read-only templates list GET only, the database administrator lists all
 * three methods.
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as APIService from "../../../services/APIService";
import { useTriggers } from "../api/queries";
import { __resetCache } from "../permissions/useAllowedRoutes";
import TriggersEditor from "./TriggersEditor";

jest.mock("../../../services/APIService", () => ({
    fetchAllowedApiRoutes: jest.fn(),
}));
jest.mock("../api/queries", () => ({
    useTriggers: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
}));
jest.mock("../api/workflows", () => ({
    setTrigger: jest.fn(),
    deleteTrigger: jest.fn(),
}));

const mockFetchAllowedApiRoutes = APIService.fetchAllowedApiRoutes as jest.Mock;

const WORKFLOW = "/database/{databaseId}/workflows/{workflowId}";
const TRIGGERS = `${WORKFLOW}/triggers`;
const TRIGGER = `${TRIGGERS}/{triggerType}`;
const route = (path: string, methods: string[]) => ({ path, methods, category: "workflows" });

const READ_ONLY_ROUTES = [
    route(WORKFLOW, ["GET"]),
    route(TRIGGERS, ["GET"]),
    route(TRIGGER, ["GET"]),
];
const ADMIN_ROUTES = [
    route(WORKFLOW, ["GET", "PUT", "DELETE"]),
    route(TRIGGERS, ["GET"]),
    route(TRIGGER, ["GET", "PUT", "DELETE"]),
];
const SET_ONLY_ROUTES = [route(TRIGGERS, ["GET"]), route(TRIGGER, ["GET", "PUT"])];

const ADD_REASON = "Triggers cannot be added to a system workflow.";
const DELETE_REASON =
    "Triggers of a system workflow cannot be deleted. Use Edit to switch the trigger off.";

const stored = {
    triggerType: "fileUpload",
    triggerBaseType: "fileUpload",
    triggerId: "",
    enabled: true,
    inputFileFilters: { allow: ["*.glb"], exclude: [] },
};

async function renderAs(routes: ReturnType<typeof route>[], systemLocked: boolean) {
    mockFetchAllowedApiRoutes.mockResolvedValue([true, { routes, userId: "u1" }]);
    (useTriggers as jest.Mock).mockReturnValue({ data: [stored], isLoading: false });
    render(
        <QueryClientProvider client={new QueryClient()}>
            <TriggersEditor
                databaseId="db1"
                workflowId="wf-1"
                pipelineRefs={[]}
                systemLocked={systemLocked}
            />
        </QueryClientProvider>
    );
    // Let the resolved listing reach the hook's state before the controls are read.
    await act(() => Promise.resolve());
    expect(screen.getByText("*.glb")).toBeInTheDocument();
}

const addButton = () => screen.queryByRole("button", { name: /add file upload trigger/i });
const editButton = () => screen.queryByRole("button", { name: "Edit trigger fileUpload" });
const deleteButton = () => screen.queryByRole("button", { name: "Delete trigger fileUpload" });

describe("TriggersEditor actions by role", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
    });

    it.each([
        ["an ordinary", false],
        ["a system", true],
    ])("a read-only role sees no Add, Edit or Delete on %s workflow", async (_, locked) => {
        await renderAs(READ_ONLY_ROUTES, locked);
        expect(addButton()).not.toBeInTheDocument();
        expect(editButton()).not.toBeInTheDocument();
        expect(deleteButton()).not.toBeInTheDocument();
        // Decided on the listing, not on a check still loading.
        expect(mockFetchAllowedApiRoutes).toHaveBeenCalledTimes(1);
    });

    it("a role that may set but not delete triggers sees Add and Edit, and no Delete", async () => {
        await renderAs(SET_ONLY_ROUTES, false);
        expect(
            await screen.findByRole("button", { name: /add file upload trigger/i })
        ).toBeEnabled();
        expect(editButton()).toBeInTheDocument();
        expect(deleteButton()).not.toBeInTheDocument();
    });

    it("an administrator sees every action enabled on an ordinary workflow", async () => {
        await renderAs(ADMIN_ROUTES, false);
        const add = await screen.findByRole("button", { name: /add file upload trigger/i });
        expect(add).not.toHaveAttribute("aria-disabled");
        expect(editButton()).not.toHaveAttribute("aria-disabled");
        expect(deleteButton()).not.toHaveAttribute("aria-disabled");
    });

    it("an administrator sees Add and Delete disabled with the reason on a system workflow", async () => {
        await renderAs(ADMIN_ROUTES, true);
        const add = await screen.findByRole("button", { name: /add file upload trigger/i });
        expect(add).toHaveAttribute("aria-disabled", "true");
        expect(add).toHaveAttribute("aria-description", ADD_REASON);
        expect(deleteButton()).toHaveAttribute("aria-disabled", "true");
        expect(deleteButton()).toHaveAttribute("aria-description", DELETE_REASON);
        expect(editButton()).not.toHaveAttribute("aria-disabled");
    });
});
