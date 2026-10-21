/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    COMPLIANCE_METADATA_SCOPES,
    COMPLIANCE_RULE_TYPES,
    ComplianceAssetAttributeField,
    ComplianceEnforcementLevel,
    ComplianceMetadataScope,
    CompliancePipelineInputFiles,
    CompliancePipelineInputFilesMode,
    CompliancePipelineRef,
    CompliancePipelineRule,
    ComplianceRule,
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
    return newRuleDraft("pipeline", existingNames);
}

// --- Rule types and the checks the Visual Builder edits ---

/** Limits the backend places on metadata, file-structure, version-coverage and asset-attribute rules. */
export const RULE_MAX_CHECKS = 25;
export const PATH_PATTERNS_MAX = 25;
export const PATTERN_MAX_LENGTH = 512;
export const TAGS_MAX = 50;
export const TAG_MAX_LENGTH = 256;

export interface RuleTypeOption {
    value: ComplianceRuleType;
    label: string;
    description: string;
}

/** Every rule type in the order "Add rule" offers them, with its user-visible label. */
export const RULE_TYPE_OPTIONS: RuleTypeOption[] = [
    {
        value: "pipeline",
        label: "Pipeline rule",
        description: "Runs a workflow on the asset's files and checks its output",
    },
    {
        value: "metadata",
        label: "Metadata rule",
        description: "Validates the asset's metadata against a metadata schema",
    },
    {
        value: "relationship",
        label: "Relationship rule",
        description: "Counts the asset's parent, child or related links",
    },
    {
        value: "fileStructure",
        label: "File structure rule",
        description: "Requires or forbids file paths and bounds the number of files",
    },
    {
        value: "versionCoverage",
        label: "Version coverage rule",
        description: "Requires every current file to be captured in an asset version",
    },
    {
        value: "assetAttributes",
        label: "Asset attributes rule",
        description: "Checks the asset's name, description or tags",
    },
];

/** The rule types whose checks the Visual Builder edits field by field. */
export const CHECK_EDITED_RULE_TYPES: ComplianceRuleType[] = [
    "fileStructure",
    "versionCoverage",
    "assetAttributes",
];

/** The rule each type starts from when it is added in the Visual Builder. */
export const EXAMPLE_RULES: Record<ComplianceRuleType, ComplianceRule> = {
    pipeline: EXAMPLE_PIPELINE_RULE,
    metadata: {
        ruleType: "metadata",
        enforcement: "warn",
        metadataSchemaRef: { databaseId: "GLOBAL", schemaName: "defaultAsset" },
        checks: [{ name: "required-fields-present", validateRequired: true, validateTypes: true }],
    },
    relationship: {
        ruleType: "relationship",
        enforcement: "warn",
        checks: [
            {
                name: "has-parent",
                direction: "parents",
                relationshipType: "parentChild",
                minCount: 1,
            },
        ],
    },
    fileStructure: {
        ruleType: "fileStructure",
        enforcement: "warn",
        checks: [
            {
                name: "model-file-present",
                description: "At least one 3D model file is present",
                requiredPaths: ["\\.(glb|gltf|obj|stl|fbx)$"],
            },
        ],
    },
    versionCoverage: {
        ruleType: "versionCoverage",
        enforcement: "warn",
        checks: [
            {
                name: "files-captured-in-a-version",
                description: "Every current file is recorded in an asset version",
                excludePaths: ["\\.previewFile\\."],
            },
        ],
    },
    assetAttributes: {
        ruleType: "assetAttributes",
        enforcement: "inform",
        checks: [
            {
                name: "naming-convention",
                description: "The asset name starts with a letter or digit and has no spaces",
                field: "assetName",
                matches: "^[A-Za-z0-9][A-Za-z0-9_.-]*$",
            },
        ],
    },
};

const RULE_NAME_PREFIX: Record<ComplianceRuleType, string> = {
    pipeline: "pipeline-rule",
    metadata: "metadata-rule",
    relationship: "relationship-rule",
    fileStructure: "file-structure-rule",
    versionCoverage: "version-coverage-rule",
    assetAttributes: "asset-attributes-rule",
};

/** A rule of `ruleType` seeded from its example, named so it does not clash with `existingNames`. */
export function newRuleDraft(ruleType: ComplianceRuleType, existingNames: string[]): RuleDraft {
    const prefix = RULE_NAME_PREFIX[ruleType];
    let index = existingNames.length + 1;
    let name = `${prefix}-${index}`;
    while (existingNames.includes(name)) {
        index += 1;
        name = `${prefix}-${index}`;
    }
    const { enforcement, pipelineRef, inputFiles, ...rest } = JSON.parse(
        JSON.stringify(EXAMPLE_RULES[ruleType])
    );
    delete rest.ruleType;
    return {
        name,
        ruleType,
        enforcement,
        pipelineRef:
            ruleType === "pipeline"
                ? { ...normalizePipelineRef(pipelineRef), templateId: "" }
                : normalizePipelineRef(undefined),
        inputFiles:
            ruleType === "pipeline"
                ? inputFilesDraftFrom(inputFiles)
                : { ...DEFAULT_INPUT_FILES_DRAFT },
        rest,
    };
}

