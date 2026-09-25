/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The resource-name counts the documentation and steering state, compared with the registry they
 * describe.
 *
 * `infra/CLAUDE.md`, its Kiro mirror, `architecture/aws-resources.md`, `architecture/details.md`, and
 * `additional/quotas.md` all quote how many tables VAMS deploys and how many SSM parameters are
 * published. Those numbers moved silently once per release until now; each is derived here from
 * `RESOURCE_PARAM_KEYS`, so adding a table without touching the prose fails in the test that names
 * the sentence.
 *
 * "Published by the registry" excludes the `lambdaFunctions` category: the search stack publishes
 * those parameters itself, each only when its feature is enabled, so they are neither in the
 * ResourceNamesBuilder count nor guaranteed to exist. details.md is the one page that also states
 * the grand total with those names included.
 */

import * as fs from "fs";
import * as path from "path";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import { documentedFigure, documentedFigures } from "../support/docFigures";

const REPO_ROOT = path.join(__dirname, "..", "..", "..");
const read = (rel: string): string => fs.readFileSync(path.join(REPO_ROOT, rel), "utf8");

const size = (category: object): number => Object.keys(category).length;
const DYNAMO = size(RESOURCE_PARAM_KEYS.dynamoTables);
const LEGACY = size(RESOURCE_PARAM_KEYS.dynamoTablesLegacy);
const S3 = size(RESOURCE_PARAM_KEYS.s3Buckets);
const LOGS = size(RESOURCE_PARAM_KEYS.cloudwatchLogGroups);
const LAMBDAS = size(RESOURCE_PARAM_KEYS.lambdaFunctions);
const PUBLISHED_BY_REGISTRY = DYNAMO + LEGACY + S3 + LOGS;

const AWS_RESOURCES = "documentation/docusaurus-site/docs/architecture/aws-resources.md";
const DETAILS = "documentation/docusaurus-site/docs/architecture/details.md";
const QUOTAS = "documentation/docusaurus-site/docs/additional/quotas.md";
const INFRA_CLAUDE = "infra/CLAUDE.md";
const KIRO_CDK = ".kiro/steering/CDK_DEVELOPMENT_WORKFLOW.md";

describe("the registry the doc counts are compared against is populated", () => {
    test("every category has entries", () => {
        // Control: a registry read that returned empty categories would make every comparison
        // below a comparison against zero.
        expect(DYNAMO).toBeGreaterThan(40);
        expect(LEGACY).toBeGreaterThan(0);
        expect(S3).toBeGreaterThan(0);
        expect(LOGS).toBeGreaterThan(0);
        expect(LAMBDAS).toBeGreaterThan(0);
    });
});

describe("architecture/aws-resources.md states the registry's counts", () => {
    const md = read(AWS_RESOURCES);

    test("DynamoDB intro: total, handler-read, and migration-source counts", () => {
        const m =
            /VAMS deploys (\d+) Amazon DynamoDB tables[^\n]*?(\d+) read by Lambda handlers and (\d+) migration source tables/.exec(
                md
            );
        expect(m).not.toBeNull();
        expect([Number(m![1]), Number(m![2]), Number(m![3])]).toEqual([
            DYNAMO + LEGACY,
            DYNAMO,
            LEGACY,
        ]);
    });

    test.each([
        ["dynamoTables/*", DYNAMO],
        ["dynamoTables/legacy/*", LEGACY],
        ["s3Buckets/*", S3],
        ["cloudwatchLogGroups/*", LOGS],
        ["lambdaFunctions/*", LAMBDAS],
    ])("SSM Parameter Store table row %s", (group, expected) => {
        const escaped = group.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
        const row = new RegExp("resourceNames/" + escaped + "`\\s*\\|\\s*(\\d+)\\s*\\|");
        expect(documentedFigure(md, row, AWS_RESOURCES)).toBe(expected);
    });
});

describe("architecture/details.md states the resource-name parameter counts", () => {
    const md = read(DETAILS);

    test("Resource Name Resolution: grand total, tables, Lambda names, registry-materialized", () => {
        // The one page that counts the search-stack-published lambdaFunctions/* names in its total.
        expect(
            documentedFigures(
                md,
                /— (\d+) in the shipped configuration \((\d+) DynamoDB tables/,
                DETAILS
            )
        ).toEqual([PUBLISHED_BY_REGISTRY + LAMBDAS, DYNAMO + LEGACY]);
        expect(documentedFigure(md, /(\d+) Lambda function names?\)/, DETAILS)).toBe(LAMBDAS);
        expect(documentedFigure(md, /materializes (\d+) of them/, DETAILS)).toBe(
            PUBLISHED_BY_REGISTRY
        );
    });
});

describe("additional/quotas.md states the table counts", () => {
    test("DynamoDB 'Table count' row: handler-read tables plus tables retained for migration", () => {
        expect(
            documentedFigures(
                read(QUOTAS),
                /\| Table count\s*\|\s*(\d+) tables \(plus (\d+) retained for migration\)/,
                QUOTAS
            )
        ).toEqual([DYNAMO, LEGACY]);
    });
});

describe("infra/CLAUDE.md and its Kiro mirror state the registry's counts", () => {
    const claude = read(INFRA_CLAUDE);
    const kiro = read(KIRO_CDK);

    test("infra/CLAUDE.md", () => {
        expect(
            documentedFigure(
                claude,
                /# (\d+) SSM String parameters, one per registry descriptor/,
                INFRA_CLAUDE
            )
        ).toBe(PUBLISHED_BY_REGISTRY);
        expect(
            documentedFigure(
                claude,
                /ResourceNamesBuilder \(publishes (\d+) SSM parameters\)/,
                INFRA_CLAUDE
            )
        ).toBe(PUBLISHED_BY_REGISTRY);
        expect(documentedFigure(claude, /`dynamo\.\*` — (\d+) DynamoDB tables/, INFRA_CLAUDE)).toBe(
            DYNAMO
        );
        expect(
            documentedFigure(
                claude,
                /`ResourceNamesBuilder` consumes (\d+) as its own/,
                INFRA_CLAUDE
            )
        ).toBe(PUBLISHED_BY_REGISTRY);
        expect(
            documentedFigure(
                claude,
                /\*\*SSM String parameters\*\* \((\d+) resource-name parameters published by ResourceNamesBuilder/,
                INFRA_CLAUDE
            )
        ).toBe(PUBLISHED_BY_REGISTRY);
    });

    test(".kiro/steering/CDK_DEVELOPMENT_WORKFLOW.md", () => {
        expect(
            documentedFigure(
                kiro,
                /ResourceNamesBuilder \(publishes (\d+) SSM resource-name parameters\)/,
                KIRO_CDK
            )
        ).toBe(PUBLISHED_BY_REGISTRY);
        expect(
            documentedFigure(kiro, /\/\/ (\d+) DynamoDB tables -- see the interface/, KIRO_CDK)
        ).toBe(DYNAMO);
    });
});
