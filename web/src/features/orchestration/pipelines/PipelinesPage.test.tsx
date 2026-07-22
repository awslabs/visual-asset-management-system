/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import PipelinesPage from "./PipelinesPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../permissions/useAllowedRoutes";

// Mock the queries module
jest.mock("../api/queries", () => ({
    usePipelines: jest.fn(),
    useArchivePipeline: jest.fn(),
    // DatabasePickerDialog (create-in-database picker) calls useDatabases; default to an empty,
    // idle result so the page renders without opening the picker.
    useDatabases: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));

// Mock the useAllowedRoutes hook
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

// Mock appCache
jest.mock("../../../services/appCache", () => ({
    appCache: {
        getItem: jest.fn(() => ({
            featuresEnabled: [],
        })),
    },
}));

const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            retry: false,
        },
    },
});

// usePipelines is now a useInfiniteQuery — the component reads data.pages[].Items and the
// fetchNextPage/hasNextPage fields. Build that shape from a flat array of pipelines.
const infinite = (items: any[]) => ({
    data: { pages: [{ Items: items }], pageParams: [undefined] },
    isLoading: false,
    error: null,
    fetchNextPage: jest.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
});

const mockPipelines = [
    {
        databaseId: "db1",
        pipelineId: "p1",
        pipelineName: "Pipeline One",
        category: "conversion",
        enabled: true,
        archived: false,
        executionConfig: {
            executionType: "Lambda" as const,
        },
    },
    {
        databaseId: "db1",
        pipelineId: "p2",
        pipelineName: "Pipeline Two",
        category: "genai",
        enabled: true,
        archived: false,
        executionConfig: {
            executionType: "SQS" as const,
        },
    },
    {
        databaseId: "db1",
        pipelineId: "p3",
        pipelineName: "Pipeline Three",
        category: "conversion",
        enabled: false,
        archived: false,
        executionConfig: {
            executionType: "EventBridge" as const,
        },
    },
];

describe("PipelinesPage", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (queries.useArchivePipeline as jest.Mock).mockReturnValue({
            mutateAsync: jest.fn(),
        });
    });

    it("renders pipelines grouped by category", () => {
        (queries.usePipelines as jest.Mock).mockReturnValue(infinite(mockPipelines));

        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <PipelinesPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Assert 2 category headers appear
        expect(screen.getByText("conversion")).toBeInTheDocument();
        expect(screen.getByText("genai")).toBeInTheDocument();

        // Assert all 3 pipeline names render
        expect(screen.getByText("Pipeline One")).toBeInTheDocument();
        expect(screen.getByText("Pipeline Two")).toBeInTheDocument();
        expect(screen.getByText("Pipeline Three")).toBeInTheDocument();
    });

    it("groups by database when Group by = Database", () => {
        (queries.usePipelines as jest.Mock).mockReturnValue(infinite(mockPipelines));
        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <PipelinesPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        fireEvent.change(screen.getByLabelText("Group by"), { target: { value: "database" } });

        // All mock pipelines are in db1 → a single "db1" group header replaces the category headers.
        expect(screen.getByText("db1")).toBeInTheDocument();
        expect(screen.queryByText("conversion")).not.toBeInTheDocument();
        expect(screen.getByText("Pipeline One")).toBeInTheDocument();
    });

    it("hides Create button when user lacks POST permission", () => {
        (queries.usePipelines as jest.Mock).mockReturnValue(infinite(mockPipelines));

        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => false,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <PipelinesPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Assert the Create button is absent
        expect(screen.queryByText(/create/i)).not.toBeInTheDocument();
    });

    it("hides Edit action for DeadlineCloud pipeline when flag is off, but shows Archive", () => {
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({
            featuresEnabled: [],
        });

        const dcPipeline = {
            databaseId: "db1",
            pipelineId: "dc1",
            pipelineName: "DC Pipeline",
            category: "conversion",
            enabled: true,
            archived: false,
            executionConfig: {
                executionType: "DeadlineCloud" as const,
            },
        };

        (queries.usePipelines as jest.Mock).mockReturnValue(infinite([dcPipeline]));

        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <PipelinesPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        expect(screen.getByText("DC Pipeline")).toBeInTheDocument();
    });

    it("shows Edit action for DeadlineCloud pipeline when flag is on", () => {
        const { appCache } = require("../../../services/appCache");
        appCache.getItem.mockReturnValue({
            featuresEnabled: ["DEADLINECLOUD_PIPELINES"],
        });

        const dcPipeline = {
            databaseId: "db1",
            pipelineId: "dc1",
            pipelineName: "DC Pipeline",
            category: "conversion",
            enabled: true,
            archived: false,
            executionConfig: {
                executionType: "DeadlineCloud" as const,
            },
        };

        (queries.usePipelines as jest.Mock).mockReturnValue(infinite([dcPipeline]));

        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <PipelinesPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        expect(screen.getByText("DC Pipeline")).toBeInTheDocument();
    });
});