/** Whether a rule type is one of the vams-rules-v1 types. */
export const isKnownRuleType = (value: any): value is ComplianceRuleType =>
    COMPLIANCE_RULE_TYPES.includes(value);

/** The check a rule of `ruleType` gains from "Add check", numbered after the existing ones. */
export function newCheck(ruleType: ComplianceRuleType, existing: number): Record<string, any> {
    const name = `check-${existing + 1}`;
    if (ruleType === "fileStructure") return { name, minFiles: 1 };
    if (ruleType === "assetAttributes") return { name, field: "tags", minTags: 1 };
    return { name };
}

const isEmptyValue = (value: any) =>
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0);

/**
 * A check with `patch` applied. A key patched to an empty value (undefined, "", []) is removed, so
 * the JSON carries only the criteria a check uses; keys the patch does not name are kept as they are.
 */
export function patchCheck(
    check: Record<string, any>,
    patch: Record<string, any>
): Record<string, any> {
    const next = { ...check };
    for (const [key, value] of Object.entries(patch)) {
        if (isEmptyValue(value)) {
            delete next[key];
        } else {
            next[key] = value;
        }
    }
    return next;
}

/** The checks of a rule's opaque part, as a list of objects. */
export const checksOf = (rule: RuleDraft): Record<string, any>[] =>
    Array.isArray(rule.rest.checks)
        ? rule.rest.checks.map((check: any) => (check && typeof check === "object" ? check : {}))
        : [];

/** A string list attribute of a check; [] when absent or not a list. */
export const checkList = (check: Record<string, any>, key: string): string[] =>
    stringList(check[key]);

/** A number input's text as a number; undefined when empty or not a number. */
export function numberFromInput(text: string): number | undefined {
    if (text.trim() === "") return undefined;
    const value = Number(text);
    return Number.isFinite(value) ? value : undefined;
}

/** Field errors of one check, keyed by the check field; `check` holds an error of the whole check. */
export type CheckErrors = Partial<Record<string, string>>;

/** The rule-level error of a check list as the backend reports it, or undefined. */
export function checksCountError(checks: Record<string, any>[]): string | undefined {
    if (checks.length === 0) return "checks: ensure this value has at least 1 items";
    if (checks.length > RULE_MAX_CHECKS) {
        return `checks: ensure this value has at most ${RULE_MAX_CHECKS} items`;
    }
    return undefined;
}

function patternListError(field: string, patterns: string[]): string | undefined {
    if (patterns.length > PATH_PATTERNS_MAX) {
        return `${field}: ensure this value has at most ${PATH_PATTERNS_MAX} items`;
    }
    if (patterns.some((pattern) => !pattern || pattern.length > PATTERN_MAX_LENGTH)) {
        return `Every '${field}' pattern must be 1 to ${PATTERN_MAX_LENGTH} characters`;
    }
    return undefined;
}

function countError(field: string, value: any): string | undefined {
    if (value === undefined || value === null) return undefined;
    if (typeof value !== "number" || !Number.isInteger(value)) {
        return `${field}: value is not a valid integer`;
    }
    if (value < 0) return `${field}: ensure this value is greater than or equal to 0`;
    return undefined;
}

function tagListError(field: string, tags: string[]): string | undefined {
    if (tags.length > TAGS_MAX) return `${field}: ensure this value has at most ${TAGS_MAX} items`;
    if (tags.some((tag) => !tag.trim())) return `Every '${field}' entry must be a non-empty string`;
    if (tags.some((tag) => tag.length > TAG_MAX_LENGTH)) {
        return `${field} must be lower than ${TAG_MAX_LENGTH} characters`;
    }
    return undefined;
}

const setError = (errors: CheckErrors, key: string, message: string | undefined) => {
    if (message && !errors[key]) errors[key] = message;
};

function nameError(check: Record<string, any>): string | undefined {
    return typeof check.name === "string" && check.name.trim()
        ? undefined
        : "Check name is required";
}

/**
 * Errors of a file-structure check with the messages the backend returns: at least one criterion,
 * `minFiles <= maxFiles`, non-negative whole counts, and at most 25 patterns of 1 to 512 characters
 * per list. Whether a pattern is a valid regular expression is checked by the API when the schema is
 * saved.
 */
