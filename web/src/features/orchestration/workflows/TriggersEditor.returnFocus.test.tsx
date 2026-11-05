/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The trigger delete confirmation returns focus to the Delete button that opened it when it closes
 * without deleting, and, since a deleted trigger's row leaves the list, to the Add button of the
 * trigger's type after the delete, or to another trigger's row when the role may not add.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TriggersEditor from "./TriggersEditor";
import type { WorkflowTrigger } from "../types";

jest.mock("../api/queries", () => ({
    useTriggers: jest.fn(),
    useTemplates: jest.fn(() => ({ data: [], isLoading: false })),
}));
jest.mock("../api/workflows", () => ({ setTrigger: jest.fn(), deleteTrigger: jest.fn() }));
jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));

const { useTriggers } = jest.requireMock("../api/queries");
const { deleteTrigger } = jest.requireMock("../api/workflows");
const { useAllowedRoutes } = jest.requireMock("../permissions/useAllowedRoutes");

const trigger = (triggerType: string) =>
    ({
        triggerType,
        triggerBaseType: "fileUpload",
        triggerId: "",
        enabled: true,
    } as WorkflowTrigger);

const renderEditor = (triggers: WorkflowTrigger[]) => {
    useTriggers.mockReturnValue({ data: triggers, isLoading: false });
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
        <QueryClientProvider client={queryClient}>
            <TriggersEditor
                databaseId="db1"
                workflowId="wf-1"
                pipelineRefs={[{ pipelineId: "p1", pipelineDatabaseId: "db1" }]}
            />
        </QueryClientProvider>
    );
};

const openDelete = async (user: ReturnType<typeof userEvent.setup>, key: string) => {
    const opener = screen.getByRole("button", { name: `Delete trigger ${key}`, hidden: true });
    opener.focus();
    await user.keyboard("{Enter}");
    return { opener, dialog: await screen.findByRole("dialog", { name: "Confirm Delete" }) };
};

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

describe("TriggersEditor delete confirmation returns focus", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        // The delete stays in flight, so the row is still listed as the dialog closes.
        deleteTrigger.mockImplementation(() => new Promise(jest.fn()));
        useAllowedRoutes.mockReturnValue({ loading: false, can: () => true });
    });

    it("returns focus to the Delete button after Cancel, and to the type's Add button after the delete", async () => {
        const user = userEvent.setup({ delay: null });
        renderEditor([trigger("fileUpload")]);
        const cancelled = await openDelete(user, "fileUpload");
        await user.click(within(cancelled.dialog).getByRole("button", { name: "Cancel" }));
        await closed();
        await waitFor(() => expect(cancelled.opener).toHaveFocus());

        const { dialog } = await openDelete(user, "fileUpload");
        await user.click(within(dialog).getByRole("button", { name: "Delete" }));
        await closed();
        expect(deleteTrigger).toHaveBeenCalledTimes(1);
        await waitFor(() =>
            expect(screen.getByRole("button", { name: /^Add .* trigger$/ })).toHaveFocus()
        );
    });

    it("returns focus to another trigger's row after the delete when the role may not add", async () => {
        const user = userEvent.setup({ delay: null });
        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: (method: string) => method === "DELETE",
        });
        renderEditor([trigger("fileUpload"), trigger("fileUpload#2")]);
        expect(screen.queryByRole("button", { name: /^Add .* trigger$/ })).toBeNull();
        const { dialog } = await openDelete(user, "fileUpload#2");
        await user.click(within(dialog).getByRole("button", { name: "Delete" }));
        await closed();
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "Delete trigger fileUpload" })).toHaveFocus()
        );
    });

    it("returns focus to the trigger list after deleting the only trigger when the role may not add", async () => {
        const user = userEvent.setup({ delay: null });
        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: (method: string) => method === "DELETE",
        });
        renderEditor([trigger("fileUpload")]);
        const { dialog } = await openDelete(user, "fileUpload");
        await user.click(within(dialog).getByRole("button", { name: "Delete" }));
        await closed();
        await waitFor(() => expect(screen.getByRole("group", { name: "Triggers" })).toHaveFocus());
        expect(document.body).not.toHaveFocus();
    });
});
