/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { validateWorkflow, allPipelineRefsSelected } from "./workflowValidation";
import { Workflow, Pipeline } from "../types";

describe("validateWorkflow", () => {
    it.each(["one", "multi"])(
        "does not error for a results-only workflow that takes input files (arity %s)",
        (arity) => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p" }],
                    systemConfig: {
                        inputFileArity: arity,
                        outputTarget: { locationType: "none" },
                    },
                } as any,
                {}
            );
            expect(r.errors).toEqual([]);
        }
    );

    it("errors when an asset-output workflow with arity none does not allow output override", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p" }],
                systemConfig: {
                    inputFileArity: "none",
                    outputTarget: { locationType: "asset", allowOverride: false },
                },
            } as any,
            {}
        );
        expect(r.errors.some((e) => /output override/i.test(e))).toBe(true);
    });

    it("does not error when an asset-output workflow with arity none allows output override", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p" }],
                systemConfig: {
                    inputFileArity: "none",
                    outputTarget: { locationType: "asset", allowOverride: true },
                },
            } as any,
            {}
        );
        expect(r.errors).toEqual([]);
    });

    it("errors when a referenced pipeline is archived (the backend rejects the save)", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: {},
            } as any,
            { "db:p": { archived: true } as any }
        );
        expect(r.errors.some((e) => /archived/i.test(e))).toBe(true);
    });

    it("errors when a pipeline reference has nothing selected", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "", pipelineDatabaseId: "" }],
                systemConfig: {},
            } as any,
            {}
        );
        expect(r.errors.some((e) => /no pipeline selected/i.test(e))).toBe(true);
    });

    it("names the offending position when a later reference is blank", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [
                    { pipelineId: "p", pipelineDatabaseId: "db" },
                    { pipelineId: "", pipelineDatabaseId: "" },
                ],
                systemConfig: {},
            } as any,
            {}
        );
        expect(r.errors.some((e) => /Pipeline #2 has no pipeline selected/.test(e))).toBe(true);
    });

    it.each(["prep: stage/1", "a b", "with.dot", "ab", "x".repeat(64)])(
        "errors on a job name that is not a legal state-name / path segment: %s",
        (jobName) => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db", jobName }],
                    systemConfig: {},
                } as any,
                {}
            );
            expect(r.errors.some((e) => /job name/i.test(e))).toBe(true);
        }
    );

    it.each(["prep-stage-1", "prep_stage_1", "Stage1", undefined, ""])(
        "accepts a legal/absent job name: %s",
        (jobName) => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db", jobName }],
                    systemConfig: {},
                } as any,
                {}
            );
            expect(r.errors.some((e) => /job name/i.test(e))).toBe(false);
        }
    );

    it("reports whether every reference names a pipeline", () => {
        expect(allPipelineRefsSelected([{ pipelineId: "p" }])).toBe(true);
        expect(allPipelineRefsSelected([{ pipelineId: "p" }, { pipelineId: "" }])).toBe(false);
        expect(allPipelineRefsSelected([])).toBe(true);
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

    // The arity pairs the backend's validate_workflow_save treats as incompatible, and the pairs it
    // accepts. The client must not diverge in either direction.
    it.each([
        ["none", "one"],
        ["none", "multi"],
        ["multi", "one"],
    ])("warns when workflow arity %s cannot satisfy pipeline arity %s", (wfArity, pArity) => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileArity: wfArity },
            } as any,
            { "db:p": { systemConfig: { inputFileArity: pArity } } as any }
        );
        expect(r.warnings.some((w) => /arity/i.test(w))).toBe(true);
    });

    it.each([
        ["one", "one"],
        ["one", "multi"],
        ["one", "none"],
        ["multi", "multi"],
        ["multi", "none"],
        ["none", "none"],
    ])("does not warn when workflow arity %s satisfies pipeline arity %s", (wfArity, pArity) => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileArity: wfArity },
            } as any,
            { "db:p": { systemConfig: { inputFileArity: pArity } } as any }
        );
        expect(r.warnings.some((w) => /arity/i.test(w))).toBe(false);
    });

    it("defaults an absent workflow arity to 'one' like the backend does", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: {},
            } as any,
            { "db:p": { systemConfig: { inputFileArity: "multi" } } as any }
        );
        expect(r.warnings.some((w) => /arity/i.test(w))).toBe(false);
    });

    it("warns when a pipeline uses a metadata input the workflow gate has off", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { metadataInputs: { fileMetadata: true } },
            } as any,
            {
                "db:p": {
                    systemConfig: {
                        metadataInputs: { assetMetadata: true, fileMetadata: true },
                    },
                } as any,
            }
        );
        expect(r.warnings.some((w) => /assetMetadata/.test(w))).toBe(true);
        expect(r.warnings.some((w) => /fileMetadata/.test(w))).toBe(false);
    });

    it("warns when the workflow and pipeline allow-filters are disjoint", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileFilters: { allow: ["*.glb"] } },
            } as any,
            { "db:p": { systemConfig: { inputFileFilters: { allow: ["*.las"] } } } as any }
        );
        expect(r.warnings.some((w) => /exclude everything/i.test(w))).toBe(true);
    });

    // The backend compares the two extension forms by extension and treats any wildcard pattern as
    // possibly overlapping, so these pairs must not warn on the client either.
    it.each([
        [["*.glb"], [".glb"]],
        [[".glb"], ["*.glb"]],
        [["/models/*"], ["*.glb"]],
        [["*skip*"], ["/models/a.glb"]],
    ])("does not warn for allow-filters the backend considers overlapping: %s / %s", (wf, p) => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileFilters: { allow: wf } },
            } as any,
            { "db:p": { systemConfig: { inputFileFilters: { allow: p } } } as any }
        );
        expect(r.warnings.some((w) => /exclude everything/i.test(w))).toBe(false);
    });

    it("warns when two exact non-wildcard keys cannot both match", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileFilters: { allow: ["/models/a.glb"] } },
            } as any,
            { "db:p": { systemConfig: { inputFileFilters: { allow: ["/models/b.glb"] } } } as any }
        );
        expect(r.warnings.some((w) => /exclude everything/i.test(w))).toBe(true);
    });

    it("does not warn when the allow-filters overlap", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileFilters: { allow: ["*.glb", "*.las"] } },
            } as any,
            { "db:p": { systemConfig: { inputFileFilters: { allow: ["*.las"] } } } as any }
        );
        expect(r.warnings.some((w) => /exclude everything/i.test(w))).toBe(false);
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
