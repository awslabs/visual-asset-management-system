/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { ApiRouteDescriptor } from "../apiRouteRegistry";

export interface OpenApiSpecOptions {
    authorizerFnArn: string;
    authorizerRole: string;
    region: string;
    partition: string;
    cors: { allowOrigins: string; allowHeaders: string; allowMethods: string };
    endpointType: "REGIONAL" | "PRIVATE";
    vpcEndpointIds?: string[];
    title: string;
    /** Integration timeout applied to every Lambda proxy integration, in seconds. */
    timeoutSeconds: number;
}

// Authenticated routes: cache keyed by the Authorization header (per-token), short TTL.
const SECURITY_SCHEME_NAME = "VamsAuthorizer";
const AUTH_CACHE_TTL_SECONDS = 30;

// Anonymous routes (e.g. amplify-config, version): the SAME custom authorizer still runs
// — it performs the IP-restriction check and then allows the ignored path (no
// authentication). The identity source is the source IP (always present, so the authorizer
// is always invoked rather than returning a hard 401 for a missing Authorization header),
// which also keys the cache per-IP. A longer TTL minimizes Lambda invocations for these
// unauthenticated, high-frequency bootstrap endpoints. Keeping an authorizer on every
// route also satisfies account guardrails that flag methods with no authorizer.
const ANON_SECURITY_SCHEME_NAME = "VamsAnonymousAuthorizer";
const ANON_AUTH_CACHE_TTL_SECONDS = 900;

function lambdaProxyUri(partition: string, region: string, fnArn: string): string {
    // Partition-aware API Gateway → Lambda proxy invoke URI.
    return `arn:${partition}:apigateway:${region}:lambda:path/2015-03-31/functions/${fnArn}/invocations`;
}

function corsOptionsOperation(opts: OpenApiSpecOptions): object {
    // MOCK integration that returns the CORS headers (preflight).
    return {
        responses: {
            "200": {
                description: "CORS preflight",
                headers: {
                    "Access-Control-Allow-Origin": { schema: { type: "string" } },
                    "Access-Control-Allow-Methods": { schema: { type: "string" } },
                    "Access-Control-Allow-Headers": { schema: { type: "string" } },
                },
            },
        },
        "x-amazon-apigateway-integration": {
            type: "mock",
            requestTemplates: { "application/json": '{"statusCode": 200}' },
            responses: {
                default: {
                    statusCode: "200",
                    responseParameters: {
                        "method.response.header.Access-Control-Allow-Origin": `'${opts.cors.allowOrigins}'`,
                        "method.response.header.Access-Control-Allow-Methods": `'${opts.cors.allowMethods}'`,
                        "method.response.header.Access-Control-Allow-Headers": `'${opts.cors.allowHeaders}'`,
                    },
                },
            },
        },
    };
}

