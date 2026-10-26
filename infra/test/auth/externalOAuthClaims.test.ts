/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The two external OAuth claim settings, from `config.json` to the two places that read them
 * (issues #788 and #816):
 *
 * - `useExternalOAuthIdp.lambdaAuthorizorJWTAudienceClaims` (default `["aud"]`): the token claims the
 *   authorizer compares with `lambdaAuthorizorJWTAudience`, in order. Reaches the authorizer as
 *   `JWT_AUDIENCE_CLAIMS` (a JSON array).
 * - `useExternalOAuthIdp.lambdaAuthorizorJWTUserIdClaim` (default `""` = not set): the claim whose
 *   value is the VAMS user id. Reaches the authorizer as `JWT_USER_ID_CLAIM` and the web application
 *   as `externalOAuthIdpUserIdClaim` in `GET /api/amplify-config` (`"undefined"` when not set, the
 *   convention of the other `externalOAuthIdp*` fields), so both resolve the same user id.
 *
 * `getConfig()` fills both defaults when a config predates them, validates them only in external
 * OAuth mode, and does not count them among the external IdP fields that are required.
 *
 * Durable (root CLAUDE.md Rule 13).
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import { Template } from "aws-cdk-lib/assertions";
import * as fs from "fs";
import { runInNewContext } from "vm";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import { buildApiGatewayAuthorizerRestFunction } from "../../lib/lambdaBuilder/authFunctions";
import { AmplifyConfigLambdaConstruct } from "../../lib/nestedStacks/apiLambda/constructs/amplify-config-lambda-construct";
import { authResources } from "../../lib/nestedStacks/auth/authBuilder-nestedStack";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

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

/** External OAuth in place of Cognito, with every field getConfig() requires. */
const withExternalOAuth = (c: any) => {
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
    idp.lambdaAuthorizorJWTAudience = "test-client";
};

const loadConfig = (base: unknown, region: string, mutate?: (c: any) => void) => {
    const config = templateFor(base, region);
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

/** Back to the real readFileSync: the synth tests below read assets and templates from disk. */
const restoreReadFileSync = () => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
};

const idpOf = (c: any) => c.app.authProvider.useExternalOAuthIdp;

const USER_ID_CLAIM_ERROR = /lambdaAuthorizorJWTUserIdClaim/;
const AUDIENCE_CLAIMS_ERROR = /lambdaAuthorizorJWTAudienceClaims/;

describe.each([
    ["commercial", commercialTemplate, "us-east-1"],
    ["govcloud", govcloudTemplate, "us-gov-west-1"],
    ["eusovereign", eusovereignTemplate, "eusc-de-east-1"],
] as const)("%s template: external OAuth claim settings in getConfig()", (_name, raw, region) => {
    afterEach(restoreReadFileSync);

    test("the template ships both settings at their defaults", () => {
        const idp = (raw as any).app.authProvider.useExternalOAuthIdp;
        expect(idp.lambdaAuthorizorJWTUserIdClaim).toBe("");
        expect(idp.lambdaAuthorizorJWTAudienceClaims).toEqual(["aud"]);
    });

    test("a config that predates both settings loads with the defaults and is not refused", () => {
        const config = loadConfig(raw, region, (c) => {
            withExternalOAuth(c);
            delete idpOf(c).lambdaAuthorizorJWTUserIdClaim;
            delete idpOf(c).lambdaAuthorizorJWTAudienceClaims;
        })();
        expect(idpOf(config).lambdaAuthorizorJWTUserIdClaim).toBe("");
        expect(idpOf(config).lambdaAuthorizorJWTAudienceClaims).toEqual(["aud"]);
    });
});

describe("getConfig() validation of the external OAuth claim settings", () => {
    afterEach(restoreReadFileSync);

    const withSettings = (userIdClaim: unknown, audienceClaims: unknown) =>
        loadConfig(commercialTemplate, "us-east-1", (c) => {
            withExternalOAuth(c);
            idpOf(c).lambdaAuthorizorJWTUserIdClaim = userIdClaim;
            idpOf(c).lambdaAuthorizorJWTAudienceClaims = audienceClaims;
        });

    test.each(["", "username", "cognito:username", "email", "custom:employee.id", "oid"])(
        "user id claim %p is accepted",
        (claim) => {
            expect(idpOf(withSettings(claim, ["aud"])()).lambdaAuthorizorJWTUserIdClaim).toBe(
                claim
            );
        }
    );

    test.each([["user name"], ["user/name"], ["   "], [7], [["email"]]])(
        "user id claim %p is rejected",
        (claim) => {
            expect(withSettings(claim, ["aud"])).toThrow(USER_ID_CLAIM_ERROR);
        }
    );

    test.each([[["aud"]], [["aud", "client_id"]], [["client_id"]], [["client_id", "aud"]]])(
        "audience claims %p are accepted",
        (claims) => {
            expect(idpOf(withSettings("", claims)()).lambdaAuthorizorJWTAudienceClaims).toEqual(
                claims
            );
        }
    );

    test.each([[[]], [["azp"]], [["aud", "aud"]], ["aud"], [[1]], [["AUD"]]])(
        "audience claims %p are rejected",
        (claims) => {
            expect(withSettings("", claims)).toThrow(AUDIENCE_CLAIMS_ERROR);
        }
    );

    test("null is not set, like an absent field: both settings take their defaults", () => {
        // getConfig()'s `== undefined` backfills treat null as absent, which is how the
        // ConfigBuilder serializes an unset field.
        const idp = idpOf(withSettings(null, null)());
        expect(idp.lambdaAuthorizorJWTUserIdClaim).toBe("");
        expect(idp.lambdaAuthorizorJWTAudienceClaims).toEqual(["aud"]);
    });

    test("Cognito mode ignores both settings", () => {
        // The shipped commercial config is Cognito; invalid values in the disabled section do not
        // stop it, since nothing reads them.
        const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
            idpOf(c).lambdaAuthorizorJWTUserIdClaim = "user name";
            idpOf(c).lambdaAuthorizorJWTAudienceClaims = ["azp"];
        })();
        expect(config.app.authProvider.useCognito.enabled).toBe(true);
    });
});

