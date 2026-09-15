/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    ComplianceEnforcementLevel,
    CompliancePipelineRef,
    CompliancePipelineRule,
    ComplianceRuleType,
    VAMS_RULES_V1_FORMAT,
    VamsRulesV1SchemaBody,
} from "../../services/ComplianceService";

/** Identifier shape the backend accepts for database, workflow, pipeline and template ids. */
export const COMPLIANCE_ID_PATTERN = /^[-_a-zA-Z0-9]{3,63}$/;

export const ENFORCEMENT_OPTIONS: { value: ComplianceEnforcementLevel; label: string }[] = [
    { value: "quarantine", label: "Quarantine" },
    { value: "warn", label: "Warn" },
    { value: "inform", label: "Inform" },
];

export interface PipelineRefField {
    key: keyof CompliancePipelineRef;
    label: string;
    placeholder: string;
    optional?: boolean;
}

/** The pipelineRef fields in display order, with their user-visible labels. */
export const PIPELINE_REF_FIELDS: PipelineRefField[] = [
    { key: "databaseId", label: "Workflow database", placeholder: "database-id or GLOBAL" },
    { key: "workflowId", label: "Workflow ID", placeholder: "workflow-id" },
    { key: "pipelineDatabaseId", label: "Pipeline database", placeholder: "database-id or GLOBAL" },
    { key: "pipelineId", label: "Pipeline ID", placeholder: "pipeline-id" },
    {
        key: "templateId",
        label: "Template ID (optional)",
        placeholder: "template-id",
        optional: true,
    },
];

export const EMPTY_PIPELINE_REF: CompliancePipelineRef = {
    databaseId: "",
    workflowId: "",
    pipelineDatabaseId: "",
    pipelineId: "",
    templateId: "",
};

/** The pipeline rule a schema starts from when one is added in the Visual Builder. */
export const EXAMPLE_PIPELINE_RULE: CompliancePipelineRule = {
    ruleType: "pipeline",
    enforcement: "warn",
    pipelineRef: {
        databaseId: "GLOBAL",
        workflowId: "example-workflow",
        pipelineDatabaseId: "GLOBAL",
        pipelineId: "example-pipeline",
    },
    inputParameters: {},
    checks: [
        {
            name: "example-check",
            description: "Compares one field of the pipeline output against a tolerance",
            outputField: "measuredValue",
            tolerance: { operator: "lte", value: 100 },
        },
    ],
};

export type PipelineRefErrors = Partial<Record<keyof CompliancePipelineRef, string>>;

/**
 * Field-level errors for a pipelineRef: the four ids are required and every present id must
 * match the backend identifier pattern. An empty map means the reference is valid.
 */
export function validatePipelineRef(ref: CompliancePipelineRef): PipelineRefErrors {
    const errors: PipelineRefErrors = {};
    for (const field of PIPELINE_REF_FIELDS) {
        const value = (ref[field.key] || "").trim();
        if (!value) {
            if (!field.optional) {
                errors[field.key] = `${field.label} is required`;
            }
            continue;
        }
        if (!COMPLIANCE_ID_PATTERN.test(value)) {
            errors[field.key] = "Use 3-63 letters, digits, hyphens or underscores";
        }
    }
    return errors;
}

/**
 * One rule of a vams-rules-v1 body as the Visual Builder edits it. `rest` holds every rule
 * property the builder does not expose (checks, inputParameters, metadataSchemaRef, ...) so a
 * round trip through the builder preserves them byte for byte.
 */
export interface RuleDraft {
    name: string;
    ruleType: ComplianceRuleType;
    enforcement: ComplianceEnforcementLevel;
    pipelineRef: CompliancePipelineRef;
    rest: Record<string, any>;
}

export interface RulesDraft {
    extends: string;
    rules: RuleDraft[];
}

export function isVamsRulesBody(parsed: any): parsed is VamsRulesV1SchemaBody {
    return (
        !!parsed &&
        typeof parsed === "object" &&
        !Array.isArray(parsed) &&
        parsed.schemaFormat === VAMS_RULES_V1_FORMAT
    );
}

function normalizePipelineRef(raw: any): CompliancePipelineRef {
    const ref = raw && typeof raw === "object" ? raw : {};
    return {
        databaseId: typeof ref.databaseId === "string" ? ref.databaseId : "",
        workflowId: typeof ref.workflowId === "string" ? ref.workflowId : "",
        pipelineDatabaseId:
            typeof ref.pipelineDatabaseId === "string" ? ref.pipelineDatabaseId : "",
        pipelineId: typeof ref.pipelineId === "string" ? ref.pipelineId : "",
        templateId: typeof ref.templateId === "string" ? ref.templateId : "",
    };
}

export function rulesDraftFromBody(body: VamsRulesV1SchemaBody): RulesDraft {
    const rules: RuleDraft[] = [];
    const rawRules = body.rules && typeof body.rules === "object" ? body.rules : {};
    for (const [name, def] of Object.entries(rawRules)) {
        const d: any = def && typeof def === "object" ? def : {};
        const { ruleType, enforcement, pipelineRef, ...rest } = d;
        rules.push({
            name,
            ruleType: ruleType || "pipeline",
            enforcement: enforcement || "warn",
            pipelineRef: normalizePipelineRef(pipelineRef),
            rest,
        });
    }
    return { extends: typeof body.extends === "string" ? body.extends : "", rules };
}

export function bodyFromRulesDraft(draft: RulesDraft): VamsRulesV1SchemaBody {
    const body: VamsRulesV1SchemaBody = { schemaFormat: VAMS_RULES_V1_FORMAT, rules: {} };
    if (draft.extends.trim()) {
        body.extends = draft.extends.trim();
    }
    for (const rule of draft.rules) {
        const name = rule.name.trim();
        if (!name) continue;
        const def: Record<string, any> = {
            ruleType: rule.ruleType,
            enforcement: rule.enforcement,
        };
        if (rule.ruleType === "pipeline") {
            const ref: Record<string, string> = {};
            for (const field of PIPELINE_REF_FIELDS) {
                const value = (rule.pipelineRef[field.key] || "").trim();
                if (value || !field.optional) {
                    ref[field.key] = value;
                }
            }
            def.pipelineRef = ref;
        }
        body.rules[name] = { ...def, ...rule.rest } as any;
    }
    return body;
}

export function newPipelineRuleDraft(existingNames: string[]): RuleDraft {
    let index = existingNames.length + 1;
    let name = `pipeline-rule-${index}`;
    while (existingNames.includes(name)) {
        index += 1;
        name = `pipeline-rule-${index}`;
    }
    const { ruleType, enforcement, pipelineRef, ...rest } = EXAMPLE_PIPELINE_RULE;
    return {
        name,
        ruleType,
        enforcement,
        pipelineRef: { ...pipelineRef, templateId: "" },
        rest: JSON.parse(JSON.stringify(rest)),
    };
}
