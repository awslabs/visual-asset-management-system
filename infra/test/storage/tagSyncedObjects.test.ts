/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `tagSyncedObjects`: per bucket registration, whether bucket sync writes the VAMS:databaseId and
 * VAMS:assetId object tags on the file versions it syncs. Default true for the VAMS-created bucket
 * (`app.assetBuckets.tagSyncedObjects`) and for each external entry; `getConfig()` rejects a value that
 * is not a boolean; the deploy custom resource writes it onto every registration row.
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as fs from "fs";
import * as path from "path";
import { validateTagSyncedObjects } from "../../config/config";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { createPopulateS3AssetBucketsTableCustomResource } from "../../lib/nestedStacks/storage/customResources/populateS3AssetBucketsTable";
import { s3AssetBucketRecords, addS3AssetBucket } from "../../lib/helper/s3AssetBuckets";
import { newTestApp } from "../support/testApp";
import { actionsOf, resourcesOf, statementsOf, synthTemplate } from "../support/templateSynth";

jest.setTimeout(600_000);

const EXTERNAL = {
    bucketArn: "arn:aws:s3:::customer-bucket",
    baseAssetsPrefix: "data/",
    defaultSyncDatabaseId: "imports",
};

describe("getConfig() validation of tagSyncedObjects", () => {
    test("booleans and an unset value are accepted", () => {
        expect(() => validateTagSyncedObjects({})).not.toThrow();
        expect(() =>
            validateTagSyncedObjects({
                tagSyncedObjects: false,
                externalAssetBuckets: [{ ...EXTERNAL, tagSyncedObjects: true }, EXTERNAL],
            })
        ).not.toThrow();
    });

    test("a string on the created bucket or an external entry is refused, naming the key", () => {
        expect(() => validateTagSyncedObjects({ tagSyncedObjects: "false" as any })).toThrow(
            /app\.assetBuckets\.tagSyncedObjects must be a boolean/
        );
        expect(() =>
            validateTagSyncedObjects({
                externalAssetBuckets: [EXTERNAL, { ...EXTERNAL, tagSyncedObjects: "no" as any }],
            })
        ).toThrow(
            /app\.assetBuckets\.externalAssetBuckets\[1\]\.tagSyncedObjects must be a boolean/
        );
    });
});

describe("ConfigBuilder mirrors the validation", () => {
    const ruleIds = (mutate: (c: any) => void): string[] => {
        const cfg: any = makeDefaultConfig("commercial");
        mutate(cfg);
        return evaluateRules(cfg).map((issue: any) => issue.id);
    };

    test("the default and booleans raise no rule", () => {
        expect(ruleIds(() => undefined)).not.toContain("tag-synced-objects-not-boolean");
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.tagSyncedObjects = false;
            })
        ).not.toContain("tag-synced-objects-not-boolean");
    });

    test("a string on the created bucket or an external entry is refused", () => {
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.tagSyncedObjects = "false";
            })
        ).toContain("tag-synced-objects-not-boolean");
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.externalAssetBuckets = [{ ...EXTERNAL, tagSyncedObjects: "no" }];
            })
        ).toContain("tag-synced-objects-not-boolean");
    });
});

describe("shipped configuration carries the default", () => {
    const configDir = path.join(__dirname, "../../config");
    test.each([
        "config.json",
        "config.template.commercial.json",
        "config.template.govcloud.json",
        "config.template.eusovereign.json",
    ])("%s sets app.assetBuckets.tagSyncedObjects to true", (file) => {
        const config = JSON.parse(fs.readFileSync(path.join(configDir, file), "utf8"));
        expect(config.app.assetBuckets.tagSyncedObjects).toBe(true);
    });

    test("ConfigBuilder's defaults set it to true", () => {
        const defaults = fs.readFileSync(
            path.join(
                __dirname,
                "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults.ts"
            ),
            "utf8"
        );
        expect(defaults).toMatch(/useS3TransferAcceleration: false,\s+tagSyncedObjects: true,/);
    });
});

