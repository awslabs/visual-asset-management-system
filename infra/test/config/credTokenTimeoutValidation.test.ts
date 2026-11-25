/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `app.authProvider.useCognito.credTokenTimeoutSeconds` is the ID and access token lifetime of the
 * Amazon Cognito web app client (`cognitoWebClientTokenAndAuthFlowSettings`). The CDK
 * `UserPoolClient` accepts only a whole number of minutes from 5 minutes to 1 day there and throws
 * on anything else, so with Amazon Cognito enabled such a value stops the synthesis at that client
 * with a message that does not name the configuration field; with Amazon Cognito off the value is
 * not used.
 *
 * `getConfig()` warns about such a value and leaves the resolved value as it is, so a configuration
 * that deploys today still deploys. Every warning arm also asserts that the value is unchanged.
 *
 * CDK context and the environment variable deliver the value as a string, so each source has its
 * own arms. An absent, null, empty or zero `config.json` value falls through to the environment
 * variable or to 3600 and stays silent. The ConfigBuilder's hand-ported rule is asserted against
 * the same values, because `configBuilderSync.test.ts` covers only `schema.ts` and `defaults.ts`.
 * The last block pins the range to what the CDK client accepts, so a change to either side fails
 * here.
 *
 * Every warning arm asserts on the MESSAGE, and every silent arm on the absence of any warning that
 * names the field, because `getConfig()` can emit other warnings for the same configuration.
 */

import * as fs from "fs";
import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { cognitoWebClientTokenAndAuthFlowSettings } from "../../lib/nestedStacks/auth/constructs/cognito-web-native-construct";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const ENV_KEY = "CRED_TOKEN_TIMEOUT_SECONDS";
const savedEnv = process.env[ENV_KEY];

/** Removes the key from config.json, so the environment variable is the source consulted. */
const KEY_ABSENT = Symbol("key absent");

/**
 * Builds a config.json from the commercial template with `credTokenTimeoutSeconds` set to `value`
 * (or removed), and returns a thunk that calls getConfig() with the given CDK context.
 */
function resolve(value: unknown, context: Record<string, unknown> = {}): () => Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (value === KEY_ABSENT) {
        delete config.app.authProvider.useCognito.credTokenTimeoutSeconds;
    } else {
        config.app.authProvider.useCognito.credTokenTimeoutSeconds = value;
    }
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp({ context }));
}

/** Calls getConfig() with console.warn silenced. Returns the resolved value and the warnings that name the field. */
function run(value: unknown, context: Record<string, unknown> = {}) {
    const load = resolve(value, context);
    const captured: string[] = [];
    const spy = jest.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
        captured.push(args.map(String).join(" "));
    });
    let timeout: unknown;
    try {
        timeout = load().app.authProvider.useCognito.credTokenTimeoutSeconds;
    } finally {
        spy.mockRestore();
    }
    return {
        timeout: String(timeout),
        warnings: captured.filter((message) => message.includes("credTokenTimeoutSeconds")),
    };
}

const OUT_OF_RANGE =
    /app\.authProvider\.useCognito\.credTokenTimeoutSeconds should be a whole number of minutes, in seconds, from 300 to 86400/;

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
    // Restored to delegating rather than cleared, or a later read in this process returns undefined.
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

afterAll(() => {
    if (savedEnv === undefined) delete process.env[ENV_KEY];
    else process.env[ENV_KEY] = savedEnv;
});

describe("credTokenTimeoutSeconds from config.json", () => {
    test("the shipped commercial template resolves to 3600 seconds without a warning", () => {
        // The control for every warning below: the value VAMS ships must stay silent.
        const result = run(3600);
        expect(result.timeout).toBe("3600");
        expect(result.warnings).toEqual([]);
    });

    test.each([[300], [1800], [86400], ["1800"]])(
        "%p is in range and resolves as given without a warning",
        (v) => {
            const result = run(v);
            expect(result.timeout).toBe(String(v));
            expect(result.warnings).toEqual([]);
        }
    );

    test.each([[KEY_ABSENT], [null], [""], [0]])(
        "an unset or zero value (%p) falls back to 3600 seconds without a warning",
        (v) => {
            const result = run(v);
            expect(result.timeout).toBe("3600");
            expect(result.warnings).toEqual([]);
        }
    );

    test.each([[299], [60], [-60]])("%p is below the range: warns and resolves unchanged", (v) => {
        expectWarned(run(v), String(v));
    });

    test.each([[86401], [86460], [172800]])(
        "%p is above the range: warns and resolves unchanged",
        (v) => {
            expectWarned(run(v), String(v));
        }
    );

    test.each([[3601], [3630], [3600.5]])(
        "%p is not a whole number of minutes: warns and resolves unchanged",
        (v) => {
            expectWarned(run(v), String(v));
        }
    );

    test.each([["abc"], ["   "], [true], ["1h"]])(
        "%p is not a number of seconds: warns and resolves unchanged",
        (v) => {
            expectWarned(run(v), String(v));
        }
    );
});

