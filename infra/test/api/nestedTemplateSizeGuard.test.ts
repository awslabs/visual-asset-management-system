/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { synthTemplate, SynthResult, TemplateName } from "../support/templateSynth";
import { nestedTemplateKeyFor } from "../support/iamPolicySize";

/**
 * Size guard for every CloudFormation template the app emits, in the cells that make them largest.
 *
 * CloudFormation rejects a template body over 1,000,000 bytes when it creates the change set:
 * "Template may not exceed 1000000 bytes in size". Nothing is created and an upgrade of an existing
 * stack is refused the same way. The jest synth harness synthesizes fine at any size, so before this
 * guard the failure surfaced only on a real deploy (#1535: with `useGlobalVpc.useForAllLambdas` on, the
 * ApiBuilder2 nested stack was 1,009,044 bytes while development, at 986,768, had passed with 13,232
 * bytes to spare).
 *
 * WHAT IS MEASURED. The template CloudFormation receives from `cdk synth`, which differs from a bare
 * `app.synth()` in two ways that move the size by about 5% each, so both are reproduced here:
 *
 *  - the CLI turns on `aws:cdk:enable-path-metadata`, a `Metadata.aws:cdk:path` entry on every resource
 *    (about +19,000 bytes in ApiBuilder2);
 *  - the stack name is spelled into the ARNs, SSM paths and resource names of every Lambda role, so
 *    template size grows with `app.baseStackName`: about 2,700 bytes per extra character in ApiBuilder2
 *    in the worst cell. The harness's own name is shorter than a real one, so each cell here uses the
 *    reference name `vams-core-demo26-us-east-1` (26 characters, the shape of the sandbox stacks and
 *    of the documented default `vams-core-prod-us-east-1`). Measured this way the worst cell is within
 *    1% of the template `cdk synth` writes for the same configuration (884,145 against 889,255 bytes).
 *
 * WHICH CELLS. `useForAllLambdas` adds a VpcConfig block and a security group to every function in every
 * API stack, which makes it the largest-template configuration; a customer managed key adds a KMS grant
 * statement to every role; Download as Zip and the Deadline Cloud execution type each add Lambdas to
 * ApiBuilder2 (Download as Zip also needs the global VPC). The "worst" cells stack them. GovCloud and
 * EU Sovereign are measured on their shipped templates and with the same additions, because the
 * partitions we cannot deploy to must not reach the limit first.
 *
 * THE THRESHOLDS. A function with its VpcConfig and security group is 14,000 to 29,000 bytes in the API
 * stacks, so the headroom below the limit is room for that many more functions, or for CDK and cdk-nag
 * raising the metadata they stamp on each resource, before a deploy is refused.
 *
 *  - STANDARD, 920,000 bytes (8% below the limit): the shipped templates and the cells a customer is
 *    likely to run (customer managed key, every Lambda in the VPC, Download as Zip).
 *  - STACKED, 960,000 bytes (4% below): the cells that stack every optional feature that adds Lambdas to
 *    ApiBuilder2 (adds Deadline Cloud), and a 20-character base name. They are real configurations but
 *    rarer, so they get the smaller margin; a template over it is the next deploy failure.
 *
 * A template over its threshold fails here, in the pull request that crossed it, with the table printed
 * below. The remedy is to build the new functions in a new nested stack (ApiBuilder3NestedStack has
 * room, `infra/CLAUDE.md` "API Stack Ceilings"), not to raise the number.
 */
jest.setTimeout(900_000);

/** CloudFormation: maximum template body, in bytes. Hard limit, per template. Not adjustable. */
const CFN_HARD_LIMIT_BYTES = 1_000_000;

const STANDARD_THRESHOLD_BYTES = 920_000;
const STACKED_THRESHOLD_BYTES = 960_000;

interface Cell {
    name: string;
    template: TemplateName;
    threshold: number;
    /** Characters of app.baseStackName before the Region suffix. */
    baseName?: string;
    mutate?: (c: any) => void;
}

/** The reference base name; with `-<region>` appended this gives `vams-core-demo26-us-east-1`. */
const REFERENCE_BASE_NAME = "demo26";

/**
 * Name the stack the way `getConfig()` does: the Region is appended to `app.baseStackName`, the core
 * stack is `vams-core-<base>-<region>`, and the SSM parameter prefixes are `/vams-<base>-<region>/...`.
 */
