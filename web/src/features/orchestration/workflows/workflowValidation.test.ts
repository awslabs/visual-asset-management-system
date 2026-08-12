/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { validateWorkflow, allPipelineRefsSelected } from "./workflowValidation";
import { Workflow, Pipeline } from "../types";

describe("validateWorkflow", () => {
    /** A resolvable reference + the matching one-entry pipeline map, keyed as the picker keys them. */
    const RESOLVABLE_REF = { pipelineId: "p", pipelineDatabaseId: "db" };
    const RESOLVABLE_MAP = { "db:p": {} as Pipeline };

    it.each(["one", "multi"])(
        "does not error for a results-only workflow that takes input files (arity %s)",
        (arity) => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [RESOLVABLE_REF],
                    systemConfig: {
                        inputFileArity: arity,
                        outputTarget: { locationType: "none" },
                    },
                } as any,
                RESOLVABLE_MAP
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
                specifiedPipelines: [RESOLVABLE_REF],
                systemConfig: {
                    inputFileArity: "none",
                    outputTarget: { locationType: "asset", allowOverride: true },
                },
            } as any,
            RESOLVABLE_MAP
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
        // The gate must be an EXPLICIT false. assetMetadata is turned off here; fileMetadata is on.
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: {
                    metadataInputs: { assetMetadata: false, fileMetadata: true },
                },
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

    it("does not warn when the workflow merely OMITS the key the pipeline uses", () => {
        // A key a map omits carries its builder default (ON), so an omission is not a gate. Reading
        // the raw value would warn that the workflow suppresses metadata it actually provides — the
        // rule validate_workflow_save applies in common/workflows/executionValidation.py.
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { metadataInputs: {} },
            } as any,
            { "db:p": { systemConfig: { metadataInputs: { assetMetadata: true } } } as any }
        );
        expect(r.warnings.filter((w) => /metadata input/.test(w))).toEqual([]);
    });

    it("checks databaseMetadata too, not just the three file/asset keys", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { metadataInputs: { databaseMetadata: false } },
            } as any,
            // The pipeline omits the key, which means it USES it (default ON).
            { "db:p": { systemConfig: { metadataInputs: {} } } as any }
        );
        expect(r.warnings.some((w) => /databaseMetadata/.test(w))).toBe(true);
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

    // The workflow's EXCLUDE list is a second, independent way to starve a pipeline: exclude is
    // applied after allow, so the allow-lists can agree perfectly and the file still never arrives.
    it("warns when the workflow excludes the pipeline's only accepted type", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: {
                    inputFileFilters: { allow: ["*.glb", "*.obj"], exclude: ["*.glb"] },
                },
            } as any,
            { "db:p": { systemConfig: { inputFileFilters: { allow: ["*.glb"] } } } as any }
        );
        // The allow-lists overlap, so the disjoint check stays quiet — only the exclude check fires.
        expect(r.warnings.some((w) => /exclude everything/i.test(w))).toBe(false);
        expect(r.warnings.some((w) => /no accepted input type/i.test(w))).toBe(true);
    });

    it("names what a pipeline is left with when only some of its types are excluded", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                // '.glb' and '*.glb' are the same type to the matcher.
                systemConfig: { inputFileFilters: { exclude: [".glb"] } },
            } as any,
            {
                "db:p": {
                    systemConfig: { inputFileFilters: { allow: ["*.glb", "*.obj"] } },
                } as any,
            }
        );
        expect(r.warnings.some((w) => /only \*\.obj/.test(w))).toBe(true);
    });

    it("does not warn for a wildcard exclude it cannot resolve", () => {
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: { inputFileFilters: { exclude: ["*.previewFile.*"] } },
            } as any,
            { "db:p": { systemConfig: { inputFileFilters: { allow: ["*.glb"] } } } as any }
        );
        expect(r.warnings.some((w) => /exclude/i.test(w))).toBe(false);
    });

    it("errors rather than warns when a pipeline is not in pipelinesById", () => {
        // The per-pipeline warnings all compare against a resolved record, so none of them can be
        // computed; the unresolvable reference itself is the blocking message.
        const r = validateWorkflow(
            {
                specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                systemConfig: {},
            } as any,
            {}
        );
        expect(r.warnings.length).toBe(0);
        expect(r.errors.some((e) => /not an available pipeline/.test(e))).toBe(true);
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

    // A duplicate job name collapses two steps into ONE state machine state: the ASL state name is
    // (uuid1().hex[:5] + "-" + name)[:80], uuid1().hex[:5] is time-based so states built in one call
    // share the prefix, and create_state_machine keys its states by name — so the second overwrites
    // the first and one pipeline silently never runs.
    describe("duplicate job names", () => {
        it("errors when two steps share a job name", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [
                        { pipelineId: "p1", pipelineDatabaseId: "db", jobName: "convert-step" },
                        { pipelineId: "p2", pipelineDatabaseId: "db", jobName: "convert-step" },
                    ],
                    systemConfig: {},
                } as any,
                { "db:p1": {} as any, "db:p2": {} as any }
            );
            expect(r.errors.some((e) => /job name 'convert-step' is already used/.test(e))).toBe(
                true
            );
        });

        // The name is also an S3 output-path segment, where two casings are two folders that read as
        // the same step, so the comparison ignores case.
        it("errors on a job name that repeats only in a different case", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [
                        { pipelineId: "p1", pipelineDatabaseId: "db", jobName: "Convert-Step" },
                        { pipelineId: "p2", pipelineDatabaseId: "db", jobName: "convert-step" },
                    ],
                    systemConfig: {},
                } as any,
                { "db:p1": {} as any, "db:p2": {} as any }
            );
            expect(r.errors.some((e) => /already used by pipeline #1/.test(e))).toBe(true);
        });

        it("names the later position, not the first", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [
                        { pipelineId: "p1", pipelineDatabaseId: "db", jobName: "stage" },
                        { pipelineId: "p2", pipelineDatabaseId: "db", jobName: "other" },
                        { pipelineId: "p3", pipelineDatabaseId: "db", jobName: "stage" },
                    ],
                    systemConfig: {},
                } as any,
                { "db:p1": {} as any, "db:p2": {} as any, "db:p3": {} as any }
            );
            const collision = r.errors.filter((e) => /already used by pipeline/.test(e));
            expect(collision).toHaveLength(1);
            expect(collision[0]).toMatch(
                /Pipeline #3 job name 'stage' is already used by pipeline #1/
            );
        });

        // Blank is the documented default (the step falls back to the pipeline id), so several blanks
        // are not a collision.
        it("does not error when several steps leave the job name blank", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [
                        { pipelineId: "p1", pipelineDatabaseId: "db", jobName: "" },
                        { pipelineId: "p2", pipelineDatabaseId: "db" },
                    ],
                    systemConfig: {},
                } as any,
                { "db:p1": {} as any, "db:p2": {} as any }
            );
            expect(r.errors.some((e) => /already used/.test(e))).toBe(false);
        });
    });

    // The backend keys per-step execute params, resolved configs and filtered inputs by the
    // pipeline composite key (executeWorkflow.py), so a repeated reference means step 2 overwrites
    // step 1 and only one of them runs.
    describe("duplicate pipeline references", () => {
        it("errors when the same pipeline is referenced twice", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [
                        { pipelineId: "convert", pipelineDatabaseId: "db1" },
                        { pipelineId: "convert", pipelineDatabaseId: "db1" },
                    ],
                    systemConfig: {},
                } as any,
                { "db1:convert": {} as any }
            );
            expect(
                r.errors.some((e) =>
                    /Pipeline #2 \('convert'\) is already used by pipeline #1/.test(e)
                )
            ).toBe(true);
        });

        // Different databases are different pipelines; the composite key is what collides.
        it("does not error for the same pipeline id in two different databases", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [
                        { pipelineId: "convert", pipelineDatabaseId: "db1" },
                        { pipelineId: "convert", pipelineDatabaseId: "GLOBAL" },
                    ],
                    systemConfig: {},
                } as any,
                { "db1:convert": {} as any, "GLOBAL:convert": {} as any }
            );
            expect(r.errors.some((e) => /already used by pipeline/.test(e))).toBe(false);
        });

        // Two empty cards are already reported as unselected; they must not also be a duplicate.
        it("does not report two blank cards as a duplicate reference", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [
                        { pipelineId: "", pipelineDatabaseId: "" },
                        { pipelineId: "", pipelineDatabaseId: "" },
                    ],
                    systemConfig: {},
                } as any,
                {}
            );
            expect(r.errors.some((e) => /already used by pipeline/.test(e))).toBe(false);
        });
    });

    // An unresolvable reference renders as an empty dropdown with Save enabled; the backend's own
    // rejection ("A referenced pipeline was not found.") names no card.
    describe("unresolvable pipeline references", () => {
        it("errors naming the position when a reference is absent from the pipeline map", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [
                        { pipelineId: "p1", pipelineDatabaseId: "db" },
                        { pipelineId: "gone", pipelineDatabaseId: "db" },
                    ],
                    systemConfig: {},
                } as any,
                { "db:p1": {} as any }
            );
            expect(
                r.errors.some((e) => /Pipeline #2 references 'db:gone', which is not an/.test(e))
            ).toBe(true);
        });

        // The list is fetched asynchronously: reporting during the fetch would flash an error on
        // every edit-mode load.
        it("stays quiet about an unresolved reference while the pipeline list is still loading", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p1", pipelineDatabaseId: "db" }],
                    systemConfig: {},
                } as any,
                {},
                { pipelinesLoaded: false }
            );
            expect(r.errors.some((e) => /not an available pipeline/.test(e))).toBe(false);
        });

        it("does not report a blank card as unresolvable (it is already reported as unselected)", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "", pipelineDatabaseId: "" }],
                    systemConfig: {},
                } as any,
                {}
            );
            expect(r.errors.some((e) => /not an available pipeline/.test(e))).toBe(false);
            expect(r.errors.some((e) => /no pipeline selected/.test(e))).toBe(true);
        });
    });

    // Every other filter rule is workflow-vs-one-pipeline. _evaluate applies EVERY step's filters to
    // the SAME selection, so two steps with disjoint allow lists cannot both be satisfied by one file.
    // Verified against the backend: with wf arity 'one' and steps allowing *.glb / *.obj,
    // validate_execution errors for every possible single-file selection.
    describe("cross-step allow-list intersection", () => {
        const disjointSteps = {
            "db:glbOnly": {
                systemConfig: { inputFileFilters: { allow: ["*.glb"] } },
            } as any,
            "db:objOnly": {
                systemConfig: { inputFileFilters: { allow: ["*.obj"] } },
            } as any,
        };
        const disjointRefs = [
            { pipelineId: "glbOnly", pipelineDatabaseId: "db" },
            { pipelineId: "objOnly", pipelineDatabaseId: "db" },
        ];

        it("warns when two steps' allow lists have no pattern in common", () => {
            const r = validateWorkflow(
                { specifiedPipelines: disjointRefs, systemConfig: {} } as any,
                disjointSteps
            );
            expect(r.warnings.some((w) => /accept no input file in common/.test(w))).toBe(true);
            expect(r.warnings.some((w) => /every execution will fail at launch/.test(w))).toBe(
                true
            );
        });

        // Measured: at arity 'multi' a two-file selection carrying one of each DOES pass
        // validate_execution, so the warning must not claim the workflow can never run.
        it("does not claim launch failure for a multi-file workflow", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: disjointRefs,
                    systemConfig: { inputFileArity: "multi" },
                } as any,
                disjointSteps
            );
            expect(r.warnings.some((w) => /select a file for each of them/.test(w))).toBe(true);
            expect(r.warnings.some((w) => /every execution will fail at launch/.test(w))).toBe(
                false
            );
        });

        it("does not warn when the two steps' allow lists overlap", () => {
            const r = validateWorkflow(
                { specifiedPipelines: disjointRefs, systemConfig: {} } as any,
                {
                    "db:glbOnly": {
                        systemConfig: { inputFileFilters: { allow: ["*.glb", "*.obj"] } },
                    } as any,
                    "db:objOnly": {
                        systemConfig: { inputFileFilters: { allow: ["*.obj"] } },
                    } as any,
                }
            );
            expect(r.warnings.some((w) => /no input file in common/.test(w))).toBe(false);
        });

        // An open allow list admits anything, so it can never be disjoint from another.
        it.each([[[]], [["*"]], [["**"]], [undefined]])(
            "does not warn when one step's allow list is open: %s",
            (allow) => {
                const r = validateWorkflow(
                    { specifiedPipelines: disjointRefs, systemConfig: {} } as any,
                    {
                        "db:glbOnly": { systemConfig: { inputFileFilters: { allow } } } as any,
                        "db:objOnly": {
                            systemConfig: { inputFileFilters: { allow: ["*.obj"] } },
                        } as any,
                    }
                );
                expect(r.warnings.some((w) => /no input file in common/.test(w))).toBe(false);
            }
        );

        // An arity-'none' step is handed an empty input list before its filters are reached
        // (_evaluate returns early), so its allow list never judges the selection.
        it("does not warn when the non-overlapping step takes no input files", () => {
            const r = validateWorkflow(
                { specifiedPipelines: disjointRefs, systemConfig: {} } as any,
                {
                    "db:glbOnly": {
                        systemConfig: { inputFileFilters: { allow: ["*.glb"] } },
                    } as any,
                    "db:objOnly": {
                        systemConfig: {
                            inputFileArity: "none",
                            inputFileFilters: { allow: ["*.obj"] },
                        },
                    } as any,
                }
            );
            expect(r.warnings.some((w) => /no input file in common/.test(w))).toBe(false);
        });

        it("reports each disjoint pair once, not once per ordering", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [
                        ...disjointRefs,
                        { pipelineId: "stlOnly", pipelineDatabaseId: "db" },
                    ],
                    systemConfig: {},
                } as any,
                {
                    ...disjointSteps,
                    "db:stlOnly": {
                        systemConfig: { inputFileFilters: { allow: ["*.stl"] } },
                    } as any,
                }
            );
            // glb/obj, glb/stl, obj/stl — three unordered pairs.
            expect(r.warnings.filter((w) => /no input file in common/.test(w))).toHaveLength(3);
        });
    });

    // A workflow assetScope more permissive than a step's is INERT: scopeKeyAllowedEverywhere
    // withholds the option because a step declines it, and neither validateWorkflow nor the backend's
    // validate_workflow_save had any assetScope branch to say so.
    describe("assetScope narrowed by a step", () => {
        it.each([
            ["wholeAssetAllowed", "selecting a whole asset"],
            ["folderAllowed", "selecting a folder"],
        ])("warns when the workflow grants %s and a step declines it", (key, label) => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                    systemConfig: { assetScope: { [key]: true } },
                } as any,
                { "db:p": { systemConfig: { assetScope: { [key]: false } } } as any }
            );
            expect(r.warnings.some((w) => w.includes(label) && /will not be offered/.test(w))).toBe(
                true
            );
        });

        // `wholeAsset` is the shorthand the vamsSchema registration bundles emit; the backend's
        // normalize_asset_scope folds it into wholeAssetAllowed, so both spellings must be read.
        it("reads the `wholeAsset` shorthand on the pipeline side", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                    systemConfig: { assetScope: { wholeAssetAllowed: true } },
                } as any,
                { "db:p": { systemConfig: { assetScope: { wholeAsset: false } } } as any }
            );
            expect(r.warnings.some((w) => /whole asset/.test(w))).toBe(true);
        });

        it("reads the `wholeAsset` shorthand on the workflow side", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                    systemConfig: { assetScope: { wholeAsset: true } },
                } as any,
                { "db:p": { systemConfig: { assetScope: { wholeAssetAllowed: false } } } as any }
            );
            expect(r.warnings.some((w) => /whole asset/.test(w))).toBe(true);
        });

        // An explicit canonical key wins over the shorthand, matching normalize_asset_scope.
        it("prefers the canonical key when a scope carries both spellings", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                    systemConfig: { assetScope: { wholeAssetAllowed: true } },
                } as any,
                {
                    "db:p": {
                        systemConfig: {
                            assetScope: { wholeAsset: false, wholeAssetAllowed: true },
                        },
                    } as any,
                }
            );
            expect(r.warnings.some((w) => /whole asset/.test(w))).toBe(false);
        });

        // An OMITTED pipeline key defers to the workflow gate (_scope_errors with declared_only), so
        // it is not a refusal and there is nothing inert to report.
        it("does not warn when the step merely omits the key", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                    systemConfig: { assetScope: { wholeAssetAllowed: true, folderAllowed: true } },
                } as any,
                { "db:p": { systemConfig: { assetScope: {} } } as any }
            );
            expect(r.warnings.some((w) => /will not be offered/.test(w))).toBe(false);
        });

        // An arity-'none' step never receives input files, so the backend never applies its scope to
        // the selection — warning would withhold nothing and blame the wrong step.
        it("does not warn when the declining step takes no input files", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                    systemConfig: { assetScope: { wholeAssetAllowed: true } },
                } as any,
                {
                    "db:p": {
                        systemConfig: {
                            inputFileArity: "none",
                            assetScope: { wholeAssetAllowed: false },
                        },
                    } as any,
                }
            );
            expect(r.warnings.some((w) => /will not be offered/.test(w))).toBe(false);
        });

        // Fail closed the other way too: a workflow that does not grant the selection has nothing to
        // report, whatever the step says.
        it("does not warn when the workflow does not grant the selection", () => {
            const r = validateWorkflow(
                {
                    specifiedPipelines: [{ pipelineId: "p", pipelineDatabaseId: "db" }],
                    systemConfig: { assetScope: { wholeAssetAllowed: false } },
                } as any,
                { "db:p": { systemConfig: { assetScope: { wholeAssetAllowed: false } } } as any }
            );
            expect(r.warnings.some((w) => /will not be offered/.test(w))).toBe(false);
        });
    });
});
