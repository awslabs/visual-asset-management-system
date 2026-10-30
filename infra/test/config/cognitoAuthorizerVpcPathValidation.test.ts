/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The `getConfig()` rule for an API Gateway authorizer that has no path to Amazon Cognito.
 *
 * With `useGlobalVpc.useForAllLambdas` the authorizer runs in the VPC's isolated subnets and verifies
 * every Amazon Cognito token against the user pool's JSON Web Key Set on `cognito-idp.<region>`. In a VPC
 * that VAMS creates those subnets have no route out of the VPC, so the Amazon Cognito user pools
 * interface endpoint is the authorizer's only path. VAMS creates it where it can (see
 * `test/vpc/cognitoUserPoolsEndpoint.test.ts`). Where it cannot (a partition without Amazon Cognito
 * PrivateLink, or a user pool with a SAML or OIDC domain) the deployment would succeed and then time out
 * on every signed-in request, so `getConfig()` rejects it. An imported VPC's subnets may have their own
 * route, so the same combination there, and operator-managed endpoints, are warned about instead.
 *
 * The ConfigBuilder's hand-ported rules are asserted against the same arms, because
 * `configBuilderSync.test.ts` covers only `schema.ts` and `defaults.ts`.
 *
 * `getConfig()` reads `config/config.json` from disk, so these tests mock `fs.readFileSync` to serve a
 * chosen template, as `configPartitionValidation.test.ts` does.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import { newTestApp } from "../support/testApp";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Serve `configJson` for config.json; delegate every other path to the real fs. */
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

/** A deployable config derived from a template, with every Lambda in the VPC. */
const allLambdasInVpc = (base: unknown, region: string, mutate?: (c: any) => void) => {
    const config = JSON.parse(JSON.stringify(base));
    config.env.region = region;
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = true;
    config.app.useGlobalVpc.addVpcEndpoints = true;
    // A Lambda-in-VPC deployment rejects a public Serverless collection (an unrelated rule).
    config.app.openSearch.useServerless.allowPublic = false;
    mutate?.(config);
    return config;
};

/** Runs getConfig() on the config; returns the thrown error message (or "") and the warnings. */
const runGetConfig = (config: unknown): { error: string; warnings: string[] } => {
    serveConfig(config);
    const warnings: string[] = [];
    const spy = jest.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
        warnings.push(args.map(String).join(" "));
    });
    try {
        Config.getConfig(newTestApp());
        return { error: "", warnings };
    } catch (e) {
        return { error: (e as Error).message, warnings };
    } finally {
        spy.mockRestore();
    }
};

const NO_PATH_ERROR = /its only path to Amazon Cognito is an interface endpoint/;
const NEEDS_PATH_WARNING = /VAMS does not create the Amazon Cognito user pools interface endpoint/;

const IMPORTED_VPC = (c: any) => {
    c.app.useGlobalVpc.optionalExternalVpcId = "vpc-0123456789abcdef0";
    c.app.useGlobalVpc.optionalExternalIsolatedSubnetIds = "subnet-0aaa,subnet-0bbb";
};

describe("an in-VPC authorizer with no path to Amazon Cognito is rejected", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("accepts the configuration VAMS creates the user pools endpoint for", () => {
        // Commercial partition, native Amazon Cognito sign-in, VAMS-created VPC and endpoints: the
        // configuration of the reported defect, which the endpoint now serves.
        const { error, warnings } = runGetConfig(allLambdasInVpc(commercialTemplate, "us-east-1"));
        expect(error).toBe("");
        expect(warnings.filter((w) => NEEDS_PATH_WARNING.test(w))).toEqual([]);
    });

    test.each([
        ["us-gov-west-1", "aws-us-gov", govcloudTemplate],
        ["eusc-de-east-1", "aws-eusc", eusovereignTemplate],
    ])("rejects %s (%s), which has no Amazon Cognito PrivateLink", (region, partition, base) => {
        const { error } = runGetConfig(allLambdasInVpc(base, region));
        expect(error).toMatch(NO_PATH_ERROR);
        expect(error).toContain(`not available in the '${partition}' partition`);
    });

    test.each([
        ["SAML", (c: any) => (c.app.authProvider.useCognito.useSaml = true)],
        ["OIDC", (c: any) => (c.app.authProvider.useCognito.useOidc = true)],
    ])(
        "rejects a user pool with %s federation, whose domain the endpoint cannot serve",
        (_n, fed) => {
            const { error } = runGetConfig(allLambdasInVpc(commercialTemplate, "us-east-1", fed));
            expect(error).toMatch(NO_PATH_ERROR);
            expect(error).toContain("SAML or OIDC federation has a domain");
        }
    );

    test.each([
        ["us-gov-west-1", govcloudTemplate],
        ["eusc-de-east-1", eusovereignTemplate],
    ])(
        "accepts the shipped %s template, which keeps Lambda functions outside the VPC",
        (region, base) => {
            // Control: the rule is keyed on useForAllLambdas, not on the partition alone.
            const config = allLambdasInVpc(base, region, (c) => {
                c.app.useGlobalVpc.useForAllLambdas = (
                    base as any
                ).app.useGlobalVpc.useForAllLambdas;
            });
            expect(config.app.useGlobalVpc.useForAllLambdas).toBe(false);
            expect(runGetConfig(config).error).toBe("");
        }
    );

    test("accepts an external OAuth identity provider, which the rule does not cover", () => {
        const { error } = runGetConfig(
            allLambdasInVpc(govcloudTemplate, "us-gov-west-1", (c) => {
                c.app.authProvider.useCognito.enabled = false;
                const idp = c.app.authProvider.useExternalOAuthIdp;
                idp.enabled = true;
                idp.idpAuthProviderUrl = "https://idp.example.com";
                idp.idpAuthClientId = "vams-client";
                idp.idpAuthProviderScope = "openid";
                idp.idpAuthProviderTokenEndpoint = "/oauth2/token";
                idp.idpAuthProviderAuthorizationEndpoint = "/oauth2/authorize";
                idp.lambdaAuthorizorJWTIssuerUrl = "https://idp.example.com";
                idp.lambdaAuthorizorJWTAudience = "vams-client";
            })
        );
        expect(error).toBe("");
    });
});