function applyStackName(c: any, baseName: string): void {
    const full = `${baseName}-${c.env.region}`;
    c.app.baseStackName = full;
    c.env.coreStackName = `vams-core-${full}`;
    const prefix = `/vams-${full}`;
    c.resourceNamesSSMParamPrefix = `${prefix}/resourceNames`;
    c.openSearchDomainEndpointSSMParam = `${prefix}/aos/endPoint`;
    c.locationServiceApiKeyArnSSMParam = `${prefix}/location/apiKeyArn`;
    c.webUrlDeploymentSSMParam = `${prefix}/web/deployedUrl`;
}

/** Customer managed key, every Lambda in the global VPC (which AOSS then requires to be non-public). */
const KMS_AND_VPC_FOR_ALL_LAMBDAS = (c: any): void => {
    c.app.useKmsCmkEncryption.enabled = true;
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.useForAllLambdas = true;
    c.app.openSearch.useServerless.allowPublic = false;
};

/** The above with Download as Zip, which adds its function and routes to ApiBuilder2. */
const WORST = (c: any): void => {
    KMS_AND_VPC_FOR_ALL_LAMBDAS(c);
    c.app.pipelines.useZipDownloads.enabled = true;
};

const CELLS: Cell[] = [
    {
        name: "commercial (as shipped)",
        template: "commercial",
        threshold: STANDARD_THRESHOLD_BYTES,
    },
    {
        name: "commercial KMS + VPC for all Lambdas",
        template: "commercial",
        threshold: STANDARD_THRESHOLD_BYTES,
        mutate: KMS_AND_VPC_FOR_ALL_LAMBDAS,
    },
    {
        name: "commercial worst (KMS, VPC all, zip)",
        template: "commercial",
        threshold: STANDARD_THRESHOLD_BYTES,
        mutate: WORST,
    },
    {
        name: "commercial worst + Deadline Cloud",
        template: "commercial",
        threshold: STACKED_THRESHOLD_BYTES,
        mutate: (c) => {
            WORST(c);
            c.app.pipelines.deadlineCloudExecutionTypeEnabled = true;
        },
    },
    {
        name: "commercial worst, 20-character base name",
        template: "commercial",
        threshold: STACKED_THRESHOLD_BYTES,
        baseName: "abcdefghijklmnopqrst",
        mutate: WORST,
    },
    { name: "govcloud (as shipped)", template: "govcloud", threshold: STANDARD_THRESHOLD_BYTES },
    {
        name: "govcloud worst (KMS, VPC all, zip)",
        template: "govcloud",
        threshold: STANDARD_THRESHOLD_BYTES,
        mutate: WORST,
    },
    {
        name: "eusovereign (as shipped)",
        template: "eusovereign",
        threshold: STANDARD_THRESHOLD_BYTES,
    },
    {
        name: "eusovereign worst (KMS, VPC all, zip)",
        template: "eusovereign",
        threshold: STANDARD_THRESHOLD_BYTES,
        mutate: WORST,
    },
];

const CLI_CONTEXT = { "aws:cdk:enable-path-metadata": true };

/** Each cell is synthesized with the reference (or its own) stack name and the CLI's path metadata. */
const synthCell = (cell: Cell): SynthResult =>
    synthTemplate(cell.template, {
        mutate: (c) => {
            applyStackName(c, cell.baseName ?? REFERENCE_BASE_NAME);
            cell.mutate?.(c);
        },
        mutateKey: `${cell.name}::${cell.baseName ?? REFERENCE_BASE_NAME}`,
        context: CLI_CONTEXT,
    });

/** Serialized size of one emitted template. CDK writes templates minified, so this is the S3 object. */
const templateBytes = (s: SynthResult, key: string): number =>
    JSON.stringify(s.templates[key]).length;

/** The construct id a nested template belongs to: the key minus the stack name, `.nested` and the 8-hex id. */
function label(key: string): string {
    if (!key.endsWith(".nested")) return "(core stack)";
    return key
        .replace(/\.nested$/, "")
        .replace(/[0-9A-F]{8}$/, "")
        .replace(/^vamscore[a-z0-9]*?(?=[A-Z])/, "");
}

interface SizeRow {
    key: string;
    label: string;
    bytes: number;
}

function sizesOf(s: SynthResult): SizeRow[] {
    return Object.keys(s.templates)
        .map((key) => ({ key, label: label(key), bytes: templateBytes(s, key) }))
        .sort((a, b) => b.bytes - a.bytes);
}

const pct = (bytes: number): string => `${((bytes / CFN_HARD_LIMIT_BYTES) * 100).toFixed(1)}%`;

const table: string[] = [];

afterAll(() => {
    // One table for the whole file, printed once, so a failing run names the cell and the template.
    // eslint-disable-next-line no-console
    console.log(
        `Template body sizes against the ${CFN_HARD_LIMIT_BYTES.toLocaleString()} byte CloudFormation ` +
            `limit (thresholds ${STANDARD_THRESHOLD_BYTES.toLocaleString()} standard, ${STACKED_THRESHOLD_BYTES.toLocaleString()} stacked):\n` +
            table.join("\n")
    );
});

