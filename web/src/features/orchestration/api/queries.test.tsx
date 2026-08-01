/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { computeRefetchInterval, qk, useExecutions, useTemplateMutations } from "./queries";
import * as executionService from "./executions";
import * as pipelineService from "./pipelines";

jest.mock("./executions");
jest.mock("./pipelines");

describe("computeRefetchInterval", () => {
    it("polls only while a row is non-terminal", () => {
        expect(computeRefetchInterval([{ executionStatus: "RUNNING" }])).toBe(5000);
        expect(computeRefetchInterval([{ executionStatus: "SUCCEEDED" }])).toBe(false);
        expect(computeRefetchInterval([])).toBe(false);
    });

    it("returns 5000 if any row is NEW", () => {
        expect(computeRefetchInterval([{ executionStatus: "NEW" }])).toBe(5000);
    });

    it("returns 5000 if any row in a mixed set is non-terminal", () => {
        expect(
            computeRefetchInterval([
                { executionStatus: "SUCCEEDED" },
                { executionStatus: "RUNNING" },
                { executionStatus: "FAILED" },
            ])
        ).toBe(5000);
    });

    it("returns false if all rows are terminal", () => {
        expect(
            computeRefetchInterval([
                { executionStatus: "SUCCEEDED" },
                { executionStatus: "FAILED" },
                { executionStatus: "ABORTED" },
            ])
        ).toBe(false);
    });
});

describe("qk (query key factory)", () => {
    it("generates stable keys for pipelines", () => {
        expect(qk.pipelines()).toEqual(["pipelines", null, null]);
        expect(qk.pipelines("db1")).toEqual(["pipelines", "db1", null]);
        expect(qk.pipelines("db1", { includeArchived: true })).toEqual([
            "pipelines",
            "db1",
            { includeArchived: true },
        ]);
    });

    it("generates stable keys for a single pipeline", () => {
        expect(qk.pipeline("db1", "p1")).toEqual(["pipeline", "db1", "p1"]);
    });

    it("generates stable keys for templates", () => {
        expect(qk.templates("db1", "p1")).toEqual(["templates", "db1", "p1"]);
    });

    it("generates stable keys for workflows", () => {
        expect(qk.workflows()).toEqual(["workflows", null, null]);
        expect(qk.workflows("db1")).toEqual(["workflows", "db1", null]);
    });

    it("generates stable keys for a single workflow", () => {
        expect(qk.workflow("db1", "w1")).toEqual(["workflow", "db1", "w1"]);
    });

    it("generates stable keys for triggers", () => {
        expect(qk.triggers("db1", "w1")).toEqual(["triggers", "db1", "w1"]);
    });

    it("generates stable keys for executions (all scopes)", () => {
        expect(qk.executions({ kind: "global" })).toEqual(["executions", { kind: "global" }, null]);
        expect(qk.executions({ kind: "workflow", databaseId: "db1", workflowId: "w1" })).toEqual([
            "executions",
            { kind: "workflow", databaseId: "db1", workflowId: "w1" },
            null,
        ]);
        expect(
            qk.executions(
                { kind: "asset", databaseId: "db1", assetId: "a1" },
                { status: "RUNNING" }
            )
        ).toEqual([
            "executions",
            { kind: "asset", databaseId: "db1", assetId: "a1" },
            { status: "RUNNING" },
        ]);
    });

    it("generates stable keys for a single execution", () => {
        expect(qk.execution("exec1")).toEqual(["execution", "exec1"]);
    });

    it("generates stable keys for allowedRoutes", () => {
        expect(qk.allowedRoutes()).toEqual(["allowedRoutes"]);
    });
});

describe("useExecutions", () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    };

    beforeEach(() => {
        jest.clearAllMocks();
        (executionService.listExecutionsGlobal as jest.Mock).mockResolvedValue([
            true,
            { Items: [] },
        ]);
    });

    it("sends the workflow's database as workflowDatabaseId, the key the global list filters on", async () => {
        const { result } = renderHook(
            () => useExecutions({ kind: "workflow", databaseId: "db1", workflowId: "wf1" }),
            { wrapper }
        );

        await waitFor(() => expect(result.current.isSuccess).toBe(true));

        expect(executionService.listExecutionsGlobal).toHaveBeenCalledWith(
            expect.objectContaining({ workflowId: "wf1", workflowDatabaseId: "db1" })
        );
        const sent = (executionService.listExecutionsGlobal as jest.Mock).mock.calls[0][0];
        expect(sent.databaseId).toBeUndefined();
    });
});

describe("useTemplateMutations", () => {
    let client: QueryClient;
    let invalidated: any[][];

    const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    beforeEach(() => {
        jest.clearAllMocks();
        client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        invalidated = [];
        jest.spyOn(client, "invalidateQueries").mockImplementation((filters?: any) => {
            invalidated.push(filters?.queryKey);
            return Promise.resolve();
        });
    });

    it.each([
        ["createTemplate", { databaseId: "db1", pipelineId: "p1", body: {} as any }],
        ["updateTemplate", { databaseId: "db1", pipelineId: "p1", templateId: "t1", body: {} }],
        ["deleteTemplate", { databaseId: "db1", pipelineId: "p1", templateId: "t1" }],
    ])("%s invalidates the pipeline detail and list caches", async (name, vars) => {
        (pipelineService as any)[name].mockResolvedValue([true, {}]);
        const { result } = renderHook(() => useTemplateMutations(), { wrapper });

        await (result.current as any)[name].mutateAsync(vars);

        expect(invalidated).toEqual(
            expect.arrayContaining([
                qk.templates("db1", "p1"),
                qk.pipeline("db1", "p1"),
                ["pipelines"],
            ])
        );
    });
});
