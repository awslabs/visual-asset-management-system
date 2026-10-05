/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `app.authProvider.presignedUrlTimeoutSeconds` is the `ExpiresIn` that the asset download, stream,
 * upload and export handlers pass to Amazon S3 when they sign a URL. They read it from
 * `PRESIGNED_URL_TIMEOUT_SECONDS`, most of them through `int()`, and botocore signs whatever it is
 * given, so a value Amazon S3 does not accept deploys cleanly and fails on every request:
 *
 *  * above 604800 seconds (7 days, the Signature Version 4 maximum) the URL is issued and Amazon S3
 *    answers `AuthorizationQueryParametersError` on first use;
 *  * a fractional or non-numeric value makes `int()` raise in the download, stream and export
 *    handlers, and the upload handler signs it verbatim into a URL Amazon S3 rejects;
 *  * zero signs a URL that has already expired, and a negative value one Amazon S3 rejects.
 *
 * `getConfig()` warns about such a value at synthesis and leaves the resolved value as it is, so
 * every warning arm also asserts that the value reaching the Lambda environment is unchanged.
 *
 * CDK context and the environment variable deliver the value as a string, so each source has its
 * own arms. An absent, null, empty or zero `config.json` value falls through to the environment
 * variable or to 86400 and stays silent. The ConfigBuilder's hand-ported rule is asserted against
 * the same values, because `configBuilderSync.test.ts` covers only `schema.ts` and `defaults.ts`.
 *
 * Every warning arm asserts on the MESSAGE, and every silent arm on the absence of any warning that
 * names the field, because `getConfig()` can emit other warnings for the same configuration.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const ENV_KEY = "PRESIGNED_URL_TIMEOUT_SECONDS";
const savedEnv = process.env[ENV_KEY];

/** Removes the key from config.json, so the environment variable is the source consulted. */
const KEY_ABSENT = Symbol("key absent");

/**
 * Builds a config.json from the commercial template with `presignedUrlTimeoutSeconds` set to
 * `value` (or removed), and returns a thunk that calls getConfig() with the given CDK context.
 */
function resolve(value: unknown, context: Record<string, unknown> = {}): () => Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (value === KEY_ABSENT) {
        delete config.app.authProvider.presignedUrlTimeoutSeconds;
    } else {
        config.app.authProvider.presignedUrlTimeoutSeconds = value;
    }
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp({ context }));
}

/**
 * Calls getConfig() with console.warn silenced. Returns the resolved timeout as the string the
 * Lambda environment receives, and the warnings that name the field.
 */
function run(value: unknown, context: Record<string, unknown> = {}) {
    const load = resolve(value, context);
    const captured: string[] = [];
    const spy = jest.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
        captured.push(args.map(String).join(" "));
    });
    let timeout: unknown;
    try {
        timeout = load().app.authProvider.presignedUrlTimeoutSeconds;
    } finally {
        spy.mockRestore();
    }
    return {
        timeout: String(timeout),
        warnings: captured.filter((message) => message.includes("presignedUrlTimeoutSeconds")),
    };
}

const OUT_OF_RANGE =
    /app\.authProvider\.presignedUrlTimeoutSeconds should be a whole number of seconds between 1 and 604800/;

/** The assertions every out-of-range arm makes: one warning, naming the value, nothing changed. */
function expectWarned(result: ReturnType<typeof run>, value: string) {
    expect(result.warnings).toHaveLength(1);
    expect(result.warnings[0]).toMatch(OUT_OF_RANGE);
    expect(result.warnings[0]).toContain(`Got: '${value}'`);
    expect(result.timeout).toBe(value);
}

beforeEach(() => {
    // An operator's shell or a .env file must not decide an arm that expects the variable unset.
    delete process.env[ENV_KEY];
});

