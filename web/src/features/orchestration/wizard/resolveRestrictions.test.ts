/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    resolveRestrictions,
    stepsFromWorkflow,
    isOpenAllowList,
    summarizeRestrictions,
} from "./resolveRestrictions";

const wf = (over: any = {}) => ({
    inputFileArity: "one",
    metadataInputs: {},
    inputFileFilters: {},
    outputTarget: { locationType: "asset" },
    ...over,
});

describe("isOpenAllowList", () => {
    // "No restriction" has several spellings; all must read alike so a '*' at one level defers to the
    // next rather than acting as a pattern. Mirrors executionValidation.is_open_allow_list.
    it.each([undefined, [], [""], ["  "], ["*"], ["**"], ["*.*"], ["/*"], ["*", " "]])(
        "treats %s as open",
        (allow) => {
            expect(isOpenAllowList(allow as any)).toBe(true);
        }
    );

    it.each([["*.glb"], [".glb"], ["/models/*"], ["*", "*.glb"]])(
        "treats %s as a restriction",
        (...allow) => {
            expect(isOpenAllowList(allow as any)).toBe(false);
        }
    );
});

describe("resolveRestrictions", () => {
    it("uses the workflow's allow list when it is restrictive", () => {
        // The workflow gate is the outer boundary — no pipeline can widen it.
        const r = resolveRestrictions(wf({ inputFileFilters: { allow: ["*.glb"] } }), [
            { systemConfig: { inputFileFilters: { allow: ["*.obj", "*.e57"] } } },
        ]);
        expect(r.allow).toEqual(["*.glb"]);
        expect(r.source).toBe("workflow");
    });

    it("falls through to the pipelines when the workflow allow list is open", () => {
        const r = resolveRestrictions(wf({ inputFileFilters: { allow: ["*"] } }), [
            { systemConfig: { inputFileFilters: { allow: ["*.glb"] } } },
            { systemConfig: { inputFileFilters: { allow: ["*.obj"] } } },
        ]);
        expect(r.allow.sort()).toEqual(["*.glb", "*.obj"]);
        expect(r.source).toBe("pipelines");
    });

    it("unions the pipelines rather than intersecting them", () => {
        // The steps are alternatives from a file's point of view: a file ANY step accepts is one the
        // workflow can act on. Intersecting would report "nothing allowed" for a runnable workflow.
        const r = resolveRestrictions(wf(), [
            { systemConfig: { inputFileFilters: { allow: ["*.glb"] } } },
            { systemConfig: { inputFileFilters: { allow: ["*.las"] } } },
        ]);
        expect(r.allow.sort()).toEqual(["*.glb", "*.las"]);
    });

    it("reports no restriction when any step accepts anything", () => {
        const r = resolveRestrictions(wf(), [
            { systemConfig: { inputFileFilters: { allow: ["*.glb"] } } },
            { systemConfig: { inputFileFilters: { allow: [] } } },
        ]);
        expect(r.allow).toEqual([]);
        expect(r.source).toBe("none");
    });

    it("unions excludes across every level", () => {
        const r = resolveRestrictions(wf({ inputFileFilters: { exclude: ["*.tmp"] } }), [
            { systemConfig: { inputFileFilters: { exclude: ["*.previewFile.*"] } } },
            { systemConfig: { inputFileFilters: { exclude: ["*.tmp"] } } },
        ]);
        expect(r.exclude.sort()).toEqual(["*.previewFile.*", "*.tmp"]);
    });

    it("lets a chosen template's overrides narrow the result", () => {
        // The whole reason this is computed client-side rather than read from the backend aggregate:
        // the template is known here and changes the answer.
        const steps = [
            {
                systemConfig: { inputFileFilters: { allow: ["*.glb", "*.obj"] } },
                templateOverrides: { inputFileFilters: { allow: ["*.glb"] } },
            },
        ];
        const r = resolveRestrictions(wf(), steps);
        expect(r.allow).toEqual(["*.glb"]);
    });

    it("lets a template override the arity a pipeline declares", () => {
        const r = resolveRestrictions(wf({ inputFileArity: "multi" }), [
            {
                systemConfig: { inputFileArity: "none" },
                templateOverrides: { inputFileArity: "one" },
            },
        ]);
        // The workflow's own arity is what gates the selection; the step override is reflected in the
        // per-step effective config used for filters.
        expect(r.arity).toBe("multi");
    });

    it("collapses duplicate patterns case-insensitively", () => {
        const r = resolveRestrictions(wf(), [
            { systemConfig: { inputFileFilters: { allow: ["*.GLB"] } } },
            { systemConfig: { inputFileFilters: { allow: ["*.glb"] } } },
        ]);
        expect(r.allow).toEqual(["*.GLB"]);
    });

    it("reports metadata only when the gate is on AND a step asks for it", () => {
        const r = resolveRestrictions(
            wf({ metadataInputs: { assetMetadata: true, fileMetadata: true } }),
            [{ systemConfig: { metadataInputs: { assetMetadata: true } } }]
        );
        expect(r.metadataInputs).toEqual(["Asset metadata"]);
        expect(r.metadataGatedOff).toEqual([]);
    });

    it("flags metadata a step wants but the workflow suppresses", () => {
        const r = resolveRestrictions(wf({ metadataInputs: { assetMetadata: false } }), [
            { systemConfig: { metadataInputs: { assetMetadata: true } } },
        ]);
        expect(r.metadataInputs).toEqual([]);
        expect(r.metadataGatedOff).toEqual(["Asset metadata"]);
    });

    it("carries the workflow's arity and output type", () => {
        const r = resolveRestrictions(
            wf({ inputFileArity: "none", outputTarget: { locationType: "none" } }),
            []
        );
        expect(r.arity).toBe("none");
        expect(r.outputType).toBe("none");
    });

    it("defaults arity and output type the way the backend does", () => {
        const r = resolveRestrictions({}, []);
        expect(r.arity).toBe("one");
        expect(r.outputType).toBe("asset");
    });

    it("marks the result provisional while a step's template is unknown", () => {
        const r = resolveRestrictions(wf(), [{ systemConfig: {}, templateKnown: false }]);
        expect(r.templatesResolved).toBe(false);
    });
});

