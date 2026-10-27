/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * With external OAuth enabled, `getConfig()` treats null, "" and "UNDEFINED" as unset for the fields
 * VAMS passes to the authorizer and the web client, so a template null fails at synth instead of in
 * the authorizer or the browser. The MFA scope, principal domain and discovery endpoint are optional:
 * only "" and "UNDEFINED" are rejected for them. The ConfigBuilder's `oauth-fields` rule applies the
 * same split and states it in the same words as the `getConfig()` error, and the configuration
 * reference repeats that sentence and marks the three optional fields.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import {
    evaluateRules,
    RULES,
} from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const EXTERNAL_IDP_RULE =
    "External OAuth IdP requires its provider URL, client ID, scope, token and authorization endpoints, JWT issuer URL and audience. The MFA scope, principal domain and discovery endpoint may be left null, but not empty or UNDEFINED.";
const EXTERNAL_IDP_ERROR = `Configuration Error: ${EXTERNAL_IDP_RULE}`;

const REQUIRED = [
    "idpAuthProviderUrl",
    "idpAuthClientId",
    "idpAuthProviderScope",
    "idpAuthProviderTokenEndpoint",
    "idpAuthProviderAuthorizationEndpoint",
    "lambdaAuthorizorJWTIssuerUrl",
    "lambdaAuthorizorJWTAudience",
];
const OPTIONAL = [
    "idpAuthProviderScopeMfa",
    "idpAuthPrincipalDomain",
    "idpAuthProviderDiscoveryEndpoint",
];

/** The commercial template switched to a complete external OAuth configuration, then `mutate`. */
function externalOAuthConfig(mutate: (idp: any) => void = () => undefined): any {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    config.app.authProvider.useCognito.enabled = false;
    const idp = config.app.authProvider.useExternalOAuthIdp;
    Object.assign(idp, {
        enabled: true,
        idpAuthProviderUrl: "https://idp.example.com",
        idpAuthClientId: "client-id",
        idpAuthProviderScope: "openid",
        idpAuthProviderScopeMfa: null,
        idpAuthPrincipalDomain: null,
        idpAuthProviderTokenEndpoint: "/oauth2/token",
        idpAuthProviderAuthorizationEndpoint: "/oauth2/authorize",
        idpAuthProviderDiscoveryEndpoint: null,
        lambdaAuthorizorJWTIssuerUrl: "https://idp.example.com",
        lambdaAuthorizorJWTAudience: "vams",
    });
    mutate(idp);
    return config;
}

function resolve(config: any): () => Config.Config {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

const builderFlags = (config: any) => evaluateRules(config).some((r) => r.id === "oauth-fields");

describe("external OAuth required fields", () => {
    test("getConfig() and the ConfigBuilder state the rule in the same words", () => {
        const rule = RULES.find((r) => r.id === "oauth-fields");
        expect(rule?.message).toBe(EXTERNAL_IDP_RULE);
        const config = externalOAuthConfig((idp) => {
            idp.idpAuthClientId = null;
        });
        expect(resolve(config)).toThrow(new Error(EXTERNAL_IDP_ERROR));
    });

    test("the configuration reference states the rule and marks the optional fields", () => {
        const reference: string = realReadFileSync(
            path.join(
                __dirname,
                "../../../documentation/docusaurus-site/docs/deployment/configuration-reference.md"
            ),
            "utf-8"
        );
        const lines = reference.split("\n");
        const rowFor = (field: string) =>
            lines.find((line) =>
                line.startsWith(`| \`app.authProvider.useExternalOAuthIdp.${field}\``)
            ) ?? "";
        const warningStart = lines.findIndex((line) => line.startsWith(":::warning[Required"));
        const warningEnd = lines.indexOf(":::", warningStart + 1);
        const warning = lines.slice(warningStart, warningEnd).join("\n");

        expect(warningStart).toBeGreaterThan(-1);
        expect(warning).toContain(EXTERNAL_IDP_RULE);
        for (const field of REQUIRED) {
            expect(warning).toContain(`\`${field}\``);
            expect(rowFor(field)).not.toBe("");
            expect(rowFor(field)).not.toContain("Optional.");
        }
        for (const field of OPTIONAL) {
            expect(warning).toContain(`\`${field}\``);
            expect(rowFor(field)).toMatch(/\| `null` +\| Optional\. /);
        }
    });

    test("a complete configuration with the optional fields null is accepted (control)", () => {
        expect(resolve(externalOAuthConfig())).not.toThrow();
        expect(builderFlags(externalOAuthConfig())).toBe(false);
    });

    test.each(REQUIRED)("a null %s is rejected by getConfig() and the ConfigBuilder", (field) => {
        const config = externalOAuthConfig((idp) => {
            idp[field] = null;
        });
        expect(resolve(config)).toThrow(EXTERNAL_IDP_ERROR);
        expect(builderFlags(config)).toBe(true);
    });

    test.each(OPTIONAL)("an empty or UNDEFINED %s is still rejected", (field) => {
        for (const value of ["", "UNDEFINED"]) {
            const config = externalOAuthConfig((idp) => {
                idp[field] = value;
            });
            expect(resolve(config)).toThrow(EXTERNAL_IDP_ERROR);
            expect(builderFlags(config)).toBe(true);
        }
    });
});
