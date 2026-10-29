/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search-domain Lambda functions (search, fileIndexer, assetIndexer, crOsReindexer, and the
 * vector-search functions built on the same predicate) run inside the VPC in exactly three cases: a
 * provisioned OpenSearch domain, a private Serverless collection, or `useForAllLambdas`. The predicate
 * is exported once from `lib/helper/searchPlacement.ts`; a builder that inlines its own copy can drift
 * from the VPC endpoint condition that assumes it, which is a Lambda with no route to the service it
 * calls. The truth table pins the predicate; the T1 arms pin that the builders actually consult it.
 */

import type { ConfigPublic } from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import { searchLambdasInVpc } from "../../lib/helper/searchPlacement";
import { SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

jest.setTimeout(600_000);

/** The raw templates, keyed as the harness keys them, so the predicate reads what the synth read. */
const RAW_TEMPLATES: Record<TemplateName, unknown> = {
    commercial: commercialTemplate,
    govcloud: govcloudTemplate,
    eusovereign: eusovereignTemplate,
};

/** A ConfigPublic carrying only the fields the predicate reads. */
function placementConfig(opts: {
    provisioned: boolean;
    serverless: boolean;
    allowPublic: boolean;
    vpcEnabled: boolean;
    useForAllLambdas: boolean;
}): ConfigPublic {
    return {
        app: {
            useGlobalVpc: { enabled: opts.vpcEnabled, useForAllLambdas: opts.useForAllLambdas },
            openSearch: {
                useProvisioned: { enabled: opts.provisioned },
                useServerless: { enabled: opts.serverless, allowPublic: opts.allowPublic },
            },
        },
    } as unknown as ConfigPublic;
}

describe("searchLambdasInVpc", () => {
    test.each([
        // provisioned, serverless, allowPublic, vpcEnabled, useForAllLambdas, expected
        [false, true, true, false, false, false], // commercial template: public serverless, no VPC
        [true, false, false, true, false, true], // govcloud/eusovereign templates: provisioned
        [false, true, false, true, false, true], // private serverless collection
        [false, false, false, true, true, true], // useForAllLambdas, no OpenSearch at all
        [false, false, false, true, false, false], // VPC on, OpenSearch off, lambdas outside
        [false, true, false, false, false, true], // private serverless is the reason, VPC flag aside
        [false, false, false, false, true, false], // useForAllLambdas without a VPC does not count
    ])(
        "provisioned=%s serverless=%s allowPublic=%s vpc=%s useForAllLambdas=%s -> %s",
        (provisioned, serverless, allowPublic, vpcEnabled, useForAllLambdas, expected) => {
            expect(
                searchLambdasInVpc(
                    placementConfig({
                        provisioned,
                        serverless,
                        allowPublic,
                        vpcEnabled,
                        useForAllLambdas,
                    })
                )
            ).toBe(expected);
        }
    );

    test("the commercial template resolves to outside the VPC", () => {
        // Anchors the table to a shipped configuration rather than to hand-built objects only.
        expect(searchLambdasInVpc(commercialTemplate as unknown as ConfigPublic)).toBe(false);
    });
});

const SEARCH_HANDLERS = [
    "handlers.osSemanticSearch.search.lambda_handler",
    "handlers.osSemanticSearch.osFileIndexer.lambda_handler",
    "handlers.osSemanticSearch.osAssetIndexer.lambda_handler",
    "handlers.indexing.crReindexer.lambda_handler",
];

/** The Lambda functions built by the four refactored builders, by handler string. */
function searchDomainFunctions(synth: SynthResult) {
    return synth.where("AWS::Lambda::Function", (r) =>
        SEARCH_HANDLERS.includes(r.properties.Handler)
    );
}

describe("the builders place the search-domain Lambdas where the predicate says", () => {
    test.each<TemplateName>(["commercial", "govcloud", "eusovereign"])(
        "%s: VpcConfig presence equals searchLambdasInVpc(template)",
        (name) => {
            const synth = synthTemplate(name);
            const functions = searchDomainFunctions(synth);
            // Positive control: all four handlers are emitted by every shipped template (the reindexer is
            // created in both OpenSearch branches regardless of reindexOnCdkDeploy).
            expect(functions.map((f) => f.properties.Handler).sort()).toEqual(
                [...SEARCH_HANDLERS].sort()
            );
            // The raw template is what the harness synthesizes from, so it is what the predicate is
            // evaluated against.
            const expected = searchLambdasInVpc(RAW_TEMPLATES[name] as unknown as ConfigPublic);
            for (const fn of functions) {
                expect({
                    handler: fn.properties.Handler,
                    inVpc: "VpcConfig" in fn.properties,
                }).toEqual({
                    handler: fn.properties.Handler,
                    inVpc: expected,
                });
            }
        }
    );

    test("commercial + private Serverless collection: the third arm places them in the VPC", () => {
        // The hybrid proves the arm no shipped template exercises. addVpcEndpoints stays true so the
        // aoss-data endpoint the private collection needs is created by the synth.
        const synth = synthTemplate("commercial", {
            mutate: (c: any) => {
                c.app.useGlobalVpc.enabled = true;
                c.app.openSearch.useServerless.allowPublic = false;
            },
            mutateKey: "search-placement-private-serverless",
        });
        const functions = searchDomainFunctions(synth);
        expect(functions).toHaveLength(SEARCH_HANDLERS.length);
        for (const fn of functions) {
            expect("VpcConfig" in fn.properties).toBe(true);
        }
    });
});