describe("credTokenTimeoutSeconds from CDK context", () => {
    test("a context value wins over config.json without a warning", () => {
        const result = run(3600, { credTokenTimeoutSeconds: "1800" });
        expect(result.timeout).toBe("1800");
        expect(result.warnings).toEqual([]);
    });

    test.each([["299"], ["0"], ["90000"], ["3601"]])(
        "context value %p warns and resolves unchanged",
        (v) => {
            // A context "0" is a non-empty string, so it does not fall through the way a 0 in
            // config.json does.
            expectWarned(run(3600, { credTokenTimeoutSeconds: v }), v);
        }
    );
});

describe("credTokenTimeoutSeconds from CRED_TOKEN_TIMEOUT_SECONDS", () => {
    test("used when config.json does not set the key", () => {
        process.env[ENV_KEY] = "7200";
        const result = run(KEY_ABSENT);
        expect(result.timeout).toBe("7200");
        expect(result.warnings).toEqual([]);
    });

    test("config.json still wins over the environment variable", () => {
        process.env[ENV_KEY] = "7200";
        const result = run(3600);
        expect(result.timeout).toBe("3600");
        expect(result.warnings).toEqual([]);
    });

    test("an out-of-range environment value warns and resolves unchanged", () => {
        process.env[ENV_KEY] = "100000";
        expectWarned(run(KEY_ABSENT), "100000");
    });
});

const RULE_ID = "cred-token-timeout-range";

/** The token timeout rule the ConfigBuilder reports for the commercial preset holding `value`, if any. */
const builderRule = (value: unknown) => {
    const cfg = makeDefaultConfig("commercial");
    cfg.app.authProvider.useCognito.credTokenTimeoutSeconds = value;
    return evaluateRules(cfg).find((rule) => rule.id === RULE_ID);
};

describe("ConfigBuilder mirror of the token timeout warning", () => {
    test.each([[299], [-60], [86401], [3601], [3600.5], ["abc"], ["   "], [true], [" 3600"]])(
        "fires for %p",
        (v) => {
            expect(builderRule(v)).toBeDefined();
        }
    );

    test.each([[3600], [300], [86400], ["1800"], [""], [null], [undefined], [0]])(
        "stays silent for %p",
        (v) => {
            expect(builderRule(v)).toBeUndefined();
        }
    );

    test("the rule is a warning, as getConfig() only warns", () => {
        expect(builderRule(299)?.severity).toBe("warning");
    });

    test("[control] the builder reports a numeric range rule at all", () => {
        // Without this, "stays silent" is satisfied by an evaluateRules() that returns nothing.
        const cfg = makeDefaultConfig("commercial");
        cfg.app.authProvider.presignedUrlTimeoutSeconds = 604801;
        expect(evaluateRules(cfg).map((rule) => rule.id)).toContain("presigned-url-timeout-range");
    });
});

describe("the warned range is the range the web app client accepts", () => {
    /** Builds the web app client with the token settings VAMS derives from `seconds`. */
    const buildClient = (seconds: unknown) => {
        const stack = new cdk.Stack(newTestApp(), "TokenRange");
        const pool = new cognito.UserPool(stack, "Pool");
        const settings = cognitoWebClientTokenAndAuthFlowSettings({
            app: { authProvider: { useCognito: { credTokenTimeoutSeconds: seconds } } },
        } as unknown as Config.Config);
        new cognito.UserPoolClient(stack, "Client", { userPool: pool, ...settings });
        return Template.fromStack(stack);
    };

    test.each([[300], [3600], [86400], ["1800"]])("%p builds the client", (v) => {
        const template = buildClient(v);
        template.hasResourceProperties("AWS::Cognito::UserPoolClient", {
            IdTokenValidity: Number(v) / 60,
            AccessTokenValidity: Number(v) / 60,
        });
    });

    test.each([[299], [240], [86401], [86460], [3601], ["abc"], [true]])(
        "%p, which getConfig() warns about, is refused by the client",
        (v) => {
            expect(() => buildClient(v)).toThrow();
        }
    );
});
