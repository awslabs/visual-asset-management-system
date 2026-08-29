/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The `getConfig()` rules governing how a Cognito user pool federates.
 *
 * A deployment picks exactly one of three shapes, and the default must stay the plain one:
 *
 * | `useCognito.enabled` | `useSaml` | `useOidc` | Result                                     |
 * | -------------------- | --------- | --------- | ------------------------------------------ |
 * | true                 | false     | false     | native Cognito username/password (default) |
 * | true                 | true      | false     | SAML federation through the hosted UI      |
 * | true                 | false     | true      | OIDC federation through the hosted UI      |
 * | true                 | true      | true      | rejected — one method or neither           |
 * | false                | either    | either    | accepted; both flags resolve to false      |
 *
 * Both federation methods need the Amazon Cognito hosted UI, which the restricted partitions do not
 * offer, so both are commercial-only. OIDC additionally requires real provider settings in
 * `oidc-config.ts`: the file ships with placeholders, and deploying those registers an identity
 * provider that cannot complete a login, failing at the IdP rather than during synth.
 *
 * `getConfig()` reads `config/config.json` from disk, so these tests mock `fs.readFileSync` to serve
 * a chosen template (see configPartitionValidation.test.ts for the same harness and its caveats).
 */

import * as fs from "fs";
import * as Config from "../config/config";
import commercialTemplate from "../config/config.template.commercial.json";
import govcloudTemplate from "../config/config.template.govcloud.json";
import eusovereignTemplate from "../config/config.template.eusovereign.json";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

// Real provider settings, standing in for a filled-out oidc-config.ts. The shipped file holds
// placeholders on purpose, so a test that wants the "valid OIDC" path has to supply them.
jest.mock("../config/oidc-config", () => ({
    oidcSettings: {
        name: "ExternalOIDC",
        displayName: "SSO",
        cognitoDomainPrefix: "vams-test",
        clientId: "test-client",
        clientSecretArn:
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams/oidc/client-secret",
        issuerUrl: "https://idp.test.example.com",
        scopes: ["openid", "email"],
        attributeMapping: {},
        manageDomain: true,
    },
}));

import { oidcSettings } from "../config/oidc-config";
import { newTestApp } from "./support/testApp";

const serveConfig = (configJson: unknown) => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (path: string, ...rest: unknown[]) => {
            if (typeof path === "string" && path.endsWith("config.json")) {
                return JSON.stringify(configJson);
            }
            return realReadFileSync(path, ...rest);
        }
    );
};

const templateFor = (base: unknown, region: string) => {
    const config = JSON.parse(JSON.stringify(base));
    config.env.region = region;
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    config.app.adminEmailAddress = "admin@example.com";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    return config;
};

