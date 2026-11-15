/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Bedrock model for the GenAI 3D labeling pipeline is per-partition, and must not be inherited.
 *
 * Which foundation models an account may invoke differs by partition AND by account entitlement, and the
 * configured value does double duty: it is the `modelId` the pipeline Lambda passes to the Bedrock
 * Converse API, and it is the model named in that Lambda's `bedrock:InvokeModel` resource ARN. A value
 * that is right for the commercial partition therefore produces a GovCloud or EU Sovereign deployment
 * that synthesizes cleanly, deploys cleanly, and fails at the first invoke with an IAM policy naming a
 * model that does not exist in that partition.
 *
 * So the GovCloud and EU Sovereign templates ship the field BLANK and `getConfig()` requires it when the
 * pipeline is enabled outside the commercial partition. The commercial default stays, because removing it
 * would break every existing commercial deployment that never set the field.
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

function modelId(name: string): unknown {
    return template(name).app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId;
}

/** getConfig()'s test for "no usable model id was supplied". */
function isUnset(value: unknown): boolean {
    return value == undefined || value === "" || value === "UNDEFINED";
}

describe("GenAI 3D labeling Bedrock model, by partition", () => {
    it("ships blank in the GovCloud template", () => {
        expect(isUnset(modelId("govcloud"))).toBe(true);
    });

    it("ships blank in the EU Sovereign template", () => {
        expect(isUnset(modelId("eusovereign"))).toBe(true);
    });

    it("ships a usable default in the commercial template", () => {
        // Commercial keeps a default so an existing deployment that never set the field still deploys.
        expect(isUnset(modelId("commercial"))).toBe(false);
    });

    it("never names a commercial model in a sovereign template", () => {
        // The specific failure this closes: the sovereign templates previously carried
        // "global.anthropic.claude-sonnet-4-20250514-v1:0", a commercial inference profile.
        for (const name of ["govcloud", "eusovereign"]) {
            expect(String(modelId(name) ?? "")).not.toMatch(/anthropic|amazon\.|stability|meta\./);
        }
    });

    describe("the resolution rule getConfig() applies", () => {
        /** Whether the deployment is rejected: enabled, no model id, and not the commercial partition. */
        function isRejected(partition: string, enabled: boolean, value: unknown): boolean {
            return partition !== "aws" && enabled && isUnset(value);
        }

        it("defaults rather than rejecting in the commercial partition", () => {
            expect(isRejected("aws", true, "")).toBe(false);
        });

        it("rejects an enabled pipeline with no model id in GovCloud", () => {
            expect(isRejected("aws-us-gov", true, "")).toBe(true);
        });

        it("rejects an enabled pipeline with no model id in the EU Sovereign Cloud", () => {
            expect(isRejected("aws-eusc", true, undefined)).toBe(true);
        });

        it("treats the UNDEFINED placeholder as no model id", () => {
            expect(isRejected("aws-us-gov", true, "UNDEFINED")).toBe(true);
        });

        it("stays silent when the pipeline is disabled", () => {
            // A blank value on a disabled pipeline is not a misconfiguration, so it must not block a
            // GovCloud or EU Sovereign deployment that never uses Bedrock at all.
            expect(isRejected("aws-us-gov", false, "")).toBe(false);
            expect(isRejected("aws-eusc", false, "")).toBe(false);
        });

        it("accepts a supplied model id or inference-profile ARN anywhere", () => {
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