export function buildOpenApiSpec(routes: ApiRouteDescriptor[], opts: OpenApiSpecOptions): object {
    const paths: Record<string, any> = {};

    for (const r of routes) {
        const verb = r.method.toLowerCase();
        if (!paths[r.path]) paths[r.path] = {};
        const op: any = {
            responses: { "200": { description: "Success" } },
            "x-amazon-apigateway-integration": {
                type: "aws_proxy",
                httpMethod: "POST", // Lambda proxy integration is always POST
                uri: lambdaProxyUri(opts.partition, opts.region, r.lambdaFn.functionArn),
                payloadFormatVersion: "1.0",
                // How long API Gateway waits for the handler before returning 504. Applies to
                // every route; the CORS OPTIONS MOCK integration below is unaffected (it
                // returns immediately and has no backend to wait on).
                timeoutInMillis: opts.timeoutSeconds * 1000,
            },
        };
        // Every route is protected by a custom authorizer. Authenticated routes use the
        // token-keyed authorizer; anonymous routes use the IP-only authorizer (still runs
        // the IP-restriction check, then allows the ignored path without authentication).
        op.security = [
            { [r.allowAnonymous ? ANON_SECURITY_SCHEME_NAME : SECURITY_SCHEME_NAME]: [] },
        ];
        paths[r.path][verb] = op;
        // One OPTIONS per path (idempotent — last writer identical).
        paths[r.path].options = corsOptionsOperation(opts);
    }

    const spec: any = {
        openapi: "3.0.1",
        info: { title: opts.title, version: "1.0" },
        paths,
        components: {
            securitySchemes: {
                // Authenticated routes: REQUEST authorizer keyed by the Authorization header.
                [SECURITY_SCHEME_NAME]: {
                    type: "apiKey",
                    name: "Authorization",
                    in: "header",
                    "x-amazon-apigateway-authtype": "custom",
                    "x-amazon-apigateway-authorizer": {
                        type: "request",
                        identitySource: "method.request.header.Authorization",
                        authorizerUri: lambdaProxyUri(
                            opts.partition,
                            opts.region,
                            opts.authorizerFnArn
                        ),
                        authorizerCredentials: opts.authorizerRole,
                        authorizerResultTtlInSeconds: AUTH_CACHE_TTL_SECONDS,
                    },
                },
                // Anonymous routes: SAME authorizer Lambda, but keyed on the source IP so
                // it is always invoked (runs the IP-restriction check, then allows the
                // ignored path) and never returns a hard 401 for a missing Authorization
                // header. Longer cache TTL reduces Lambda invocations for these endpoints.
                [ANON_SECURITY_SCHEME_NAME]: {
                    type: "apiKey",
                    // 'name'/'in' are unused for an identity source of $context.identity.sourceIp,
                    // but the OpenAPI apiKey scheme requires them; Authorization is harmless here.
                    name: "Authorization",
                    in: "header",
                    "x-amazon-apigateway-authtype": "custom",
                    "x-amazon-apigateway-authorizer": {
                        type: "request",
                        identitySource: "context.identity.sourceIp",
                        authorizerUri: lambdaProxyUri(
                            opts.partition,
                            opts.region,
                            opts.authorizerFnArn
                        ),
                        authorizerCredentials: opts.authorizerRole,
                        authorizerResultTtlInSeconds: ANON_AUTH_CACHE_TTL_SECONDS,
                    },
                },
            },
        },
    };

    // Always emit an explicit resource policy so the deployed policy matches the endpoint
    // type regardless of the API's prior state. CloudFormation / API Gateway does NOT clear a
    // previously-set resource policy when the imported OpenAPI Body merely omits
    // `x-amazon-apigateway-policy` — so switching an existing API from PRIVATE to REGIONAL
    // (or vice versa) would otherwise leave the old policy in place. A stale PRIVATE policy
    // (with an `aws:SourceVpce` condition) left on a now-REGIONAL API denies every public
    // caller with 403 AccessDeniedException ("no resource-based policy allows execute-api:Invoke")
    // at the resource-policy layer — before the method/authorizer/CORS MOCK runs — which the
    // browser surfaces as a missing-CORS/preflight failure. Emitting the policy for both
    // endpoint types guarantees the transition overwrites the old one.
    if (opts.endpointType === "PRIVATE") {
        spec["x-amazon-apigateway-endpoint-configuration"] = {
            vpcEndpointIds: opts.vpcEndpointIds || [],
        };
        // PRIVATE: restrict invoke to the execute-api VPC interface endpoint(s).
        spec["x-amazon-apigateway-policy"] = {
            Version: "2012-10-17",
            Statement: [
                {
                    Effect: "Allow",
                    Principal: "*",
                    Action: "execute-api:Invoke",
                    Resource: "execute-api:/*",
                    Condition: {
                        StringEquals: { "aws:SourceVpce": opts.vpcEndpointIds || [] },
                    },
                },
            ],
        };
    } else {
        // REGIONAL: public endpoint. An allow-all resource policy is functionally equivalent
        // to having no resource policy (the custom Lambda authorizer still governs
        // authentication); its purpose here is to explicitly overwrite any stale PRIVATE
        // policy when an existing API is switched to REGIONAL.
        spec["x-amazon-apigateway-policy"] = {
            Version: "2012-10-17",
            Statement: [
                {
                    Effect: "Allow",
                    Principal: "*",
                    Action: "execute-api:Invoke",
                    Resource: "execute-api:/*",
                },
            ],
        };
    }

    return spec;
}
