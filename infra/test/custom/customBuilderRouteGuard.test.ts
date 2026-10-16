/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The synth-time checks CustomBuilderNestedStack runs on what the fork's buildCustomResources() does.
 *
 * A custom route is never anonymous, and no route registered before the fork's code runs becomes
 * anonymous while it runs. An anonymous route needs an entry in the authorizer's ignored-path list,
 * which is core-owned, so no fork file can widen the unauthenticated surface. RouteRegistry stores
 * frozen descriptors, so an earlier route cannot be edited through list(); the stack additionally
 * compares the anonymous routes before and after the call, which also catches a descriptor replaced
 * in the registry's private array.
 *
 * A returned feature switch is never a core VAMS_APP_FEATURES value: core switches are enabled by the
 * core stack alone, and publishing one from the fork would tell the web a core feature is deployed.
 *
 * The fork-owned customResources.ts is mocked here so the checks are exercised without editing it.
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { HttpMethod } from "aws-cdk-lib/aws-apigatewayv2";
import { Construct } from "constructs";
import { VAMS_APP_FEATURES } from "../../common/vamsAppFeatures";
import {
    ApiRouteDescriptor,
    RouteRegistry,
    attachFunctionToApi,
} from "../../lib/nestedStacks/apiLambda/apiRouteRegistry";
import { CustomBuilderNestedStack } from "../../lib/nestedStacks/custom/customBuilder-nestedStack";
import { buildCustomResources } from "../../lib/nestedStacks/custom/customResources";
import { CustomResourcesProps } from "../../lib/nestedStacks/custom/customStackHelpers";
import { newTestApp } from "../support/testApp";

jest.mock("../../lib/nestedStacks/custom/customResources", () => ({
    buildCustomResources: jest.fn(),
}));

const mockedBuild = buildCustomResources as jest.MockedFunction<typeof buildCustomResources>;

function probeFn(scope: Construct): lambda.Function {
    return new lambda.Function(scope, "ProbeFn", {
        runtime: lambda.Runtime.NODEJS_22_X,
        handler: "index.handler",
        code: lambda.Code.fromInline("exports.handler = async () => ({})"),
    });
}

/** Build the custom stack around a buildCustomResources() implementation. */
function buildWith(
    impl: (scope: Construct, props: CustomResourcesProps) => { enabledFeatures: string[] },
    registry = new RouteRegistry()
) {
    mockedBuild.mockImplementation(impl);
    const parent = new cdk.Stack(newTestApp(), "CustomGuardParent");
    const stack = new CustomBuilderNestedStack(parent, "CustomBuilder", {
        registry,
    } as any);
    return { stack, registry };
}

/** Build the custom stack with a buildCustomResources() that registers one route. */
function buildWithRoute(allowAnonymous: boolean | undefined, registry = new RouteRegistry()) {
    return buildWith((scope) => {
        attachFunctionToApi(scope, probeFn(scope), {
            routePath: "/custom/probe",
            method: HttpMethod.GET,
            registry,
            allowAnonymous,
        });
        return { enabledFeatures: ["CUSTOM_PROBE"] };
    }, registry);
}

/** A registry holding one authenticated route registered before the custom stack. */
function registryWithCoreRoute(): RouteRegistry {
    const registry = new RouteRegistry();
    registry.register({ path: "/database", method: HttpMethod.GET, lambdaFn: {} as any });
    return registry;
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
        // Control for the before/after comparison: an anonymous route that already existed does not
        // trip the guard, so the core bootstrap routes registered as anonymous are unaffected.
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

describe("routes registered before the custom stack", () => {
    afterEach(() => mockedBuild.mockReset());

    test("cannot be edited through list(): the descriptors are frozen", () => {
        const registry = registryWithCoreRoute();
        expect(() => {
            buildWith((scope, props) => {
                const core = props.registry.list()[0] as { allowAnonymous?: boolean };
                core.allowAnonymous = true;
                return { enabledFeatures: [] };
            }, registry);
        }).toThrow(TypeError);
        expect(registry.list()[0].allowAnonymous).toBeUndefined();
    });

    test("cannot be re-pointed at another Lambda through list()", () => {
        const registry = registryWithCoreRoute();
        const original = registry.list()[0].lambdaFn;
        expect(() => {
            buildWith((scope, props) => {
                const core = props.registry.list()[0] as { lambdaFn: lambda.IFunction };
                core.lambdaFn = probeFn(scope);
                return { enabledFeatures: [] };
            }, registry);
        }).toThrow(TypeError);
        expect(registry.list()[0].lambdaFn).toBe(original);
    });

    test("made anonymous by replacing the stored descriptor fails synth", () => {
        // Bypasses the freeze by swapping the element in the registry's private array; the
        // before/after comparison of anonymous routes still catches it.
        const registry = registryWithCoreRoute();
        expect(() =>
            buildWith((scope, props) => {
                const routes = (props.registry as unknown as { routes: ApiRouteDescriptor[] })
                    .routes;
                routes[0] = { ...routes[0], allowAnonymous: true };
                return { enabledFeatures: [] };
            }, registry)
        ).toThrow(/no existing route can be made anonymous.*GET \/database/);
    });

    test("are left as they were by a fork that only adds its own routes", () => {
        // Control for the three cases above: the same registry and a well-behaved fork synthesize.
        const registry = registryWithCoreRoute();
        const before = registry.list()[0];
        expect(() => buildWithRoute(undefined, registry)).not.toThrow();
        expect(registry.list()[0]).toBe(before);
        expect(Object.isFrozen(registry.list()[1])).toBe(true);
    });
});

describe("custom feature switches", () => {
    afterEach(() => mockedBuild.mockReset());

    test("a core VAMS_APP_FEATURES value fails synth", () => {
        expect(() =>
            buildWith(() => ({
                enabledFeatures: ["CUSTOM_PROBE", VAMS_APP_FEATURES.ALLOWUNSAFEEVAL],
            }))
        ).toThrow(/returned core VAMS_APP_FEATURES values.*ALLOWUNSAFEEVAL/);
    });

    test("every core value is refused", () => {
        for (const core of Object.values(VAMS_APP_FEATURES)) {
            expect(() => buildWith(() => ({ enabledFeatures: [core] }))).toThrow(
                new RegExp(`core VAMS_APP_FEATURES values.*${core}`)
            );
            mockedBuild.mockReset();
        }
    });

    test("a custom value is published", () => {
        // Control: a value outside VAMS_APP_FEATURES passes the same check.
        const { stack } = buildWith(() => ({ enabledFeatures: ["CUSTOM_PROBE"] }));
        expect(stack.enabledFeatures).toEqual(["CUSTOM_PROBE"]);
    });
});
