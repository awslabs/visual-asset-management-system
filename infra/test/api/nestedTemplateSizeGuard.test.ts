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
 * Why those cells. `useForAllLambdas` adds a VpcConfig block, a security group and a CDK Nag metadata
 * set to every function in every API stack, which makes it the largest-template configuration; a
 * customer managed key adds a KMS grant statement to every role; the Deadline Cloud execution type and
 * Download as Zip each add Lambdas to ApiBuilder2 (Download as Zip also needs the global VPC). The
 * "worst" cells stack all of them. GovCloud and EU Sovereign are measured on their shipped templates
 * and with the same worst-case additions, because partitions we cannot deploy to must not reach the
 * limit first.
 *
 * THE THRESHOLD, 920,000 bytes (8% below the limit). A Lambda with a VPC config and its security group
 * is 14,000 to 29,000 bytes in the API stacks, so 80,000 bytes is room for about four more functions,
 * or for CDK and cdk-nag raising the metadata they stamp on each resource, before the hard limit
 * stops a deploy. A template over the threshold fails here, in the pull request that crossed it, with
 * the table printed below; the remedy is to build the new functions in the next API stack
 * (ApiBuilder3NestedStack, `infra/CLAUDE.md` "API Stack Ceilings") rather than to raise the number.
 */
jest.setTimeout(900_000);

/** CloudFormation: maximum template body, in bytes. Hard limit, per template. Not adjustable. */
const CFN_HARD_LIMIT_BYTES = 1_000_000;

/** A template at or over this fails the guard. */
const SIZE_THRESHOLD_BYTES = 920_000;

interface Cell {
    name: string;
    template: TemplateName;
    mutate?: (c: any) => void;
}

/** Customer managed key, every Lambda in the global VPC (which AOSS then requires to be non-public). */
const KMS_AND_VPC_FOR_ALL_LAMBDAS = (c: any): void => {
    c.app.useKmsCmkEncryption.enabled = true;
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.useForAllLambdas = true;
    c.app.openSearch.useServerless.allowPublic = false;
};

/** The above with the two optional features that add Lambdas to ApiBuilder2. */
const WORST = (c: any): void => {
    KMS_AND_VPC_FOR_ALL_LAMBDAS(c);
    c.app.pipelines.useZipDownloads.enabled = true;
};

const CELLS: Cell[] = [
    { name: "commercial (as shipped)", template: "commercial" },
    {
        name: "commercial KMS + VPC for all Lambdas",
        template: "commercial",
        mutate: KMS_AND_VPC_FOR_ALL_LAMBDAS,
    },
    {
        name: "commercial worst (KMS, VPC all, zip)",
        template: "commercial",
        mutate: WORST,
    },
    {
        name: "commercial worst + Deadline Cloud",
        template: "commercial",
        mutate: (c) => {
            WORST(c);
            c.app.pipelines.deadlineCloudExecutionTypeEnabled = true;
        },
    },
    { name: "govcloud (as shipped)", template: "govcloud" },
    { name: "govcloud worst (KMS, VPC all, zip)", template: "govcloud", mutate: WORST },
    { name: "eusovereign (as shipped)", template: "eusovereign" },
    { name: "eusovereign worst (KMS, VPC all, zip)", template: "eusovereign", mutate: WORST },
];

const synthCell = (cell: Cell): SynthResult =>
    cell.mutate
        ? synthTemplate(cell.template, { mutate: cell.mutate, mutateKey: cell.name })
        : synthTemplate(cell.template);

/** Serialized size of one emitted template. CDK writes templates minified, so this is the S3 object. */
const templateBytes = (s: SynthResult, key: string): number =>
    JSON.stringify(s.templates[key]).length;

/** The construct id a nested template belongs to: the key minus `.nested` and the 8-hex unique id. */
function label(key: string): string {
    if (!key.endsWith(".nested")) return "(core stack)";
    return key
        .replace(/\.nested$/, "")
        .replace(/[0-9A-F]{8}$/, "")
        .replace(/^vamst1(commercial|govcloud|eusovereign)/, "");
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
            `limit (guard threshold ${SIZE_THRESHOLD_BYTES.toLocaleString()}):\n` +
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
            `  ${cell.name}\n` +
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

        const over = rows.filter((r) => r.bytes > SIZE_THRESHOLD_BYTES);
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
        const rootKey = Object.keys(result.templates).find((k) => /^vams-t1-[a-z]+$/.test(k))!;
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
