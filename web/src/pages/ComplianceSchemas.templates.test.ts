/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The templates the Compliance Schemas page offers are the shipped templates under
 * `documentation/complianceSchemaTemplates/`, body for body. The backend suite parses every shipped
 * template through the vams-rules-v1 models (`test_shipped_schema_templates.py`), so this equality
 * is what holds the web templates to the models too. Every check of a rule type the Visual Builder
 * edits also passes the builder's own validation, and the templates exercise every rule type.
 */

import * as fs from "fs";
import * as path from "path";
import { SCHEMA_TEMPLATES } from "./ComplianceSchemas";
import { CHECK_VALIDATORS } from "../components/compliance/complianceSchemaRules";
import { COMPLIANCE_RULE_TYPES } from "../services/ComplianceService";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
    loader: { config: jest.fn() },
}));

const TEMPLATES_DIR = path.join(
    __dirname,
    "..",
    "..",
    "..",
    "documentation",
    "complianceSchemaTemplates"
);

/** Web template key → shipped template file. */
const SHIPPED: Record<string, string> = {
    engineering: "engineering-asset-standard.json",
    classification: "data-classification.json",
    model3d: "3d-model-quality.json",
    retention: "retention-policy.json",
};

const shippedBody = (file: string) =>
    JSON.parse(fs.readFileSync(path.join(TEMPLATES_DIR, file), "utf8")).schemaBody;

describe("Compliance schema templates", () => {
    it("ships a JSON template for every web template that mirrors one", () => {
        expect(
            fs
                .readdirSync(TEMPLATES_DIR)
                .filter((f) => f.endsWith(".json"))
                .sort()
        ).toEqual(Object.values(SHIPPED).sort());
    });

    it.each(Object.entries(SHIPPED))("the %s template is the shipped %s body", (key, file) => {
        expect(SCHEMA_TEMPLATES[key].body).toEqual(shippedBody(file));
    });

    it("exercises every rule type", () => {
        const types = new Set(
            Object.values(SCHEMA_TEMPLATES).flatMap(({ body }) =>
                Object.values(body.rules as Record<string, any>).map((rule) => rule.ruleType)
            )
        );
        expect([...types].sort()).toEqual([...COMPLIANCE_RULE_TYPES].sort());
    });

    it("passes the builder's validation on every check it edits", () => {
        for (const { body } of Object.values(SCHEMA_TEMPLATES)) {
            for (const rule of Object.values(body.rules as Record<string, any>)) {
                const validate = CHECK_VALIDATORS[rule.ruleType as keyof typeof CHECK_VALIDATORS];
                if (!validate) continue;
                for (const check of rule.checks) {
                    expect(validate(check)).toEqual({});
                }
            }
        }
    });
});
