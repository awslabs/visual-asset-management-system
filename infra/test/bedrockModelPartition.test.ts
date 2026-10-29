/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Bedrock analysis model for the SYSTEM GenAI metadata pipeline is per-partition, and must not be inherited.
 *
 * Which foundation models an account may invoke differs by partition AND by account entitlement, and the
 * configured value does double duty: it is the `modelId` the pipeline Lambda passes to the Bedrock
 * Converse API, and it is the model named in that Lambda's `bedrock:InvokeModel` resource ARN. A value
 * that is right for the commercial partition therefore produces a GovCloud or EU Sovereign deployment
 * that synthesizes cleanly, deploys cleanly, and fails at the first invoke with an IAM policy naming a
 * model that does not exist in that partition.
 *
 * So the EU Sovereign template ships the field BLANK with the GenAI layer off, the GovCloud template names
 * a `us-gov.` inference profile, and `getConfig()` rejects an empty id whenever the GenAI layer is on and
 * rejects an inference-profile prefix from another partition.
 *
 * `getConfig()` reads config.json from disk and needs a full CDK app, so — following
 * apiGatewayTimeout.test.ts — these tests read the shipped templates and exercise the same predicate
 * rather than invoking getConfig() itself. The template assertions are the ones that matter: they are
 * what catches a commercial model id being copied back into a sovereign template.
 */

import * as fs from "fs";
import * as path from "path";

const CONFIG_DIR = path.join(__dirname, "..", "config");

function template(name: string): any {
    return JSON.parse(
        fs.readFileSync(path.join(CONFIG_DIR, `config.template.${name}.json`), "utf-8")
    );
}

function pipeline(name: string): any {
    return template(name).app.pipelines.useSystemGenAiMetadata;
}

function modelId(name: string): unknown {
    return pipeline(name).bedrockModels.analysisModelId;
}

/** getConfig()'s test for "no usable model id was supplied". */
function isUnset(value: unknown): boolean {
    return value == undefined || (typeof value === "string" && value.trim() === "");
}

describe("SYSTEM GenAI analysis model, by partition", () => {
    it("ships blank in the EU Sovereign template, with the GenAI layer off", () => {
        expect(isUnset(modelId("eusovereign"))).toBe(true);
        expect(pipeline("eusovereign").useGenAiAnalysis).toBe(false);
    });

    it("ships a GovCloud inference profile in the GovCloud template", () => {
        expect(String(modelId("govcloud"))).toMatch(/^us-gov\./);
    });

    it("ships a usable default in the commercial template", () => {
        // Commercial keeps a default so an existing deployment that never set the field still deploys.
        expect(isUnset(modelId("commercial"))).toBe(false);
    });

    it("never names a commercial inference profile in a sovereign template", () => {
        for (const name of ["govcloud", "eusovereign"]) {
            expect(String(modelId(name) ?? "")).not.toMatch(/^(global|us)\./);
        }
    });

    describe("the resolution rule getConfig() applies", () => {
        /** Whether the deployment is rejected: GenAI analysis on and no model id, or a foreign profile prefix. */
        function isRejected(partition: string, analysisOn: boolean, value: unknown): boolean {
            if (!analysisOn) return false;
            if (isUnset(value)) return true;
            const id = String(value);
            if (id.startsWith("global.") && partition !== "aws") return true;
            if (id.startsWith("us-gov.") && partition !== "aws-us-gov") return true;
            // GovCloud lets the commercial "us." form through with a warning; elsewhere it is rejected.
            if (id.startsWith("us.") && partition !== "aws" && partition !== "aws-us-gov")
                return true;
            return false;
        }

        it("rejects GenAI analysis with no model id in every partition", () => {
            for (const partition of ["aws", "aws-us-gov", "aws-eusc"]) {
                expect(isRejected(partition, true, "")).toBe(true);
            }
        });

        it("stays silent when the GenAI layer is off", () => {
            // An attributes-only pipeline names no model, so a blank value must not block a deployment.
            expect(isRejected("aws-us-gov", false, "")).toBe(false);
            expect(isRejected("aws-eusc", false, "")).toBe(false);
        });

        it("rejects a cross-Region profile prefix from another partition", () => {
            expect(isRejected("aws-us-gov", true, String(modelId("commercial")))).toBe(true);
            expect(isRejected("aws", true, String(modelId("govcloud")))).toBe(true);
            expect(isRejected("aws-eusc", true, "us.anthropic.claude-sonnet-4-20250514-v1:0")).toBe(
                true
            );
        });

        it("accepts each shipped template's own model id", () => {
            expect(isRejected("aws", true, modelId("commercial"))).toBe(false);
            expect(isRejected("aws-us-gov", true, modelId("govcloud"))).toBe(false);
        });

        it("accepts a bare in-Region model id or an inference-profile ARN anywhere", () => {
            expect(
                isRejected("aws-us-gov", true, "anthropic.claude-3-5-sonnet-20240620-v1:0")
            ).toBe(false);
            expect(
                isRejected(
                    "aws-eusc",
                    true,
                    "arn:aws-eusc:bedrock:eusc-de-east-1:111122223333:inference-profile/eu.example"
                )
            ).toBe(false);
        });
    });
});