export function validateFileStructureCheck(check: Record<string, any>): CheckErrors {
    const errors: CheckErrors = {};
    setError(errors, "name", nameError(check));
    const required = checkList(check, "requiredPaths");
    const forbidden = checkList(check, "forbiddenPaths");
    setError(errors, "requiredPaths", patternListError("requiredPaths", required));
    setError(errors, "forbiddenPaths", patternListError("forbiddenPaths", forbidden));
    setError(errors, "minFiles", countError("minFiles", check.minFiles));
    setError(errors, "maxFiles", countError("maxFiles", check.maxFiles));
    const hasMin = check.minFiles !== undefined && check.minFiles !== null;
    const hasMax = check.maxFiles !== undefined && check.maxFiles !== null;
    if (!required.length && !forbidden.length && !hasMin && !hasMax) {
        errors.check =
            "At least one of 'requiredPaths', 'forbiddenPaths', 'minFiles' or 'maxFiles' is required";
    }
    if (
        hasMin &&
        hasMax &&
        !errors.minFiles &&
        !errors.maxFiles &&
        check.minFiles > check.maxFiles
    ) {
        errors.maxFiles = "'minFiles' must be <= 'maxFiles'";
    }
    return errors;
}

/** Errors of a version-coverage check with the messages the backend returns. */
export function validateVersionCoverageCheck(check: Record<string, any>): CheckErrors {
    const errors: CheckErrors = {};
    setError(errors, "name", nameError(check));
    setError(
        errors,
        "excludePaths",
        patternListError("excludePaths", checkList(check, "excludePaths"))
    );
    return errors;
}

export const ASSET_ATTRIBUTE_FIELD_OPTIONS: {
    value: ComplianceAssetAttributeField;
    label: string;
}[] = [
    { value: "assetName", label: "Asset name" },
    { value: "description", label: "Description" },
    { value: "tags", label: "Tags" },
];

/**
 * Errors of an asset-attribute check with the messages the backend returns: a name or description
 * takes exactly one of `matches` / `notMatches` and no tag criteria; tags take at least one tag
 * criterion and no pattern; lists and counts are bounded as the API bounds them.
 */
export function validateAssetAttributeCheck(check: Record<string, any>): CheckErrors {
    const errors: CheckErrors = {};
    setError(errors, "name", nameError(check));
    if (!ASSET_ATTRIBUTE_FIELD_OPTIONS.some((option) => option.value === check.field)) {
        errors.field = "field: unexpected value; permitted: 'assetName', 'description', 'tags'";
        return errors;
    }
    const required = checkList(check, "requiredTags");
    const forbidden = checkList(check, "forbiddenTags");
    const hasMinTags = check.minTags !== undefined && check.minTags !== null;
    const hasPattern = typeof check.matches === "string" || typeof check.notMatches === "string";
    if (check.field === "tags") {
        if (hasPattern) errors.check = "'matches' and 'notMatches' do not apply to the tags field";
        else if (!required.length && !forbidden.length && !hasMinTags) {
            errors.check =
                "At least one of 'requiredTags', 'forbiddenTags' or 'minTags' is required";
        }
        setError(errors, "requiredTags", tagListError("requiredTags", required));
        setError(errors, "forbiddenTags", tagListError("forbiddenTags", forbidden));
        setError(errors, "minTags", countError("minTags", check.minTags));
        return errors;
    }
    if (required.length || forbidden.length || hasMinTags) {
        errors.check = "'requiredTags', 'forbiddenTags' and 'minTags' apply to the tags field only";
    } else if ((typeof check.matches === "string") === (typeof check.notMatches === "string")) {
        errors.pattern = "Exactly one of 'matches' or 'notMatches' is required for this field";
    } else {
        const key = typeof check.matches === "string" ? "matches" : "notMatches";
        const pattern: string = check[key];
        if (!pattern || pattern.length > PATTERN_MAX_LENGTH) {
            errors.pattern = `Every '${key}' pattern must be 1 to ${PATTERN_MAX_LENGTH} characters`;
        }
    }
    return errors;
}

/** The validator of a check-edited rule type's checks. */
export const CHECK_VALIDATORS: Partial<
    Record<ComplianceRuleType, (check: Record<string, any>) => CheckErrors>
> = {
    fileStructure: validateFileStructureCheck,
    versionCoverage: validateVersionCoverageCheck,
    assetAttributes: validateAssetAttributeCheck,
};

// --- Metadata rule scopes ---

export interface MetadataScopeOption {
    value: ComplianceMetadataScope;
    label: string;
    description: string;
}

/** The metadata rule scopes in display order, with their user-visible labels. */
export const METADATA_SCOPE_OPTIONS: MetadataScopeOption[] = [
    {
        value: "asset",
        label: "Asset metadata",
        description: "The asset's own metadata. The schema must be an asset metadata schema.",
    },
    {
        value: "files",
        label: "File metadata",
        description:
            "The metadata of each current file. The schema must be a file metadata schema; every file in scope must pass.",
    },
    {
        value: "fileAttributes",
        label: "File attributes",
        description:
            "The attributes of each current file. The schema must be a file attribute schema; every file in scope must pass.",
    },
    {
        value: "links",
        label: "Asset link metadata",
        description:
            "The metadata of each asset link. The schema must be an asset link metadata schema; every link in scope must pass.",
    },
];

