/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The REST API's gateway responses: the responses API Gateway produces itself, never a Lambda's.
 *
 * What is pinned here, on a minimal `SpecRestApi` with an explicit `Deployment` as the REST API
 * construct builds it:
 *   * the DEFAULT_4XX, DEFAULT_5XX and WAF_FILTERED responses, each carrying the CORS headers, under
 *     the construct ids the deployed resources already have;
 *   * the WAF_FILTERED body: a 403 whose JSON names the AWS WAF web ACL and API Gateway's extended
 *     request ID, which the web ACL log records, under the keys the VAMS CLI and the web app read;
 *   * the coupling to the deployment. A gateway response reaches the stage only through a new
 *     deployment, and the deployment's logical id otherwise covers the API definition alone, so the
 *     deployment depends on every response and its logical id changes with any of them;
 *   * the same responses in the API definition (`x-amazon-apigateway-gateway-responses`). An import
 *     of the definition resets every gateway response it omits to the default, and AWS
 *     CloudFormation imports the definition again on each change to it.
 */

import * as cdk from "aws-cdk-lib";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import { Template } from "aws-cdk-lib/assertions";
import {
    addGatewayResponses,
    GatewayResponseDefinition,
    gatewayResponseDefinitions,
    openApiGatewayResponses,
    WAF_FILTERED_RESPONSE_TEMPLATE,
} from "../../lib/nestedStacks/apiLambda/constructs/rest-api-gateway-construct";
import { newTestApp } from "../support/testApp";

/** The CORS headers the REST API construct puts on its gateway responses. */
const CORS: Record<string, string> = {
    "Access-Control-Allow-Origin": "'*'",
    "Access-Control-Allow-Headers":
        "'Authorization,Content-Type,Origin,Range,X-Amz-Date,X-Api-Key,X-Amz-Security-Token,X-Amz-User-Agent,Access-Control-Allow-Origin'",
};

/** One `GET /x` mock integration: the smallest API definition API Gateway deploys. */
const SPEC = {
    openapi: "3.0.1",
    info: { title: "GatewayResponsesTest", version: "1" },
    paths: {
        "/x": {
            get: {
                responses: { "200": { description: "OK" } },
                "x-amazon-apigateway-integration": {
                    type: "mock",
                    requestTemplates: { "application/json": '{"statusCode": 200}' },
                    responses: { default: { statusCode: "200" } },
                },
            },
        },
    },
};

/** The stack's template, with `definitions` added to its API and deployment. */
function build(
    definitions: GatewayResponseDefinition[] = gatewayResponseDefinitions(CORS)
): Template {
    const stack = new cdk.Stack(newTestApp(), "GatewayResponsesTest", {
        env: { account: "123456789012", region: "us-east-1" },
    });
    const api = new apigw.SpecRestApi(stack, "Api", {
        apiDefinition: apigw.ApiDefinition.fromInline(SPEC),
        deploy: false,
        cloudWatchRole: false,
    });
    const deployment = new apigw.Deployment(stack, "Deployment", { api });
    addGatewayResponses(stack, api, deployment, definitions);
    return Template.fromStack(stack);
}

/** The definitions with one edit applied to the WAF_FILTERED entry of a copy. */
function withWafFiltered(
    edit: (d: GatewayResponseDefinition) => void
): GatewayResponseDefinition[] {
    const definitions = gatewayResponseDefinitions(CORS).map((d) => ({
        ...d,
        responseHeaders: { ...d.responseHeaders },
        templates: d.templates ? { ...d.templates } : undefined,
    }));
    const waf = definitions.find((d) => d.type === apigw.ResponseType.WAF_FILTERED);
    expect(waf).toBeDefined();
    edit(waf as GatewayResponseDefinition);
    return definitions;
}

/** The one Deployment's logical id and resource. */
function deploymentOf(template: Template): [string, any] {
    const deployments = Object.entries<any>(template.findResources("AWS::ApiGateway::Deployment"));
    expect(deployments).toHaveLength(1);
    return deployments[0];
}

/** Each gateway response's logical id and properties, keyed by its response type. */
function responsesByType(template: Template): Record<string, { logicalId: string; props: any }> {
    return Object.fromEntries(
        Object.entries<any>(template.findResources("AWS::ApiGateway::GatewayResponse")).map(
            ([logicalId, r]) => [r.Properties.ResponseType, { logicalId, props: r.Properties }]
        )
    );
}

const CORS_PARAMETERS = {
    "gatewayresponse.header.Access-Control-Allow-Origin": "'*'",
    "gatewayresponse.header.Access-Control-Allow-Headers": CORS["Access-Control-Allow-Headers"],
};

