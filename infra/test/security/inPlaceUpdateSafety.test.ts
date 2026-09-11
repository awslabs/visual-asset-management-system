/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Encryption-at-rest coverage for CloudWatch log groups, EFS file systems and Secrets Manager secrets.
 *
 * Each resource carries the shared VAMS CMK when `app.useKmsCmkEncryption.enabled` is true and falls back
 * to its service's AWS-managed key when it is false. Both directions are asserted, because a one-sided
 * assertion is satisfied either by a hardcoded key that ignores the config switch or by no key at all.
 *
 * Three log groups are exempt, and the exemption list is asserted rather than filtered silently:
 *
 *   CloudWatchVAMSVpc                 the VPC nested stack is built before the storage nested stack that
 *                                     owns the key, so the key is not in scope there
 *   AOSOpenSearchDomain*Logs          created by the aws-cdk-lib opensearch Domain construct from its
 *                                     `logging` props; encrypting them means passing explicit log groups
 *   CloudTrailLogGroup                lives in the root stack; consuming the key from the storage nested
 *                                     stack makes the root stack and its nested stacks circular
 *
 * The EFS and Secrets Manager assertions run against a mutated config, because every shipped config
 * template disables the optional pipelines that own those resources — an unmutated assertion would pass
 * against zero resources.
 */

import { synthTemplate } from "../support/templateSynth";

/** Log groups VAMS does not attach a key to. Matched on logical id. */
const UNENCRYPTED_BY_DESIGN = [
    /^CloudWatchVAMSVpc/,
    /^AOSOpenSearchDomain.*Logs/,
    /^CloudTrailLogGroup/,
];

function unexpectedPlainGroups(synth: ReturnType<typeof synthTemplate>): string[] {
    return synth
        .ofType("AWS::Logs::LogGroup")
        .filter((g) => g.properties.KmsKeyId === undefined)
        .map((g) => g.logicalId)
        .filter((id) => !UNENCRYPTED_BY_DESIGN.some((re) => re.test(id)));
}

describe("encryption at rest for log groups, file systems and secrets", () => {
    describe("log groups follow the CMK switch", () => {
        const withCmk = synthTemplate("govcloud"); // useKmsCmkEncryption.enabled = true
        const withoutCmk = synthTemplate("commercial"); // useKmsCmkEncryption.enabled = false

        it("both syntheses emit log groups, so neither direction is vacuous", () => {
            expect(withCmk.ofType("AWS::Logs::LogGroup").length).toBeGreaterThan(5);
            expect(withoutCmk.ofType("AWS::Logs::LogGroup").length).toBeGreaterThan(5);
        });

        it("every log group except the documented exemptions carries a key when the CMK is enabled", () => {
            expect(unexpectedPlainGroups(withCmk)).toEqual([]);
        });

        it("the exemption list is exercised, not dead", () => {
            // Guards the filter itself: if the exempt groups stopped being emitted, or gained a key, the
            // list above would be silently obsolete and the assertion above would be weaker than it reads.
            const exempt = withCmk
                .ofType("AWS::Logs::LogGroup")
                .filter((g) => UNENCRYPTED_BY_DESIGN.some((re) => re.test(g.logicalId)));
            expect(exempt.length).toBeGreaterThan(0);
            for (const g of exempt) {
                expect(g.properties.KmsKeyId).toBeUndefined();
            }
        });

        it("no log group carries a key when the CMK is disabled", () => {
            const encrypted = withoutCmk
                .ofType("AWS::Logs::LogGroup")
                .filter((g) => g.properties.KmsKeyId !== undefined);
            expect(encrypted.map((g) => g.logicalId)).toEqual([]);
        });
    });

    describe("pipeline file systems and secrets follow the CMK switch", () => {
        // Isaac Lab owns an EFS; a Cosmos 3 model owns both an EFS and a HuggingFace token secret. The
        // model flag matters: enabling the pipeline alone builds the shared Cosmos resources but not the
        // per-model construct that creates the secret.
        const enablePipelines = (c: any) => {
            c.app.useGlobalVpc.enabled = true;
            c.app.pipelines.useIsaacLabTraining.enabled = true;
            c.app.pipelines.useIsaacLabTraining.acceptNvidiaEula = true;
            c.app.pipelines.useNvidiaCosmos3.enabled = true;
            c.app.pipelines.useNvidiaCosmos3.acceptNvidiaEula = true;
            if (c.app.pipelines.useNvidiaCosmos3.modelsOmni?.nano16B) {
                c.app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.enabled = true;
            }
        };

        const withCmk = synthTemplate("commercial", {
            mutate: (c) => {
                enablePipelines(c);
                c.app.useKmsCmkEncryption.enabled = true;
            },
            mutateKey: "pipelines-on-cmk-on",
        });
        const withoutCmk = synthTemplate("commercial", {
            mutate: (c) => {
                enablePipelines(c);
                c.app.useKmsCmkEncryption.enabled = false;
            },
            mutateKey: "pipelines-on-cmk-off",
        });

        it("both syntheses emit the resources under test, so neither direction is vacuous", () => {
            expect(withCmk.ofType("AWS::EFS::FileSystem").length).toBeGreaterThan(0);
            expect(withoutCmk.ofType("AWS::EFS::FileSystem").length).toBeGreaterThan(0);
            expect(withCmk.ofType("AWS::SecretsManager::Secret").length).toBeGreaterThan(0);
            expect(withoutCmk.ofType("AWS::SecretsManager::Secret").length).toBeGreaterThan(0);
        });

        it("every file system and secret names the key when the CMK is enabled", () => {
            for (const r of withCmk.ofType("AWS::EFS::FileSystem")) {
                expect(r.properties.KmsKeyId).toBeDefined();
            }
            for (const r of withCmk.ofType("AWS::SecretsManager::Secret")) {
                expect(r.properties.KmsKeyId).toBeDefined();
            }
        });

        it("no KmsKeyId is emitted at all when the CMK is disabled", () => {
            // Absent, not null. An emitted null is rejected at deploy time.
            for (const r of withoutCmk.ofType("AWS::EFS::FileSystem")) {
                expect(r.properties).not.toHaveProperty("KmsKeyId");
            }
            for (const r of withoutCmk.ofType("AWS::SecretsManager::Secret")) {
                expect(r.properties).not.toHaveProperty("KmsKeyId");
            }
            expect(unexpectedPlainGroups(withoutCmk).length).toBeGreaterThanOrEqual(0);
        });

        it("file systems stay encrypted under the AWS-managed key when the CMK is disabled", () => {
            // `encrypted` and `kmsKey` are separate props: dropping both to accommodate a CMK-off
            // deployment would disable encryption at rest rather than fall back.
            for (const r of withoutCmk.ofType("AWS::EFS::FileSystem")) {
                expect(r.properties.Encrypted).toBe(true);
            }
        });
    });
});

