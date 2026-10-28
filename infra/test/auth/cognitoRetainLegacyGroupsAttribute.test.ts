/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * How `getConfig()` resolves `app.authProvider.useCognito.retainLegacyGroupsAttribute`.
 *
 * The field decides whether the user pool declares the `custom:groups` attribute. A user pool that
 * already declares it must keep doing so, because the AWS CloudFormation user pool handler refuses
 * any change to an existing schema attribute; a new user pool must not, because Amazon Cognito
 * rejects the attribute name when it creates the pool. A `config.json` without the key resolves to
 * `true` with a configuration warning, so a deployment whose config predates the key keeps its pool
 * unchanged, and the three config templates a new deployment starts from set `false`.
 *
 * The ConfigBuilder's hand-ported warning rule is asserted against the same cases, because
 * `configBuilderSync.test.ts` covers only `schema.ts` and `defaults.ts`.
 *
 * Every warning arm asserts on the MESSAGE, and every silent arm on the absence of any warning that
 * names the field, because `getConfig()` can emit other warnings for the same configuration.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import { newTestApp } from "../support/testApp";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const FIELD = "retainLegacyGroupsAttribute";

/** Removes the key from config.json, as in a config.json written before the key existed. */
const KEY_ABSENT = Symbol("key absent");

/**
 * Turn Cognito off and configure the external OAuth IdP in its place, so `getConfig()` accepts the
 * config for a reason other than the field under test.
 */
const withoutCognito = (c: any) => {
    c.app.authProvider.useCognito.enabled = false;
    const idp = c.app.authProvider.useExternalOAuthIdp;
    idp.enabled = true;
    idp.idpAuthProviderUrl = "https://idp.test.example.com";
    idp.idpAuthClientId = "test-client";
    idp.idpAuthProviderScope = "openid";
    idp.idpAuthProviderScopeMfa = "openid";
    idp.idpAuthPrincipalDomain = "test.example.com";
    idp.idpAuthProviderTokenEndpoint = "https://idp.test.example.com/oauth2/token";
    idp.idpAuthProviderAuthorizationEndpoint = "https://idp.test.example.com/oauth2/authorize";
    idp.idpAuthProviderDiscoveryEndpoint =
        "https://idp.test.example.com/.well-known/openid-configuration";
    idp.lambdaAuthorizorJWTIssuerUrl = "https://idp.test.example.com";
    idp.lambdaAuthorizorJWTAudience = "test-audience";
};

/**
 * Calls getConfig() over the commercial template with the field set to `value` (or removed) and
 * console.warn captured. Returns the resolved field and the warnings that name it.
 */
function run(value: unknown, mutate?: (c: any) => void) {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    config.app.adminEmailAddress = "admin@example.com";
    if (value === KEY_ABSENT) {
        delete config.app.authProvider.useCognito[FIELD];
    } else {
        config.app.authProvider.useCognito[FIELD] = value;
    }
    mutate?.(config);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );

    const captured: string[] = [];
    const spy = jest.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
        captured.push(args.map(String).join(" "));
    });
    let resolved: unknown;
    try {
        resolved = Config.getConfig(newTestApp()).app.authProvider.useCognito[FIELD];
    } finally {
        spy.mockRestore();
    }
    return { resolved, warnings: captured.filter((message) => message.includes(FIELD)) };
}

const UNSET_WARNING =
    /Configuration Warning: app\.authProvider\.useCognito\.retainLegacyGroupsAttribute is not set, so it defaults to true/;

afterEach(() => {
    // Restored to delegating rather than cleared, or a later read in this process returns undefined.
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("retainLegacyGroupsAttribute in getConfig()", () => {
    test("an absent key resolves to true and warns once, naming what a new deployment must set", () => {
        const result = run(KEY_ABSENT);
        expect(result.resolved).toBe(true);
        expect(result.warnings).toHaveLength(1);
        expect(result.warnings[0]).toMatch(UNSET_WARNING);
        expect(result.warnings[0]).toMatch(/A NEW deployment must set it to false/);
        expect(result.warnings[0]).toMatch(/Attribute names MUST conform to the following rules/);
    });

    test("a null value is treated as absent", () => {
        const result = run(null);
        expect(result.resolved).toBe(true);
        expect(result.warnings).toHaveLength(1);
        expect(result.warnings[0]).toMatch(UNSET_WARNING);
    });

    test.each([[true], [false]])("an explicit %p is kept and stays silent", (value) => {
        const result = run(value);
        expect(result.resolved).toBe(value);
        expect(result.warnings).toEqual([]);
    });

    test("with Cognito disabled an absent key stays silent", () => {
        // No user pool is created, so the field describes nothing.
        const result = run(KEY_ABSENT, withoutCognito);
        expect(result.warnings).toEqual([]);
    });
});

describe("the shipped configuration files", () => {
    test.each([
        ["commercial", commercialTemplate],
        ["govcloud", govcloudTemplate],
        ["eusovereign", eusovereignTemplate],
    ])("config.template.%s.json sets false, which a new deployment needs", (_name, template) => {
        expect((template as any).app.authProvider.useCognito[FIELD]).toBe(false);
    });

    test("the tracked config.json does not set false", () => {
        // An existing deployment's config.json is a modified copy of this file, so a `false` added
        // here would merge into it on update and remove the attribute from an existing user pool.
        const configJson = JSON.parse(
            realReadFileSync(path.join(__dirname, "../../config/config.json"), "utf8")
        );
        expect(configJson.app.authProvider.useCognito.enabled).toBe(true);
        expect(configJson.app.authProvider.useCognito[FIELD]).not.toBe(false);
    });
});

const RULE_ID = "cognito-legacy-groups-attribute-unset-warn";

/** The rule the ConfigBuilder reports for the commercial preset holding `value`, if any. */
const builderRule = (value: unknown, mutate?: (c: any) => void) => {
    const cfg = makeDefaultConfig("commercial");
    if (value === KEY_ABSENT) {
        delete cfg.app.authProvider.useCognito[FIELD];
    } else {
        cfg.app.authProvider.useCognito[FIELD] = value;
    }
    mutate?.(cfg);
    return evaluateRules(cfg).find((rule) => rule.id === RULE_ID);
};

describe("ConfigBuilder mirror of the unset-field warning", () => {
    test.each([[KEY_ABSENT], [null]])("fires for %p with Cognito enabled", (value) => {
        expect(builderRule(value)).toBeDefined();
    });

    test.each([[true], [false]])("stays silent for %p", (value) => {
        expect(builderRule(value)).toBeUndefined();
    });

    test("stays silent with Cognito disabled", () => {
        expect(
            builderRule(KEY_ABSENT, (c) => (c.app.authProvider.useCognito.enabled = false))
        ).toBeUndefined();
    });

    test("the rule is a warning, as getConfig() only warns", () => {
        expect(builderRule(KEY_ABSENT)?.severity).toBe("warning");
    });

    test("[control] the builder reports a Cognito warning at all", () => {
        // Without this, "stays silent" is satisfied by an evaluateRules() that returns nothing.
        const cfg = makeDefaultConfig("commercial");
        cfg.app.authProvider.useCognito.useUserPasswordAuthFlow = true;
        expect(evaluateRules(cfg).map((rule) => rule.id)).toContain("auth-userpassword-warn");
    });
});
