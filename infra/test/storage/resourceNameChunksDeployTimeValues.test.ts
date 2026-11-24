/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The deploy-time values in the resource-name chunk parameters, across the configuration matrix.
 *
 * The OpenSearch endpoint and index names, the web URL, the Amazon Location Service map API key ARN
 * and the large object copy engine's function name are published by the stacks that create them
 * (lateCoreResourceNames.ts) and packed with the core names. Per cell this asserts: each value is
 * carried exactly when its feature is deployed, with the same value as the parameter (or custom
 * resource input) that holds it today; the chunk count and layout; the DependsOn every chunk shares
 * (storage plus each stack a value is read from); a root template with no cycle, no nested stack
 * waiting for a chunk, no new parameter and within the quotas; the per-key copies ordered after the
 * resource their value must not be read before; the copy engine's parameter kept as it was; and no
 * new IAM grant for any of it.
 */

import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import { enableEdh } from "../addon/edh/support/edhSynth";
import {
    actionsOf,
    handlerOf,
    lambdaByHandler,
    resourcesOf,
    statementsOf,
    SynthResult,
    synthTemplate,
    TemplateName,
} from "../support/templateSynth";
import {
    chunksOf,
    dependencyCycleIn,
    dependsOnOf,
    expectChunksInRootStack,
    expectRootTemplateWithinLimits,
    prefixOf,
    rawChunkValues,
    rootStackKey,
    stacksReadByChunks,
    valueTargetOf,
} from "./support/resourceNameChunkSynth";

jest.setTimeout(2_400_000);

const K = RESOURCE_PARAM_KEYS;
const ENGINE = K.lambdaFunctions.largeObjectCopyEngine;
const OPENSEARCH = [K.openSearch.endpoint, K.openSearch.assetIndexName, K.openSearch.fileIndexName];
const WEB = K.web.deployedUrl;
const LOCATION = K.location.apiKeyArn;
const LATE_KEYS = [ENGINE, ...OPENSEARCH, WEB, LOCATION];

function allAddonsOn(c: any): void {
    enableEdh(c);
    c.app.addons.usePhysnaSync.enabled = true;
    c.app.addons.usePhysnaSync.tenantId = "11111111-2222-3333-4444-555555555555";
    c.app.addons.useGarnetFramework.enabled = true;
    c.app.addons.useGarnetFramework.garnetApiEndpoint = "https://garnet.example.com";
    c.app.addons.useGarnetFramework.garnetApiToken = "placeholder";
    c.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl =
        "https://sqs.us-east-1.amazonaws.com/123456789012/garnet-ingest";
}

/** An API-only deployment, which getConfig() allows with a warning: no static web stack is built. */
function apiOnly(c: any): void {
    c.app.useCloudFront.enabled = false;
    c.app.useAlb.enabled = false;
}

function alb(c: any): void {
    c.app.useGlobalVpc.enabled = true;
    c.app.useCloudFront.enabled = false;
    c.app.useAlb.enabled = true;
    c.app.useAlb.domainHost = "vams-t1-alb.example.com";
    c.app.useAlb.certificateArn =
        "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    c.app.useAlb.optionalHostedZoneId = "";
}

interface Cell {
    label: string;
    template: TemplateName;
    key?: string;
    mutate?: (c: any) => void;
    /** The late core keys this cell publishes, in publication order. */
    late: string[];
    /** The nested stacks (construct ids) every chunk waits for. */
    readFrom: string[];
    /** Names per chunk, recorded. */
    layout: number[];
}

const everyLateKey = LATE_KEYS;
const DEFAULT_READ = [
    "ApiBuilder2",
    "LocationService",
    "SearchBuilder",
    "StaticWeb",
    "StorageResourcesBuilder",
];

