// infra/test/apiRouteRegistry.test.ts
import { HttpMethod } from "aws-cdk-lib/aws-apigatewayv2";
import { RouteRegistry } from "../lib/nestedStacks/apiLambda/apiRouteRegistry";

// Minimal fake that satisfies the lambda.IFunction fields the registry stores.
const fakeFn = (arn: string): any => ({ functionArn: arn, functionName: arn });

describe("RouteRegistry", () => {
    it("stores and lists descriptors", () => {
        const r = new RouteRegistry();
        r.register({ path: "/database", method: HttpMethod.GET, lambdaFn: fakeFn("a") });
        r.register({ path: "/database", method: HttpMethod.POST, lambdaFn: fakeFn("b") });
        expect(r.list()).toHaveLength(2);
    });

    it("throws on duplicate path+method", () => {
        const r = new RouteRegistry();
        r.register({ path: "/database", method: HttpMethod.GET, lambdaFn: fakeFn("a") });
        expect(() =>
            r.register({ path: "/database", method: HttpMethod.GET, lambdaFn: fakeFn("c") })
        ).toThrow(/duplicate/i);
    });

    it("preserves allowAnonymous", () => {
        const r = new RouteRegistry();
        r.register({
            path: "/api/version",
            method: HttpMethod.GET,
            lambdaFn: fakeFn("v"),
            allowAnonymous: true,
        });
        expect(r.list()[0].allowAnonymous).toBe(true);
    });
});
