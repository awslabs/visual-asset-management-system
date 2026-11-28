/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The archive confirmation opens from a row's menu, whose item closes with the menu, so it returns
 * focus to the row's actions button when it closes without archiving (Escape, Cancel, a rejected
 * archive), and to the list's search box once the archive has taken the row off the list. The
 * database picker returns focus to Create Pipeline.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import PipelinesPage from "./PipelinesPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../permissions/useAllowedRoutes";

jest.mock("../api/queries", () => ({
    usePipelines: jest.fn(),
    useArchivePipeline: jest.fn(),
    useDatabases: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));
jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));
jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));

const PIPELINE = {
    databaseId: "db1",
    pipelineId: "p1",
    pipelineName: "Pipeline One",
    category: "conversion",
    enabled: true,
    archived: false,
    executionConfig: { executionType: "Lambda" as const },
};

const archive = jest.fn();

const page = (databaseId?: string) => (
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <PipelinesPage databaseId={databaseId} />
        </MemoryRouter>
    </QueryClientProvider>
);

const actions = () =>
    screen.getByRole("button", { name: "Actions for Pipeline One", hidden: true });

const openArchive = async (user: ReturnType<typeof userEvent.setup>) => {
    actions().focus();
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("menuitem", { name: "Archive" }));
    return screen.findByRole("dialog", { name: "Archive Pipeline One" });
};

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

describe("PipelinesPage dialogs return focus", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });
        (queries.useArchivePipeline as jest.Mock).mockReturnValue({
            mutateAsync: archive,
            isPending: false,
        });
        (queries.usePipelines as jest.Mock).mockReturnValue({
            data: { pages: [{ Items: [PIPELINE] }], pageParams: [undefined] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            refetch: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
            isFetching: false,
        });
    });

    it("returns focus to the row's actions button after Escape and Cancel", async () => {
        const user = userEvent.setup({ delay: null });
        render(page("db1"));
        await openArchive(user);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());

        await user.click(within(await openArchive(user)).getByRole("button", { name: "Cancel" }));
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());
    });

    it("returns focus to the search box once the archive took the row off the list", async () => {
        const user = userEvent.setup({ delay: null });
        archive.mockResolvedValue({});
        render(page("db1"));
        await user.click(within(await openArchive(user)).getByRole("button", { name: "Archive" }));
        await closed();
        await waitFor(() => expect(screen.getByRole("textbox", { name: "Search" })).toHaveFocus());
    });

    it("returns focus to the row's actions button after a rejected archive", async () => {
        const user = userEvent.setup({ delay: null });
        archive.mockRejectedValue(new Error("denied"));
        render(page("db1"));
        await user.click(within(await openArchive(user)).getByRole("button", { name: "Archive" }));
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());
    });

    it("returns focus to Create Pipeline when the database picker is cancelled", async () => {
        const user = userEvent.setup({ delay: null });
        render(page());
        const create = screen.getByRole("button", { name: "Create Pipeline" });
        create.focus();
        await user.keyboard("{Enter}");
        const picker = await screen.findByRole("dialog", { name: "Create pipeline in database" });
        await user.click(within(picker).getByRole("button", { name: "Cancel" }));
        await closed();
        await waitFor(() => expect(create).toHaveFocus());
    });
});
