/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    DEFAULT_INPUT_FILES_DRAFT,
    INPUT_FILES_MAX_FILTERS,
    INPUT_FILES_MAX_KEYS,
    INPUT_FILES_MODE_OPTIONS,
    PIPELINE_REF_FIELDS,
    bodyFromRulesDraft,
    inputFilesDraftFrom,
    inputFilesFromDraft,
    isVamsRulesBody,
    newPipelineRuleDraft,
    rulesDraftFromBody,
    validateInputFiles,
    validatePipelineRef,
} from "./complianceSchemaRules";
import { CompliancePipelineRef, VamsRulesV1SchemaBody } from "../../services/ComplianceService";

const validRef: CompliancePipelineRef = {
    databaseId: "GLOBAL",
    workflowId: "quality-check",
    pipelineDatabaseId: "db-1",
    pipelineId: "pipe-1",
    templateId: "tmpl-1",
};

describe("pipelineRef contract", () => {
    it("exposes exactly the contract-3 fields with the specified labels", () => {
        expect(PIPELINE_REF_FIELDS.map((f) => f.key)).toEqual([
            "databaseId",
            "workflowId",
            "pipelineDatabaseId",
            "pipelineId",
            "templateId",
        ]);
        expect(PIPELINE_REF_FIELDS.map((f) => f.label)).toEqual([
            "Workflow database",
            "Workflow ID",
            "Pipeline database",
            "Pipeline ID",
            "Template ID (optional)",
        ]);
        expect(PIPELINE_REF_FIELDS.find((f) => f.key === "templateId")?.optional).toBe(true);
    });

    it("treats a fully populated ref as valid", () => {
        expect(validatePipelineRef(validRef)).toEqual({});
    });

    it("requires the four ids but not the template id", () => {
        const errors = validatePipelineRef({
            databaseId: "",
            workflowId: "",
            pipelineDatabaseId: "",
            pipelineId: "",
        });
        expect(Object.keys(errors).sort()).toEqual([
            "databaseId",
            "pipelineDatabaseId",
            "pipelineId",
            "workflowId",
        ]);
    });

    it("rejects an id that violates the identifier pattern", () => {
        expect(
            validatePipelineRef({ ...validRef, pipelineId: "no spaces" }).pipelineId
        ).toBeTruthy();
    });
});

describe("vams-rules-v1 draft round-trip", () => {
    const body: VamsRulesV1SchemaBody = {
        schemaFormat: "vams-rules-v1",
        extends: "base-schema",
        rules: {
            "output-check": {
                ruleType: "pipeline",
                enforcement: "quarantine",
                pipelineRef: validRef,
                inputFiles: { mode: "matching", filter: ["*.stl", "*.obj"] },
                inputParameters: { region: "us-east-1" },
                checks: [
                    {
                        name: "poly",
                        outputField: "polygonCount",
                        tolerance: { operator: "lte", value: 100 },
                    },
                ],
            },
        },
    };

    it("recognizes a vams-rules-v1 body", () => {
        expect(isVamsRulesBody(body)).toBe(true);
        expect(isVamsRulesBody({ type: "object" })).toBe(false);
    });

    it("preserves the pipelineRef, inputFiles and rule internals through a builder round-trip", () => {
        const rebuilt = bodyFromRulesDraft(rulesDraftFromBody(body));
        expect(rebuilt).toEqual(body);
    });

    it("reads a rule without inputFiles as the matching default and writes it out explicitly", () => {
        const { inputFiles, ...ruleWithoutSelection } = body.rules["output-check"] as any;
        const legacy: VamsRulesV1SchemaBody = {
            ...body,
            rules: { "output-check": ruleWithoutSelection },
        };
        const draft = rulesDraftFromBody(legacy);
        expect(draft.rules[0].inputFiles).toEqual(DEFAULT_INPUT_FILES_DRAFT);
        expect(bodyFromRulesDraft(draft).rules["output-check"]).toEqual({
            ...ruleWithoutSelection,
            inputFiles: { mode: "matching" },
        });
    });

    it("round-trips the wholeAsset and explicit selections", () => {
        const wholeAsset = {
            ...body,
            rules: {
                r: { ...body.rules["output-check"], inputFiles: { mode: "wholeAsset" } },
            },
        } as VamsRulesV1SchemaBody;
        expect(bodyFromRulesDraft(rulesDraftFromBody(wholeAsset))).toEqual(wholeAsset);

        const explicit = {
            ...body,
            rules: {
                r: {
                    ...body.rules["output-check"],
                    inputFiles: { mode: "explicit", keys: ["/models/a.stl", "/models/b.stl"] },
                },
            },
        } as VamsRulesV1SchemaBody;
        expect(bodyFromRulesDraft(rulesDraftFromBody(explicit))).toEqual(explicit);
    });

    it("leaves inputFiles on a non-pipeline rule untouched", () => {
        const metadata: VamsRulesV1SchemaBody = {
            schemaFormat: "vams-rules-v1",
            rules: {
                meta: {
                    ruleType: "metadata",
                    enforcement: "warn",
                    metadataSchemaRef: { databaseId: "GLOBAL", schemaName: "asset-metadata" },
                    inputFiles: { mode: "explicit", keys: ["/x"] },
                    checks: [{ name: "required", validateRequired: true }],
                } as any,
            },
        };
        expect(bodyFromRulesDraft(rulesDraftFromBody(metadata))).toEqual(metadata);
    });

    it("seeds a new pipeline rule carrying all five pipelineRef fields and the default selection", () => {
        const draft = newPipelineRuleDraft([]);
        expect(draft.ruleType).toBe("pipeline");
        expect(Object.keys(draft.pipelineRef).sort()).toEqual([
            "databaseId",
            "pipelineDatabaseId",
            "pipelineId",
            "templateId",
            "workflowId",
        ]);
        expect(draft.inputFiles).toEqual(DEFAULT_INPUT_FILES_DRAFT);
        expect(bodyFromRulesDraft({ extends: "", rules: [draft] }).rules[draft.name]).toMatchObject(
            { inputFiles: { mode: "matching" } }
        );
    });
});

