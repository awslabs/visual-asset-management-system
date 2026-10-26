/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import { runInNewContext } from "vm";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import { AmplifyConfigLambdaConstruct } from "../../lib/nestedStacks/apiLambda/constructs/amplify-config-lambda-construct";
import { VamsVersionLambdaConstruct } from "../../lib/nestedStacks/apiLambda/constructs/vams-version-lambda-construct";
import { authResources } from "../../lib/nestedStacks/auth/authBuilder-nestedStack";
import { addBehaviorToCloudFrontDistribution } from "../../lib/nestedStacks/staticWebApp/constructs/cloudfront-s3-website-construct";
import commercial from "../../config/config.template.commercial.json";
import govcloud from "../../config/config.template.govcloud.json";
import eusovereign from "../../config/config.template.eusovereign.json";
import { newTestApp } from "../support/testApp";

interface BootstrapResponse {
    statusCode: number;
    headers: Record<string, string>;
    body: string;
}

type BootstrapHandler = (event: {
    requestContext?: { domainName: string; stage: string };
}) => Promise<BootstrapResponse>;

function synthesizedHandler(stack: cdk.Stack, fn: lambda.Function): BootstrapHandler {
    const template = Template.fromStack(stack).toJSON();
    const id = stack.getLogicalId(fn.node.defaultChild as lambda.CfnFunction);
    const code = template.Resources[id].Properties.Code.ZipFile;
    expect(typeof code).toBe("string");
    const exports: { handler?: BootstrapHandler } = {};
    runInNewContext(code, { exports });
    if (!exports.handler) throw new Error("Bootstrap Lambda did not export a handler");
    return exports.handler;
}

describe.each([
    ["commercial", commercial, "us-east-1"],
    ["govcloud", govcloud, "us-gov-west-1"],
    ["eusovereign", eusovereign, "eusc-de-east-1"],
] as const)("%s bootstrap responses", (_name, raw, region) => {
    let amplifyHandler: BootstrapHandler;
    let versionHandler: BootstrapHandler;

    beforeAll(() => {
        const config = JSON.parse(JSON.stringify(raw)) as Config.Config;
        config.env.region = region;
        config.env.account = "123456789012";
        Service.SetConfig(config);
        const stack = new cdk.Stack(newTestApp(), "Bootstrap", {
            env: { account: config.env.account, region },
        });
        const amplify = new AmplifyConfigLambdaConstruct(stack, "Amplify", {
            config,
            authResources: {
                cognito: {
                    userPoolId: "test-pool",
                    webClientId: "test-client",
                    identityPoolId: "test-identity",
                },
            } as unknown as authResources,
            region,
            stackName: "Bootstrap",
            apiUrl: "https://fallback.example.com/api/",
        });
        const version = new VamsVersionLambdaConstruct(stack, "Version", { config });
        amplifyHandler = synthesizedHandler(stack, amplify.lambdaFn);
        versionHandler = synthesizedHandler(stack, version.lambdaFn);
    });

    test.each(["amplify-config", "version"])("%s prevents caching at the origin", async (route) => {
        const handler = route === "amplify-config" ? amplifyHandler : versionHandler;
        const response = await handler({});
        expect(response.headers["Cache-Control"]).toBe("no-cache, no-store");
    });

    test("amplify-config preserves the deployment configuration, API URL and CORS", async () => {
        const response = await amplifyHandler({
            requestContext: { domainName: "api.example.com", stage: "api" },
        });
        expect(response.statusCode).toBe(200);
        expect(response.headers).toEqual(
            expect.objectContaining({
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            })
        );
        expect(JSON.parse(response.body)).toEqual(
            expect.objectContaining({
                api: "https://api.example.com/api/",
                region,
                stackName: "Bootstrap",
                cognitoUserPoolId: raw.app.authProvider.useCognito.enabled
                    ? "test-pool"
                    : "undefined",
            })
        );
        expect(JSON.parse((await amplifyHandler({})).body).api).toBe(
            "https://fallback.example.com/api/"
        );
    });

    test("version preserves its JSON response and CORS", async () => {
        const response = await versionHandler({});
        expect(response.statusCode).toBe(200);
        expect(response.headers).toEqual(
            expect.objectContaining({
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            })
        );
        expect(JSON.parse(response.body)).toEqual({ version: Config.VAMS_VERSION });
    });
});

test("the API cache policy honors origin headers while forwarding authorization", () => {
    const stack = new cdk.Stack(newTestApp(), "CachePolicy");
    const distribution = new cloudfront.Distribution(stack, "Distribution", {
        defaultBehavior: { origin: new origins.HttpOrigin("web.example.com") },
    });
    addBehaviorToCloudFrontDistribution(stack, distribution, "api.example.com", "api");
    const template = Template.fromStack(stack);
    template.hasResourceProperties("AWS::CloudFront::CachePolicy", {
        CachePolicyConfig: {
            MinTTL: 0,
            DefaultTTL: 86400,
            ParametersInCacheKeyAndForwardedToOrigin: {
                HeadersConfig: { HeaderBehavior: "whitelist", Headers: ["Authorization"] },
                EnableAcceptEncodingGzip: true,
            },
        },
    });
    template.hasResourceProperties("AWS::CloudFront::OriginRequestPolicy", {
        OriginRequestPolicyConfig: { QueryStringsConfig: { QueryStringBehavior: "all" } },
    });
});
