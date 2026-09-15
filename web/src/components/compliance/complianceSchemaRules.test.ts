/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    PIPELINE_REF_FIELDS,
    bodyFromRulesDraft,
    isVamsRulesBody,
    newPipelineRuleDraft,
    rulesDraftFromBody,
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

    it("preserves the pipelineRef and rule internals through a builder round-trip", () => {
        const rebuilt = bodyFromRulesDraft(rulesDraftFromBody(body));
        expect(rebuilt).toEqual(body);
    });

    it("seeds a new pipeline rule carrying all five pipelineRef fields", () => {
        const draft = newPipelineRuleDraft([]);
        expect(draft.ruleType).toBe("pipeline");
        expect(Object.keys(draft.pipelineRef).sort()).toEqual([
            "databaseId",
            "pipelineDatabaseId",
            "pipelineId",
            "templateId",
            "workflowId",
        ]);
    });
});
