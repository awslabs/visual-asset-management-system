/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { computeRefetchInterval, qk } from "./queries";

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