describe("REST API gateway responses", () => {
    it("defines DEFAULT_4XX, DEFAULT_5XX and WAF_FILTERED under the existing construct ids", () => {
        // A changed id would give a deployed response a new logical id, and CloudFormation would
        // create the new resource and then delete the old one, resetting the response type.
        expect(gatewayResponseDefinitions(CORS).map((d) => [d.id, d.type.responseType])).toEqual([
            ["GatewayResponseDefault4XX", "DEFAULT_4XX"],
            ["GatewayResponseDefault5XX", "DEFAULT_5XX"],
            ["GatewayResponseWafFiltered", "WAF_FILTERED"],
        ]);
    });

    it("emits three gateway responses, each with the CORS headers", () => {
        const responses = responsesByType(build());
        expect(Object.keys(responses).sort()).toEqual([
            "DEFAULT_4XX",
            "DEFAULT_5XX",
            "WAF_FILTERED",
        ]);
        for (const { props } of Object.values(responses)) {
            expect(props.ResponseParameters).toEqual(CORS_PARAMETERS);
            expect(props.RestApiId).toEqual({ Ref: expect.stringMatching(/^Api/) });
        }
    });

    it("keeps the default responses' status codes and bodies", () => {
        const responses = responsesByType(build());
        for (const type of ["DEFAULT_4XX", "DEFAULT_5XX"]) {
            expect(responses[type].props.StatusCode).toBeUndefined();
            expect(responses[type].props.ResponseTemplates).toBeUndefined();
        }
    });

    it("answers a request the web ACL blocked with a 403 and the JSON template", () => {
        const waf = responsesByType(build()).WAF_FILTERED.props;
        expect(waf.StatusCode).toBe("403");
        expect(waf.ResponseTemplates).toEqual({
            "application/json": WAF_FILTERED_RESPONSE_TEMPLATE,
        });
    });

    it("names the AWS WAF web ACL and the extended request ID in the body, under the keys clients read", () => {
        expect(JSON.parse(WAF_FILTERED_RESPONSE_TEMPLATE)).toEqual({
            message: "Request blocked by the deployment's AWS WAF web ACL",
            blockedBy: "waf",
            requestId: "$context.extendedRequestId",
        });
    });

    it("makes the deployment depend on every gateway response", () => {
        const template = build();
        const [, deployment] = deploymentOf(template);
        const responseIds = Object.values(responsesByType(template)).map((r) => r.logicalId);
        expect(responseIds).toHaveLength(3);
        expect(deployment.DependsOn ?? []).toEqual(expect.arrayContaining(responseIds));
    });

    it("gives identical definitions the same deployment logical id", () => {
        expect(deploymentOf(build())[0]).toBe(deploymentOf(build())[0]);
    });

    it.each<[string, (d: GatewayResponseDefinition) => void]>([
        [
            "its body template differs by one character",
            (d) => {
                d.templates = {
                    "application/json": WAF_FILTERED_RESPONSE_TEMPLATE.replace(
                        "blocked",
                        "blockeD"
                    ),
                };
            },
        ],
        [
            "its status code differs",
            (d) => {
                d.statusCode = "429";
            },
        ],
        [
            "its headers differ",
            (d) => {
                d.responseHeaders["Access-Control-Allow-Origin"] = "'https://example.com'";
            },
        ],
    ])(
        "gives a new deployment logical id when the WAF_FILTERED response changes: %s",
        (_, edit) => {
            const before = build();
            const after = build(withWafFiltered(edit));
            // The API definition is the same in both stacks, so only the responses can move the id.
            const restApiBody = (t: Template) =>
                Object.values<any>(t.findResources("AWS::ApiGateway::RestApi"))[0].Properties.Body;
            expect(restApiBody(after)).toEqual(restApiBody(before));
            expect(deploymentOf(after)[0]).not.toBe(deploymentOf(before)[0]);
        }
    );
});

describe("REST API gateway responses in the API definition", () => {
    it("lists every response type the resources define", () => {
        expect(
            Object.keys(openApiGatewayResponses(gatewayResponseDefinitions(CORS))).sort()
        ).toEqual(["DEFAULT_4XX", "DEFAULT_5XX", "WAF_FILTERED"]);
    });

    it("gives each response the status code, headers and body its resource has", () => {
        const inDefinition = openApiGatewayResponses(gatewayResponseDefinitions(CORS));
        for (const [type, { props }] of Object.entries(responsesByType(build()))) {
            expect(inDefinition[type]).toEqual({
                ...(props.StatusCode ? { statusCode: props.StatusCode } : {}),
                responseParameters: props.ResponseParameters,
                ...(props.ResponseTemplates ? { responseTemplates: props.ResponseTemplates } : {}),
            });
        }
    });

    it("carries the CORS headers on every response", () => {
        for (const response of Object.values(
            openApiGatewayResponses(gatewayResponseDefinitions(CORS))
        )) {
            expect(response.responseParameters).toEqual(CORS_PARAMETERS);
        }
    });
});