/* -------------------------------------------------------------------------------------------------
 * The authorizer environment
 * ---------------------------------------------------------------------------------------------- */

const synthConfig = (mutate?: (c: any) => void): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test";
    config.app.baseStackName = "vams-test";
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test/resourceNames";
    mutate?.(config);
    return config;
};

const buildStorageResources = (stack: cdk.Stack): storageResources => {
    const table = (id: string) =>
        new dynamodb.Table(stack, id, {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        });
    const logGroup = (id: string) => new logs.LogGroup(stack, id);
    return {
        encryption: {},
        cloudWatchAuditLogGroups: {
            authentication: logGroup("AuditAuthentication"),
            authorization: logGroup("AuditAuthorization"),
            fileUpload: logGroup("AuditFileUpload"),
            fileDownload: logGroup("AuditFileDownload"),
            fileDownloadStreamed: logGroup("AuditFileDownloadStreamed"),
            authOther: logGroup("AuditAuthOther"),
            authChanges: logGroup("AuditAuthChanges"),
            actions: logGroup("AuditActions"),
            errors: logGroup("AuditErrors"),
        },
        dynamo: {
            apiKeyStorageTable: table("ApiKeyTable"),
            constraintsStorageTable: table("ConstraintsTable"),
            userRolesStorageTable: table("UserRolesTable"),
            rolesStorageTable: table("RolesTable"),
        },
    } as unknown as storageResources;
};

/** The REST authorizer's environment for a config. */
const authorizerEnvironment = (config: Config.Config): Record<string, unknown> => {
    Service.SetConfig(config);
    const stack = new cdk.Stack(newTestApp(), "AuthorizerEnv", {
        env: { account: config.env.account, region: config.env.region },
    });
    const layer = lambda.LayerVersion.fromLayerVersionArn(
        stack,
        "AuthorizerLayer",
        "arn:aws:lambda:us-east-1:123456789012:layer:vams-authorizer:1"
    ) as lambda.LayerVersion;
    buildApiGatewayAuthorizerRestFunction(
        stack,
        layer,
        buildStorageResources(stack),
        config,
        undefined as unknown as ec2.IVpc,
        []
    );
    const functions = Object.values(
        Template.fromStack(stack).findResources("AWS::Lambda::Function", {
            Properties: { Handler: "handlers.auth.apiGatewayAuthorizerRest.lambda_handler" },
        })
    );
    expect(functions).toHaveLength(1);
    return (functions[0] as any).Properties.Environment.Variables;
};