afterEach(() => {
    // Restored to delegating rather than cleared, or a later read in this process returns
    // undefined.
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

afterAll(() => {
    if (savedEnv === undefined) delete process.env[ENV_KEY];
    else process.env[ENV_KEY] = savedEnv;
});

describe("presignedUrlTimeoutSeconds from config.json", () => {
    test("the shipped commercial template resolves to 86400 seconds without a warning", () => {
        // The control for every warning below: the value VAMS ships must stay silent.
        const result = run(86400);
        expect(result.timeout).toBe("86400");
        expect(result.warnings).toEqual([]);
    });

    test.each([[1], [3600], [604800], ["3600"]])(
        "%p is in range and resolves as given without a warning",
        (v) => {
            const result = run(v);
            expect(result.timeout).toBe(String(v));
            expect(result.warnings).toEqual([]);
        }
    );

    test.each([[KEY_ABSENT], [null], [""], [0]])(
        "an unset or zero value (%p) falls back to 86400 seconds without a warning",
        (v) => {
            // "" is what the ConfigBuilder writes for a cleared number field; 0 falls through the
            // same way, to the environment variable or the default.
            const result = run(v);
            expect(result.timeout).toBe("86400");
            expect(result.warnings).toEqual([]);
        }
    );

    test.each([[604801], [2592000], [-60], [3600.5], ["abc"], ["   "], [true]])(
        "%p warns, naming the field, the bounds and the value, and resolves unchanged",
        (v) => {
            expectWarned(run(v), String(v));
        }
    );
});

describe("presignedUrlTimeoutSeconds from CDK context", () => {
    test("a context value wins over config.json without a warning", () => {
        const result = run(86400, { presignedUrlTimeoutSeconds: "600" });
        expect(result.timeout).toBe("600");
        expect(result.warnings).toEqual([]);
    });

    test.each([["604801"], ["0"], ["3600.5"]])(
        "context value %p warns and resolves unchanged",
        (v) => {
            // A context "0" is a non-empty string, so it does not fall through the way a 0 in
            // config.json does, and it signs URLs that have already expired.
            expectWarned(run(86400, { presignedUrlTimeoutSeconds: v }), v);
        }
    );
});

describe("presignedUrlTimeoutSeconds from PRESIGNED_URL_TIMEOUT_SECONDS", () => {
    test("used when config.json does not set the key", () => {
        process.env[ENV_KEY] = "7200";
        const result = run(KEY_ABSENT);
        expect(result.timeout).toBe("7200");
        expect(result.warnings).toEqual([]);
    });

    test("config.json still wins over the environment variable", () => {
        // The resolution's precedence: context, then config.json, then the environment variable.
        process.env[ENV_KEY] = "7200";
        const result = run(3600);
        expect(result.timeout).toBe("3600");
        expect(result.warnings).toEqual([]);
    });

    test("an out-of-range environment value warns and resolves unchanged", () => {
        process.env[ENV_KEY] = "604801";
        expectWarned(run(KEY_ABSENT), "604801");
    });

    test("an empty environment value counts as unset", () => {
        process.env[ENV_KEY] = "";
        const result = run(KEY_ABSENT);
        expect(result.timeout).toBe("86400");
        expect(result.warnings).toEqual([]);
    });
});

const RULE_ID = "presigned-url-timeout-range";

/** The timeout rule the ConfigBuilder reports for the commercial preset holding `value`, if any. */
const builderRule = (value: unknown) => {
    const cfg = makeDefaultConfig("commercial");
    cfg.app.authProvider.presignedUrlTimeoutSeconds = value;
    return evaluateRules(cfg).find((rule) => rule.id === RULE_ID);
};

describe("ConfigBuilder mirror of the presigned URL timeout warning", () => {
    test.each([[604801], [2592000], [-60], [3600.5], ["abc"], ["   "], [true], [" 3600"]])(
        "fires for %p",
        (v) => {
            expect(builderRule(v)).toBeDefined();
        }
    );

    test.each([[86400], [1], [604800], ["3600"], [""], [null], [undefined], [0]])(
        "stays silent for %p",
        (v) => {
            expect(builderRule(v)).toBeUndefined();
        }
    );

    test("the rule is a warning, as getConfig() only warns", () => {
        expect(builderRule(604801)?.severity).toBe("warning");
    });

    test("[control] the builder reports a numeric range rule at all", () => {
        // Without this, "stays silent" is satisfied by an evaluateRules() that returns nothing.
        const cfg = makeDefaultConfig("commercial");
        cfg.app.api.apiGatewayRest.apiGatewayTimeoutTime = 5;
        expect(evaluateRules(cfg).map((rule) => rule.id)).toContain("api-timeout-range");
    });
});
