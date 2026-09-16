/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    ComplianceEnforcementLevel,
    CompliancePipelineInputFiles,
    CompliancePipelineInputFilesMode,
    CompliancePipelineRef,
    CompliancePipelineRule,
    ComplianceRuleType,
    PIPELINE_INPUT_FILES_MODES,
    VAMS_RULES_V1_FORMAT,
    VamsRulesV1SchemaBody,
} from "../../services/ComplianceService";

/** Identifier shape the backend accepts for database, workflow, pipeline and template ids. */
export const COMPLIANCE_ID_PATTERN = /^[-_a-zA-Z0-9]{3,63}$/;

/** Limits the backend places on a pipeline rule's input-file selection. */
export const INPUT_FILES_MAX_FILTERS = 32;
export const INPUT_FILES_MAX_FILTER_LENGTH = 256;
export const INPUT_FILES_MAX_KEYS = 64;

export const ENFORCEMENT_OPTIONS: { value: ComplianceEnforcementLevel; label: string }[] = [
    { value: "quarantine", label: "Quarantine" },
    { value: "warn", label: "Warn" },
    { value: "inform", label: "Inform" },
];

export interface InputFilesModeOption {
    value: CompliancePipelineInputFilesMode;
    label: string;
    description: string;
}

/** The input-file selection modes in display order, with their user-visible labels. */
export const INPUT_FILES_MODE_OPTIONS: InputFilesModeOption[] = [
    {
        value: "matching",
        label: "Matching files",
        description:
            "The asset's files that pass the workflow's and pipeline's input filters, narrowed by the globs below. A single-input workflow needs exactly one match.",
    },
    {
        value: "wholeAsset",
        label: "Whole asset",
        description: "The asset root. Accepted only by a workflow that allows whole-asset input.",
    },
    {
        value: "explicit",
        label: "Explicit files",
        description: "Exactly the listed asset-relative paths; each must exist on the asset.",
    },
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
    inputFiles: { mode: "matching" },
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
 * A pipeline rule's input-file selection as the Visual Builder edits it. Every mode keeps its
 * list so switching modes back and forth loses nothing; only the active mode's list is written.
 */
export interface InputFilesDraft {
    mode: CompliancePipelineInputFilesMode;
    filter: string[];
    keys: string[];
}

export const DEFAULT_INPUT_FILES_DRAFT: InputFilesDraft = {
    mode: "matching",
    filter: [],
    keys: [],
};

export interface InputFilesErrors {
    mode?: string;
    filter?: string;
    keys?: string;
}

const stringList = (raw: any): string[] =>
    Array.isArray(raw) ? raw.filter((entry) => typeof entry === "string") : [];

/** The draft of a rule's `inputFiles`; a missing selection is the `matching` default. */
export function inputFilesDraftFrom(raw: any): InputFilesDraft {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return { ...DEFAULT_INPUT_FILES_DRAFT };
    }
    const mode: CompliancePipelineInputFilesMode = PIPELINE_INPUT_FILES_MODES.includes(raw.mode)
        ? raw.mode
        : "matching";
    return { mode, filter: stringList(raw.filter), keys: stringList(raw.keys) };
}

const trimmedEntries = (entries: string[]): string[] =>
    entries.map((entry) => entry.trim()).filter(Boolean);

/**
 * The `inputFiles` a draft writes: the mode plus the list that mode uses, trimmed and without
 * empty entries. `matching` without globs is the bare default `{ mode: "matching" }`.
 */
export function inputFilesFromDraft(draft: InputFilesDraft): CompliancePipelineInputFiles {
    switch (draft.mode) {
        case "explicit":
            return { mode: "explicit", keys: trimmedEntries(draft.keys) };
        case "wholeAsset":
            return { mode: "wholeAsset" };
        default: {
            const filter = trimmedEntries(draft.filter);
            return filter.length ? { mode: "matching", filter } : { mode: "matching" };
        }
    }
}

/**
 * Field-level errors of an input-file selection as the backend would reject it: globs only
 * with `matching`, keys only and at least one with `explicit`, every key an asset-relative
 * `/path`, and the list sizes within their limits. An empty map means the selection is valid.
 */
export function validateInputFiles(draft: InputFilesDraft): InputFilesErrors {
    const errors: InputFilesErrors = {};
    if (!PIPELINE_INPUT_FILES_MODES.includes(draft.mode)) {
        errors.mode = "Choose how the input files are selected";
        return errors;
    }
    if (draft.mode === "matching") {
        const filter = trimmedEntries(draft.filter);
        if (filter.length > INPUT_FILES_MAX_FILTERS) {
            errors.filter = `Use at most ${INPUT_FILES_MAX_FILTERS} globs`;
        } else if (filter.some((glob) => glob.length > INPUT_FILES_MAX_FILTER_LENGTH)) {
            errors.filter = `Each glob may have at most ${INPUT_FILES_MAX_FILTER_LENGTH} characters`;
        }
    }
    if (draft.mode === "explicit") {
        const keys = trimmedEntries(draft.keys);
        if (keys.length === 0) {
            errors.keys = "List at least one file path";
        } else if (keys.length > INPUT_FILES_MAX_KEYS) {
            errors.keys = `Use at most ${INPUT_FILES_MAX_KEYS} file paths`;
        } else if (keys.some((key) => !key.startsWith("/") || key.length < 2)) {
            errors.keys = "Each path must begin with / and name a file, e.g. /models/part.stl";
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
    inputFiles: InputFilesDraft;
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
        const { ruleType, enforcement, pipelineRef, inputFiles, ...rest } = d;
        const type: ComplianceRuleType = ruleType || "pipeline";
        // Only a pipeline rule selects input files; on any other rule the property is opaque.
        if (type !== "pipeline" && inputFiles !== undefined) {
            rest.inputFiles = inputFiles;
        }
        rules.push({
            name,
            ruleType: type,
            enforcement: enforcement || "warn",
            pipelineRef: normalizePipelineRef(pipelineRef),
            inputFiles:
                type === "pipeline"
                    ? inputFilesDraftFrom(inputFiles)
                    : { ...DEFAULT_INPUT_FILES_DRAFT },
            rest,
        });
    }
    return { extends: typeof body.extends === "string" ? body.extends : "", rules };
}

/**
 * The body a draft describes. A pipeline rule always carries its `inputFiles` (the bare
 * `{ mode: "matching" }` when it uses the default) so the JSON shows how files are selected.
 */
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
            def.inputFiles = inputFilesFromDraft(rule.inputFiles);
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
    const { ruleType, enforcement, pipelineRef, inputFiles, ...rest } = EXAMPLE_PIPELINE_RULE;
    return {
        name,
        ruleType,
        enforcement,
        pipelineRef: { ...pipelineRef, templateId: "" },
        inputFiles: inputFilesDraftFrom(inputFiles),
        rest: JSON.parse(JSON.stringify(rest)),
    };
}
