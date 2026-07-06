/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";

/** One API route contributed by any nested stack. */
export interface ApiRouteDescriptor {
    path: string; // OpenAPI path, e.g. "/database/{databaseId}/assets/{assetId}"
    method: apigateway.HttpMethod;
    lambdaFn: lambda.IFunction;
    allowAnonymous?: boolean; // true → IP-only anonymous authorizer, no token required
}

/**
 * Cross-stack route registry. Nested stacks register descriptors while they build their
 * Lambdas; the REST API builder reads the full set last and renders one OpenAPI document.
 */
export class RouteRegistry {
    private readonly routes: ApiRouteDescriptor[] = [];
    private readonly seen = new Set<string>();

    register(d: ApiRouteDescriptor): void {
        const key = `${d.method} ${d.path}`;
        if (this.seen.has(key)) {
            throw new Error(`ApiRouteRegistry: duplicate route registered: ${key}`);
        }
        this.seen.add(key);
        this.routes.push(d);
    }

    list(): ApiRouteDescriptor[] {
        return [...this.routes];
    }
}

/** Configuration for attaching a single Lambda-backed route to the API via the registry. */
export interface apiGatewayLambdaConfiguration {
    routePath: string;
    method: apigateway.HttpMethod;
    registry: RouteRegistry;
    allowAnonymous?: boolean;
}

/**
 * Register a Lambda-backed route into the cross-stack {@link RouteRegistry}. The API
 * implementation (built last) renders the full registry into the API definition, so this
 * does not create any API resource itself — it only records the route descriptor.
 *
 * `scope` is retained for call-site compatibility (and potential future per-route
 * resources) but is currently unused.
 */
export function attachFunctionToApi(
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    scope: Construct,
    lambdaFunction: lambda.Function,
    apiGatewayConfiguration: apiGatewayLambdaConfiguration
): void {
    apiGatewayConfiguration.registry.register({
        path: apiGatewayConfiguration.routePath,
        method: apiGatewayConfiguration.method,
        lambdaFn: lambdaFunction,
        allowAnonymous: apiGatewayConfiguration.allowAnonymous,
    });
}