/** The scopes that iterate the asset's current files and take include / exclude paths. */
export const METADATA_FILE_SCOPES: ComplianceMetadataScope[] = ["files", "fileAttributes"];

/** `linkDirection` choices; "" is the absent value (both directions). */
export const LINK_DIRECTION_OPTIONS: { value: string; label: string }[] = [
    { value: "", label: "Any direction" },
    { value: "parents", label: "Parents (links to this asset)" },
    { value: "children", label: "Children (links from this asset)" },
    { value: "related", label: "Both directions" },
];

/** `relationshipType` choices; "" is the absent value (either type). */
export const LINK_RELATIONSHIP_TYPE_OPTIONS: { value: string; label: string }[] = [
    { value: "", label: "Any relationship type" },
    { value: "parentChild", label: "Parent-child" },
    { value: "related", label: "Related" },
];

/** A metadata rule's scope as its opaque properties carry it; absent or unknown reads as `asset`. */
export function metadataScopeOf(rest: Record<string, any>): ComplianceMetadataScope {
    return COMPLIANCE_METADATA_SCOPES.includes(rest.scope) ? rest.scope : "asset";
}

/**
 * The opaque properties of a metadata rule switched to `scope`: the filters another scope takes are
 * removed, so the body never carries a field the API refuses for the new scope. `asset` is written
 * as no `scope` at all, the default.
 */
export function withMetadataScope(
    rest: Record<string, any>,
    scope: ComplianceMetadataScope
): Record<string, any> {
    const next = { ...rest };
    delete next.scope;
    if (scope !== "asset") next.scope = scope;
    if (!METADATA_FILE_SCOPES.includes(scope)) {
        delete next.includePaths;
        delete next.excludePaths;
    }
    if (scope !== "links") {
        delete next.linkDirection;
        delete next.relationshipType;
    }
    return next;
}

export interface MetadataScopeErrors {
    scope?: string;
    includePaths?: string;
    excludePaths?: string;
    linkDirection?: string;
    relationshipType?: string;
}

/**
 * Errors of a metadata rule's scope fields with the messages the API returns: a known scope, path
 * filters on a file scope only (at most 25 patterns of 1 to 512 characters each), link filters on the
 * links scope only, each from its own vocabulary. Whether a pattern is a valid regular expression is
 * checked by the API when the schema is saved.
 */
export function validateMetadataScope(rest: Record<string, any>): MetadataScopeErrors {
    const errors: MetadataScopeErrors = {};
    if (rest.scope !== undefined && !COMPLIANCE_METADATA_SCOPES.includes(rest.scope)) {
        errors.scope =
            "scope: value is not a valid enumeration member; permitted: 'asset', 'files', 'fileAttributes', 'links'";
        return errors;
    }
    const scope = metadataScopeOf(rest);
    const includePaths = stringList(rest.includePaths);
    const excludePaths = stringList(rest.excludePaths);
    if (METADATA_FILE_SCOPES.includes(scope)) {
        setScopeError(errors, "includePaths", patternListError("includePaths", includePaths));
        setScopeError(errors, "excludePaths", patternListError("excludePaths", excludePaths));
    } else if (includePaths.length || excludePaths.length) {
        errors.scope =
            "'includePaths' and 'excludePaths' apply to the files and fileAttributes scopes only";
    }
    const hasLinkFilter =
        (rest.linkDirection !== undefined && rest.linkDirection !== null) ||
        (rest.relationshipType !== undefined && rest.relationshipType !== null);
    if (scope !== "links" && hasLinkFilter && !errors.scope) {
        errors.scope = "'linkDirection' and 'relationshipType' apply to the links scope only";
    }
    if (scope === "links") {
        if (
            rest.linkDirection !== undefined &&
            !LINK_DIRECTION_OPTIONS.some((o) => o.value && o.value === rest.linkDirection)
        ) {
            errors.linkDirection =
                "linkDirection: unexpected value; permitted: 'parents', 'children', 'related'";
        }
        if (
            rest.relationshipType !== undefined &&
            !LINK_RELATIONSHIP_TYPE_OPTIONS.some(
                (o) => o.value && o.value === rest.relationshipType
            )
        ) {
            errors.relationshipType =
                "relationshipType: unexpected value; permitted: 'parentChild', 'related'";
        }
    }
    return errors;
}

function setScopeError(
    errors: MetadataScopeErrors,
    key: keyof MetadataScopeErrors,
    message: string | undefined
) {
    if (message && !errors[key]) errors[key] = message;
}