/**
 * Turn Cognito off and configure the external OAuth IdP in its place.
 *
 * A deployment needs one authentication provider, and `getConfig()` separately requires every external
 * IdP field once that provider is enabled. Without filling them, disabling Cognito throws for THAT
 * reason and the assertion about the federation flags never gets exercised.
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

const loadConfig = (base: unknown, region: string, mutate?: (c: any) => void) => {
    const config = templateFor(base, region);
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

const BOTH_MESSAGE = /useCognito.useSaml and useCognito.useOidc cannot both be enabled/;
const OIDC_NEEDS_COGNITO = /useCognito.useOidc requires useCognito.enabled to be true/;
const SAML_NEEDS_COGNITO = /useCognito.useSaml requires useCognito.enabled to be true/;

describe("federation defaults", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("the shipped commercial config is plain Cognito", () => {
        // The default path every existing deployment is on; neither federation flag may creep on.
        const config = loadConfig(commercialTemplate, "us-east-1")();
        expect(config.app.authProvider.useCognito.enabled).toBe(true);
        expect(config.app.authProvider.useCognito.useSaml).toBe(false);
        expect(config.app.authProvider.useCognito.useOidc).toBe(false);
    });

    test("a config predating useOidc still loads, defaulting it to false", () => {
        // Backwards compatibility: an operator's existing config.json has no useOidc key at all.
        const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
            delete c.app.authProvider.useCognito.useOidc;
        })();
        expect(config.app.authProvider.useCognito.useOidc).toBe(false);
    });

    test("an absent defaultUserRoleName resolves to the empty string, which disables it", () => {
        // The backend treats "" as "no default role", so the resolved value must not become
        // undefined (which would be written to the Lambda env var as the string "undefined").
        const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
            delete c.app.authProvider.authorizerOptions.defaultUserRoleName;
        })();
        expect(config.app.authProvider.authorizerOptions.defaultUserRoleName).toBe("");
    });
});

describe("SAML and OIDC are mutually exclusive", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("rejects both federation methods at once", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.authProvider.useCognito.useSaml = true;
            c.app.authProvider.useCognito.useOidc = true;
        });
        expect(run).toThrow(BOTH_MESSAGE);
    });

    test("accepts OIDC alone", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.authProvider.useCognito.useOidc = true;
        });
        expect(run).not.toThrow();
    });

    test("accepts SAML alone", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.authProvider.useCognito.useSaml = true;
        });
        expect(run).not.toThrow(BOTH_MESSAGE);
    });
});

describe("the federation flags are ignored when Cognito is disabled", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    // useSaml/useOidc federate the Cognito USER POOL, so without the pool they describe nothing.
    // getConfig() resolves them to false rather than rejecting the config, and the normalization is the
    // load-bearing half: the static web builder and the API layer read these flags WITHOUT re-checking
    // useCognito.enabled, so a stale `true` would configure a user pool client that does not exist and
    // publish a federated login screen with no pool behind it.
    test("useOidc is accepted and resolved to false", () => {
        const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
            withoutCognito(c);
            c.app.authProvider.useCognito.useOidc = true;
        })();
        expect(config.app.authProvider.useCognito.useOidc).toBe(false);
    });

    test("useSaml is accepted and resolved to false", () => {
        const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
            withoutCognito(c);
            c.app.authProvider.useCognito.useSaml = true;
        })();
        expect(config.app.authProvider.useCognito.useSaml).toBe(false);
    });

    test("both flags together are accepted and resolved to false", () => {
        // The mutual-exclusion rule must not fire either — with no user pool there is nothing to be
        // ambiguous about.
        const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
            withoutCognito(c);
            c.app.authProvider.useCognito.useSaml = true;
            c.app.authProvider.useCognito.useOidc = true;
        })();
        expect(config.app.authProvider.useCognito.useSaml).toBe(false);
        expect(config.app.authProvider.useCognito.useOidc).toBe(false);
    });

    test("a restricted partition does not reject an ignored federation flag", () => {
        // The partition checks exist because the hosted UI is unavailable there; with Cognito disabled
        // no hosted UI is created, so the flag is irrelevant rather than invalid.
        const config = loadConfig(govcloudTemplate, "us-gov-west-1", (c) => {
            withoutCognito(c);
            c.app.authProvider.useCognito.useOidc = true;
        })();
        expect(config.app.authProvider.useCognito.useOidc).toBe(false);
    });

    test("placeholder OIDC provider settings do not reject the config either", () => {
        jest.replaceProperty(oidcSettings as any, "issuerUrl", "https://your-idp.example.com");
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            withoutCognito(c);
            c.app.authProvider.useCognito.useOidc = true;
        });
        expect(run).not.toThrow();
        jest.replaceProperty(oidcSettings as any, "issuerUrl", "https://idp.test.example.com");
    });
});

describe("federation is commercial-partition only", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("rejects OIDC in GovCloud", () => {
        const run = loadConfig(govcloudTemplate, "us-gov-west-1", (c) => {
            c.app.authProvider.useCognito.enabled = true;
            c.app.authProvider.useCognito.useOidc = true;
        });
        expect(run).toThrow(/useCognito.useOidc is not supported in the 'aws-us-gov' partition/);
    });

    test("rejects OIDC in the EU Sovereign Cloud", () => {
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c) => {
            c.app.authProvider.useCognito.enabled = true;
            c.app.authProvider.useCognito.useOidc = true;
        });
        expect(run).toThrow(/useCognito.useOidc is not supported in the 'aws-eusc' partition/);
    });
});

describe("OIDC provider settings are validated before deploy", () => {
    const enableOidc = (c: any) => {
        c.app.authProvider.useCognito.useOidc = true;
    };

    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
        jest.replaceProperty(oidcSettings as any, "issuerUrl", "https://idp.test.example.com");
        jest.replaceProperty(
            oidcSettings as any,
            "clientSecretArn",
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams/oidc/client-secret"
        );
        jest.replaceProperty(oidcSettings as any, "scopes", ["openid", "email"]);
        jest.replaceProperty(oidcSettings as any, "clientId", "test-client");
    });

    test("rejects the shipped placeholder issuer URL", () => {
        jest.replaceProperty(oidcSettings as any, "issuerUrl", "https://your-idp.example.com");
        expect(loadConfig(commercialTemplate, "us-east-1", enableOidc)).toThrow(
            /still holds placeholder values/
        );
    });

    test("rejects the shipped placeholder secret ARN", () => {
        jest.replaceProperty(
            oidcSettings as any,
            "clientSecretArn",
            "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:vams/oidc/client-secret"
        );
        expect(loadConfig(commercialTemplate, "us-east-1", enableOidc)).toThrow(
            /still holds placeholder values/
        );
    });

    test("rejects an empty required field", () => {
        jest.replaceProperty(oidcSettings as any, "clientId", "");
        expect(loadConfig(commercialTemplate, "us-east-1", enableOidc)).toThrow(
            /requires oidcSettings.clientId to be set/
        );
    });

    test("rejects a non-https issuer URL", () => {
        jest.replaceProperty(oidcSettings as any, "issuerUrl", "http://idp.test.example.com");
        expect(loadConfig(commercialTemplate, "us-east-1", enableOidc)).toThrow(
            /issuerUrl must be an https URL/
        );
    });

    test("rejects scopes without openid", () => {
        jest.replaceProperty(oidcSettings as any, "scopes", ["email", "profile"]);
        expect(loadConfig(commercialTemplate, "us-east-1", enableOidc)).toThrow(
            /must include the "openid" scope/
        );
    });

    test("none of these checks run when OIDC is disabled", () => {
        // A deployment that never enables OIDC must not be blocked by the placeholder file.
        jest.replaceProperty(oidcSettings as any, "issuerUrl", "https://your-idp.example.com");
        expect(loadConfig(commercialTemplate, "us-east-1")).not.toThrow();
    });
});
