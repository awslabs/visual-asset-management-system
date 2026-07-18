/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import PipelinesPage from "./PipelinesPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../permissions/useAllowedRoutes";

// Mock the queries module
jest.mock("../api/queries", () => ({
    usePipelines: jest.fn(),
    useArchivePipeline: jest.fn(),
}));

// Mock the useAllowedRoutes hook
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            retry: false,
        },
    },
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
        (queries.usePipelines as jest.Mock).mockReturnValue({
            data: mockPipelines,
            isLoading: false,
            error: null,
        });

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

    it("hides Create button when user lacks POST permission", () => {
        (queries.usePipelines as jest.Mock).mockReturnValue({
            data: mockPipelines,
            isLoading: false,
            error: null,
        });

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
});
