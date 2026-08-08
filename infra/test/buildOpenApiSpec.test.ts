import { HttpMethod } from "aws-cdk-lib/aws-apigatewayv2";
import { buildOpenApiSpec } from "../lib/nestedStacks/apiLambda/constructs/buildOpenApiSpec";

const fakeFn = (arn: string): any => ({ functionArn: arn });
const baseOpts = {
    authorizerFnArn: "arn:aws:lambda:us-east-1:111:function:authz",
    authorizerRole: "arn:aws:iam::111:role/authzInvoke",
    region: "us-east-1",
    partition: "aws",
    cors: {
        allowOrigins: "*",
        allowHeaders: "Authorization,Content-Type",
        allowMethods: "GET,POST,PUT,DELETE,OPTIONS",
    },
    endpointType: "REGIONAL" as const,
    title: "VAMS",
    timeoutSeconds: 29,
};

describe("buildOpenApiSpec", () => {
    it("emits a path with an AWS_PROXY integration referencing the lambda ARN", () => {
        const spec: any = buildOpenApiSpec(
            [
                {
                    path: "/database",
                    method: HttpMethod.GET,
                    lambdaFn: fakeFn("arn:aws:lambda:us-east-1:111:function:dbFn"),
                },
            ],
            baseOpts
        );
        const op = spec.paths["/database"].get;
        const integ = op["x-amazon-apigateway-integration"];
        expect(integ.type).toBe("aws_proxy");
        expect(integ.httpMethod).toBe("POST"); // lambda proxy is always POST to the integration
        expect(integ.uri).toContain("dbFn");
        expect(integ.uri).toContain("apigateway"); // partition-aware invoke URI
    });

    it("adds a CORS OPTIONS method per path", () => {
        const spec: any = buildOpenApiSpec(
            [{ path: "/database", method: HttpMethod.GET, lambdaFn: fakeFn("a") }],
            baseOpts
        );
        expect(spec.paths["/database"].options).toBeDefined();
    });

    it("protects authenticated routes with the token authorizer and anonymous routes with the IP authorizer", () => {
        const spec: any = buildOpenApiSpec(
            [
                { path: "/database", method: HttpMethod.GET, lambdaFn: fakeFn("a") },
                {
                    path: "/api/version",
                    method: HttpMethod.GET,
                    lambdaFn: fakeFn("v"),
                    allowAnonymous: true,
                },
            ],
            baseOpts
        );
        // Authenticated route -> token-keyed authorizer.
        expect(spec.paths["/database"].get.security).toEqual([{ VamsAuthorizer: [] }]);
        // Anonymous route -> still has an authorizer (NOT no-auth), the IP-only one.
        expect(spec.paths["/api/version"].get.security).toEqual([{ VamsAnonymousAuthorizer: [] }]);
        // Both schemes defined.
        expect(spec.components.securitySchemes.VamsAuthorizer).toBeDefined();
        expect(spec.components.securitySchemes.VamsAnonymousAuthorizer).toBeDefined();
    });

    it("anonymous authorizer is invoked per-request via source-IP identity and uses a longer cache TTL", () => {
        const spec: any = buildOpenApiSpec(
            [
                {
                    path: "/api/version",
                    method: HttpMethod.GET,
                    lambdaFn: fakeFn("v"),
                    allowAnonymous: true,
                },
            ],
            baseOpts
        );
        const authed =
            spec.components.securitySchemes.VamsAuthorizer["x-amazon-apigateway-authorizer"];
        const anon =
            spec.components.securitySchemes.VamsAnonymousAuthorizer[
                "x-amazon-apigateway-authorizer"
            ];
        // Anonymous authorizer keys on source IP (always present -> never a hard 401 for a
        // missing Authorization header) so the Lambda always runs the IP-restriction check.
        expect(anon.identitySource).toBe("context.identity.sourceIp");
        expect(authed.identitySource).toBe("method.request.header.Authorization");
        // Both use the same authorizer Lambda.
        expect(anon.authorizerUri).toBe(authed.authorizerUri);
        // Anonymous routes cache longer to minimize Lambda invocations.
        expect(anon.authorizerResultTtlInSeconds).toBeGreaterThan(
            authed.authorizerResultTtlInSeconds
        );
        expect(anon.authorizerResultTtlInSeconds).toBe(900);
    });

    it("applies the configured integration timeout, in milliseconds, to every route", () => {
        const spec: any = buildOpenApiSpec(
            [
                { path: "/database", method: HttpMethod.GET, lambdaFn: fakeFn("a") },
                {
                    path: "/api/version",
                    method: HttpMethod.GET,
                    lambdaFn: fakeFn("v"),
                    allowAnonymous: true,
                },
            ],
            { ...baseOpts, timeoutSeconds: 120 }
        );
        expect(spec.paths["/database"].get["x-amazon-apigateway-integration"].timeoutInMillis).toBe(
            120000
        );
        expect(
            spec.paths["/api/version"].get["x-amazon-apigateway-integration"].timeoutInMillis
        ).toBe(120000);
    });

    it("does not set a timeout on the CORS OPTIONS mock integration", () => {
        const spec: any = buildOpenApiSpec(
            [{ path: "/database", method: HttpMethod.GET, lambdaFn: fakeFn("a") }],
            { ...baseOpts, timeoutSeconds: 120 }
        );
        const optionsInteg = spec.paths["/database"].options["x-amazon-apigateway-integration"];
        expect(optionsInteg.type).toBe("mock");
        expect(optionsInteg.timeoutInMillis).toBeUndefined();
    });

    it("adds private endpoint config + resource policy when PRIVATE", () => {
        const spec: any = buildOpenApiSpec(
            [{ path: "/database", method: HttpMethod.GET, lambdaFn: fakeFn("a") }],
            { ...baseOpts, endpointType: "PRIVATE", vpcEndpointIds: ["vpce-123"] }
        );
        expect(spec["x-amazon-apigateway-endpoint-configuration"].vpcEndpointIds).toContain(
            "vpce-123"
        );
        expect(spec["x-amazon-apigateway-policy"]).toBeDefined();
    });
});
