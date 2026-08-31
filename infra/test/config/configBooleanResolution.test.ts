/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A boolean flag set to "off" must resolve to off.
 *
 * `app.node.tryGetContext()` and `process.env` both yield strings, and every non-empty string is truthy
 * in JavaScript. `getConfig()` resolved five flags with `||` chains over those raw values wrapped in a
 * `<boolean>` cast — which is a TypeScript assertion, erased at compile time, checking nothing. So
 * `-c useWaf=false` and `AWS_USE_WAF=false` both read as TRUE, and the operator's explicit "off"
 * enabled the feature. It reached FIPS endpoint selection, AWS WAF creation, OpenSearch
 * reindex-on-deploy, the deferred index-schema deploy, and the VPC-stack context skip.
 *
 * Driven through `getConfig()` rather than by unit-testing a helper, because the defect was never in the
 * parsing — there was no parsing. It was in what `getConfig()` did with the raw value, so that is the
 * seam worth asserting at.
 *
 * The precedence assertions matter as much as the parsing ones. The `||` chains resolved to the first
 * source that was TRUE, letting a false fall through to the next; that is preserved deliberately, since
 * re-ordering how a flag set in two places resolves is a different change from reading a string
 * correctly. Without those tests a "fix" that made `config.json` authoritative would pass.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Serves a `config.json` built from the commercial template and delegates every other read. */
function serveConfig(mutate: (c: any) => void) {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
}

/** Resolve `app.useWaf` under a given context value, config.json value and environment variable. */
function resolveUseWaf(opts: { context?: unknown; inFile?: boolean; env?: string }): boolean {
    serveConfig((c) => {
        if (opts.inFile !== undefined) c.app.useWaf = opts.inFile;
        else delete c.app.useWaf;
    });
    if (opts.env === undefined) delete process.env.AWS_USE_WAF;
    else process.env.AWS_USE_WAF = opts.env;

    const context = opts.context === undefined ? {} : { useWaf: opts.context };
    return Config.getConfig(newTestApp({ context })).app.useWaf;
}

describe("boolean configuration resolution", () => {
    const savedWaf = process.env.AWS_USE_WAF;
    const savedFips = process.env.AWS_USE_FIPS_ENDPOINT;

    afterEach(() => {
        // Restored to delegating rather than cleared, or the synth harness in any later test in this
        // process reads nothing instead of failing outright.
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    afterAll(() => {
        if (savedWaf === undefined) delete process.env.AWS_USE_WAF;
        else process.env.AWS_USE_WAF = savedWaf;
        if (savedFips === undefined) delete process.env.AWS_USE_FIPS_ENDPOINT;
        else process.env.AWS_USE_FIPS_ENDPOINT = savedFips;
    });

    test("the flag is resolvable at all, in both directions", () => {
        // The control. Every assertion below compares against true or false, so a getConfig() that
        // always returned one of them would satisfy half of them by accident.
        expect(resolveUseWaf({ inFile: true })).toBe(true);
        expect(resolveUseWaf({ inFile: false })).toBe(false);
    });

    describe("a CDK context value", () => {
        test.each([
            ["false", false],
            ["FALSE", false],
            ["0", false],
            ["no", false],
            ["off", false],
            ["", false],
        ])("-c useWaf=%s resolves to %s", (value, expected) => {
            // Each of these was TRUE before, because a non-empty string is truthy — the operator's
            // explicit "off" turned the feature on.
            expect(resolveUseWaf({ context: value, inFile: false })).toBe(expected);
        });

        test.each([
            ["true", true],
            ["TRUE", true],
            ["1", true],
            ["yes", true],
            ["on", true],
        ])("-c useWaf=%s resolves to %s", (value, expected) => {
            expect(resolveUseWaf({ context: value, inFile: false })).toBe(expected);
        });

        test("a real boolean from context still works", () => {
            // `cdk.json` context is JSON, so a caller can supply an actual boolean rather than a string.
            expect(resolveUseWaf({ context: true, inFile: false })).toBe(true);
            expect(resolveUseWaf({ context: false, inFile: false })).toBe(false);
        });

        test("an unrecognised value warns rather than being taken silently either way", () => {
            const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
            try {
                expect(resolveUseWaf({ context: "enabled", inFile: false })).toBe(false);
                const messages = warn.mock.calls.map((c) => String(c[0])).join("\n");
                expect(messages).toContain("useWaf");
                expect(messages).toContain("enabled");
            } finally {
                warn.mockRestore();
            }
        });
    });

    describe("an environment variable", () => {
        test("AWS_USE_WAF=false resolves to false", () => {
            expect(resolveUseWaf({ env: "false", inFile: false })).toBe(false);
        });

        test("AWS_USE_WAF=true resolves to true", () => {
            expect(resolveUseWaf({ env: "true", inFile: false })).toBe(true);
        });
    });

    describe("precedence is unchanged", () => {
        test("a true context value wins over a false config.json value", () => {
            expect(resolveUseWaf({ context: "true", inFile: false })).toBe(true);
        });

        test("a false context value falls through to a true config.json value", () => {
            // The `||` chains resolved to the first source that was TRUE rather than the first source
            // that was PRESENT. Preserved on purpose: a fix that made an explicit false authoritative
            // would change how existing deployments resolve a flag set in two places.
            expect(resolveUseWaf({ context: "false", inFile: true })).toBe(true);
        });

        test("a false config.json value falls through to a true environment variable", () => {
            expect(resolveUseWaf({ inFile: false, env: "true" })).toBe(true);
        });

        test("all three sources false resolves to false", () => {
            expect(resolveUseWaf({ context: "false", inFile: false, env: "false" })).toBe(false);
        });
    });

    test("every flag resolved this way parses a string, not just useWaf", () => {
        // The defect was in five places. Asserted through useFips, which has its own environment
        // variable, so the fix cannot have been applied to one call site only.
        serveConfig((c) => {
            c.app.useFips = false;
        });
        process.env.AWS_USE_FIPS_ENDPOINT = "false";
        expect(Config.getConfig(newTestApp()).app.useFips).toBe(false);

        serveConfig((c) => {
            c.app.openSearch.reindexOnCdkDeploy = false;
        });
        expect(
            Config.getConfig(newTestApp({ context: { reindexOnCdkDeploy: "false" } })).app
                .openSearch.reindexOnCdkDeploy
        ).toBe(false);

        serveConfig((c) => {
            c.app.openSearch.useServerless.deployDeferredIndexSchema = false;
        });
        expect(
            Config.getConfig(newTestApp({ context: { deployDeferredIndexSchema: "false" } })).app
                .openSearch.useServerless.deployDeferredIndexSchema
        ).toBe(false);

        serveConfig((c) => {
            c.env.loadContextIgnoreVPCStacks = false;
        });
        expect(
            Config.getConfig(newTestApp({ context: { loadContextIgnoreVPCStacks: "false" } })).env
                .loadContextIgnoreVPCStacks
        ).toBe(false);
    });
});