describe("template size guard: no template nears the CloudFormation body limit", () => {
    test.each(CELLS)("$name", (cell) => {
        const s = synthCell(cell);
        const rows = sizesOf(s);

        // Control: a harness that emitted nothing, or only the root, would pass every bound below.
        expect(rows.length).toBeGreaterThan(8);
        expect(rows.filter((r) => r.key.endsWith(".nested")).length).toBeGreaterThan(8);
        expect(rows[0].bytes).toBeGreaterThan(100_000);

        table.push(
            `  ${cell.name} (limit ${cell.threshold.toLocaleString()})\n` +
                rows
                    .slice(0, 5)
                    .map(
                        (r) =>
                            `    ${String(r.bytes).padStart(9)}  ${pct(r.bytes).padStart(6)}  ${
                                r.label
                            }`
                    )
                    .join("\n")
        );

        const over = rows.filter((r) => r.bytes > cell.threshold);
        expect(over.map((r) => `${r.label}: ${r.bytes} bytes (${pct(r.bytes)})`)).toEqual([]);
        // Stated separately so the message for a template over the HARD limit is unmistakable.
        expect(rows.filter((r) => r.bytes >= CFN_HARD_LIMIT_BYTES)).toEqual([]);
    });
});

describe("the third API stack carries the audit search service", () => {
    const s = (): SynthResult => synthCell(CELLS[0]);

    test("the audit log service Lambda is in ApiBuilder3 and not in ApiBuilder2", () => {
        const result = s();
        const stack3 = nestedTemplateKeyFor(result, "ApiBuilder3");
        const stack2 = nestedTemplateKeyFor(result, "ApiBuilder2");
        const handlerIn = (key: string): number =>
            Object.values(result.templates[key].Resources as Record<string, any>).filter(
                (r: any) =>
                    r.Type === "AWS::Lambda::Function" &&
                    r.Properties?.Handler === "handlers.audit.auditLogService.lambda_handler"
            ).length;
        expect(handlerIn(stack3)).toBe(1);
        expect(handlerIn(stack2)).toBe(0);
    });

    test("the audit query table stays in the storage stack", () => {
        const result = s();
        const tablesIn = (key: string): string[] =>
            Object.entries(result.templates[key].Resources as Record<string, any>)
                .filter(([, r]: [string, any]) => r.Type === "AWS::DynamoDB::Table")
                .map(([id]) => id);
        expect(
            tablesIn(nestedTemplateKeyFor(result, "StorageResourcesBuilder")).some((id) =>
                id.startsWith("AuditLogQueriesStorageTable")
            )
        ).toBe(true);
        expect(tablesIn(nestedTemplateKeyFor(result, "ApiBuilder3"))).toEqual([]);
        expect(tablesIn(nestedTemplateKeyFor(result, "ApiBuilder2"))).toEqual([]);
    });

    test("the root orders ApiBuilder3 after storage and resource names, and the REST API after it", () => {
        const result = s();
        const rootKey = Object.keys(result.templates).find((k) => /^vams-core-/.test(k))!;
        const stacks = Object.entries(result.templates[rootKey].Resources as Record<string, any>)
            .filter(([, r]: [string, any]) => r.Type === "AWS::CloudFormation::Stack")
            .map(([id, r]: [string, any]) => ({
                id,
                dependsOn: [r.DependsOn ?? []].flat() as string[],
            }));
        const byConstruct = (construct: string) =>
            stacks.find((st) => st.id.startsWith(`${construct}NestedStack`))!;
        const api3 = byConstruct("ApiBuilder3");
        const names = (construct: string) => byConstruct(construct).id;
        expect(api3.dependsOn).toEqual(
            expect.arrayContaining([
                names("StorageResourcesBuilder"),
                names("ResourceNamesBuilder"),
            ])
        );
        // Rule 9: the stack that renders the routes is created after every stack that registered one.
        expect(byConstruct("RestApi").dependsOn).toContain(api3.id);
    });

    test("the five audit routes are in the REST API's OpenAPI document", () => {
        const result = s();
        const api = result.ofType("AWS::ApiGateway::RestApi")[0];
        const paths = Object.keys(api.properties.Body?.paths ?? {});
        for (const p of [
            "/audit/log-groups",
            "/audit/queries",
            "/audit/queries/{queryId}",
            "/audit/queries/{queryId}/export",
        ]) {
            expect(paths).toContain(p);
        }
    });
});