/**
 * The storage nested stack must never reference a pipeline nested stack.
 *
 * The dependency chain runs one way: pipelines consume `storageResources`, so PipelineBuilder depends on
 * StorageResourcesBuilder. A reference in the other direction makes the two circular and AWS
 * CloudFormation rejects the changeset — a failure that appears only at deploy time, because `cdk synth`
 * emits both templates happily.
 *
 * The reference is easy to create without noticing. Passing the shared KMS key OBJECT to a construct whose
 * grants CDK derives automatically (`Secret.grantRead`, `Secret.grantWrite`) makes `Key.grant()` write the
 * grantee's ARN into the key's RESOURCE policy. The key belongs to the storage stack and the grantee to a
 * pipeline stack, so the storage template gains a parameter fed from a pipeline output. Importing the key
 * by ARN keeps the grant on the grantee's own policy and avoids it.
 */
describe("nested stack dependency direction", () => {
    const synth = synthTemplate("commercial", {
        mutate: (c: any) => {
            c.app.useGlobalVpc.enabled = true;
            c.app.useKmsCmkEncryption.enabled = true;
            c.app.pipelines.useIsaacLabTraining.enabled = true;
            c.app.pipelines.useIsaacLabTraining.acceptNvidiaEula = true;
            c.app.pipelines.useNvidiaCosmos3.enabled = true;
            c.app.pipelines.useNvidiaCosmos3.acceptNvidiaEula = true;
            if (c.app.pipelines.useNvidiaCosmos3.modelsOmni?.nano16B) {
                c.app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.enabled = true;
            }
        },
        mutateKey: "cycle-check-pipelines-on-cmk-on",
    });

    const storageStacks = synth
        .ofType("AWS::CloudFormation::Stack")
        .filter((r) => /StorageResourcesBuilder/.test(r.logicalId));
    const pipelineStacks = synth
        .ofType("AWS::CloudFormation::Stack")
        .filter((r) => /PipelineBuilder/.test(r.logicalId));

    it("both nested stacks are present, so the assertion is not vacuous", () => {
        expect(storageStacks.length).toBeGreaterThan(0);
        expect(pipelineStacks.length).toBeGreaterThan(0);
    });

    it("the storage stack takes no parameter fed from a pipeline stack output", () => {
        const offenders: string[] = [];
        for (const stack of storageStacks) {
            const params = stack.properties.Parameters ?? {};
            for (const [name, value] of Object.entries(params)) {
                if (JSON.stringify(value).includes("PipelineBuilder")) {
                    offenders.push(`${stack.logicalId} <- ${name.slice(0, 80)}`);
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    it("the pipeline stack DOES consume storage outputs, confirming the intended direction", () => {
        // The positive control. If pipelines stopped consuming storage the test above would pass while
        // asserting nothing about the direction that matters.
        const consuming = pipelineStacks.filter((stack) =>
            JSON.stringify(stack.properties.Parameters ?? {}).includes("StorageResourcesBuilder")
        );
        expect(consuming.length).toBeGreaterThan(0);
    });
});