describe("REST authorizer environment", () => {
    test("external OAuth: both settings reach the authorizer", () => {
        const env = authorizerEnvironment(
            synthConfig((c) => {
                withExternalOAuth(c);
                idpOf(c).lambdaAuthorizorJWTUserIdClaim = "email";
                idpOf(c).lambdaAuthorizorJWTAudienceClaims = ["aud", "client_id"];
            })
        );
        expect(env.AUTH_MODE).toBe("external");
        expect(env.JWT_AUDIENCE).toBe("test-client");
        expect(env.JWT_USER_ID_CLAIM).toBe("email");
        expect(JSON.parse(env.JWT_AUDIENCE_CLAIMS as string)).toEqual(["aud", "client_id"]);
    });

    test("external OAuth at the defaults: the user id claim is empty and the audience claim is aud", () => {
        const env = authorizerEnvironment(synthConfig(withExternalOAuth));
        expect(env.AUTH_MODE).toBe("external");
        expect(env.JWT_USER_ID_CLAIM).toBe("");
        expect(JSON.parse(env.JWT_AUDIENCE_CLAIMS as string)).toEqual(["aud"]);
    });

    test("Cognito mode: neither variable is set", () => {
        const env = authorizerEnvironment(synthConfig());
        expect(env.AUTH_MODE).toBe("cognito"); // control: this is the Cognito authorizer
        expect(env).not.toHaveProperty("JWT_USER_ID_CLAIM");
        expect(env).not.toHaveProperty("JWT_AUDIENCE_CLAIMS");
        expect(env).not.toHaveProperty("JWT_AUDIENCE");
    });
});

/* -------------------------------------------------------------------------------------------------
 * GET /api/amplify-config
 * ---------------------------------------------------------------------------------------------- */

type BootstrapHandler = (event: {
    requestContext?: { domainName: string; stage: string };
}) => Promise<{ statusCode: number; body: string }>;

/** The amplify-config response body for a config. */
const amplifyConfigBody = async (config: Config.Config): Promise<Record<string, unknown>> => {
    Service.SetConfig(config);
    const stack = new cdk.Stack(newTestApp(), "AmplifyConfig", {
        env: { account: config.env.account, region: config.env.region },
    });
    const construct = new AmplifyConfigLambdaConstruct(stack, "Amplify", {
        config,
        authResources: {
            cognito: { userPoolId: "pool", webClientId: "client", identityPoolId: "identity" },
        } as unknown as authResources,
        region: config.env.region,
        stackName: "AmplifyConfig",
        apiUrl: "",
    });
    const template = Template.fromStack(stack).toJSON();
    const id = stack.getLogicalId(construct.lambdaFn.node.defaultChild as lambda.CfnFunction);
    const exports: { handler?: BootstrapHandler } = {};
    runInNewContext(template.Resources[id].Properties.Code.ZipFile, { exports });
    const response = await exports.handler!({
        requestContext: { domainName: "api.example.com", stage: "api" },
    });
    expect(response.statusCode).toBe(200);
    return JSON.parse(response.body);
};

describe("GET /api/amplify-config", () => {
    test("carries the configured user id claim beside the other external IdP fields", async () => {
        const body = await amplifyConfigBody(
            synthConfig((c) => {
                withExternalOAuth(c);
                idpOf(c).lambdaAuthorizorJWTUserIdClaim = "username";
            })
        );
        expect(body.externalOAuthIdpClientId).toBe("test-client"); // control
        expect(body.externalOAuthIdpUserIdClaim).toBe("username");
    });

    test('says "undefined" when the claim is not set, like the other external IdP fields', async () => {
        const body = await amplifyConfigBody(synthConfig(withExternalOAuth));
        expect(body.externalOAuthIdpClientId).toBe("test-client"); // control
        expect(body.externalOAuthIdpUserIdClaim).toBe("undefined");
    });

    test("the audience claims are not sent to the browser", async () => {
        const body = await amplifyConfigBody(
            synthConfig((c) => {
                withExternalOAuth(c);
                idpOf(c).lambdaAuthorizorJWTAudienceClaims = ["aud", "client_id"];
            })
        );
        expect(JSON.stringify(body)).not.toContain("client_id");
    });
});
