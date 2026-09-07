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
    describe("assetScope selection rules", () => {
        // A run is submitted once and every step is checked against the selection, so the narrowest
        // level decides what the picker may offer. Unlike metadataInputs, an OMITTED scope key is NOT a
        // grant — the backend's _scope_errors rejects a whole-asset selection under a scope that does
        // not say it is allowed, so the picker must not offer one either.
        const resolve = (wfScope: any, stepScopes: any[]) =>
            resolveRestrictions(
                wf({ assetScope: wfScope }),
                stepScopes.map((s) => ({ systemConfig: { assetScope: s } })) as any
            );

        it("offers both when the workflow and every step allow them", () => {
            const r = resolve({ wholeAssetAllowed: true, folderAllowed: true }, [
                { wholeAssetAllowed: true, folderAllowed: true },
            ]);
            expect(r.wholeAssetAllowed).toBe(true);
            expect(r.folderAllowed).toBe(true);
        });

        it("a single step declining whole-asset removes it", () => {
            const r = resolve({ wholeAssetAllowed: true, folderAllowed: true }, [
                { wholeAssetAllowed: false },
            ]);
            expect(r.wholeAssetAllowed).toBe(false);
            // The step said nothing about folders, so that rule is untouched.
            expect(r.folderAllowed).toBe(true);
        });

        it("a step that declares no scope narrows nothing", () => {
            const r = resolve({ wholeAssetAllowed: true, folderAllowed: true }, [{}]);
            expect(r.wholeAssetAllowed).toBe(true);
            expect(r.folderAllowed).toBe(true);
        });

        it("a silent workflow grants neither (fails closed)", () => {
            const r = resolve({}, [{ wholeAssetAllowed: true, folderAllowed: true }]);
            expect(r.wholeAssetAllowed).toBe(false);
            expect(r.folderAllowed).toBe(false);
        });

        it("accepts the wholeAsset shorthand the vamsSchema bundles emit", () => {
            expect(resolve({ wholeAsset: true }, [{}]).wholeAssetAllowed).toBe(true);
            expect(resolve({ wholeAsset: true }, [{ wholeAsset: false }]).wholeAssetAllowed).toBe(
                false
            );
        });

        it("a template override can narrow the step's scope", () => {
            const r = resolveRestrictions(
                wf({ assetScope: { wholeAssetAllowed: true, folderAllowed: true } }),
                [
                    {
                        systemConfig: { assetScope: { wholeAssetAllowed: true } },
                        templateOverrides: { assetScope: { wholeAssetAllowed: false } },
                    },
                ] as any
            );
            expect(r.wholeAssetAllowed).toBe(false);
        });

        it("an arity-none step's scope does not narrow the selection", () => {
            // An arity-'none' step receives no input files whatever the run selected, so the backend
            // never applies its scope to the selection (_evaluate assigns it [] and continues before
            // _scope_errors). Applying it here would withhold an option the backend accepts.
            const r = resolveRestrictions(
                wf({ assetScope: { wholeAssetAllowed: true, folderAllowed: true } }),
                [
                    {
                        systemConfig: {
                            inputFileArity: "none",
                            assetScope: { wholeAsset: false, folderAllowed: false },
                        },
                    },
                    {
                        systemConfig: {
                            inputFileArity: "one",
                            assetScope: { wholeAssetAllowed: true, folderAllowed: true },
                        },
                    },
                ] as any
            );
            expect(r.wholeAssetAllowed).toBe(true);
            expect(r.folderAllowed).toBe(true);
        });

        it("a template override that makes a step arity-none also drops its scope", () => {
            const r = resolveRestrictions(
                wf({ assetScope: { wholeAssetAllowed: true, folderAllowed: true } }),
                [
                    {
                        systemConfig: {
                            inputFileArity: "one",
                            assetScope: { wholeAssetAllowed: false, folderAllowed: false },
                        },
                        templateOverrides: { inputFileArity: "none" },
                    },
                ] as any
            );
            expect(r.wholeAssetAllowed).toBe(true);
            expect(r.folderAllowed).toBe(true);
        });

        it("a template override that makes a step arity-one reinstates its scope", () => {
            const r = resolveRestrictions(
                wf({ assetScope: { wholeAssetAllowed: true, folderAllowed: true } }),
                [
                    {
                        systemConfig: {
                            inputFileArity: "none",
                            assetScope: { wholeAssetAllowed: false, folderAllowed: false },
                        },
                        templateOverrides: { inputFileArity: "one" },
                    },
                ] as any
            );
            expect(r.wholeAssetAllowed).toBe(false);
            expect(r.folderAllowed).toBe(false);
        });

        it("a step that omits its arity is treated as consuming files", () => {
            // The backend's _arity defaults an absent value to 'one', so an omitted arity must not be
            // read as 'none' and let a declining scope through.
            const r = resolveRestrictions(
                wf({ assetScope: { wholeAssetAllowed: true, folderAllowed: true } }),
                [{ systemConfig: { assetScope: { wholeAssetAllowed: false } } }] as any
            );
            expect(r.wholeAssetAllowed).toBe(false);
        });

        it("still fails closed when the only step is arity-none and the workflow is silent", () => {
            const r = resolveRestrictions(wf({ assetScope: {} }), [
                { systemConfig: { inputFileArity: "none" } },
            ] as any);
            expect(r.wholeAssetAllowed).toBe(false);
            expect(r.folderAllowed).toBe(false);
        });
    });

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
        // Every key an assertion does not name is turned off explicitly on both sides: an omitted key
        // defaults ON, so leaving one out would add it to the expected lists.
        const r = resolveRestrictions(
            wf({
                metadataInputs: {
                    assetMetadata: true,
                    fileMetadata: true,
                    fileAttributes: false,
                    databaseMetadata: false,
                },
            }),
            [
                {
                    systemConfig: {
                        metadataInputs: {
                            assetMetadata: true,
                            fileMetadata: false,
                            fileAttributes: false,
                            databaseMetadata: false,
                        },
                    },
                },
            ]
        );
        expect(r.metadataInputs).toEqual(["Asset metadata"]);
        expect(r.metadataInputKeys).toEqual(["assetMetadata"]);
        expect(r.metadataGatedOff).toEqual([]);
    });

    it("flags metadata a step wants but the workflow suppresses", () => {
        const off = {
            assetMetadata: false,
            fileMetadata: false,
            fileAttributes: false,
            databaseMetadata: false,
        };
        const r = resolveRestrictions(wf({ metadataInputs: off }), [
            { systemConfig: { metadataInputs: { ...off, assetMetadata: true } } },
        ]);
        expect(r.metadataInputs).toEqual([]);
        expect(r.metadataGatedOff).toEqual(["Asset metadata"]);
    });

    it("treats every metadata key as ON when a stored map omits it", () => {
        // The record builders default all four keys to True, so a map that omits one carries no
        // value for it and must still read as providing that metadata — mirroring
        // METADATA_INPUT_DEFAULTS in executionRecords.py. An empty map is not an opt-out of
        // everything: reading it that way would hide the metadata-source pickers for every workflow
        // saved with a partial map, and the execute path collects the metadata regardless.
        const r = resolveRestrictions(wf({ metadataInputs: {} }), [{ systemConfig: {} }]);
        expect(r.metadataInputKeys).toEqual([
            "databaseMetadata",
            "assetMetadata",
            "fileMetadata",
            "fileAttributes",
        ]);
        expect(r.metadataGatedOff).toEqual([]);
    });

    it("reads a partial map as opting out of only the key it names", () => {
        // A client that sends {fileMetadata: false} persists exactly that key, since the API stores
        // systemConfig wholesale. The other three stay on.
        const r = resolveRestrictions(wf({ metadataInputs: { fileMetadata: false } }), [
            { systemConfig: { metadataInputs: { fileMetadata: false } } },
        ]);
        expect(r.metadataInputKeys).toEqual([
            "databaseMetadata",
            "assetMetadata",
            "fileAttributes",
        ]);
        expect(r.metadataGatedOff).toEqual([]);
    });

    it("reports databaseMetadata gated off when the workflow turns it off explicitly", () => {
        const off = {
            assetMetadata: false,
            fileMetadata: false,
            fileAttributes: false,
            databaseMetadata: false,
        };
        const r = resolveRestrictions(wf({ metadataInputs: off }), [
            { systemConfig: { metadataInputs: { ...off, databaseMetadata: true } } },
        ]);
        expect(r.metadataInputKeys).toEqual([]);
        expect(r.metadataGatedOff).toEqual(["Database metadata"]);
    });

    it("reports all four metadata types when every gate is on", () => {
        const all = {
            assetMetadata: true,
            fileMetadata: true,
            fileAttributes: true,
            databaseMetadata: true,
        };
        const r = resolveRestrictions(wf({ metadataInputs: all }), [
            { systemConfig: { metadataInputs: all } },
        ]);
        expect(r.metadataInputs).toEqual([
            "Database metadata",
            "Asset metadata",
            "File metadata",
            "File attributes",
        ]);
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
