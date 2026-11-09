/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * With EDH Workstations disabled, as every shipped template has it, the deployment carries nothing of
 * the add-on, and its nested stacks deploy in the order they have without the add-on.
 *
 * Footprint: no `/addon/edh` route, no construct whose id names the add-on (`Edh...`), no `EDH_*`
 * value or `edh*` object type, field or table key, and no output named for the add-on, in any template
 * of the commercial, GovCloud and EU Sovereign assemblies. The control is the same scan of the EDH-on
 * hybrid, which finds each kind.
 *
 * Deploy order: the core stack makes AddonBuilder deploy after ApiBuilder only for an add-on that calls
 * the API key service Lambda built there. With EDH off, AddonBuilder depends on the storage and
 * resource-name stacks alone, as it does with no add-on and with the other add-ons (Physna, Garnet) on.
 * Turning EDH on adds dependencies to AddonBuilder only (ApiBuilder, and the VPC stack its Lambdas are
 * placed in); every other nested stack keeps the dependencies it has for the same VPC setting.
 */

import { ALL_TEMPLATES, SynthResult, TemplateName, synthTemplate } from "../../support/templateSynth";
import { enableEdh } from "./support/edhSynth";

// Up to eight full-app synths, most shared with other suites through the per-run cache.
jest.setTimeout(900_000);

const edhOn = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enableEdh, mutateKey: "edhWorkstationsEnabled" });

/** The VPC setting the EDH hybrid turns on, with the add-on itself left off. */
const vpcOnly = (): SynthResult =>
    synthTemplate("commercial", {
        mutate: (c) => {
            c.app.useGlobalVpc.enabled = true;
        },
        mutateKey: "edhFootprintVpcOnly",
    });

/** Physna Sync and Garnet on, EDH off. */
const otherAddons = (): SynthResult =>
    synthTemplate("commercial", {
        mutateKey: "routing-client-grants-addons-on",
        mutate: (c) => {
            c.app.addons.usePhysnaSync.enabled = true;
            c.app.addons.usePhysnaSync.tenantId = "11111111-2222-3333-4444-555555555555";
            c.app.addons.useGarnetFramework.enabled = true;
            c.app.addons.useGarnetFramework.garnetApiEndpoint = "https://garnet.example.com";
            c.app.addons.useGarnetFramework.garnetApiToken = "token";
            c.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl =
                "https://sqs.us-east-1.amazonaws.com/123456789012/garnet-ingest";
        },
    });

/** Each kind of EDH trace, found in the text of every template of an assembly. */
const TRACES: Record<string, RegExp> = {
    "an /addon/edh route": /\/addon\/edh/g,
    "an EDH_* value": /EDH_[A-Z]/g,
    "an edh* object type, field or table key": /"edh[A-Z]|dynamoTables\/edh/g,
    "a construct or output id naming the add-on": /Edh[A-Z]/g,
};

function traces(s: SynthResult): Record<string, number> {
    const text = JSON.stringify(s.templates);
    return Object.fromEntries(
        Object.entries(TRACES).map(([kind, re]) => [kind, (text.match(re) ?? []).length])
    );
}

/** Output names of every template that mention the add-on. */
const edhOutputs = (s: SynthResult): string[] =>
    Object.entries<any>(s.templates).flatMap(([stack, tpl]) =>
        Object.keys(tpl.Outputs ?? {})
            .filter((name) => /edh/i.test(name))
            .map((name) => `${stack}/${name}`)
    );

/** A nested-stack logical id without its 8-hex-digit hash, which follows the construct path. */
const unhashed = (logicalId: string): string => logicalId.replace(/[0-9A-F]{8}$/, "");

/** The root template's nested stacks, each with what it depends on, hash suffixes removed. */
function rootGraph(s: SynthResult): Record<string, string[]> {
    const addon = s
        .ofType("AWS::CloudFormation::Stack")
        .find((r) => r.logicalId.startsWith("AddonBuilder"));
    expect(addon).toBeDefined();
    const graph: Record<string, string[]> = {};
    for (const r of s.ofType("AWS::CloudFormation::Stack")) {
        if (r.stack !== addon!.stack) continue;
        const dependsOn = ([] as string[]).concat(r.raw.DependsOn ?? []);
        graph[unhashed(r.logicalId)] = dependsOn.map(unhashed).sort();
    }
    return graph;
}

const addonDependencies = (graph: Record<string, string[]>): string[] => {
    const key = Object.keys(graph).find((k) => k.startsWith("AddonBuilder"));
    expect(key).toBeDefined();
    return graph[key!];
};
const isApiBuilder = (dependency: string) => /^ApiBuilder(?!2)/.test(dependency);
const isCoreBuilderOfEveryStack = (dependency: string) =>
    /^StorageResourcesBuilder|^ResourceNamesBuilder/.test(dependency);

describe("the EDH-on hybrid carries every kind of trace (control)", () => {
    test.each(["commercial", "govcloud"] as TemplateName[])("%s", (name) => {
        const found = traces(edhOn(name));
        for (const [kind, count] of Object.entries(found)) {
            expect([kind, count > 0]).toEqual([kind, true]);
        }
        expect(edhOutputs(edhOn(name)).length).toBeGreaterThan(0);
    });
});

describe("a shipped template carries nothing of the add-on", () => {
    test.each(ALL_TEMPLATES)("%s", (name) => {
        const stock = synthTemplate(name);
        expect(stock.resources.length).toBeGreaterThan(0);
        expect(traces(stock)).toEqual(Object.fromEntries(Object.keys(TRACES).map((k) => [k, 0])));
        expect(edhOutputs(stock)).toEqual([]);
    });
});

describe("with EDH off the nested stacks deploy in the order they have without the add-on", () => {
    test.each(ALL_TEMPLATES)(
        "%s as shipped: AddonBuilder depends on the storage and resource-name stacks only",
        (name) => {
            const dependencies = addonDependencies(rootGraph(synthTemplate(name)));
            expect(dependencies.length).toBe(2);
            expect(dependencies.every(isCoreBuilderOfEveryStack)).toBe(true);
        }
    );

    test("Physna Sync and Garnet on: the same two, and the same root graph as with no add-on", () => {
        const graph = rootGraph(otherAddons());
        expect(addonDependencies(graph).every(isCoreBuilderOfEveryStack)).toBe(true);
        expect(graph).toEqual(rootGraph(synthTemplate("commercial")));
    });

    test("EDH on: AddonBuilder alone gains dependencies, on ApiBuilder and on the VPC stack its Lambdas use", () => {
        const on = rootGraph(edhOn("commercial"));
        const off = rootGraph(vpcOnly());
        const others = (graph: Record<string, string[]>) =>
            Object.fromEntries(Object.entries(graph).filter(([k]) => !k.startsWith("AddonBuilder")));
        expect(others(on)).toEqual(others(off));
        const before = addonDependencies(off);
        const after = addonDependencies(on);
        expect(before.every((d) => after.includes(d))).toBe(true);
        const added = after.filter((d) => !before.includes(d));
        expect(added.filter(isApiBuilder)).toHaveLength(1);
        expect(added.filter((d) => !isApiBuilder(d)).every((d) => /^VPCBuilder/.test(d))).toBe(true);
    });
});
