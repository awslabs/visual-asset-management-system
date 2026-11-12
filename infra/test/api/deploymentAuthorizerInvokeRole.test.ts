/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The REST API deployment follows the role API Gateway assumes to invoke the authorizer (#1210).
 *
 * The stage serves a deployment, a snapshot of the API definition taken when the deployment was
 * created. The definition names the invoke role as `Fn::GetAtt <logical id>.Arn`, which is the same
 * before and after the role is renamed, so a deployment whose logical id covered the definition alone
 * outlived a rename: the role was replaced and deleted, and the stage kept a deployment whose
 * authorizer credentials named it, answering every route with a 500 AuthorizerConfigurationException.
 *
 * Pinned here, on a minimal `SpecRestApi` that names its invoke role the way the REST API construct
 * does, with the real `IamRoleTransform` aspect naming the role:
 *   * one configuration gives one deployment logical id, also across two apps in one process (the CDK
 *     token counter keeps running between them, so a hashed token string would differ);
 *   * setting, changing or removing the role name prefix, or the aspect starting to name the role,
 *     gives a new deployment while the API definition stays the same;
 *   * a permissions boundary alone keeps the deployment: it updates the role in place, and its ARN;
 *   * a change to the API definition still gives a new deployment;
 *   * the deployment depends on the role and its policy;
 *   * a pre-created role, which has no resource in the template, adds nothing to the deployment.
 */

import * as cdk from "aws-cdk-lib";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as iam from "aws-cdk-lib/aws-iam";
import { Template } from "aws-cdk-lib/assertions";
import { IamRoleTransform } from "../../lib/aspects/iam-role-transform.aspect";
import { addAuthorizerInvokeRoleToDeployment } from "../../lib/nestedStacks/apiLambda/constructs/rest-api-gateway-construct";
import { newTestApp } from "../support/testApp";

const STACK = "DeploymentInvokeRoleTest";
const ROLE_ID = "RestAuthorizerInvokeRole";
const AUTHORIZER_ARN = "arn:aws:lambda:us-east-1:123456789012:function:authorizer";

interface BuildOptions {
    /** `IamRoleNamePrefix`; undefined applies no aspect, so CloudFormation names the role. */
    prefix?: string;
    permissionsBoundaryArn?: string;
    /** Adds a second route to the API definition. */
    extraRoute?: boolean;
    /** Maps the role to a pre-created role of this name (`iam.Role.customizeRoles`). */
    precreatedRoleName?: string;
}

/** A one-route API definition whose authorizer credentials name `roleArn`, as buildOpenApiSpec does. */
function spec(roleArn: string, extraRoute: boolean): Record<string, unknown> {
    const route = {
        get: {
            security: [{ authorizer: [] }],
            responses: { "200": { description: "OK" } },
            "x-amazon-apigateway-integration": {
                type: "mock",
                requestTemplates: { "application/json": '{"statusCode": 200}' },
                responses: { default: { statusCode: "200" } },
            },
        },
    };
    return {
        openapi: "3.0.1",
        info: { title: STACK, version: "1" },
        paths: extraRoute ? { "/x": route, "/y": route } : { "/x": route },
        components: {
            securitySchemes: {
                authorizer: {
                    type: "apiKey",
                    name: "Authorization",
                    in: "header",
                    "x-amazon-apigateway-authtype": "custom",
                    "x-amazon-apigateway-authorizer": {
                        type: "request",
                        identitySource: "method.request.header.Authorization",
                        authorizerUri: `arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/${AUTHORIZER_ARN}/invocations`,
                        authorizerCredentials: roleArn,
                    },
                },
            },
        },
    };
}

function build(opts: BuildOptions = {}): Template {
    const app = newTestApp();
    if (opts.precreatedRoleName) {
        iam.Role.customizeRoles(app, {
            usePrecreatedRoles: { [`${STACK}/${ROLE_ID}`]: opts.precreatedRoleName },
        });
    }
    const stack = new cdk.Stack(app, STACK, {
        env: { account: "123456789012", region: "us-east-1" },
    });
    if (opts.prefix !== undefined) {
        cdk.Aspects.of(stack).add(new IamRoleTransform(opts.prefix, opts.permissionsBoundaryArn));
    }
    const role = new iam.Role(stack, ROLE_ID, {
        assumedBy: new iam.ServicePrincipal("apigateway.amazonaws.com"),
    });
    role.addToPrincipalPolicy(
        new iam.PolicyStatement({ actions: ["lambda:InvokeFunction"], resources: [AUTHORIZER_ARN] })
    );
    const definition = spec(role.roleArn, opts.extraRoute ?? false);
    const api = new apigw.SpecRestApi(stack, "Api", {
        apiDefinition: apigw.ApiDefinition.fromInline(definition),
        deploy: false,
        cloudWatchRole: false,
    });
    const deployment = new apigw.Deployment(stack, "Deployment", { api });
    deployment.addToLogicalId(definition);
    addAuthorizerInvokeRoleToDeployment(deployment, role);
    new apigw.Stage(stack, "Stage", { deployment, stageName: "api" });
    return Template.fromStack(stack);
}

/** The one Deployment's logical id and resource. */
function deploymentOf(template: Template): [string, any] {
    const deployments = Object.entries<any>(template.findResources("AWS::ApiGateway::Deployment"));
    expect(deployments).toHaveLength(1);
    return deployments[0];
}

const deploymentId = (t: Template) => deploymentOf(t)[0];

const restApiBody = (t: Template) =>
    Object.values<any>(t.findResources("AWS::ApiGateway::RestApi"))[0].Properties.Body;

function roleOf(template: Template): [string, any] {
    const roles = Object.entries<any>(template.findResources("AWS::IAM::Role"));
    expect(roles).toHaveLength(1);
    return roles[0];
}

describe("REST API deployment and the authorizer invoke role", () => {
    it("gives one configuration one deployment logical id, in two apps of one process", () => {
        expect(deploymentId(build({ prefix: "" }))).toBe(deploymentId(build({ prefix: "" })));
        expect(deploymentId(build({ prefix: "vamsx-" }))).toBe(
            deploymentId(build({ prefix: "vamsx-" }))
        );
    });

    it("points the stage at the deployment", () => {
        const template = build({ prefix: "" });
        const stages = Object.values<any>(template.findResources("AWS::ApiGateway::Stage"));
        expect(stages).toHaveLength(1);
        expect(stages[0].Properties.DeploymentId).toEqual({ Ref: deploymentId(template) });
    });

    it.each<[string, BuildOptions, BuildOptions]>([
        ["a prefix is set", { prefix: "" }, { prefix: "vamsx-" }],
        ["the prefix changes", { prefix: "vamsx-" }, { prefix: "vamsy-" }],
        ["the prefix is removed", { prefix: "vamsx-" }, { prefix: "" }],
        ["the aspect starts naming the role", {}, { prefix: "" }],
    ])(
        "gives a new deployment logical id when %s, though the API definition is the same",
        (_, before, after) => {
            const a = build(before);
            const b = build(after);
            // The control: the role really is renamed, which replaces it, and the definition does not
            // show it, so only the role's name can move the deployment's logical id.
            expect(roleOf(b)[0]).toBe(roleOf(a)[0]);
            expect(roleOf(b)[1].Properties.RoleName).not.toEqual(roleOf(a)[1].Properties.RoleName);
            expect(restApiBody(b)).toEqual(restApiBody(a));
            expect(deploymentId(b)).not.toBe(deploymentId(a));
        }
    );

    it("keeps the deployment logical id when only the permissions boundary changes", () => {
        const a = build({ prefix: "vamsx-" });
        const b = build({
            prefix: "vamsx-",
            permissionsBoundaryArn: "arn:aws:iam::123456789012:policy/vams-test-boundary",
        });
        // The boundary is updated in place: the role, its name and its ARN stay.
        expect(roleOf(a)[1].Properties.PermissionsBoundary).toBeUndefined();
        expect(roleOf(b)[1].Properties.PermissionsBoundary).toBe(
            "arn:aws:iam::123456789012:policy/vams-test-boundary"
        );
        expect(roleOf(b)[1].Properties.RoleName).toEqual(roleOf(a)[1].Properties.RoleName);
        expect(deploymentId(b)).toBe(deploymentId(a));
    });

    it("gives a new deployment logical id when the API definition changes", () => {
        const a = build({ prefix: "vamsx-" });
        const b = build({ prefix: "vamsx-", extraRoute: true });
        expect(roleOf(b)[1].Properties.RoleName).toEqual(roleOf(a)[1].Properties.RoleName);
        expect(restApiBody(b)).not.toEqual(restApiBody(a));
        expect(deploymentId(b)).not.toBe(deploymentId(a));
    });

    it("makes the deployment depend on the invoke role and its policy", () => {
        const template = build({ prefix: "" });
        const [roleId] = roleOf(template);
        const policyIds = Object.keys(template.findResources("AWS::IAM::Policy"));
        expect(policyIds).toHaveLength(1);
        expect(deploymentOf(template)[1].DependsOn ?? []).toEqual(
            expect.arrayContaining([roleId, ...policyIds])
        );
    });

    it("adds nothing for a pre-created role, whose ARN is a literal in the API definition", () => {
        const a = build({ prefix: "", precreatedRoleName: "vams-precreated-a" });
        const b = build({ prefix: "", precreatedRoleName: "vams-precreated-b" });
        expect(Object.keys(a.findResources("AWS::IAM::Role"))).toEqual([]);
        expect(JSON.stringify(restApiBody(a))).toContain("role/vams-precreated-a");
        // A different pre-created role is a different definition, which moves the logical id.
        expect(deploymentId(b)).not.toBe(deploymentId(a));
    });
});