const CELLS: Cell[] = [
    {
        label: "commercial default (OpenSearch Serverless, Location, CloudFront domain)",
        template: "commercial",
        late: everyLateKey,
        readFrom: DEFAULT_READ,
        layout: [13, 13, 13, 13, 10, 9, 11],
    },
    {
        label: "minimal: CloudFront, no VPC, no CMK, no WAF, OpenSearch off",
        template: "commercial",
        key: "dtv-minimal",
        mutate: (c) => {
            c.app.useCloudFront.enabled = true;
            c.app.useGlobalVpc.enabled = false;
            c.app.useKmsCmkEncryption.enabled = false;
            c.app.useWaf = false;
            c.app.openSearch.useServerless.enabled = false;
            c.app.openSearch.useProvisioned.enabled = false;
        },
        late: [ENGINE, WEB, LOCATION],
        readFrom: ["ApiBuilder2", "LocationService", "StaticWeb", "StorageResourcesBuilder"],
        layout: [13, 13, 13, 13, 10, 9, 8],
    },
    {
        label: "hardened: KMS CMK, global VPC for all Lambdas with endpoints, WAF",
        template: "commercial",
        key: "dtv-hardened",
        mutate: (c) => {
            c.app.useKmsCmkEncryption.enabled = true;
            c.app.useGlobalVpc.enabled = true;
            c.app.useGlobalVpc.useForAllLambdas = true;
            c.app.useGlobalVpc.addVpcEndpoints = true;
            c.app.useWaf = true;
        },
        late: everyLateKey,
        readFrom: DEFAULT_READ,
        layout: [13, 13, 13, 13, 10, 9, 11],
    },
    {
        label: "OpenSearch provisioned",
        template: "commercial",
        key: "dtv-provisioned",
        mutate: (c) => {
            c.app.openSearch.useServerless.enabled = false;
            c.app.openSearch.useProvisioned.enabled = true;
            c.app.useGlobalVpc.enabled = true;
        },
        late: everyLateKey,
        readFrom: DEFAULT_READ,
        layout: [13, 13, 13, 13, 10, 9, 11],
    },
    {
        label: "OpenSearch Serverless private NEXTGEN, deferred index schema",
        template: "commercial",
        key: "dtv-private-nextgen-deferred",
        mutate: (c) => {
            c.app.useGlobalVpc.enabled = true;
            c.app.useGlobalVpc.addVpcEndpoints = false;
            c.app.openSearch.useServerless.allowPublic = false;
            c.app.openSearch.useServerless.nextGen = true;
            c.app.openSearch.useServerless.deployDeferredIndexSchema = false;
        },
        late: everyLateKey,
        readFrom: DEFAULT_READ,
        layout: [13, 13, 13, 13, 10, 9, 11],
    },
    {
        label: "Location Service off",
        template: "commercial",
        key: "dtv-location-off",
        mutate: (c) => {
            c.app.useLocationService.enabled = false;
        },
        late: [ENGINE, ...OPENSEARCH, WEB],
        readFrom: ["ApiBuilder2", "SearchBuilder", "StaticWeb", "StorageResourcesBuilder"],
        layout: [13, 13, 13, 13, 10, 9, 10],
    },
    {
        label: "CloudFront custom domain (a literal web URL)",
        template: "commercial",
        key: "dtv-cloudfront-custom-domain",
        mutate: (c) => {
            c.app.useCloudFront.customDomain.enabled = true;
            c.app.useCloudFront.customDomain.domainHost = "vams-t1-web.example.com";
            c.app.useCloudFront.customDomain.certificateArn =
                "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
            c.app.useCloudFront.customDomain.optionalHostedZoneId = "";
        },
        late: everyLateKey,
        readFrom: ["ApiBuilder2", "LocationService", "SearchBuilder", "StorageResourcesBuilder"],
        layout: [13, 13, 13, 13, 10, 9, 11],
    },
    {
        label: "ALB with a configured domain (a literal web URL)",
        template: "commercial",
        key: "dtv-alb",
        mutate: alb,
        late: everyLateKey,
        readFrom: ["ApiBuilder2", "LocationService", "SearchBuilder", "StorageResourcesBuilder"],
        layout: [13, 13, 13, 13, 10, 9, 11],
    },
    {
        label: "API only: no web distribution (neither CloudFront nor an ALB)",
        template: "commercial",
        key: "dtv-api-only",
        mutate: apiOnly,
        late: [ENGINE, ...OPENSEARCH, LOCATION],
        readFrom: ["ApiBuilder2", "LocationService", "SearchBuilder", "StorageResourcesBuilder"],
        layout: [13, 13, 13, 13, 10, 9, 10],
    },
    {
        label: "every add-on on",
        template: "commercial",
        key: "resourceNameChunksAllAddonsOn",
        mutate: allAddonsOn,
        late: everyLateKey,
        readFrom: ["AddonBuilder", ...DEFAULT_READ],
        layout: [13, 13, 13, 13, 10, 9, 11, 2],
    },
    {
        label: "GovCloud template",
        template: "govcloud",
        late: [ENGINE, ...OPENSEARCH, WEB],
        readFrom: ["ApiBuilder2", "SearchBuilder", "StorageResourcesBuilder"],
        layout: [13, 13, 13, 13, 10, 9, 10],
    },
    {
        label: "EU Sovereign template",
        template: "eusovereign",
        late: [ENGINE, ...OPENSEARCH, WEB],
        readFrom: ["ApiBuilder2", "SearchBuilder", "StorageResourcesBuilder"],
        layout: [13, 13, 13, 13, 10, 10, 8],
    },
];

