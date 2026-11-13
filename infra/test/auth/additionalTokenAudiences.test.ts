/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * app.authProvider.<provider>.additionalTokenAudiences: extra token audiences the API
 * authorizer accepts alongside the web app client (Cognito) or lambdaAuthorizorJWTAudience (external
 * IdP), for example an app client registered for a remote MCP server.
 *
 * Pinned here:
 * - getConfig() defaults an absent list to empty, so an existing config.json keeps working;
 * - a list under a disabled provider is ignored with a warning and normalized to empty;
 * - an invalid list (not an array, too long, blank, padded, wildcard, duplicate, a Cognito entry that
 *   is not an app client id, an external entry repeating the primary audience) is rejected at synth,
 *   and the ConfigBuilder flags the same configurations;
 * - the authorizer function gets ADDITIONAL_APP_CLIENT_IDS / JWT_ADDITIONAL_AUDIENCES ONLY when a list
 *   is set, so a deployment without the option synthesizes the authorizer exactly as before.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { newTestApp } from "../support/testApp";
import { lambdaByHandler, synthTemplate } from "../support/templateSynth";

jest.setTimeout(900_000);

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const AUTHORIZER_HANDLER = "handlers.auth.apiGatewayAuthorizerRest.lambda_handler";
const MCP_CLIENT = "4abcdefghijklmnopqrstuvw12";

function baseConfig(mutate: (c: any) => void = () => undefined): any {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    return config;
}

function externalConfig(mutate: (idp: any) => void = () => undefined): any {
    return baseConfig((config) => {
        config.app.authProvider.useCognito.enabled = false;
        const idp = config.app.authProvider.useExternalOAuthIdp;
        Object.assign(idp, {
            enabled: true,
            idpAuthProviderUrl: "https://idp.example.com",
            idpAuthClientId: "client-id",
            idpAuthProviderScope: "openid",
            idpAuthProviderTokenEndpoint: "/oauth2/token",
            idpAuthProviderAuthorizationEndpoint: "/oauth2/authorize",
            lambdaAuthorizorJWTIssuerUrl: "https://idp.example.com",
            lambdaAuthorizorJWTAudience: "vams",
        });
        mutate(idp);
    });
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

const builderErrors = (config: any, provider: string) =>
    evaluateRules(config).filter(
        (r) => r.id.startsWith(`${provider}-additional-token-audiences`) && r.severity === "error"
    );

afterEach(() => {
    jest.restoreAllMocks();
});

describe("additionalTokenAudiences configuration", () => {
    test("an absent list defaults to empty for both providers", () => {
        const config = baseConfig((c) => {
            delete c.app.authProvider.useCognito.additionalTokenAudiences;
            delete c.app.authProvider.useExternalOAuthIdp.additionalTokenAudiences;
        });
        const resolved = resolve(config)();
        expect(resolved.app.authProvider.useCognito.additionalTokenAudiences).toEqual([]);
        expect(resolved.app.authProvider.useExternalOAuthIdp.additionalTokenAudiences).toEqual([]);
    });

    test("a valid Cognito list is kept", () => {
        const config = baseConfig((c) => {
            c.app.authProvider.useCognito.additionalTokenAudiences = [MCP_CLIENT];
        });
        expect(resolve(config)().app.authProvider.useCognito.additionalTokenAudiences).toEqual([
            MCP_CLIENT,
        ]);
        expect(builderErrors(config, "cognito")).toEqual([]);
    });

    test("a valid external list is kept", () => {
        const config = externalConfig((idp) => {
            idp.additionalTokenAudiences = ["vams-mcp", "https://mcp.example.com"];
        });
        expect(
            resolve(config)().app.authProvider.useExternalOAuthIdp.additionalTokenAudiences
        ).toEqual(["vams-mcp", "https://mcp.example.com"]);
        expect(builderErrors(config, "external")).toEqual([]);
    });

    test("a list under a disabled provider is ignored with a warning", () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        const config = baseConfig((c) => {
            c.app.authProvider.useExternalOAuthIdp.additionalTokenAudiences = ["vams-mcp"];
        });
        const resolved = resolve(config)();
        expect(resolved.app.authProvider.useExternalOAuthIdp.additionalTokenAudiences).toEqual([]);
        expect(
            warn.mock.calls.some((args) =>
                String(args[0]).includes(
                    "useExternalOAuthIdp.additionalTokenAudiences is ignored because useExternalOAuthIdp.enabled is false"
                )
            )
        ).toBe(true);
        expect(
            evaluateRules(config).some(
                (r) => r.id === "external-additional-token-audiences-ignored-warn"
            )
        ).toBe(true);
    });

    test.each([
        ["not an array", "abc"],
        ["a blank entry", [""]],
        ["a padded entry", [` ${MCP_CLIENT}`]],
        ["a wildcard", ["*"]],
        ["a partial wildcard", ["4abc*"]],
        ["a duplicate", [MCP_CLIENT, MCP_CLIENT]],
        ["a non-client-id", ["https://mcp.example.com"]],
        ["more than ten", Array.from({ length: 11 }, (_, i) => `client${i}`)],
    ])("a Cognito list with %s is rejected", (_label, value) => {
        const config = baseConfig((c) => {
            c.app.authProvider.useCognito.additionalTokenAudiences = value;
        });
        expect(resolve(config)).toThrow(
            /Configuration Error: app\.authProvider\.useCognito\.additionalTokenAudiences/
        );
        expect(builderErrors(config, "cognito").length).toBeGreaterThan(0);
    });

    test.each([
        ["a blank entry", ["  "]],
        ["a wildcard", ["vams-*"]],
        ["a duplicate", ["vams-mcp", "vams-mcp"]],
        ["the primary audience", ["vams"]],
    ])("an external list with %s is rejected", (_label, value) => {
        const config = externalConfig((idp) => {
            idp.additionalTokenAudiences = value;
        });
        expect(resolve(config)).toThrow(
            /Configuration Error: app\.authProvider\.useExternalOAuthIdp\.additionalTokenAudiences/
        );
        expect(builderErrors(config, "external").length).toBeGreaterThan(0);
    });
});

