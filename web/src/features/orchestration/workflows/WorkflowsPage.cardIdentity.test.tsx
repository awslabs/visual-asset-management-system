/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The page must give the grouped card list a STABLE per-card identity.
 *
 * Each card owns an uncontrolled Radix actions menu, so a list change that keeps a card's POSITION
 * while changing its contents hands an open menu to whichever workflow landed there — and the Archive
 * item then names, and acts on, a workflow the operator never opened the menu for. `PipelinesPage`
 * supplies a stable key for exactly this reason.
 *
 * This asserts the CONTRACT the page supplies. That per-item state genuinely follows a supplied
 * `getKey` across a reorder — and genuinely does NOT without one — is proved once, at the primitive,
 * in CategoryGroupedList.test.tsx, which carries both directions. Re-proving it here would be a test
 * of Radix's behaviour under jsdom rather than of this page.
 */

import React from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import WorkflowsPage from "./WorkflowsPage";

jest.mock("../api/queries", () => ({
    useWorkflows: jest.fn(),
    useWorkflowMutations: jest.fn(() => ({ archiveWorkflow: { mutateAsync: jest.fn() } })),
    useDatabases: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));

jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(() => ({ loading: false, can: () => true })),
}));

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useNavigate: () => jest.fn(),
}));

jest.mock("../wizard/ExecuteWizard", () => ({ __esModule: true, default: () => null }));

/** The props of every CategoryGroupedList the page rendered, newest last. */
const listProps: any[] = [];

jest.mock("../components/CategoryGroupedList", () => ({
    __esModule: true,
    default: (props: any) => {
        listProps.push(props);
        return <div data-testid="grouped-list" />;
    },
}));

const workflow = (id: string, name: string) => ({
    workflowId: id,
    workflowName: name,
    databaseId: "db1",
    category: "Processing",
    enabled: true,
    archived: false,
    specifiedPipelines: [{ pipelineId: "p1" }],
});

const ALPHA = workflow("wf-alpha", "Workflow Alpha");
// Same workflow id in a DIFFERENT database: ids are unique only within one, so a key built from the
// id alone would collide these two onto one card.
const ALPHA_ELSEWHERE = { ...workflow("wf-alpha", "Workflow Alpha"), databaseId: "db2" };
const BETA = workflow("wf-beta", "Workflow Beta");

const renderPage = (items: any[]) => {
    const { useWorkflows } = require("../api/queries");
    useWorkflows.mockReturnValue({
        data: { pages: [{ Items: items }], pageParams: [undefined] },
        isLoading: false,
        error: null,
        fetchNextPage: jest.fn(),
        hasNextPage: false,
        isFetchingNextPage: false,
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
        <QueryClientProvider client={queryClient}>
            <MemoryRouter>
                <WorkflowsPage databaseId="db1" />
            </MemoryRouter>
        </QueryClientProvider>
    );
    return listProps[listProps.length - 1];
};

describe("WorkflowsPage card identity", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        listProps.length = 0;
    });

    it("supplies a card key at all", () => {
        // Omitting it is the defect: CategoryGroupedList then falls back to the array index.
        expect(renderPage([ALPHA, BETA]).getKey).toBeInstanceOf(Function);
    });

    it("identifies a card by its database and workflow id, not its position", () => {
        const { getKey } = renderPage([ALPHA, BETA]);

        expect(getKey(ALPHA)).toBe("db1:wf-alpha");
        expect(getKey(BETA)).toBe("db1:wf-beta");
    });

    it("keeps two workflows sharing an id in different databases apart", () => {
        // Control: a key that collided these would reintroduce the same subtree-reuse hazard for the
        // pair, so the composite is load-bearing rather than decorative.
        const { getKey } = renderPage([ALPHA, ALPHA_ELSEWHERE]);

        expect(getKey(ALPHA)).not.toBe(getKey(ALPHA_ELSEWHERE));
    });
});
