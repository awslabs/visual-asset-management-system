/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    CHECK_EDITED_RULE_TYPES,
    CHECK_VALIDATORS,
    EXAMPLE_RULES,
    PATH_PATTERNS_MAX,
    RULE_MAX_CHECKS,
    RULE_TYPE_OPTIONS,
    TAGS_MAX,
    bodyFromRulesDraft,
    checksCountError,
    newCheck,
    newRuleDraft,
    numberFromInput,
    patchCheck,
    validateAssetAttributeCheck,
    validateFileStructureCheck,
    validateVersionCoverageCheck,
} from "./complianceSchemaRules";
import { COMPLIANCE_RULE_TYPES } from "../../services/ComplianceService";

describe("rule types", () => {
    it("offers every vams-rules-v1 rule type, each with an example", () => {
        expect(RULE_TYPE_OPTIONS.map((o) => o.value)).toEqual(COMPLIANCE_RULE_TYPES);
        expect(COMPLIANCE_RULE_TYPES).toEqual([
            "pipeline",
            "metadata",
            "relationship",
            "fileStructure",
            "versionCoverage",
            "assetAttributes",
        ]);
        for (const type of COMPLIANCE_RULE_TYPES) {
            expect(EXAMPLE_RULES[type].ruleType).toBe(type);
        }
    });

    it("seeds each new rule from its example under a unique name", () => {
        const draft = newRuleDraft("fileStructure", ["file-structure-rule-1"]);
        expect(draft.name).toBe("file-structure-rule-2");
        const body = bodyFromRulesDraft({ extends: "", rules: [draft] });
        expect(body.rules[draft.name]).toEqual(EXAMPLE_RULES.fileStructure);
        expect(body.rules[draft.name]).not.toHaveProperty("pipelineRef");
        expect(body.rules[draft.name]).not.toHaveProperty("inputFiles");
    });

    it("gives every example check of a builder-edited type a clean bill", () => {
        for (const type of CHECK_EDITED_RULE_TYPES) {
            const validate = CHECK_VALIDATORS[type];
            expect(validate).toBeDefined();
            if (!validate) continue;
            for (const check of EXAMPLE_RULES[type].checks as Record<string, any>[]) {
                expect(validate(check)).toEqual({});
            }
            expect(validate(newCheck(type, 0))).toEqual({});
        }
    });
});

describe("check editing helpers", () => {
    it("removes a key patched to an empty value and keeps the rest", () => {
        const check = { name: "c", requiredPaths: ["a"], minFiles: 1, custom: true };
        expect(patchCheck(check, { requiredPaths: [], minFiles: undefined })).toEqual({
            name: "c",
            custom: true,
        });
        expect(patchCheck(check, { maxFiles: 4 })).toEqual({ ...check, maxFiles: 4 });
    });

    it("reads a number input", () => {
        expect(numberFromInput("")).toBeUndefined();
        expect(numberFromInput("  ")).toBeUndefined();
        expect(numberFromInput("3")).toBe(3);
        expect(numberFromInput("1.5")).toBe(1.5);
    });

    it("bounds the number of checks", () => {
        expect(checksCountError([])).toBe("checks: ensure this value has at least 1 items");
        expect(checksCountError(Array(RULE_MAX_CHECKS).fill({}))).toBeUndefined();
        expect(checksCountError(Array(RULE_MAX_CHECKS + 1).fill({}))).toBe(
            "checks: ensure this value has at most 25 items"
        );
    });
});

describe("file structure check", () => {
    it("needs a criterion and a name", () => {
        expect(validateFileStructureCheck({ name: "c" }).check).toBe(
            "At least one of 'requiredPaths', 'forbiddenPaths', 'minFiles' or 'maxFiles' is required"
        );
        expect(validateFileStructureCheck({ name: " ", minFiles: 0 }).name).toBe(
            "Check name is required"
        );
    });

    it("orders and types the counts", () => {
        expect(validateFileStructureCheck({ name: "c", minFiles: 3, maxFiles: 2 }).maxFiles).toBe(
            "'minFiles' must be <= 'maxFiles'"
        );
        expect(validateFileStructureCheck({ name: "c", minFiles: -1 }).minFiles).toBe(
            "minFiles: ensure this value is greater than or equal to 0"
        );
        expect(validateFileStructureCheck({ name: "c", maxFiles: 1.5 }).maxFiles).toBe(
            "maxFiles: value is not a valid integer"
        );
    });

    it("bounds the pattern lists", () => {
        const many = Array(PATH_PATTERNS_MAX + 1).fill("a");
        expect(validateFileStructureCheck({ name: "c", requiredPaths: many }).requiredPaths).toBe(
            "requiredPaths: ensure this value has at most 25 items"
        );
        expect(
            validateFileStructureCheck({ name: "c", forbiddenPaths: ["x".repeat(513)] })
                .forbiddenPaths
        ).toBe("Every 'forbiddenPaths' pattern must be 1 to 512 characters");
    });
});

describe("version coverage check", () => {
    it("accepts no exclusions and bounds them", () => {
        expect(validateVersionCoverageCheck({ name: "c" })).toEqual({});
        expect(
            validateVersionCoverageCheck({ name: "c", excludePaths: Array(26).fill("a") })
                .excludePaths
        ).toBe("excludePaths: ensure this value has at most 25 items");
    });
});

describe("asset attribute check", () => {
    it("takes exactly one pattern on a text field", () => {
        const message = "Exactly one of 'matches' or 'notMatches' is required for this field";
        expect(validateAssetAttributeCheck({ name: "c", field: "assetName" }).pattern).toBe(
            message
        );
        expect(
            validateAssetAttributeCheck({
                name: "c",
                field: "description",
                matches: "a",
                notMatches: "b",
            }).pattern
        ).toBe(message);
        expect(
            validateAssetAttributeCheck({ name: "c", field: "assetName", matches: "" }).pattern
        ).toBe("Every 'matches' pattern must be 1 to 512 characters");
        expect(
            validateAssetAttributeCheck({ name: "c", field: "assetName", minTags: 1, matches: "a" })
                .check
        ).toBe("'requiredTags', 'forbiddenTags' and 'minTags' apply to the tags field only");
    });

    it("takes a tag criterion and no pattern on the tags field", () => {
        expect(validateAssetAttributeCheck({ name: "c", field: "tags" }).check).toBe(
            "At least one of 'requiredTags', 'forbiddenTags' or 'minTags' is required"
        );
        expect(
            validateAssetAttributeCheck({ name: "c", field: "tags", minTags: 1, matches: "a" })
                .check
        ).toBe("'matches' and 'notMatches' do not apply to the tags field");
        expect(
            validateAssetAttributeCheck({
                name: "c",
                field: "tags",
                requiredTags: Array(TAGS_MAX + 1).fill("t"),
            }).requiredTags
        ).toBe("requiredTags: ensure this value has at most 50 items");
        expect(
            validateAssetAttributeCheck({
                name: "c",
                field: "tags",
                forbiddenTags: ["x".repeat(257)],
            }).forbiddenTags
        ).toBe("forbiddenTags must be lower than 256 characters");
    });

    it("names the permitted fields", () => {
        expect(validateAssetAttributeCheck({ name: "c", field: "other" }).field).toBe(
            "field: unexpected value; permitted: 'assetName', 'description', 'tags'"
        );
    });
});