describe("authorizer environment", () => {
    test("the default commercial synth sets no additional-audience variable", () => {
        const env = lambdaByHandler(synthTemplate("commercial"), AUTHORIZER_HANDLER).properties
            .Environment.Variables;
        expect(env.ADDITIONAL_APP_CLIENT_IDS).toBeUndefined();
        expect(env.JWT_ADDITIONAL_AUDIENCES).toBeUndefined();
        expect(env.APP_CLIENT_ID).toBeDefined();
    });

    test("the shipped templates list empty audiences, which emit no variable", () => {
        expect(commercialTemplate.app.authProvider.useCognito.additionalTokenAudiences).toEqual([]);
        expect(
            commercialTemplate.app.authProvider.useExternalOAuthIdp.additionalTokenAudiences
        ).toEqual([]);
        const s = synthTemplate("commercial", {
            mutate: (c) => {
                c.app.authProvider.useCognito.additionalTokenAudiences = [];
                c.app.authProvider.useExternalOAuthIdp.additionalTokenAudiences = [];
            },
            mutateKey: "explicit-empty-additional-token-audiences",
        });
        const env = lambdaByHandler(s, AUTHORIZER_HANDLER).properties.Environment.Variables;
        expect(env.ADDITIONAL_APP_CLIENT_IDS).toBeUndefined();
        expect(env.JWT_ADDITIONAL_AUDIENCES).toBeUndefined();
    });

    test("a configured Cognito list reaches the authorizer as JSON", () => {
        const s = synthTemplate("commercial", {
            mutate: (c) => {
                c.app.authProvider.useCognito.additionalTokenAudiences = [MCP_CLIENT];
            },
            mutateKey: "cognito-additional-token-audiences",
        });
        const env = lambdaByHandler(s, AUTHORIZER_HANDLER).properties.Environment.Variables;
        expect(JSON.parse(env.ADDITIONAL_APP_CLIENT_IDS)).toEqual([MCP_CLIENT]);
        expect(env.JWT_ADDITIONAL_AUDIENCES).toBeUndefined();
    });

    test("a configured external list reaches the authorizer as JSON", () => {
        const s = synthTemplate("commercial", {
            mutate: (c) => {
                c.app.authProvider.useCognito.enabled = false;
                Object.assign(c.app.authProvider.useExternalOAuthIdp, {
                    enabled: true,
                    idpAuthProviderUrl: "https://idp.example.com",
                    idpAuthClientId: "client-id",
                    idpAuthProviderScope: "openid",
                    idpAuthProviderTokenEndpoint: "/oauth2/token",
                    idpAuthProviderAuthorizationEndpoint: "/oauth2/authorize",
                    lambdaAuthorizorJWTIssuerUrl: "https://idp.example.com",
                    lambdaAuthorizorJWTAudience: "vams",
                    additionalTokenAudiences: ["vams-mcp"],
                });
            },
            mutateKey: "external-additional-token-audiences",
        });
        const env = lambdaByHandler(s, AUTHORIZER_HANDLER).properties.Environment.Variables;
        expect(env.JWT_AUDIENCE).toBe("vams");
        expect(JSON.parse(env.JWT_ADDITIONAL_AUDIENCES)).toEqual(["vams-mcp"]);
        expect(env.ADDITIONAL_APP_CLIENT_IDS).toBeUndefined();
    });
});