describe("bucket sync may read and write object tags on every asset bucket (synth)", () => {
    const EXTERNAL_ARN = "arn:aws:s3:::vams-tag-external";
    const TAGGING = [
        "s3:GetObjectTagging",
        "s3:GetObjectVersionTagging",
        "s3:PutObjectTagging",
        "s3:PutObjectVersionTagging",
    ];
    const synth = () =>
        synthTemplate("commercial", {
            mutate: (c: any) => {
                c.app.assetBuckets.externalAssetBuckets = [
                    {
                        bucketArn: EXTERNAL_ARN,
                        baseAssetsPrefix: "data/",
                        defaultSyncDatabaseId: "ext",
                        tagSyncedObjects: false,
                    },
                ];
            },
            mutateKey: "tag-synced-external",
        });

    /** Whether an IAM action pattern (`s3:GetObject*`) covers an action. */
    const covers = (pattern: string, action: string) =>
        new RegExp(`^${pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*")}$`).test(
            action
        );

    const objectStatements = (fnStatements: any[], bucketMatch: RegExp) =>
        fnStatements.filter(
            (st) =>
                st.Effect === "Allow" &&
                resourcesOf(st).some((r) => bucketMatch.test(r) && r.endsWith("/*"))
        );

    test.each([
        ["the created asset bucket", /AssetBucket/],
        ["an external asset bucket, tagging off in its registration", /vams-tag-external/],
    ])("each bucket-sync function holds the four tagging actions on %s", (_label, bucketMatch) => {
        const s = synth();
        const fns = s.where("AWS::Lambda::Function", (r) =>
            /handlers\.indexing\.sqsBucketSync\.lambda_handler_created/.test(
                String(r.properties.Handler)
            )
        );
        expect(fns.length).toBeGreaterThan(0);
        for (const fn of fns) {
            const actions = objectStatements(statementsOf(s, fn), bucketMatch).flatMap(actionsOf);
            for (const action of TAGGING) {
                expect(actions.some((pattern) => covers(pattern, action))).toBe(true);
            }
            // No statement on the bucket grants every S3 action.
            expect(actions).not.toContain("s3:*");
        }
    });
});

describe("the deploy custom resource writes tagSyncedObjects on each registration", () => {
    let template: Template;
    let inlineSource: string;

    beforeAll(() => {
        s3AssetBucketRecords.length = 0;
        const app = newTestApp();
        const stack = new cdk.Stack(app, "TestStack", {
            env: { account: "111111111111", region: "us-east-1" },
        });
        const table = new dynamodb.Table(stack, "S3AssetBucketsStorageTable", {
            partitionKey: { name: "bucketId", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "bucketName:baseAssetsPrefix", type: dynamodb.AttributeType.STRING },
        });
        const bucket = (id: string, name: string) =>
            s3.Bucket.fromBucketArn(stack, id, `arn:aws:s3:::${name}`);
        // The created bucket with the default, an external bucket with tagging off, and a record
        // added without the argument.
        addS3AssetBucket(
            bucket("A", "vams-a"),
            "/",
            "default",
            undefined,
            undefined,
            true,
            undefined,
            false,
            true
        );
        addS3AssetBucket(
            bucket("B", "vams-b"),
            "data/",
            "imports",
            undefined,
            undefined,
            false,
            undefined,
            false,
            false
        );
        addS3AssetBucket(bucket("C", "vams-c"), "/", "other");
        createPopulateS3AssetBucketsTableCustomResource(
            stack,
            "PopulateS3AssetBucketsTable",
            table
        );
        template = Template.fromStack(stack);
        const sources = Object.values(template.findResources("AWS::Lambda::Function"))
            .map((fn: any) => fn.Properties?.Code?.ZipFile)
            .filter(
                (code: unknown): code is string =>
                    typeof code === "string" && code.includes("bucketName:baseAssetsPrefix")
            );
        expect(sources).toHaveLength(1);
        inlineSource = sources[0];
    });

    test("each record's value reaches the custom resource properties", () => {
        const resources = Object.values(
            template.findResources("AWS::CloudFormation::CustomResource")
        );
        expect(resources).toHaveLength(1);
        const buckets = JSON.parse((resources[0] as any).Properties.buckets);
        expect(buckets.map((b: any) => [b.bucketName, b.tagSyncedObjects])).toEqual([
            ["vams-a", true],
            ["vams-b", false],
            ["vams-c", true],
        ]);
    });

    test("the populator writes the field on every row, true unless the record says false", () => {
        expect(inlineSource).toContain(
            "item['tagSyncedObjects'] = bucket.get('tagSyncedObjects') is not False"
        );
    });
});
