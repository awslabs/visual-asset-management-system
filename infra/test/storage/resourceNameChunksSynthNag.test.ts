/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The resource-name chunk parameters across the configuration matrix, with CDK Nag on: the shipped
 * templates, a minimal and a hardened commercial cell, and every add-on on. Each cell publishes its
 * chunks, grants the chunk path, and raises no Nag finding about either.
 */

import { enableEdh } from "../addon/edh/support/edhSynth";
import {
    actionsOf,
    resourcesOf,
    SynthResult,
    synthTemplate,
    TemplateName,
} from "../support/templateSynth";

jest.setTimeout(2_400_000);

// Every row has four entries: a row shorter than the test function's arity would hand Jest's
// `done` callback in as `mutate`.
type Cell = [
    label: string,
    template: TemplateName,
    key: string,
    mutate: ((c: any) => void) | undefined
];

const cells: Cell[] = [
    ["commercial default", "commercial", "rnc-nag-default", undefined],
    [
        "minimal: CloudFront, no VPC, no CMK, no WAF",
        "commercial",
        "rnc-nag-minimal",
        (c) => {
            c.app.useCloudFront.enabled = true;
            c.app.useGlobalVpc.enabled = false;
            c.app.useKmsCmkEncryption.enabled = false;
            c.app.useWaf = false;
        },
    ],
    [
        "hardened: KMS CMK, global VPC for all Lambdas, WAF",
        "commercial",
        "rnc-nag-hardened",
        (c) => {
            c.app.useKmsCmkEncryption.enabled = true;
            c.app.useGlobalVpc.enabled = true;
            c.app.useGlobalVpc.useForAllLambdas = true;
            c.app.useGlobalVpc.addVpcEndpoints = true;
            c.app.useWaf = true;
        },
    ],
    [
        "every add-on on",
        "commercial",
        "rnc-nag-addons",
        (c) => {
            enableEdh(c);
            c.app.addons.usePhysnaSync.enabled = true;
            c.app.addons.usePhysnaSync.tenantId = "11111111-2222-3333-4444-555555555555";
            c.app.addons.useGarnetFramework.enabled = true;
            c.app.addons.useGarnetFramework.garnetApiEndpoint = "https://garnet.example.com";
            c.app.addons.useGarnetFramework.garnetApiToken = "placeholder";
            c.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl =
                "https://sqs.us-east-1.amazonaws.com/123456789012/garnet-ingest";
        },
    ],
    ["GovCloud template", "govcloud", "rnc-nag-govcloud", undefined],
    ["EU Sovereign template", "eusovereign", "rnc-nag-eusc", undefined],
];

describe("resource-name chunks with CDK Nag", () => {
    test.each(cells)("%s", (_label, template, key, mutate) => {
        const s: SynthResult = synthTemplate(template, {
            ...(mutate ? { mutate, mutateKey: key } : {}),
            nag: true,
        });

        const prefix = `/vams-t1-${template}/resourceNamesChunks/`;
        const chunks = s
            .ofType("AWS::SSM::Parameter")
            .filter((r) => SynthResult.flatten(r.properties.Name).startsWith(prefix));
        expect(chunks.length).toBeGreaterThan(0);
        expect(chunks.length).toBeLessThanOrEqual(10);

        const grants = [...s.ofType("AWS::IAM::Policy"), ...s.ofType("AWS::IAM::ManagedPolicy")]
            .flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[])
            .filter((st) => resourcesOf(st).some((r) => r.includes("/resourceNamesChunks/")));
        expect(grants.length).toBeGreaterThan(0);
        expect(grants.every((st) => actionsOf(st).join() === "ssm:GetParameters")).toBe(true);

        // The GovCloud and EU Sovereign templates carry a Cognito finding of their own
        // (AwsSolutions-COG8, in AuthBuilder); every cell must have none about the chunks, and the
        // commercial cells none at all.
        const errors = s.errors
            .filter(
                (e) =>
                    template === "commercial" ||
                    /resourceNamesChunks|ResourceNameChunk/.test(`${e.path} ${e.message}`)
            )
            .map((e) => `${e.path}: ${e.message}`.slice(0, 200));
        expect(errors).toEqual([]);
        // The IAM5 wildcard on the chunk path is covered by the stack suppression with a reason.
        const unsuppressedChunkWildcards = s.errors.filter((e) =>
            /AwsSolutions-IAM5.*resourceNamesChunks/.test(e.message)
        );
        expect(unsuppressedChunkWildcards).toEqual([]);
        const suppressedChunkWildcards = s.infos.filter((e) =>
            /AwsSolutions-IAM5.*resourceNamesChunks/.test(e.message)
        );
        expect(suppressedChunkWildcards.length).toBeGreaterThan(0);
    });
});