describe("inputFiles draft", () => {
    it("offers the three modes with matching first", () => {
        expect(INPUT_FILES_MODE_OPTIONS.map((o) => o.value)).toEqual([
            "matching",
            "wholeAsset",
            "explicit",
        ]);
        expect(INPUT_FILES_MODE_OPTIONS.map((o) => o.label)).toEqual([
            "Matching files",
            "Whole asset",
            "Explicit files",
        ]);
    });

    it("normalises unknown or malformed selections to the matching default", () => {
        expect(inputFilesDraftFrom(undefined)).toEqual(DEFAULT_INPUT_FILES_DRAFT);
        expect(inputFilesDraftFrom("wholeAsset")).toEqual(DEFAULT_INPUT_FILES_DRAFT);
        expect(inputFilesDraftFrom({ mode: "bogus", filter: ["*.glb", 7] })).toEqual({
            mode: "matching",
            filter: ["*.glb"],
            keys: [],
        });
    });

    it("writes only the active mode's list, trimmed and without empty entries", () => {
        expect(
            inputFilesFromDraft({ mode: "matching", filter: [" *.glb ", ""], keys: ["/a"] })
        ).toEqual({ mode: "matching", filter: ["*.glb"] });
        expect(inputFilesFromDraft({ mode: "matching", filter: ["  "], keys: [] })).toEqual({
            mode: "matching",
        });
        expect(
            inputFilesFromDraft({ mode: "wholeAsset", filter: ["*.glb"], keys: ["/a"] })
        ).toEqual({ mode: "wholeAsset" });
        expect(
            inputFilesFromDraft({ mode: "explicit", filter: ["*.glb"], keys: [" /a ", ""] })
        ).toEqual({ mode: "explicit", keys: ["/a"] });
    });

    it("accepts the default, a filtered matching selection and a whole-asset selection", () => {
        expect(validateInputFiles(DEFAULT_INPUT_FILES_DRAFT)).toEqual({});
        expect(validateInputFiles({ mode: "matching", filter: ["*.stl", ""], keys: [] })).toEqual(
            {}
        );
        expect(validateInputFiles({ mode: "wholeAsset", filter: [], keys: [] })).toEqual({});
        expect(
            validateInputFiles({ mode: "explicit", filter: [], keys: ["/models/part.stl"] })
        ).toEqual({});
    });

    it("requires at least one asset-relative path for an explicit selection", () => {
        expect(validateInputFiles({ mode: "explicit", filter: [], keys: [] }).keys).toBeTruthy();
        expect(validateInputFiles({ mode: "explicit", filter: [], keys: [" "] }).keys).toBeTruthy();
        expect(
            validateInputFiles({ mode: "explicit", filter: [], keys: ["models/part.stl"] }).keys
        ).toBeTruthy();
        expect(validateInputFiles({ mode: "explicit", filter: [], keys: ["/"] }).keys).toBeTruthy();
    });

    it("enforces the list limits", () => {
        const tooManyGlobs = Array.from({ length: INPUT_FILES_MAX_FILTERS + 1 }, () => "*.glb");
        expect(
            validateInputFiles({ mode: "matching", filter: tooManyGlobs, keys: [] }).filter
        ).toBe(`Use at most ${INPUT_FILES_MAX_FILTERS} globs`);
        expect(
            validateInputFiles({ mode: "matching", filter: ["x".repeat(257)], keys: [] }).filter
        ).toBeTruthy();
        const tooManyKeys = Array.from({ length: INPUT_FILES_MAX_KEYS + 1 }, (_, i) => `/f${i}`);
        expect(validateInputFiles({ mode: "explicit", filter: [], keys: tooManyKeys }).keys).toBe(
            `Use at most ${INPUT_FILES_MAX_KEYS} file paths`
        );
    });

    it("flags an unknown mode", () => {
        expect(
            validateInputFiles({ mode: "bogus" as any, filter: [], keys: [] }).mode
        ).toBeTruthy();
    });
});
