/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { validateWorkflow } from "./workflowValidation";
import { Workflow, Pipeline } from "../types";

describe("validateWorkflow", () => {
    it("errors when results-only without arity none", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p" }],
                systemConfig: {
                    inputFileArity: "one",
                    outputTarget: { locationType: "none" },
                },
            } as any,
            {}
        );
        expect(r.errors.some((e) => /inputFileArity/i.test(e))).toBe(true);
    });

    it("warns when a referenced pipeline is archived", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: {},
            } as any,
            { "db:p": { archived: true } as any }
        );
        expect(r.warnings.length).toBeGreaterThan(0);
    });

    it("errors when specifiedPipelines is empty", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [],
                systemConfig: {},
            } as any,
            {}
        );
        expect(r.errors.some((e) => /at least one pipeline/i.test(e))).toBe(true);
    });

    it("errors when workflowId has invalid characters", () => {
        const r = validateWorkflow(
            {
                workflowId: "a b!",
                specifiedPipelines: [{ pipelineId: "p" }],
                systemConfig: {},
            } as any,
            {}
        );
        expect(r.errors.length).toBeGreaterThan(0);
    });

    it("does not error when results-only with arity none", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p" }],
                systemConfig: {
                    inputFileArity: "none",
                    outputTarget: { locationType: "none" },
                },
            } as any,
            {}
        );
        expect(r.errors.some((e) => /inputFileArity/i.test(e))).toBe(false);
    });

    it("does not error for a valid workflow", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: {
                    inputFileArity: "one",
                    outputTarget: { locationType: "asset" },
                },
            } as any,
            {
                "db:p": {
                    enabled: true,
                    archived: false,
                    systemConfig: { inputFileArity: "one" },
                } as any,
            }
        );
        expect(r.errors.length).toBe(0);
    });

    it("warns when a referenced pipeline is disabled", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: {},
            } as any,
            { "db:p": { enabled: false } as any }
        );
        expect(r.warnings.some((w) => /disabled/i.test(w))).toBe(true);
    });

    it("warns when workflow arity is one but pipeline requires multi", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileArity: "one" },
            } as any,
            {
                "db:p": {
                    systemConfig: { inputFileArity: "multi" },
                } as any,
            }
        );
        expect(r.warnings.some((w) => /arity/i.test(w))).toBe(true);
    });

    it("warns when workflow arity is none but pipeline requires one", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileArity: "none" },
            } as any,
            {
                "db:p": {
                    systemConfig: { inputFileArity: "one" },
                } as any,
            }
        );
        expect(r.warnings.some((w) => /arity/i.test(w))).toBe(true);
    });

    it("warns when workflow arity is none but pipeline requires multi", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileArity: "none" },
            } as any,
            {
                "db:p": {
                    systemConfig: { inputFileArity: "multi" },
                } as any,
            }
        );
        expect(r.warnings.some((w) => /arity/i.test(w))).toBe(true);
    });

    it("does not warn when pipeline is not in pipelinesById", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: {},
            } as any,
            {}
        );
        expect(r.warnings.length).toBe(0);
    });

    it.each(["https://example.com/d", "http://host/x", "", undefined])(
        "accepts a valid/empty subDashboardUrl: %s",
        (url) => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p" }],
                    systemConfig: {},
                    subDashboardUrl: url,
                } as any,
                {}
            );
            expect(r.errors.some((e) => /Sub-Dashboard URL/i.test(e))).toBe(false);
        }
    );

    it.each(["javascript:alert(1)", "data:text/html,x", "ftp://h/x", "//example.com"])(
        "errors on a dangerous/relative subDashboardUrl: %s",
        (url) => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p" }],
                    systemConfig: {},
                    subDashboardUrl: url,
                } as any,
                {}
            );
            expect(r.errors.some((e) => /Sub-Dashboard URL/i.test(e))).toBe(true);
        }
    );
});
