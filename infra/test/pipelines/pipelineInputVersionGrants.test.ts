/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every synthesized pipeline IAM statement that lets a role read asset-bucket objects also lets it
 * read them at a version.
 *
 * A pipeline reads its input file at the S3 version the workflow recorded at launch, and Amazon S3
 * authorizes a read that names a version as `s3:GetObjectVersion`, not `s3:GetObject`. A role granted
 * only `s3:GetObject` on an asset bucket passes every test that reads the current object and fails with
 * AccessDenied on the first run that names a version.
 *
 * Lambda roles get it from `grantReadPermissionsToAllAssetBuckets` (CDK `grantRead`, `s3:GetObject*`);
 * container, job and pod roles from explicit statements over `getS3AssetBucketRecords()`. This suite
 * checks what is deployed rather than how it was written: every Allow statement of every IAM policy and
 * inline role policy in the pipeline nested stacks, with every optional pipeline enabled, however the
 * statement was built. A statement names an asset bucket when a resource references the storage stack's
 * asset bucket ARN or an external asset bucket from the config, both of which the synth renders
 * recognizably. Durable (root CLAUDE.md Rule 13): a pipeline added later is in the synth too, and a
 * failure names the stack and the role's policy.
 *
 * Exempt: a statement whose every asset-bucket resource is the bucket's `pipelines/` working prefix
 * (the Video SOP/BOM job's scratch area), which no pipeline reads an input from.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

const ECR_IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/vams-smoke/thirdparty:latest";

/** Every optional pipeline and model on, as the all-pipelines synth cell runs. */
function enableEveryPipeline(c: any) {
    const enable = (node: any) => {
        if (Array.isArray(node)) return node.forEach(enable);
        if (!node || typeof node !== "object") return;
        if (typeof node.enabled === "boolean") node.enabled = true;
        if ("ecrContainerImageURI" in node) node.ecrContainerImageURI = ECR_IMAGE;
        // Images resolve from their CodeBuild ECR repositories: the local Dockerfiles some
        // pipelines build from are gitignored, so a fresh checkout has none.
        if (typeof node.useCodeBuild === "boolean") node.useCodeBuild = true;
        if ("acceptNvidiaEula" in node) node.acceptNvidiaEula = true;
        if ("huggingFaceToken" in node) node.huggingFaceToken = "hf_synth_only_placeholder";
        Object.values(node).forEach(enable);
    };
    for (const [key, value] of Object.entries(c.app.pipelines)) {
        if (key.startsWith("use")) enable(value);
    }
    c.app.useGlobalVpc.enabled = true;
}

const READ_ACTIONS = ["s3:GetObject", "s3:GetObject*", "s3:*", "*"];
const VERSION_ACTIONS = ["s3:GetObjectVersion", "s3:GetObject*", "s3:*", "*"];

/** The storage asset bucket's ARN as a nested-stack parameter, or a literal bucket ARN. */
const ASSET_BUCKET_REF = /Builder(AssetBucket[0-9A-F]{8})Arn|arn:[a-z-]+:s3:::[^"/*$]+/g;

interface Grant {
    stack: string;
    logicalId: string;
    actions: string[];
    resources: string[];
}

function statementsOf(template: any, stack: string): Grant[] {
    const out: Grant[] = [];
    for (const [logicalId, resource] of Object.entries<any>(template.Resources ?? {})) {
        const props = resource.Properties ?? {};
        const documents: any[] = [];
        if (resource.Type === "AWS::IAM::Policy" || resource.Type === "AWS::IAM::ManagedPolicy") {
            documents.push(props.PolicyDocument);
        } else if (resource.Type === "AWS::IAM::Role") {
            documents.push(...(props.Policies ?? []).map((p: any) => p.PolicyDocument));
        }
        for (const document of documents) {
            for (const statement of document?.Statement ?? []) {
                if (statement.Effect !== "Allow") continue;
                out.push({
                    stack,
                    logicalId,
                    actions: [].concat(statement.Action ?? []),
                    resources: []
                        .concat(statement.Resource ?? [])
                        .map((r) => SynthResult.flatten(r)),
                });
            }
        }
    }
    return out;
}

/** The resources of a grant that name an asset bucket. */
const assetBucketResources = (grant: Grant): string[] =>
    grant.resources.filter((r) => (r.match(ASSET_BUCKET_REF) ?? []).length > 0);

/** A resource scoped to the bucket's own `pipelines/` working prefix. */
const isWorkingPrefix = (resource: string): boolean => resource.endsWith("/pipelines/*");

describe("synthesized pipeline roles can read an asset-bucket object at a version", () => {
    let grants: Grant[];

    beforeAll(() => {
        const synth = synthTemplate("commercial", {
            mutate: enableEveryPipeline,
            mutateKey: "all-pipelines-input-version-grants",
        });
        grants = Object.entries(synth.templates)
            .filter(([stack]) => /pipeline/i.test(stack))
            .flatMap(([stack, template]) => statementsOf(template, stack));
    });

    const assetReads = (): Grant[] =>
        grants.filter(
            (g) =>
                assetBucketResources(g).length > 0 &&
                g.actions.some((a) => READ_ACTIONS.includes(a))
        );

    test("[control] the synth carries the pipeline roles that read asset buckets", () => {
        const roles = new Set(assetReads().map((g) => `${g.stack}/${g.logicalId}`));
        // The container, job and pod roles of every optional pipeline, and their Lambda roles.
        expect(roles.size).toBeGreaterThanOrEqual(40);
    });

    test("every asset-bucket read also grants the versioned read", () => {
        const missing = assetReads()
            .filter((g) => !g.actions.some((a) => VERSION_ACTIONS.includes(a)))
            .filter((g) => !assetBucketResources(g).every(isWorkingPrefix))
            .map((g) => `${g.stack}/${g.logicalId}: ${g.actions.join(",")}`);
        expect(missing).toEqual([]);
    });
});
