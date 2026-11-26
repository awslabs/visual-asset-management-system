/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The registration row of the bucket VAMS creates records the key that encrypts it (#1978): with the
 * deployment's customer managed key on, the bucket populator's `buckets` property carries
 * `bucketKmsKeyArn` for the created bucket, resolved to the stack key; without it, no key is
 * recorded. The key is not added to the external-key grants, so no IAM statement changes.
 */

import { actionsOf, resourcesOf, SynthResult, synthTemplate } from "../support/templateSynth";

jest.setTimeout(1_200_000);

const populatorBuckets = (s: SynthResult): any[] => {
    const resources = s
        .ofType("AWS::CloudFormation::CustomResource")
        .filter((r) => r.properties.buckets !== undefined);
    expect(resources).toHaveLength(1);
    return JSON.parse(SynthResult.flatten(resources[0].properties.buckets));
};

const kmsStatements = (s: SynthResult) =>
    [...s.ofType("AWS::IAM::Policy"), ...s.ofType("AWS::IAM::ManagedPolicy")]
        .flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[])
        .filter((st) => actionsOf(st).some((a) => a.startsWith("kms:")))
        .map((st) => JSON.stringify({ a: actionsOf(st).sort(), r: resourcesOf(st).sort() }))
        .sort();

describe("created bucket registration key", () => {
    test("with the customer managed key on, the created bucket records it", () => {
        const s = synthTemplate("commercial", {
            mutate: (c: any) => {
                c.app.useKmsCmkEncryption.enabled = true;
            },
            mutateKey: "created-bucket-key-cmk",
        });
        const buckets = populatorBuckets(s);
        expect(buckets).toHaveLength(1);
        expect(buckets[0].isDefault).toBe(true);
        // The stack key's ARN, a reference to the key the stack creates.
        expect(buckets[0].bucketKmsKeyArn).toMatch(
            /^\$\{[A-Za-z0-9]*VAMSEncryptionKMSKey[A-Za-z0-9]*\.Arn\}$/
        );
        expect(kmsStatements(s).length).toBeGreaterThan(0);
    });

    test("without it, no key is recorded", () => {
        const s = synthTemplate("commercial", {
            mutate: (c: any) => {
                c.app.useKmsCmkEncryption.enabled = false;
            },
            mutateKey: "created-bucket-key-nocmk",
        });
        const buckets = populatorBuckets(s);
        expect(buckets).toHaveLength(1);
        expect(buckets[0]).not.toHaveProperty("bucketKmsKeyArn");
    });
});
