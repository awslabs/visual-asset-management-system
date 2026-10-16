/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A custom route is never anonymous.
 *
 * An anonymous route needs an entry in the authorizer's ignored-path list, which is core-owned, so no
 * fork file can widen the unauthenticated surface. CustomBuilderNestedStack checks the routes the
 * fork's buildCustomResources() registers and fails synth on one marked `allowAnonymous`.
 *
 * The fork-owned customResources.ts is mocked here so the check is exercised without editing it.
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { HttpMethod } from "aws-cdk-lib/aws-apigatewayv2";
import {
    RouteRegistry,
    attachFunctionToApi,
} from "../../lib/nestedStacks/apiLambda/apiRouteRegistry";
import { CustomBuilderNestedStack } from "../../lib/nestedStacks/custom/customBuilder-nestedStack";
import { buildCustomResources } from "../../lib/nestedStacks/custom/customResources";
import { newTestApp } from "../support/testApp";

jest.mock("../../lib/nestedStacks/custom/customResources", () => ({
    buildCustomResources: jest.fn(),
}));

const mockedBuild = buildCustomResources as jest.MockedFunction<typeof buildCustomResources>;

/** Build the custom stack with a buildCustomResources() that registers one route. */
function buildWithRoute(allowAnonymous: boolean | undefined, registry = new RouteRegistry()) {
    mockedBuild.mockImplementation((scope) => {
        const fn = new lambda.Function(scope, "ProbeFn", {
            runtime: lambda.Runtime.NODEJS_22_X,
            handler: "index.handler",
            code: lambda.Code.fromInline("exports.handler = async () => ({})"),
        });
        attachFunctionToApi(scope, fn, {
            routePath: "/custom/probe",
            method: HttpMethod.GET,
            registry,
            allowAnonymous,
        });
        return { enabledFeatures: ["CUSTOM_PROBE"] };
    });
    const parent = new cdk.Stack(newTestApp(), "CustomGuardParent");
    const stack = new CustomBuilderNestedStack(parent, "CustomBuilder", {
        registry,
    } as any);
    return { stack, registry };
}

describe("custom route authorization", () => {
    afterEach(() => mockedBuild.mockReset());

    test("an authenticated custom route is registered", () => {
        const { stack, registry } = buildWithRoute(undefined);
        expect(registry.list().map((r) => `${r.method} ${r.path}`)).toEqual(["GET /custom/probe"]);
        expect(stack.enabledFeatures).toEqual(["CUSTOM_PROBE"]);
    });

    test("an anonymous custom route fails synth", () => {
        expect(() => buildWithRoute(true)).toThrow(
            /custom routes cannot be anonymous.*GET \/custom\/probe/
        );
    });

    test("an anonymous route registered BEFORE the custom stack is not attributed to it", () => {
        // Control for the slice: only routes the fork's code added are checked, so the core
        // bootstrap routes registered as anonymous elsewhere do not trip the guard.
        const registry = new RouteRegistry();
        registry.register({
            path: "/api/version",
            method: HttpMethod.GET,
            lambdaFn: {} as any,
            allowAnonymous: true,
        });
        expect(() => buildWithRoute(false, registry)).not.toThrow();
    });
});