const synthCell = (cell: Cell): SynthResult =>
    cell.mutate
        ? synthTemplate(cell.template, { mutate: cell.mutate, mutateKey: cell.key })
        : synthTemplate(cell.template);

const nestedStackId = (id: string) => `${id}NestedStack${id}NestedStackResource`;
const unhashed = (ids: string[]) => ids.map((id) => id.replace(/[0-9A-F]{8}$/, "")).sort();

/** The SSM parameter named `name`, or undefined. */
const parameterNamed = (s: SynthResult, name: string) =>
    s.ofType("AWS::SSM::Parameter").filter((r) => SynthResult.flatten(r.properties.Name) === name);

/** The schema-deploy custom resource (it writes aos/*), or undefined with OpenSearch off. */
const schemaDeploy = (s: SynthResult) =>
    s.resources.filter((r) => r.properties?.endpointSSMParam !== undefined);

/** The value each late key holds today, before this change: its legacy source, as a target. */
function legacyTargets(s: SynthResult): Map<string, string> {
    const stack = rootStackKey(s);
    const out = new Map<string, string>();
    const [schema] = schemaDeploy(s);
    if (schema) {
        out.set(
            K.openSearch.endpoint,
            valueTargetOf(
                s,
                schema.stack,
                schema.properties.collectionEndpoint ?? schema.properties.domainEndpoint
            )
        );
        out.set(
            K.openSearch.assetIndexName,
            valueTargetOf(s, schema.stack, schema.properties.assetIndexName)
        );
        out.set(
            K.openSearch.fileIndexName,
            valueTargetOf(s, schema.stack, schema.properties.fileIndexName)
        );
    }
    for (const [key, legacy] of [
        [WEB, `/${stack}/web/deployedUrl`],
        [LOCATION, `/${stack}/location/apiKeyArn`],
        [ENGINE, `${prefixOf(s)}/${ENGINE}`],
    ]) {
        const [param] = parameterNamed(s, legacy);
        if (param) out.set(key, valueTargetOf(s, param.stack, param.properties.Value));
    }
    return out;
}