describe("an in-VPC authorizer that relies on a path VAMS does not create is warned about", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("an imported VPC in a partition without Amazon Cognito PrivateLink warns, not rejects", () => {
        const { error, warnings } = runGetConfig(
            allLambdasInVpc(govcloudTemplate, "us-gov-west-1", IMPORTED_VPC)
        );
        expect(error).toBe("");
        const matched = warnings.filter((w) => NEEDS_PATH_WARNING.test(w));
        expect(matched).toHaveLength(1);
        expect(matched[0]).toContain("'aws-us-gov' partition");
    });

    test("operator-managed endpoints in a VPC VAMS creates warn, not reject", () => {
        const { error, warnings } = runGetConfig(
            allLambdasInVpc(commercialTemplate, "us-east-1", (c) => {
                c.app.useGlobalVpc.addVpcEndpoints = false;
            })
        );
        expect(error).toBe("");
        const matched = warnings.filter((w) => NEEDS_PATH_WARNING.test(w));
        expect(matched).toHaveLength(1);
        expect(matched[0]).toContain("addVpcEndpoints is false");
    });

    test("Lambda functions outside the VPC neither warn nor reject", () => {
        const { error, warnings } = runGetConfig(
            allLambdasInVpc(commercialTemplate, "us-east-1", (c) => {
                c.app.useGlobalVpc.useForAllLambdas = false;
                c.app.useGlobalVpc.addVpcEndpoints = false;
            })
        );
        expect(error).toBe("");
        expect(warnings.filter((w) => NEEDS_PATH_WARNING.test(w))).toEqual([]);
    });
});

const ERROR_RULE_ID = "vpc-all-lambdas-cognito-no-endpoint";
const WARNING_RULE_ID = "vpc-all-lambdas-cognito-needs-path-warn";

/** The ConfigBuilder's Amazon Cognito path rules for a preset in `region`. */
const builderRuleIds = (
    profile: "commercial" | "govcloud" | "eusovereign",
    region: string | null,
    mutate?: (c: any) => void
): string[] => {
    const cfg = makeDefaultConfig(profile) as any;
    cfg.env.region = region;
    cfg.app.authProvider.useCognito.enabled = true;
    cfg.app.useGlobalVpc.enabled = true;
    cfg.app.useGlobalVpc.useForAllLambdas = true;
    cfg.app.useGlobalVpc.addVpcEndpoints = true;
    mutate?.(cfg);
    return evaluateRules(cfg)
        .map((rule) => rule.id)
        .filter((id) => id === ERROR_RULE_ID || id === WARNING_RULE_ID);
};

describe("ConfigBuilder mirror of the Amazon Cognito path rules", () => {
    test.each<
        [
            string,
            "commercial" | "govcloud" | "eusovereign",
            string | null,
            (c: any) => void,
            string[]
        ]
    >([
        ["commercial, VAMS-created endpoint", "commercial", "us-east-1", () => undefined, []],
        ["GovCloud", "govcloud", "us-gov-west-1", () => undefined, [ERROR_RULE_ID]],
        ["EU Sovereign Cloud", "eusovereign", "eusc-de-east-1", () => undefined, [ERROR_RULE_ID]],
        [
            "SAML federation",
            "commercial",
            "us-east-1",
            (c) => (c.app.authProvider.useCognito.useSaml = true),
            [ERROR_RULE_ID],
        ],
        ["GovCloud, imported VPC", "govcloud", "us-gov-west-1", IMPORTED_VPC, [WARNING_RULE_ID]],
        [
            "commercial, operator-managed endpoints",
            "commercial",
            "us-east-1",
            (c) => (c.app.useGlobalVpc.addVpcEndpoints = false),
            [WARNING_RULE_ID],
        ],
        [
            "Lambda functions outside the VPC",
            "govcloud",
            "us-gov-west-1",
            (c) => (c.app.useGlobalVpc.useForAllLambdas = false),
            [],
        ],
        ["region unset", "commercial", null, () => undefined, []],
    ])("%s", (_name, profile, region, mutate, expected) => {
        expect(builderRuleIds(profile, region, mutate)).toEqual(expected);
    });
});