describe("stepsFromWorkflow", () => {
    it("resolves each ref against the pipeline map by composite key", () => {
        const steps = stepsFromWorkflow(
            { specifiedPipelines: [{ pipelineDatabaseId: "GLOBAL", pipelineId: "conv" }] } as any,
            { "GLOBAL:conv": { systemConfig: { inputFileFilters: { allow: ["*.glb"] } } } } as any
        );
        expect(steps[0].systemConfig?.inputFileFilters?.allow).toEqual(["*.glb"]);
    });

    it("marks a require-template step's config as not yet final", () => {
        // Its template can still narrow the filters, so the summary must say so rather than present
        // the pipeline's own list as the final answer.
        const steps = stepsFromWorkflow(
            { specifiedPipelines: [{ pipelineDatabaseId: "GLOBAL", pipelineId: "cosmos" }] } as any,
            { "GLOBAL:cosmos": { systemConfig: { requireTemplate: true } } } as any
        );
        expect(steps[0].templateKnown).toBe(false);
    });

    it("survives a ref whose pipeline is missing from the map", () => {
        const steps = stepsFromWorkflow(
            { specifiedPipelines: [{ pipelineDatabaseId: "GLOBAL", pipelineId: "gone" }] } as any,
            {} as any
        );
        expect(steps).toHaveLength(1);
        expect(steps[0].systemConfig).toBeUndefined();
    });
});

describe("summarizeRestrictions", () => {
    it("summarizes an unrestricted asset-output workflow", () => {
        const r = resolveRestrictions(wf(), []);
        expect(summarizeRestrictions(r)).toBe("Any file type · 1 file · writes to an asset");
    });

    it("counts file types and names a results-only run", () => {
        const r = resolveRestrictions(
            wf({
                inputFileArity: "multi",
                inputFileFilters: { allow: ["*.glb", "*.obj"] },
                outputTarget: { locationType: "none" },
            }),
            []
        );
        expect(summarizeRestrictions(r)).toBe("2 file types · 1 or more files · results only");
    });
});