describe.each(CELLS.map((cell) => [cell.label, cell] as [string, Cell]))("%s", (_label, cell) => {
    let s: SynthResult;
    beforeAll(() => {
        s = synthCell(cell);
    });

    test("carries each deploy-time value exactly when its feature is deployed, as it is today", () => {
        const chunks = chunksOf(s);
        const carried = new Map(
            chunks.flatMap((c) => [...rawChunkValues(c)].map(([k, v]) => [k, { v, c }]))
        );
        expect(LATE_KEYS.filter((k) => carried.has(k))).toEqual(cell.late);
        const legacy = legacyTargets(s);
        expect([...legacy.keys()].sort()).toEqual([...cell.late].sort());
        for (const key of cell.late) {
            const { v, c } = carried.get(key)!;
            const target = valueTargetOf(s, c.stack, v);
            expect({ key, target }).toEqual({ key, target: legacy.get(key) });
            expect(target).not.toMatch(/unresolved:/);
            // The per-key copy under the prefix carries it too, once.
            const copies = parameterNamed(s, `${prefixOf(s)}/${key}`);
            expect(copies).toHaveLength(1);
            expect(valueTargetOf(s, copies[0].stack, copies[0].properties.Value)).toBe(target);
        }
        for (const key of LATE_KEYS.filter((k) => !cell.late.includes(k))) {
            expect(parameterNamed(s, `${prefixOf(s)}/${key}`)).toEqual([]);
        }
    });

    test("keeps the chunk count at or under ten, with the recorded layout", () => {
        const chunks = expectChunksInRootStack(s);
        expect(chunks.length).toBeLessThanOrEqual(10);
        expect(chunks.map((c) => Object.keys(c.parsed.m).length)).toEqual(cell.layout);
        // The late core names close the core names: none sits in a lane chunk.
        const lateIn = chunks.filter((c) => LATE_KEYS.some((k) => k in c.parsed.m));
        expect(
            lateIn.every((c) => Object.keys(c.parsed.m).every((k) => !/^(addon|custom)\//.test(k)))
        ).toBe(true);
    });

    test("every chunk waits for storage and each stack a value is read from, with no cycle", () => {
        const chunks = chunksOf(s);
        const root = s.templates[rootStackKey(s)];
        for (const chunk of chunks) {
            expect({ index: chunk.index, dependsOn: unhashed(dependsOnOf(chunk)) }).toEqual({
                index: chunk.index,
                dependsOn: cell.readFrom.map(nestedStackId).sort(),
            });
            expect(dependsOnOf(chunk)).toEqual(stacksReadByChunks(chunks));
        }
        expect(dependencyCycleIn(root)).toEqual([]);
        const chunkIds = new Set(chunks.map((c) => c.logicalId));
        const waiting = Object.entries<any>(root.Resources)
            .filter(([id]) => !chunkIds.has(id))
            .filter(([, r]) =>
                ([] as string[]).concat(r.DependsOn ?? []).some((d) => chunkIds.has(d))
            )
            .map(([id]) => id);
        expect(waiting).toEqual([]);
        expectRootTemplateWithinLimits(s);
    });

    test("orders each per-key copy after the resource its value must not be read before", () => {
        const [schema] = schemaDeploy(s);
        for (const key of OPENSEARCH.filter((k) => cell.late.includes(k))) {
            const [copy] = parameterNamed(s, `${prefixOf(s)}/${key}`);
            expect(copy.stack).toBe(schema.stack);
            expect(([] as string[]).concat(copy.raw.DependsOn ?? [])).toContain(schema.logicalId);
        }
        if (cell.late.includes(WEB)) {
            const [copy] = parameterNamed(s, `${prefixOf(s)}/${WEB}`);
            const [legacy] = parameterNamed(s, `/${rootStackKey(s)}/web/deployedUrl`);
            expect(copy.stack).toBe(legacy.stack);
            // Both wait for the whole website construct.
            expect([...(copy.raw.DependsOn ?? [])].sort()).toEqual(
                [...(legacy.raw.DependsOn ?? [])].sort()
            );
            expect((copy.raw.DependsOn ?? []).length).toBeGreaterThan(0);
        }
        if (cell.late.includes(LOCATION)) {
            const [copy] = parameterNamed(s, `${prefixOf(s)}/${LOCATION}`);
            const apiKey = s.ofType("AWS::Location::APIKey");
            expect(apiKey).toHaveLength(1);
            expect(([] as string[]).concat(copy.raw.DependsOn ?? [])).toContain(
                apiKey[0].logicalId
            );
        }
    });

    test("keeps the copy engine's parameter as it was: one, in ApiBuilder2, same construct", () => {
        const params = parameterNamed(s, `${prefixOf(s)}/${ENGINE}`);
        expect(params).toHaveLength(1);
        expect(params[0].stack).toMatch(/ApiBuilder2/);
        expect(params[0].logicalId).toMatch(
            /^LargeObjectCopyEngineFunctionNameParameter[0-9A-F]{8}$/
        );
    });

    test("grants nothing new: no statement names a per-key copy or the chunk keys, and readers hold the prefix grants", () => {
        const statements = [
            ...s.ofType("AWS::IAM::Policy"),
            ...s.ofType("AWS::IAM::ManagedPolicy"),
        ].flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[]);
        const named = statements.filter((st) =>
            resourcesOf(st).some((r) => /\/resourceNames\/(openSearch|web|location)\b/.test(r))
        );
        expect(named).toEqual([]);
        const readers = [
            "handlers.config.configService.lambda_handler",
            "handlers.workflows.sfn.processWorkflowExecutionOutput.lambda_handler",
            ...(cell.late.includes(K.openSearch.endpoint)
                ? [
                      "handlers.osSemanticSearch.search.lambda_handler",
                      "handlers.osSemanticSearch.osFileIndexer.lambda_handler",
                      "handlers.osSemanticSearch.osAssetIndexer.lambda_handler",
                  ]
                : []),
        ];
        const prefixArn = `parameter${prefixOf(s)}/*`;
        const chunkArn = `parameter${prefixOf(s)}Chunks/*`;
        for (const handler of readers) {
            const fn = lambdaByHandler(s, handler);
            expect(handlerOf(fn)).toBe(handler);
            const ssm = statementsOf(s, fn).filter((st) =>
                actionsOf(st).some((a) => a.startsWith("ssm:"))
            );
            expect({
                handler,
                prefix: ssm.some((st) => resourcesOf(st).some((r) => r.endsWith(prefixArn))),
            }).toEqual({ handler, prefix: true });
            expect({
                handler,
                chunks: ssm.some((st) => resourcesOf(st).some((r) => r.endsWith(chunkArn))),
            }).toEqual({ handler, chunks: true });
        }
    });
});

/**
 * The secure-config function gets the web URL parameter's name (`WEB_DEPLOYED_URL_SSM_PARAM`) exactly
 * when a web distribution is deployed, which is when the static web stack publishes the web URL. On
 * an API-only deployment the variable is absent, so the handler answers an empty web URL with no
 * Parameter Store lookup instead of missing on the per-key and the legacy parameter every few minutes.
 */
describe("the secure-config function names the web URL parameter only with a web distribution", () => {
    const WEB_ENV = "WEB_DEPLOYED_URL_SSM_PARAM";
    const cellNamed = (prefix: string): Cell => {
        const found = CELLS.filter((c) => c.label.startsWith(prefix));
        expect(found).toHaveLength(1);
        return found[0];
    };
    const configServiceEnv = (s: SynthResult): Record<string, unknown> =>
        lambdaByHandler(s, "handlers.config.configService.lambda_handler").properties.Environment
            ?.Variables ?? {};

    test("API only: the variable is absent and no web URL parameter exists", () => {
        const s = synthCell(cellNamed("API only"));
        const env = configServiceEnv(s);
        // Control: the function's environment was read (the map key variables are still set).
        expect(Object.keys(env)).toContain("LOCATION_SERVICE_URL_FORMAT");
        expect(Object.keys(env)).not.toContain(WEB_ENV);
        expect(parameterNamed(s, `/${rootStackKey(s)}/web/deployedUrl`)).toEqual([]);
        expect(parameterNamed(s, `${prefixOf(s)}/${WEB}`)).toEqual([]);
    });

    test.each([
        "commercial default",
        "CloudFront custom domain",
        "ALB with a configured domain",
        "GovCloud template",
    ])("%s: the variable names the web URL parameter the static web stack creates", (prefix) => {
        const s = synthCell(cellNamed(prefix));
        const name = `/${rootStackKey(s)}/web/deployedUrl`;
        expect(configServiceEnv(s)[WEB_ENV]).toBe(name);
        expect(parameterNamed(s, name)).toHaveLength(1);
    });
});
